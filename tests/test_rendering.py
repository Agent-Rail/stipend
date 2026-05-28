"""Brand-surface golden-file tests for :mod:`stipend.rendering`.

Per /plan-eng-review decision 3A, these tests lock the terminal-output trace
block byte-for-byte. Any intentional change to the format must regenerate the
golden files in ``tests/golden/`` and the diff must pass code review as a
deliberate brand-surface update.

The fixtures use deterministic receipt / decision data so the output is
reproducible across machines and Python versions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from stipend.policy import PolicyDecision
from stipend.receipt import Receipt
from stipend.rendering import format_receipt_trace

GOLDEN_DIR = Path(__file__).parent / "golden"


def _golden(name: str) -> str:
    return (GOLDEN_DIR / name).read_text(encoding="utf-8")


# ----------------------------------------------------- fixture data

PROMPT = "Pay Acme Logistics $4,250 for invoice 14"


def _settled_receipt() -> Receipt:
    return Receipt(
        id="pmt_mock_9bD4xyzw",
        status="settled",
        recipient="acme-logistics",
        amount_cents=425_000,
        currency="USD",
        memo="invoice 14",
        rail="MOCK_ACH",
        backend="mock",
        created_at=datetime(2026, 5, 27, 10, 0, 0, tzinfo=UTC),
        settled_at=datetime(2026, 5, 27, 10, 0, 2, tzinfo=UTC),
        rail_fee_cents=25,
        net_cents=424_975,
        counterparty_id="acme_2cQ8wxyzasdf",
    )


def _pending_receipt() -> Receipt:
    return Receipt(
        id="pmt_mock_9bD4xyzw",
        status="pending",
        recipient="acme-logistics",
        amount_cents=425_000,
        currency="USD",
        memo="invoice 14",
        rail="MOCK_ACH",
        backend="mock",
        created_at=datetime(2026, 5, 27, 10, 0, 0, tzinfo=UTC),
        rail_fee_cents=None,
        net_cents=None,
        counterparty_id="acme_2cQ8wxyzasdf",
    )


def _allow_decision() -> PolicyDecision:
    return PolicyDecision(
        decision="allow",
        rule=None,
        reason="per_txn $25,000  ✓   daily  $4,250 / $250,000  ✓",
        amount_cents=425_000,
        recipient="acme-logistics",
        currency="USD",
    )


def _denied_decision() -> PolicyDecision:
    return PolicyDecision(
        decision="deny",
        rule="recipient_blocked",
        reason="recipient 'sanctioned-co' matches blocked pattern 'sanctioned-*'",
        amount_cents=425_000,
        recipient="sanctioned-co",
        currency="USD",
    )


def _approval_decision() -> PolicyDecision:
    return PolicyDecision(
        decision="requires_approval",
        rule="approvals_threshold",
        reason="amount $12,000 is at or above the approval threshold $10,000",
        amount_cents=12_000_00,
        recipient="acme-logistics",
        currency="USD",
    )


# ------------------------------------------------------- golden tests

def test_settled_with_prompt() -> None:
    output = format_receipt_trace(
        _settled_receipt(), _allow_decision(), prompt=PROMPT, simulated_eta_seconds=2.4
    )
    assert output == _golden("pay_settled_with_prompt.txt")


def test_settled_without_prompt() -> None:
    output = format_receipt_trace(
        _settled_receipt(), _allow_decision(), simulated_eta_seconds=2.4
    )
    assert output == _golden("pay_settled.txt")


def test_pending_without_prompt() -> None:
    output = format_receipt_trace(
        _pending_receipt(), _allow_decision(), simulated_eta_seconds=2.4
    )
    assert output == _golden("pay_pending.txt")


def test_denied() -> None:
    stub = Receipt(
        id=None,
        status="denied",
        recipient="sanctioned-co",
        amount_cents=425_000,
        currency="USD",
        memo="payoff",
        backend="mock",
        counterparty_id=None,
    )
    output = format_receipt_trace(stub, _denied_decision(), prompt="Pay sanctioned-co $4,250 for payoff")
    assert output == _golden("pay_denied.txt")


def test_requires_approval() -> None:
    stub = Receipt(
        id=None,
        status="requires_approval",
        recipient="acme-logistics",
        amount_cents=12_000_00,
        currency="USD",
        memo="vendor deposit",
        backend="mock",
        counterparty_id=None,
    )
    output = format_receipt_trace(
        stub, _approval_decision(), prompt="Pay Acme Logistics $12,000 for vendor deposit"
    )
    assert output == _golden("pay_requires_approval.txt")


# To regenerate goldens after an intentional brand-surface change, run
# scripts/regen_goldens.py at the repo root. Inspect the diff before commit.
