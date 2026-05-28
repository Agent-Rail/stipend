<div align="center">

# Stipend

### An allowance for your AI agent.

A development-time policy engine, audit log, and MCP server for AI agents that handle money.
**Not** production financial infrastructure. The mockable, auditable budget your agent should have before it ever touches a real rail.

[![CI](https://github.com/agent-rail/stipend/actions/workflows/ci.yml/badge.svg)](https://github.com/agent-rail/stipend/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/stipend.svg)](https://pypi.org/project/stipend/)
[![Python](https://img.shields.io/pypi/pyversions/stipend.svg)](https://pypi.org/project/stipend/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

</div>

---

## 60-second demo

```bash
pip install stipend
stipend init my-agent && cd my-agent
stipend run "Pay Acme Logistics $4,250 for invoice 14"
```

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

Nothing left your machine. State is at `.stipend/state.db`. Audit trail is at `.stipend/audit.jsonl`.

---

## What you get

| | |
|---|---|
| **Policy engine** | YAML-defined per-transaction / daily / monthly caps, recipient allow / deny lists with glob support, approval thresholds. JSON-Schema validated. |
| **Audit log** | Append-only JSONL with versioned schema. Every decision recorded — who asked, what for, what the engine said. |
| **Mock backend** | Realistic lifecycle (`created` → `pending` → `settled`) with simulated rail fees. SQLite-backed. Zero network. |
| **MCP server** | Stdio JSON-RPC. Five tools (`pay`, `charge`, `refund`, `policy_check`, `audit_search`) exposed to any MCP client — Claude Desktop, Cursor, Continue, OpenClaw, Hermes. |
| **Python SDK** | Drop into any agent loop. Top-level verbs for actions, namespaced queries for inspection. |
| **AgentRail upgrade path** | A stubbed backend slot that raises `NotYetAvailable` until you flip to [AgentRail](https://agentrail.com) for production fiat rails. |

---

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

# Inspect what happened.
for entry in s.audit.tail(10):
    print(entry.ts, entry.tool, entry.policy_decision, entry.status)
```

When the policy denies the transaction the SDK raises `PolicyDenied`. When it's at or
above the approval threshold it raises `ApprovalRequired`. v0.1 returns the decision
only; the resubmission flow is yours to wire (or it lands in v0.2).

---

## Write a policy

```yaml
version: 1
agent: dispatch-bot
limits:
  per_transaction_cap: { USD: 25_000_00 }   # $25,000
  daily_cap:           { USD: 250_000_00 }  # $250,000
  monthly_cap:         { USD: 1_000_000_00 } # $1,000,000
recipients:
  allowed: ["acme-logistics", "carrier-*"]
  blocked: ["sanctioned-*"]
approvals:
  requires_approval_above: { USD: 10_000_00 }  # $10,000
```

Amounts are integer cents. Allow / deny lists support `fnmatch` globs. Deny wins ties.
Full reference: [docs/policy-reference.md](docs/policy-reference.md).

---

## Wire into an MCP client

```bash
stipend mcp
```

The server speaks stdio JSON-RPC. To attach Claude Desktop, copy
[`examples/mcp_client/claude_desktop_config.json`](examples/mcp_client/claude_desktop_config.json)
into your Claude config, point it at your `policy.yaml`, restart Claude Desktop,
and ask the model to pay an invoice.

Cursor and Continue have native MCP support via the same config shape. For
custom clients, see [`stipend/mcp_server.py`](stipend/mcp_server.py) — the wire
format is one JSON-RPC object per line over stdio.

---

## Privacy

By default the audit log captures the originating natural-language prompt alongside
the amount, recipient, currency, and memo. The log is plain text at
`.stipend/audit.jsonl`.

```bash
# Omit the prompt from new entries.
stipend run --no-prompt "Pay Acme Logistics $4,250 for invoice 14"

# Trim entries older than a cutoff.
stipend audit prune --older-than 7d
```

Know what's in your audit log before you commit it to a shared repo or paste it
into a bug report.

---

## Security

Stipend is **development-time tooling**. It does not move real money. The
cryptographic, regulatory, and operational concerns of moving real funds belong to
[AgentRail](https://agentrail.com).

Vulnerabilities? See [SECURITY.md](SECURITY.md) for the disclosure path.

---

## Documentation

- **[Quickstart](docs/quickstart.md)** — install, run, and see the trace block.
- **[Architecture](docs/architecture.md)** — the four-part composition, lifecycle, concurrent-write semantics.
- **[Policy reference](docs/policy-reference.md)** — every field, evaluation precedence, glob tie-break example.
- **[Upgrading to AgentRail](docs/upgrading-to-agentrail.md)** — what changes, what stays the same, what's in v0.2 vs v1.

---

## v0.2 roadmap

- HTTP-mode MCP transport (`stipend mcp --http`)
- Multi-currency policy enforcement
- Velocity rules (`max_per_minute` / `max_per_hour`)
- LLM-mode natural-language parser
- Approval-token resubmission flow
- TypeScript SDK

If any of these block your use case today, [open an issue](https://github.com/agent-rail/stipend/issues).

---

## Local development

```bash
git clone https://github.com/agent-rail/stipend.git
cd stipend
pip install -e ".[dev]"
pytest
ruff check stipend tests
pyright stipend
```

112 tests, 92% coverage. The bar-bearing modules (`policy`, `audit`, `mock`) are above 85% individually.

---

## License

Apache-2.0. See [LICENSE](LICENSE).

---

<div align="center">

Made by the team building **[AgentRail](https://agentrail.com)**.
AgentRail is the financial control plane for autonomous AI agents. Currently in private beta.

</div>
