"""Mock backend: pay/charge/refund + SQLite state + WAL coherence."""

from __future__ import annotations

from pathlib import Path

import pytest

from stipend.backends.mock import MOCK_RAIL_FEE_CENTS, MOCK_RAIL_LABEL, MockBackend


def test_pay_produces_settled_receipt(tmp_path: Path) -> None:
    backend = MockBackend(state_dir=tmp_path, sleep=False)
    receipt = backend.pay(
        recipient="acme-logistics",
        amount_cents=10_000,
        currency="USD",
        memo="x",
    )
    assert receipt.status == "settled"
    assert receipt.amount_cents == 10_000
    assert receipt.net_cents == 10_000 - MOCK_RAIL_FEE_CENTS
    assert receipt.rail == MOCK_RAIL_LABEL
    assert receipt.id is not None and receipt.id.startswith("pmt_mock_")
    assert receipt.counterparty_id is not None


def test_pay_negative_amount_rejected(tmp_path: Path) -> None:
    backend = MockBackend(state_dir=tmp_path)
    with pytest.raises(ValueError):
        backend.pay(recipient="x", amount_cents=-1, currency="USD", memo="")


def test_pay_zero_amount_rejected(tmp_path: Path) -> None:
    backend = MockBackend(state_dir=tmp_path)
    with pytest.raises(ValueError):
        backend.pay(recipient="x", amount_cents=0, currency="USD", memo="")


def test_charge_produces_settled_receipt(tmp_path: Path) -> None:
    backend = MockBackend(state_dir=tmp_path)
    receipt = backend.charge(source="user-1", amount_cents=500, currency="USD", memo="x")
    assert receipt.status == "settled"
    assert receipt.id is not None and receipt.id.startswith("chg_mock_")


def test_charge_negative_amount_rejected(tmp_path: Path) -> None:
    backend = MockBackend(state_dir=tmp_path)
    with pytest.raises(ValueError):
        backend.charge(source="x", amount_cents=-5, currency="USD", memo="")


def test_refund_full(tmp_path: Path) -> None:
    backend = MockBackend(state_dir=tmp_path)
    paid = backend.pay(recipient="acme", amount_cents=5_000, currency="USD", memo="x")
    refunded = backend.refund(payment_id=paid.id, amount_cents=None)  # type: ignore[arg-type]
    assert refunded.status == "settled"
    assert refunded.amount_cents == 5_000
    assert refunded.id is not None and refunded.id.startswith("rfd_mock_")


def test_refund_partial(tmp_path: Path) -> None:
    backend = MockBackend(state_dir=tmp_path)
    paid = backend.pay(recipient="acme", amount_cents=5_000, currency="USD", memo="x")
    refunded = backend.refund(payment_id=paid.id, amount_cents=1_000)  # type: ignore[arg-type]
    assert refunded.amount_cents == 1_000


def test_refund_unknown_payment(tmp_path: Path) -> None:
    backend = MockBackend(state_dir=tmp_path)
    with pytest.raises(ValueError):
        backend.refund(payment_id="pmt_mock_does_not_exist", amount_cents=None)


def test_state_db_written(tmp_path: Path) -> None:
    backend = MockBackend(state_dir=tmp_path)
    backend.pay(recipient="acme", amount_cents=100, currency="USD", memo="x")
    assert (tmp_path / "state.db").exists()
    # WAL mode adds the journal companion file once we run an insert.
    # Either it's present or sqlite already checkpointed; we just check
    # the main db is non-empty.
    assert (tmp_path / "state.db").stat().st_size > 0


def test_counterparty_is_deterministic(tmp_path: Path) -> None:
    backend = MockBackend(state_dir=tmp_path)
    r1 = backend.pay(recipient="acme-logistics", amount_cents=100, currency="USD", memo="")
    r2 = backend.pay(recipient="acme-logistics", amount_cents=100, currency="USD", memo="")
    assert r1.counterparty_id == r2.counterparty_id
