# Show HN draft

A draft of the Show HN post. Not final copy — the team should iterate on tone
and any quoted numbers before shipping. Per the design doc's launch-narrative
guidance, success metrics are framed around **category education and design-partner
velocity**, not GitHub stars or "Stipend → AgentRail conversions."

---

**Show HN: Stipend — an allowance for your AI agent**

Most agent demos either skip "moving money" entirely or wire the agent to a
sandbox Stripe key and hope nobody runs the demo twice. Stipend is the small
piece between an agent and a payment rail: a policy engine, an append-only
audit log, a mockable backend, and an MCP server so any MCP-compatible client
(Claude Desktop, Cursor, Continue, OpenClaw, Hermes) can drive it without
gluing JSON-RPC by hand.

It is **not** production financial infrastructure. It does not move real money.
It is the development-time twin you write your agent against before you wire
the real rails in.

```
pip install stipend
stipend init my-agent && cd my-agent
stipend run "Pay Acme Logistics $4,250 for invoice 14"
```

```
[resolve]   counterparty=acme-logistics  →  acme_…
[policy]    per_txn $25,000  ✓   daily  $4,250 / $250,000  ✓
[rail]      MOCK_ACH         ✓   ETA 2.4s simulated
[payment]   pmt_mock_…   created  status=pending
[payment]   pmt_mock_…   settled  net $4,249.75 (mock)
[audit]     written to .stipend/audit.jsonl

Sent (mock). Receipt id: pmt_mock_…
```

The five tools (`pay`, `charge`, `refund`, `policy_check`, `audit_search`) are
exposed over a stdio JSON-RPC MCP server. Drop the config snippet into Claude
Desktop and ask the model to pay an invoice; the trace renders inline.

Things we cared about while building it:

- **Concurrent writers.** SQLite WAL plus JSONL `O_APPEND` with per-line
  `fsync`. Two processes (CLI + MCP) hitting the same `.stipend/` is the
  default demo scenario; it has to work.
- **Brand surface lock.** The terminal trace is the screencast moment. CLI,
  MCP, and SDK all render it through one function; golden-file tests pin
  the byte layout. No drive-by reformat can break it without failing CI.
- **Audit privacy.** The default audit log captures the originating prompt
  alongside the amount and recipient. We document this plainly. `--no-prompt`
  omits it; `stipend audit prune --older-than 7d` clears old entries.
- **A clean upgrade slot.** When you outgrow mock, the `agentrail` backend
  raises `NotYetAvailable` until our paid product (private beta) is ready
  for you. Same client code, same policy file, same audit shape.

Apache-2.0. Python 3.11+. Made by the team building
[AgentRail](https://agentrail.com), the financial control plane for
autonomous AI agents (private beta).

Repo, docs, examples: https://github.com/agent-rail/stipend

Feedback welcome — especially from anyone who has shipped agent-driven
payments and lived through the policy / audit problems firsthand.

---

## Internal review notes (delete before posting)

- The draft does not promise GitHub stars, downloads, or signup metrics. Per
  D4 in the engineering review the underperformance trigger is podcast /
  blog mentions and design-partner forwards, not vanity counts.
- The link to agentrail.com assumes a holding page exists. Confirm before
  scheduling the post.
- The 25¢ flat rail fee makes `$4,249.75` correct; do not let copyedit drift
  it to `$4,249.85` (which was in the spec example by typo).
- Consider posting Tuesday or Wednesday morning Pacific for best HN front-page
  odds.
- Bring the screencast (founder-produced, 30 seconds) to the comments thread
  once people start asking what the demo looks like.
