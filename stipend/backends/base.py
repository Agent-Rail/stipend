"""Backend abstract base class.

All backends ship a tiny synchronous surface: ``pay``, ``charge``, ``refund``.
The :class:`stipend.core.Stipend` composer is the only thing that talks to
backends directly. Backends do NOT consult the policy engine; the composer
gates calls with the engine before dispatching.

In v0.1 there are two concrete backends:

- :class:`stipend.backends.mock.MockBackend` (default)
- :class:`stipend.backends.agentrail.AgentRailBackend` (stub; raises
  :class:`stipend.errors.NotYetAvailable`)
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from stipend.receipt import Receipt


class Backend(ABC):
    """Common backend surface."""

    name: str
    """Stable identifier for the backend; surfaced into audit entries."""

    @abstractmethod
    def pay(
        self,
        *,
        recipient: str,
        amount_cents: int,
        currency: str,
        memo: str,
    ) -> Receipt:
        """Send a payment.

        Implementations must produce a :class:`Receipt` whose ``status`` is
        ``"settled"`` for synchronous backends or ``"pending"`` for backends
        that complete settlement asynchronously. The mock backend chooses
        ``"settled"`` because v0.1 elected a synchronous-with-delay
        implementation (no worker thread; see decision T11).
        """

    @abstractmethod
    def charge(
        self,
        *,
        source: str,
        amount_cents: int,
        currency: str,
        memo: str,
    ) -> Receipt:
        """Pull a charge from ``source``."""

    @abstractmethod
    def refund(
        self,
        *,
        payment_id: str,
        amount_cents: int | None,
    ) -> Receipt:
        """Refund a prior payment, fully or partially."""
