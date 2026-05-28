"""The Receipt dataclass.

A receipt is the return value of a successful payment / charge / refund call.
It is also what gets fed to :func:`stipend.rendering.format_receipt_trace` to
produce the screencast trace block. The shape is intentionally small and
backend-agnostic so the brand surface (the trace block) renders identically
regardless of which backend produced the receipt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

ReceiptStatus = Literal["pending", "settled", "denied", "requires_approval"]


@dataclass(frozen=True)
class Receipt:
    """A backend-agnostic payment receipt.

    Attributes:
        id: The backend-issued payment identifier. For the mock backend this
            looks like ``pmt_mock_9bD4...``. For the AgentRail stub backend
            no receipt is ever issued (the stub raises before allocation).
        status: Current lifecycle state. ``pending`` is the normal post-create
            state; ``settled`` is the terminal happy-path state; ``denied`` is
            terminal due to policy; ``requires_approval`` indicates the policy
            engine returned an approval threshold decision rather than allowing
            the transaction to execute.
        recipient: Counterparty identifier as supplied by the caller.
        amount_cents: Gross transaction amount in integer cents.
        net_cents: Net amount after any backend-applied rail fee. For the mock
            backend this is ``amount_cents`` minus a flat simulated fee. For
            denied or pending receipts this is ``None``.
        currency: ISO-4217 currency code. v0.1 enforces ``USD`` only.
        memo: Free-text description as supplied by the caller.
        rail: Backend-supplied rail label, e.g. ``"MOCK_ACH"``.
        backend: The backend module that produced the receipt, e.g. ``"mock"``
            or (for future) ``"agentrail"``.
        created_at: When the receipt was first allocated.
        settled_at: When the receipt transitioned to ``settled``. ``None`` for
            non-settled receipts.
        audit_id: Identifier of the corresponding audit-log entry, if any.
        rail_fee_cents: The simulated rail fee, in cents. ``None`` when no fee
            has been applied yet (e.g. ``denied`` or ``requires_approval``).
        counterparty_id: Backend-resolved counterparty identifier, distinct
            from ``recipient`` (the caller-supplied string). For the mock
            backend this looks like ``acme_2cQ8...``.
        policy_reason: When ``status`` is ``denied`` or ``requires_approval``,
            a short human-readable explanation from the policy engine.
    """

    id: str | None
    status: ReceiptStatus
    recipient: str
    amount_cents: int
    currency: str
    memo: str
    rail: str | None = None
    backend: str = "mock"
    created_at: datetime | None = None
    settled_at: datetime | None = None
    audit_id: str | None = None
    rail_fee_cents: int | None = None
    net_cents: int | None = None
    counterparty_id: str | None = None
    policy_reason: str | None = None
    extra: dict[str, object] = field(default_factory=dict)

    def short_id(self) -> str:
        """Render a brand-style elided identifier, e.g. ``pmt_mock_9bD4…``.

        Used by :mod:`stipend.rendering` to produce the trace block. Returns
        ``"(none)"`` if the receipt has no id (denied receipts before backend
        allocation). The cutoff matches the spec block's visual rhythm:
        identifiers up to 14 chars render in full; longer ids are cut at 13
        chars plus a trailing horizontal ellipsis.
        """
        if self.id is None:
            return "(none)"
        if len(self.id) <= 14:
            return self.id
        return f"{self.id[:13]}…"
