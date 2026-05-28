"""Policy engine branch coverage.

Covers every rule in the precedence list documented in stipend.policy:
currency, per-transaction cap, blocked recipients (including tie-break with
allow), allowed-only restriction, daily and monthly windows, approval
threshold, and the default-allow path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stipend.audit import AuditEntry, AuditLog, now_iso
from stipend.errors import PolicyConfigError
from stipend.policy import Policy, PolicyEngine


def _engine(policy_path: Path, stipend_root: Path) -> PolicyEngine:
    policy = Policy.load(str(policy_path))
    audit = AuditLog(stipend_root)
    return PolicyEngine(policy, audit)


# ----------------------------------------------------------- happy paths

def test_allow_within_caps(policy_path: Path, stipend_root: Path) -> None:
    engine = _engine(policy_path, stipend_root)
    d = engine.evaluate(amount_cents=5_000_00, recipient="acme-logistics", currency="USD")
    assert d.decision == "allow"
    assert d.rule is None


def test_allow_glob_recipient(policy_path: Path, stipend_root: Path) -> None:
    engine = _engine(policy_path, stipend_root)
    d = engine.evaluate(amount_cents=1_000_00, recipient="carrier-123", currency="USD")
    assert d.decision == "allow"


# ------------------------------------------------------------- currency

def test_deny_non_usd(policy_path: Path, stipend_root: Path) -> None:
    engine = _engine(policy_path, stipend_root)
    d = engine.evaluate(amount_cents=1_00, recipient="acme-logistics", currency="EUR")
    assert d.decision == "deny"
    assert d.rule == "currency_unsupported"


# ---------------------------------------------------- per-transaction cap

def test_deny_over_per_txn(policy_path: Path, stipend_root: Path) -> None:
    engine = _engine(policy_path, stipend_root)
    d = engine.evaluate(amount_cents=30_000_00, recipient="acme-logistics", currency="USD")
    assert d.decision == "deny"
    assert d.rule == "per_transaction_cap"


# ------------------------------------------------------------ recipients

def test_deny_blocked_exact(policy_path: Path, stipend_root: Path) -> None:
    engine = _engine(policy_path, stipend_root)
    d = engine.evaluate(amount_cents=1_00, recipient="sanctioned-co", currency="USD")
    assert d.decision == "deny"
    assert d.rule == "recipient_blocked"


def test_deny_blocked_glob(policy_path: Path, stipend_root: Path) -> None:
    engine = _engine(policy_path, stipend_root)
    d = engine.evaluate(amount_cents=1_00, recipient="sanctioned-anything", currency="USD")
    assert d.decision == "deny"
    assert d.rule == "recipient_blocked"


def test_deny_blocked_wins_over_allowed(tmp_path: Path, stipend_root: Path) -> None:
    """Allow + deny both match: deny wins (Reviewer Concerns #4 / T13)."""
    (tmp_path / "policy.yaml").write_text(
        """version: 1
agent: test
recipients:
  allowed: ["carrier-*"]
  blocked: ["*-restricted"]
""",
        encoding="utf-8",
    )
    engine = _engine(tmp_path / "policy.yaml", stipend_root)
    d = engine.evaluate(amount_cents=1_00, recipient="carrier-restricted", currency="USD")
    assert d.decision == "deny"
    assert d.rule == "recipient_blocked"


def test_deny_not_allowed(policy_path: Path, stipend_root: Path) -> None:
    engine = _engine(policy_path, stipend_root)
    d = engine.evaluate(amount_cents=1_00, recipient="random-vendor", currency="USD")
    assert d.decision == "deny"
    assert d.rule == "recipient_not_allowed"


# --------------------------------------------------------------- windows

def _seed_settled_payment(audit: AuditLog, amount_cents: int, recipient: str) -> None:
    audit.append(
        AuditEntry(
            id=f"aud_{recipient}_{amount_cents}",
            ts=now_iso(),
            agent="test-agent",
            tool="pay",
            args={"recipient": recipient, "amount_cents": amount_cents, "currency": "USD"},
            policy_decision="allow",
            backend="mock",
            status="settled",
        )
    )


def test_deny_daily_cap(policy_path: Path, stipend_root: Path) -> None:
    policy = Policy.load(str(policy_path))
    audit = AuditLog(stipend_root)
    # Seed close to the daily cap.
    _seed_settled_payment(audit, 240_000_00, "acme-logistics")
    engine = PolicyEngine(policy, audit)
    d = engine.evaluate(amount_cents=15_000_00, recipient="acme-logistics", currency="USD")
    assert d.decision == "deny"
    assert d.rule == "daily_cap"


def test_allow_under_daily_cap_with_prior_spend(
    policy_path: Path, stipend_root: Path
) -> None:
    policy = Policy.load(str(policy_path))
    audit = AuditLog(stipend_root)
    _seed_settled_payment(audit, 100_000_00, "acme-logistics")
    engine = PolicyEngine(policy, audit)
    d = engine.evaluate(amount_cents=5_000_00, recipient="acme-logistics", currency="USD")
    assert d.decision == "allow"


def test_deny_monthly_cap(tmp_path: Path, stipend_root: Path) -> None:
    (tmp_path / "policy.yaml").write_text(
        """version: 1
agent: test
limits:
  per_transaction_cap: { USD: 100_000_00 }
  monthly_cap:         { USD: 50_000_00 }
recipients:
  allowed: ["acme"]
""",
        encoding="utf-8",
    )
    policy = Policy.load(str(tmp_path / "policy.yaml"))
    audit = AuditLog(stipend_root)
    _seed_settled_payment(audit, 40_000_00, "acme")
    engine = PolicyEngine(policy, audit)
    d = engine.evaluate(amount_cents=20_000_00, recipient="acme", currency="USD")
    assert d.decision == "deny"
    assert d.rule == "monthly_cap"


# ------------------------------------------------------ approval threshold

def test_requires_approval_at_threshold(policy_path: Path, stipend_root: Path) -> None:
    engine = _engine(policy_path, stipend_root)
    d = engine.evaluate(amount_cents=10_000_00, recipient="acme-logistics", currency="USD")
    assert d.decision == "requires_approval"
    assert d.rule == "approvals_threshold"


def test_requires_approval_above_threshold(
    policy_path: Path, stipend_root: Path
) -> None:
    engine = _engine(policy_path, stipend_root)
    d = engine.evaluate(amount_cents=15_000_00, recipient="acme-logistics", currency="USD")
    assert d.decision == "requires_approval"


def test_cap_wins_over_approval(policy_path: Path, stipend_root: Path) -> None:
    """A transaction over the per-txn cap denies; it does NOT 'require approval'."""
    engine = _engine(policy_path, stipend_root)
    d = engine.evaluate(amount_cents=30_000_00, recipient="acme-logistics", currency="USD")
    assert d.decision == "deny"
    assert d.rule == "per_transaction_cap"


# ----------------------------------------------------------- config errors

def test_invalid_yaml(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(": not valid yaml :", encoding="utf-8")
    with pytest.raises(PolicyConfigError):
        Policy.load(str(p))


def test_missing_required_field(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("version: 1\n", encoding="utf-8")
    with pytest.raises(PolicyConfigError):
        Policy.load(str(p))


def test_unknown_top_level_key(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("version: 1\nagent: x\nbogus: y\n", encoding="utf-8")
    with pytest.raises(PolicyConfigError):
        Policy.load(str(p))


def test_yaml_not_a_dict(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(PolicyConfigError):
        Policy.load(str(p))


def test_missing_file() -> None:
    with pytest.raises(PolicyConfigError):
        Policy.load("/does/not/exist.yaml")


# ----------------------------------------------------------- accessors

def test_accessors_when_no_limits_configured(tmp_path: Path) -> None:
    p = tmp_path / "minimal.yaml"
    p.write_text("version: 1\nagent: x\n", encoding="utf-8")
    pol = Policy.load(str(p))
    assert pol.per_transaction_cap("USD") is None
    assert pol.daily_cap("USD") is None
    assert pol.monthly_cap("USD") is None
    assert pol.approval_threshold("USD") is None
    assert pol.allowed_recipients == []
    assert pol.blocked_recipients == []


def test_allow_when_no_caps_configured(tmp_path: Path, stipend_root: Path) -> None:
    p = tmp_path / "minimal.yaml"
    p.write_text("version: 1\nagent: x\n", encoding="utf-8")
    policy = Policy.load(str(p))
    audit = AuditLog(stipend_root)
    engine = PolicyEngine(policy, audit)
    d = engine.evaluate(amount_cents=999_999_00, recipient="anyone", currency="USD")
    assert d.decision == "allow"


# ------------------------------------------------------ velocity is no-op

def test_velocity_reserved_but_not_enforced(tmp_path: Path, stipend_root: Path) -> None:
    """v0.1 accepts velocity in the schema but does not enforce it (cut)."""
    p = tmp_path / "v.yaml"
    p.write_text(
        """version: 1
agent: x
limits:
  per_transaction_cap: { USD: 100_000_00 }
velocity:
  max_per_minute: 1
""",
        encoding="utf-8",
    )
    policy = Policy.load(str(p))
    audit = AuditLog(stipend_root)
    # Two settled payments in the last minute would violate velocity if enforced.
    _seed_settled_payment(audit, 1_00, "anyone")
    _seed_settled_payment(audit, 1_00, "anyone")
    engine = PolicyEngine(policy, audit)
    d = engine.evaluate(amount_cents=1_00, recipient="anyone", currency="USD")
    assert d.decision == "allow", "v0.1 must not enforce velocity"
