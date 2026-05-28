# Quickstart

```bash
pip install stipend
stipend init my-agent
cd my-agent
stipend run "Pay Acme Logistics $4,250 for invoice 14"
```

You should see the trace block:

```
> Pay Acme Logistics $4,250 for invoice 14

[resolve]   counterparty=acme-logistics  →  acme_…
[policy]    per_txn $25,000  ✓   daily  $4,250 / $250,000  ✓
[rail]      MOCK_ACH         ✓   ETA 2.4s simulated
[payment]   pmt_mock_…   created  status=pending
[payment]   pmt_mock_…   settled  net $4,249.75 (mock)
[audit]     written to .stipend/audit.jsonl

Sent (mock). Receipt id: pmt_mock_…
```

That's the demo. Nothing left your machine. The mock backend persists state to
`.stipend/state.db` and the audit log to `.stipend/audit.jsonl`.

## Try the policy engine

Tighten the per-transaction cap to $1,000 in `policy.yaml`:

```yaml
limits:
  per_transaction_cap: { USD: 1_000_00 }
```

Re-run the same command — Stipend denies it cleanly:

```
[policy]    amount $4,250 exceeds per-transaction cap $1,000

Denied. Reason: amount $4,250 exceeds per-transaction cap $1,000
```

## Use the SDK

```python
from stipend import Stipend

s = Stipend(policy="policy.yaml", backend="mock")

receipt = s.pay(
    recipient="acme-logistics",
    amount_cents=425_000,
    currency="USD",
    memo="Invoice 14",
)

for entry in s.audit.tail(5):
    print(entry.ts, entry.tool, entry.policy_decision, entry.status)
```

## Wire into Claude Desktop

```bash
stipend mcp
```

…then add the snippet in [`examples/mcp_client/claude_desktop_config.json`](../examples/mcp_client/claude_desktop_config.json) to your Claude Desktop config.

## Privacy

By default `stipend run` records the originating natural-language command in
the audit log. To omit it:

```bash
stipend run --no-prompt "Pay Acme Logistics $4,250 for invoice 14"
```

To trim old audit entries:

```bash
stipend audit prune --older-than 7d
```

## When you outgrow mock

The `agentrail` backend is a stub that raises `NotYetAvailable` and points at
[agentrail.com](https://agentrail.com) — the paid product with real fiat rails
and bank-grade compliance. Stipend is the local development twin.
