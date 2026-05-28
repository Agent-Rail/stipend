"""Shared terminal-output formatter.

Per /plan-eng-review decision 2A, all three entry points (CLI, MCP server,
SDK consumers) render receipts through this module. The exact byte layout is
the brand surface; any change here must regenerate the golden files in
``tests/golden/``.

The format is locked at:

    > <one-line memo / prompt echo>

    [resolve]   counterparty=<recipient>  →  <counterparty_id_short>
    [policy]    <policy_summary>
    [rail]      <rail_label>         ✓   ETA <eta>s simulated
    [payment]   <id_short>   created  status=pending
    [payment]   <id_short>   settled  net <amount> (mock)
    [audit]     written to .stipend/audit.jsonl

    Sent (mock). Receipt id: <id_short>

For denied / requires_approval receipts the trace shortens accordingly; see
the test suite for golden examples of each shape.
"""

from __future__ import annotations

from stipend.policy import PolicyDecision
from stipend.receipt import Receipt

AUDIT_PATH_DISPLAY = ".stipend/audit.jsonl"


def format_receipt_trace(
    receipt: Receipt,
    policy_decision: PolicyDecision,
    prompt: str | None = None,
    *,
    simulated_eta_seconds: float | None = None,
) -> str:
    """Render the trace block for a receipt + policy decision.

    Parameters:
        receipt: The receipt returned by the backend (or a stub receipt for
            denied / requires_approval outcomes).
        policy_decision: The policy decision that produced (or denied) the
            receipt.
        prompt: The originating natural-language request, when called via the
            CLI or via MCP from a chat client. ``None`` for direct SDK calls
            and for callers that passed ``--no-prompt``.
        simulated_eta_seconds: For mock-backend ``settled`` receipts, the
            simulated rail ETA to display. Ignored for non-settled outcomes.
    """
    lines: list[str] = []
    if prompt:
        lines.append(f"> {prompt}")
        lines.append("")

    # Resolve line.
    counterparty_short = _short(receipt.counterparty_id)
    lines.append(
        f"[resolve]   counterparty={receipt.recipient}  →  {counterparty_short}"
    )

    # Policy line.
    lines.append(f"[policy]    {policy_decision.reason}")

    if policy_decision.decision == "deny":
        lines.append("")
        lines.append(f"Denied. Reason: {policy_decision.reason}")
        return "\n".join(lines) + "\n"

    if policy_decision.decision == "requires_approval":
        lines.append("")
        lines.append(
            "Requires approval. v0.1 returns this decision but does not "
            "resubmit; resolve out-of-band."
        )
        return "\n".join(lines) + "\n"

    # allow -> rail + payment + audit + footer
    rail_label = receipt.rail or "MOCK_ACH"
    eta_str = f"{simulated_eta_seconds:.1f}" if simulated_eta_seconds is not None else "—"
    lines.append(f"[rail]      {rail_label}         ✓   ETA {eta_str}s simulated")

    pmt_short = receipt.short_id()
    if receipt.status == "pending":
        lines.append(f"[payment]   {pmt_short}   created  status=pending")
    elif receipt.status == "settled":
        lines.append(f"[payment]   {pmt_short}   created  status=pending")
        net = receipt.net_cents if receipt.net_cents is not None else receipt.amount_cents
        net_fmt = _fmt_dollars(net, receipt.currency)
        lines.append(f"[payment]   {pmt_short}   settled  net {net_fmt} (mock)")

    lines.append(f"[audit]     written to {AUDIT_PATH_DISPLAY}")
    lines.append("")
    lines.append(f"Sent (mock). Receipt id: {pmt_short}")
    return "\n".join(lines) + "\n"


def _short(value: str | None) -> str:
    """Render a brand-style elided identifier or ``(unresolved)`` for None.

    Matches the cutoff used by :meth:`stipend.receipt.Receipt.short_id` so the
    brand surface's two identifier columns (counterparty and payment id) wear
    the same shape.
    """
    if value is None:
        return "(unresolved)"
    if len(value) <= 14:
        return value
    return f"{value[:13]}…"


def _fmt_dollars(cents: int, currency: str) -> str:
    """Format a cents value for the trace's payment / net lines.

    Always uses two decimal places to match the spec's
    ``net $4,249.85`` example, regardless of whether the cents value is a
    round dollar amount. The currency arg is retained for the v0.2
    multi-currency switch but v0.1 always renders in USD.
    """
    del currency  # explicit no-op marker until v0.2
    dollars = cents / 100
    return f"${dollars:,.2f}"
