"""The AgentRail backend stub.

This module exists to make the upgrade slot visible in the codebase. Every
payment method raises :class:`stipend.errors.NotYetAvailable` with the exact
string described in the v0.1 design doc. The stub never makes any network
calls; future AgentRail releases will replace this implementation entirely.
"""

from __future__ import annotations

from stipend.backends.base import Backend
from stipend.errors import NotYetAvailable
from stipend.receipt import Receipt

NOT_YET_AVAILABLE_MESSAGE = (
    "AgentRail production rails are in private beta — see agentrail.com "
    "for design-partner inquiries."
)


class AgentRailBackend(Backend):
    """Stub backend that raises :class:`NotYetAvailable` on every operation."""

    name = "agentrail"

    def pay(
        self,
        *,
        recipient: str,
        amount_cents: int,
        currency: str,
        memo: str,
    ) -> Receipt:
        raise NotYetAvailable(NOT_YET_AVAILABLE_MESSAGE)

    def charge(
        self,
        *,
        source: str,
        amount_cents: int,
        currency: str,
        memo: str,
    ) -> Receipt:
        raise NotYetAvailable(NOT_YET_AVAILABLE_MESSAGE)

    def refund(
        self,
        *,
        payment_id: str,
        amount_cents: int | None,
    ) -> Receipt:
        raise NotYetAvailable(NOT_YET_AVAILABLE_MESSAGE)
