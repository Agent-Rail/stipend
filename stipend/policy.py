"""Policy engine.

Per /plan-eng-review decisions:

- **1C**: window queries (daily / monthly) are computed by scanning the audit
  log on each evaluation. Simple, auditable, O(N) per check; acceptable at
  v0.1 throughput. Materializing running totals is a v0.2 TODO.
- **v0.1 cuts**: USD is the only allowed currency; the ``velocity`` field is
  reserved in the schema but the engine no-ops it (any velocity config is
  accepted by the schema but ignored at evaluation time). Approvals return a
  ``requires_approval`` decision but the engine does NOT implement
  resubmission semantics; that is a v0.2 TODO.
- **Reviewer Concerns #4 / T13**: when an allow-list pattern and a deny-list
  pattern both match the same recipient, the deny-list wins.

Evaluation order (precedence highest first):

1. Currency check: anything other than USD is denied.
2. Per-transaction cap: deny if exceeded.
3. Blocked recipients: deny if matched (exact or glob).
4. Allowed recipients: if the list is non-empty, deny if not matched.
5. Daily window cap: deny if would exceed.
6. Monthly window cap: deny if would exceed.
7. Approval threshold: ``requires_approval`` if at or above the threshold.
8. Otherwise: ``allow``.

The order matters: deny-list wins over allow-list because step 3 runs
first, and caps win over approval thresholds because a transaction over a
hard cap should never be eligible for approval.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jsonschema
import yaml

from stipend.audit import AuditLog
from stipend.errors import PolicyConfigError
from stipend.schemas import load_policy_schema

V01_SUPPORTED_CURRENCIES: frozenset[str] = frozenset({"USD"})
"""Currencies the v0.1 engine will allow. Anything else is denied."""

PolicyDecisionLiteral = Literal["allow", "deny", "requires_approval"]


@dataclass(frozen=True)
class PolicyDecision:
    """The result of a single policy evaluation.

    Attributes:
        decision: ``allow``, ``deny``, or ``requires_approval``.
        reason: A short human-readable explanation. Always populated; for
            ``allow`` decisions it summarizes what was checked.
        rule: Which rule produced the decision (``per_transaction_cap``,
            ``recipient_blocked``, etc.). ``None`` for ``allow`` decisions.
        amount_cents: Echo of the input amount, for audit logging.
        recipient: Echo of the input recipient, for audit logging.
        currency: Echo of the input currency, for audit logging.
    """

    decision: PolicyDecisionLiteral
    reason: str
    amount_cents: int
    recipient: str
    currency: str
    rule: str | None = None

    @property
    def allowed(self) -> bool:
        """True if the decision is ``allow``."""
        return self.decision == "allow"


@dataclass(frozen=True)
class Policy:
    """A loaded, validated policy.

    Use :meth:`Policy.load` to load from a YAML file path or :meth:`from_dict`
    to load from an already-parsed dict.
    """

    version: int
    agent: str
    raw: dict[str, Any] = field(repr=False)

    @classmethod
    def load(cls, path: str) -> Policy:
        """Load a policy from a YAML file."""
        try:
            with open(path, encoding="utf-8") as fp:
                data = yaml.safe_load(fp)
        except FileNotFoundError as exc:
            raise PolicyConfigError(f"policy file not found: {path}") from exc
        except yaml.YAMLError as exc:
            raise PolicyConfigError(f"policy YAML parse error: {exc}") from exc
        if not isinstance(data, dict):
            raise PolicyConfigError(
                f"policy file must contain a YAML object at the top level, got "
                f"{type(data).__name__}"
            )
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Policy:
        """Build a Policy from an already-parsed dict; schema-validates."""
        schema = load_policy_schema()
        try:
            jsonschema.validate(instance=data, schema=schema)
        except jsonschema.ValidationError as exc:
            # Surface the offending path so the engineer / user can find it.
            path = "/".join(str(p) for p in exc.absolute_path) or "(root)"
            raise PolicyConfigError(
                f"policy schema error at {path}: {exc.message}"
            ) from exc
        return cls(version=int(data["version"]), agent=str(data["agent"]), raw=data)

    # ----------------------------------------------------------- accessors

    def per_transaction_cap(self, currency: str) -> int | None:
        return self._cap("per_transaction_cap", currency)

    def daily_cap(self, currency: str) -> int | None:
        return self._cap("daily_cap", currency)

    def monthly_cap(self, currency: str) -> int | None:
        return self._cap("monthly_cap", currency)

    def approval_threshold(self, currency: str) -> int | None:
        return self._currency_map("approvals", "requires_approval_above").get(currency)

    @property
    def allowed_recipients(self) -> list[str]:
        return list(self.raw.get("recipients", {}).get("allowed", []))

    @property
    def blocked_recipients(self) -> list[str]:
        return list(self.raw.get("recipients", {}).get("blocked", []))

    def _cap(self, key: str, currency: str) -> int | None:
        limits = self.raw.get("limits") or {}
        return (limits.get(key) or {}).get(currency)

    def _currency_map(self, *path: str) -> dict[str, int]:
        cursor: Any = self.raw
        for p in path:
            cursor = (cursor or {}).get(p)
            if cursor is None:
                return {}
        if not isinstance(cursor, dict):
            return {}
        return cursor


class PolicyEngine:
    """The runtime policy engine.

    Constructed with a :class:`Policy` and an :class:`AuditLog`. The audit
    log is read on every :meth:`evaluate` call so cross-process state stays
    coherent (per decision 1C).
    """

    def __init__(self, policy: Policy, audit: AuditLog) -> None:
        self.policy = policy
        self.audit = audit

    def evaluate(
        self,
        amount_cents: int,
        recipient: str,
        currency: str,
    ) -> PolicyDecision:
        """Evaluate a single proposed transaction against the policy."""
        currency = currency.upper()

        # 1. Currency check.
        if currency not in V01_SUPPORTED_CURRENCIES:
            return PolicyDecision(
                decision="deny",
                rule="currency_unsupported",
                reason=f"v0.1 supports {sorted(V01_SUPPORTED_CURRENCIES)} only; got {currency}",
                amount_cents=amount_cents,
                recipient=recipient,
                currency=currency,
            )

        # 2. Per-transaction cap.
        per_txn = self.policy.per_transaction_cap(currency)
        if per_txn is not None and amount_cents > per_txn:
            return PolicyDecision(
                decision="deny",
                rule="per_transaction_cap",
                reason=(
                    f"amount {_fmt(amount_cents, currency)} exceeds per-transaction "
                    f"cap {_fmt(per_txn, currency)}"
                ),
                amount_cents=amount_cents,
                recipient=recipient,
                currency=currency,
            )

        # 3. Blocked recipients (wins over allow-list).
        for pattern in self.policy.blocked_recipients:
            if fnmatch.fnmatchcase(recipient, pattern):
                return PolicyDecision(
                    decision="deny",
                    rule="recipient_blocked",
                    reason=f"recipient {recipient!r} matches blocked pattern {pattern!r}",
                    amount_cents=amount_cents,
                    recipient=recipient,
                    currency=currency,
                )

        # 4. Allowed recipients (only enforced if non-empty).
        allowed = self.policy.allowed_recipients
        if allowed and not any(fnmatch.fnmatchcase(recipient, p) for p in allowed):
            return PolicyDecision(
                decision="deny",
                rule="recipient_not_allowed",
                reason=(
                    f"recipient {recipient!r} does not match any allowed pattern in "
                    f"{allowed}"
                ),
                amount_cents=amount_cents,
                recipient=recipient,
                currency=currency,
            )

        # 5/6. Window caps. Read the audit log per call (decision 1C).
        spent_today = self._window_spend(currency, timedelta(days=1))
        daily_cap = self.policy.daily_cap(currency)
        if daily_cap is not None and spent_today + amount_cents > daily_cap:
            return PolicyDecision(
                decision="deny",
                rule="daily_cap",
                reason=(
                    f"daily total would reach {_fmt(spent_today + amount_cents, currency)}; "
                    f"cap is {_fmt(daily_cap, currency)} (already spent "
                    f"{_fmt(spent_today, currency)})"
                ),
                amount_cents=amount_cents,
                recipient=recipient,
                currency=currency,
            )

        spent_month = self._window_spend(currency, timedelta(days=30))
        monthly_cap = self.policy.monthly_cap(currency)
        if monthly_cap is not None and spent_month + amount_cents > monthly_cap:
            return PolicyDecision(
                decision="deny",
                rule="monthly_cap",
                reason=(
                    f"monthly total would reach {_fmt(spent_month + amount_cents, currency)}; "
                    f"cap is {_fmt(monthly_cap, currency)} (already spent "
                    f"{_fmt(spent_month, currency)})"
                ),
                amount_cents=amount_cents,
                recipient=recipient,
                currency=currency,
            )

        # 7. Approval threshold.
        threshold = self.policy.approval_threshold(currency)
        if threshold is not None and amount_cents >= threshold:
            return PolicyDecision(
                decision="requires_approval",
                rule="approvals_threshold",
                reason=(
                    f"amount {_fmt(amount_cents, currency)} is at or above the approval "
                    f"threshold {_fmt(threshold, currency)}; v0.1 does not implement "
                    "resubmission, callers must resolve approvals out-of-band"
                ),
                amount_cents=amount_cents,
                recipient=recipient,
                currency=currency,
            )

        # 8. Default: allow.
        return PolicyDecision(
            decision="allow",
            rule=None,
            reason=_summarize_allow(
                amount_cents=amount_cents,
                currency=currency,
                per_txn=per_txn,
                spent_today=spent_today,
                daily_cap=daily_cap,
            ),
            amount_cents=amount_cents,
            recipient=recipient,
            currency=currency,
        )

    # --------------------------------------------------------------- private

    def _window_spend(self, currency: str, window: timedelta) -> int:
        """Sum settled or pending amounts in the given trailing window."""
        cutoff = (
            datetime.now(UTC) - window
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        total = 0
        for entry in self.audit.read_all():
            if entry.ts < cutoff:
                continue
            if entry.policy_decision != "allow":
                continue
            if entry.status not in {"pending", "settled"}:
                continue
            args = entry.args
            if args.get("currency", "").upper() != currency:
                continue
            try:
                total += int(args.get("amount_cents", 0))
            except (TypeError, ValueError):
                continue
        return total


def _fmt(cents: int, currency: str) -> str:
    """Format a cent amount for the brand-surface trace.

    The trace block is the locked brand surface (see spec). Round dollar
    amounts render without decimals (``$25,000``) and fractional amounts
    keep two decimals (``$4,249.85``). The currency code is omitted
    because v0.1 is USD-only and the trace would otherwise read as
    ``USD $...`` everywhere — clutter for no information gain. The
    ``currency`` arg is retained for the v0.2 multi-currency switch.
    """
    del currency  # explicit no-op marker until v0.2
    if cents % 100 == 0:
        return f"${cents // 100:,}"
    return f"${cents / 100:,.2f}"


def _summarize_allow(
    *,
    amount_cents: int,
    currency: str,
    per_txn: int | None,
    spent_today: int,
    daily_cap: int | None,
) -> str:
    """Compact summary string for the trace block's policy line on allow."""
    parts: list[str] = []
    if per_txn is not None:
        parts.append(f"per_txn {_fmt(per_txn, currency)}  ✓")
    if daily_cap is not None:
        parts.append(
            f"daily  {_fmt(spent_today + amount_cents, currency)} / "
            f"{_fmt(daily_cap, currency)}  ✓"
        )
    if not parts:
        return "no limits configured  ✓"
    return "   ".join(parts)
