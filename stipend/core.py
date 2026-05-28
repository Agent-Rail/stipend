"""The Stipend class.

This is the single composition point for the policy engine, audit log, and
the active backend. CLI, MCP server, and SDK consumers all construct one of
these and call its verbs.

Per /plan-eng-review decisions:

- **1D** lifecycle: per-process for CLI, per-server-session for MCP,
  caller-controlled for SDK. The class itself is stateless across instances;
  durable state lives in ``.stipend/state.db`` (mock backend) and
  ``.stipend/audit.jsonl``.

- **2B** API shape: top-level verbs (``s.pay``, ``s.charge``, ``s.refund``)
  for the most-common actions, plus nested query namespaces (``s.charges``,
  ``s.audit``). Intentional mix; matches Resend / Plaid prior art.

- **D3 / T9** privacy: the ``prompt`` kwarg on verbs is optional. ``None``
  means do not record the originating natural-language request in the audit
  entry.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from stipend.audit import AuditEntry, AuditLog, now_iso
from stipend.backends.agentrail import AgentRailBackend
from stipend.backends.base import Backend
from stipend.backends.mock import MockBackend
from stipend.errors import ApprovalRequired, PolicyDenied
from stipend.policy import Policy, PolicyDecision, PolicyEngine
from stipend.receipt import Receipt


def _new_audit_id() -> str:
    """Generate a Stipend-issued audit identifier."""
    return "aud_" + secrets.token_urlsafe(12)


def _resolve_backend(backend: str | Backend, state_dir: Path) -> Backend:
    """Resolve a backend kwarg to a concrete :class:`Backend` instance."""
    if isinstance(backend, Backend):
        return backend
    if backend == "mock":
        return MockBackend(state_dir=state_dir)
    if backend == "agentrail":
        return AgentRailBackend()
    raise ValueError(
        f"unknown backend {backend!r}; expected 'mock', 'agentrail', or a Backend instance"
    )


class Stipend:
    """Compose policy + audit + backend behind a small verb-shaped API.

    Args:
        policy: Either a path to a policy YAML file (``str`` / ``Path``) or
            an already-loaded :class:`stipend.policy.Policy`.
        backend: ``"mock"`` (default) constructs a local SQLite-backed mock;
            ``"agentrail"`` constructs the stub that raises
            :class:`stipend.errors.NotYetAvailable`; or pass a custom
            :class:`Backend` instance to override.
        root: Directory holding ``.stipend/`` state and audit log. Defaults
            to ``./.stipend/`` relative to the current working directory.
    """

    def __init__(
        self,
        policy: str | Path | Policy,
        backend: str | Backend = "mock",
        root: str | Path = ".stipend",
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

        if isinstance(policy, Policy):
            self.policy = policy
        else:
            self.policy = Policy.load(str(policy))

        self.audit = AuditLog(self.root)
        self.backend = _resolve_backend(backend, state_dir=self.root)
        self.engine = PolicyEngine(self.policy, self.audit)

        # Sub-namespaces for query operations (API shape 2B).
        self.charges = _ChargesNamespace(self)

    # ---------------------------------------------------------------- verbs

    def pay(
        self,
        *,
        recipient: str,
        amount_cents: int,
        currency: str = "USD",
        memo: str = "",
        prompt: str | None = None,
    ) -> Receipt:
        """Send a mock payment to ``recipient``.

        Raises:
            PolicyDenied: when the policy engine denies the transaction.
            ApprovalRequired: when the transaction is at or above the
                configured approval threshold. v0.1 does not resubmit.
        """
        return self._do_payment(
            tool="pay",
            recipient=recipient,
            amount_cents=amount_cents,
            currency=currency,
            memo=memo,
            prompt=prompt,
        )

    def charge(
        self,
        *,
        source: str,
        amount_cents: int,
        currency: str = "USD",
        memo: str = "",
        prompt: str | None = None,
    ) -> Receipt:
        """Pull a mock charge from ``source``.

        Same policy semantics as :meth:`pay`; the source is treated as the
        recipient string for policy purposes (so allow / deny lists work the
        same way).
        """
        return self._do_payment(
            tool="charge",
            recipient=source,
            amount_cents=amount_cents,
            currency=currency,
            memo=memo,
            prompt=prompt,
        )

    def refund(
        self,
        *,
        payment_id: str,
        amount_cents: int | None = None,
        prompt: str | None = None,
    ) -> Receipt:
        """Issue a mock refund against a prior payment.

        Bypasses the policy engine (refunds are not new outflows) but is
        still recorded in the audit log with the parent payment id.
        """
        result = self.backend.refund(payment_id=payment_id, amount_cents=amount_cents)
        audit_id = _new_audit_id()
        self.audit.append(
            AuditEntry(
                id=audit_id,
                ts=now_iso(),
                agent=self.policy.agent,
                prompt=prompt,
                tool="refund",
                args={"payment_id": payment_id, "amount_cents": amount_cents},
                policy_decision="allow",  # refunds bypass policy in v0.1
                backend=self.backend.name,
                receipt_id=result.id,
                status=result.status,
                parent_audit_id=None,
            )
        )
        return result

    def policy_check(
        self,
        *,
        amount_cents: int,
        recipient: str,
        currency: str = "USD",
    ) -> PolicyDecision:
        """Run the policy engine without executing the transaction.

        Records the check in the audit log so policy probes are visible
        downstream. Does NOT call the backend.
        """
        decision = self.engine.evaluate(amount_cents, recipient, currency)
        self.audit.append(
            AuditEntry(
                id=_new_audit_id(),
                ts=now_iso(),
                agent=self.policy.agent,
                tool="policy_check",
                args={
                    "amount_cents": amount_cents,
                    "recipient": recipient,
                    "currency": currency,
                },
                policy_decision=decision.decision,
                backend=self.backend.name,
                status="probe",
                error=decision.reason if decision.decision != "allow" else None,
            )
        )
        return decision

    # --------------------------------------------------------------- private

    def _do_payment(
        self,
        *,
        tool: str,
        recipient: str,
        amount_cents: int,
        currency: str,
        memo: str,
        prompt: str | None,
    ) -> Receipt:
        decision = self.engine.evaluate(amount_cents, recipient, currency)
        audit_id = _new_audit_id()
        if decision.decision == "deny":
            self.audit.append(
                AuditEntry(
                    id=audit_id,
                    ts=now_iso(),
                    agent=self.policy.agent,
                    prompt=prompt,
                    tool=tool,
                    args={
                        "recipient": recipient,
                        "amount_cents": amount_cents,
                        "currency": currency.upper(),
                        "memo": memo,
                    },
                    policy_decision="deny",
                    backend=self.backend.name,
                    status="denied",
                    error=decision.reason,
                )
            )
            raise PolicyDenied(decision.reason)

        if decision.decision == "requires_approval":
            self.audit.append(
                AuditEntry(
                    id=audit_id,
                    ts=now_iso(),
                    agent=self.policy.agent,
                    prompt=prompt,
                    tool=tool,
                    args={
                        "recipient": recipient,
                        "amount_cents": amount_cents,
                        "currency": currency.upper(),
                        "memo": memo,
                    },
                    policy_decision="requires_approval",
                    backend=self.backend.name,
                    status="requires_approval",
                    error=decision.reason,
                )
            )
            threshold = self.policy.approval_threshold(currency.upper()) or 0
            raise ApprovalRequired(decision.reason, threshold, currency.upper())

        # allow -> dispatch to backend
        receipt = self.backend.pay(
            recipient=recipient,
            amount_cents=amount_cents,
            currency=currency.upper(),
            memo=memo,
        )
        self.audit.append(
            AuditEntry(
                id=audit_id,
                ts=now_iso(),
                agent=self.policy.agent,
                prompt=prompt,
                tool=tool,
                args={
                    "recipient": recipient,
                    "amount_cents": amount_cents,
                    "currency": currency.upper(),
                    "memo": memo,
                },
                policy_decision="allow",
                backend=self.backend.name,
                receipt_id=receipt.id,
                status=receipt.status,
            )
        )
        # Stitch the audit id back onto the receipt so renderers can show it.
        return _with_audit_id(receipt, audit_id)


class _ChargesNamespace:
    """Sub-namespace for query operations on charges.

    Implements the namespace half of API shape 2B; verb actions live on the
    parent :class:`Stipend`.
    """

    def __init__(self, parent: Stipend) -> None:
        self._parent = parent

    def list(self) -> list[dict[str, Any]]:
        """Return all charge / pay entries from the audit log."""
        return [
            {
                "id": e.receipt_id,
                "recipient": e.args.get("recipient"),
                "amount_cents": e.args.get("amount_cents"),
                "currency": e.args.get("currency"),
                "status": e.status,
                "ts": e.ts,
            }
            for e in self._parent.audit.read_all()
            if e.tool in {"pay", "charge"}
        ]


def _with_audit_id(receipt: Receipt, audit_id: str) -> Receipt:
    """Return a copy of ``receipt`` with ``audit_id`` set."""
    from dataclasses import replace

    return replace(receipt, audit_id=audit_id)
