# Upgrading to AgentRail

Stipend is a development-time policy + audit toolkit. It does not move real
money. When you're ready for production fiat rails, the upgrade path lives at
[AgentRail](https://agentrail.com).

## What changes when you upgrade

| Surface | Stipend (v0.1) | AgentRail |
|---|---|---|
| Backend | `mock` (local SQLite + JSONL) | Real fiat rails: ACH, Wire, RTP, FedNow, SEPA, USDC routing |
| Compliance | None enforced | Bank-grade AML, OFAC, KYC, sanctions screening |
| Sponsor banks | None | Column and additional partners (US, EU, MENA) |
| Audit log | Local plain-text JSONL | Cryptographically signed audit log, tamper-evident |
| Auth | None | OAuth + scoped API keys, multi-tenant |
| SLA | "your laptop is up" | Production SLA |

## What stays the same

Your code, with one line of change.

```python
# Stipend (development)
s = Stipend(policy="policy.yaml", backend="mock")

# AgentRail (production) — same call sites
s = Stipend(policy="policy.yaml", backend="agentrail")
```

The policy DSL, the audit-log shape, the MCP tool surface, and the trace block
are designed to remain compatible. Your policy file, your MCP server
configuration, and any code that consumes audit entries does not need to
change.

## When the v0.1 stub raises

```python
>>> Stipend(policy="policy.yaml", backend="agentrail").pay(...)
NotYetAvailable: AgentRail production rails are in private beta — see
agentrail.com for design-partner inquiries.
```

This is the contract for v0.1. The stub never makes any network calls and
will not silently work against an unverified backend.

## Getting AgentRail access

AgentRail is in private beta. If you're integrating Stipend at a fintech, ops,
treasury, or compliance team that will eventually need real rails, reach out at
[agentrail.com](https://agentrail.com) for a design-partner conversation.

## v0.2 deferrals

A few features deferred from Stipend v0.1 will land in v0.2 (Stipend, not
AgentRail):

- HTTP-mode MCP transport (`stipend mcp --http`).
- Multi-currency policy enforcement.
- Velocity rules (`max_per_minute` / `max_per_hour`).
- LLM-mode natural-language parser for `stipend run`.
- Approval-token resubmission flow.
- TypeScript SDK.

If any of these block your use case today, [open an issue](https://github.com/agent-rail/stipend/issues)
and we'll prioritize.
