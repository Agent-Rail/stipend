"""AgentRail stub backend always raises NotYetAvailable."""

from __future__ import annotations

import pytest

from stipend.backends.agentrail import NOT_YET_AVAILABLE_MESSAGE, AgentRailBackend
from stipend.errors import NotYetAvailable


def test_pay_raises() -> None:
    with pytest.raises(NotYetAvailable) as exc:
        AgentRailBackend().pay(
            recipient="x", amount_cents=1, currency="USD", memo=""
        )
    assert str(exc.value) == NOT_YET_AVAILABLE_MESSAGE
    assert "agentrail.com" in str(exc.value)


def test_charge_raises() -> None:
    with pytest.raises(NotYetAvailable):
        AgentRailBackend().charge(
            source="x", amount_cents=1, currency="USD", memo=""
        )


def test_refund_raises() -> None:
    with pytest.raises(NotYetAvailable):
        AgentRailBackend().refund(payment_id="x", amount_cents=None)
