"""Stipend class composition + verb wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from stipend.backends.mock import MockBackend
from stipend.core import Stipend
from stipend.errors import ApprovalRequired, NotYetAvailable, PolicyDenied
from stipend.policy import Policy


def _make(tmp_path: Path) -> Stipend:
    pol = tmp_path / "policy.yaml"
    pol.write_text(
        """version: 1
agent: test
limits:
  per_transaction_cap: { USD: 100_000_00 }
recipients:
  allowed: ["acme-*", "carrier-*"]
  blocked: ["sanctioned-*"]
approvals:
  requires_approval_above: { USD: 50_000_00 }
""",
        encoding="utf-8",
    )
    return Stipend(policy=str(pol), backend="mock", root=str(tmp_path / ".stipend"))


def test_pay_happy_path(tmp_path: Path) -> None:
    s = _make(tmp_path)
    receipt = s.pay(recipient="acme-1", amount_cents=1_000_00, currency="USD", memo="x")
    assert receipt.status == "settled"
    assert receipt.audit_id is not None


def test_pay_denied(tmp_path: Path) -> None:
    s = _make(tmp_path)
    with pytest.raises(PolicyDenied):
        s.pay(recipient="sanctioned-co", amount_cents=1_00, currency="USD", memo="x")


def test_pay_requires_approval(tmp_path: Path) -> None:
    s = _make(tmp_path)
    with pytest.raises(ApprovalRequired) as exc:
        s.pay(recipient="acme-1", amount_cents=60_000_00, currency="USD", memo="x")
    assert exc.value.threshold_cents == 50_000_00
    assert exc.value.currency == "USD"


def test_charge_calls_through(tmp_path: Path) -> None:
    s = _make(tmp_path)
    receipt = s.charge(source="acme-1", amount_cents=500, currency="USD", memo="x")
    assert receipt.status == "settled"


def test_refund(tmp_path: Path) -> None:
    s = _make(tmp_path)
    paid = s.pay(recipient="acme-1", amount_cents=500, currency="USD", memo="x")
    refunded = s.refund(payment_id=paid.id, amount_cents=None)  # type: ignore[arg-type]
    assert refunded.status == "settled"


def test_policy_check_does_not_call_backend(tmp_path: Path) -> None:
    s = _make(tmp_path)
    decision = s.policy_check(amount_cents=1_000_00, recipient="acme-1", currency="USD")
    assert decision.decision == "allow"
    # No backend call happened: state.db should be empty.
    assert (tmp_path / ".stipend" / "state.db").exists()
    # But the audit log should have one probe entry.
    entries = s.audit.read_all()
    assert len(entries) == 1
    assert entries[0].status == "probe"


def test_backend_kwarg_accepts_instance(tmp_path: Path) -> None:
    pol = tmp_path / "p.yaml"
    pol.write_text("version: 1\nagent: x\n", encoding="utf-8")
    backend = MockBackend(state_dir=tmp_path / ".stipend")
    s = Stipend(policy=str(pol), backend=backend, root=str(tmp_path / ".stipend"))
    receipt = s.pay(recipient="anyone", amount_cents=100, currency="USD", memo="x")
    assert receipt.status == "settled"


def test_backend_kwarg_rejects_unknown(tmp_path: Path) -> None:
    pol = tmp_path / "p.yaml"
    pol.write_text("version: 1\nagent: x\n", encoding="utf-8")
    with pytest.raises(ValueError):
        Stipend(policy=str(pol), backend="quantum", root=str(tmp_path / ".stipend"))


def test_agentrail_backend_raises_through_stipend(tmp_path: Path) -> None:
    pol = tmp_path / "p.yaml"
    pol.write_text("version: 1\nagent: x\n", encoding="utf-8")
    s = Stipend(policy=str(pol), backend="agentrail", root=str(tmp_path / ".stipend"))
    with pytest.raises(NotYetAvailable):
        s.pay(recipient="x", amount_cents=100, currency="USD", memo="")


def test_charges_namespace(tmp_path: Path) -> None:
    s = _make(tmp_path)
    s.pay(recipient="acme-1", amount_cents=100, currency="USD", memo="x")
    s.charge(source="acme-2", amount_cents=200, currency="USD", memo="y")
    listed = s.charges.list()
    assert len(listed) == 2
    assert {row["recipient"] for row in listed} == {"acme-1", "acme-2"}


def test_construct_from_policy_instance(tmp_path: Path) -> None:
    pol = tmp_path / "p.yaml"
    pol.write_text("version: 1\nagent: x\n", encoding="utf-8")
    policy = Policy.load(str(pol))
    s = Stipend(policy=policy, backend="mock", root=str(tmp_path / ".stipend"))
    receipt = s.pay(recipient="x", amount_cents=100, currency="USD", memo="")
    assert receipt.status == "settled"
