# Architecture

Stipend is a small composition of four parts behind a verb-shaped API:

```
              ┌────────────┐    ┌────────────┐    ┌────────────┐
   CLI ──────►│            │    │            │    │            │
              │            │    │            │    │  MockBackend│
              │  Stipend   │───►│   Backend  │───►│ (SQLite +   │
   MCP ──────►│ (composer) │    │   (ABC)    │    │ JSONL state)│
              │            │    │            │    └────────────┘
              │            │    │            │
   SDK ──────►│            │    │            │    ┌────────────┐
              └─────┬──────┘    └────────────┘    │AgentRail   │
                    │                              │(stub:      │
                    ▼                              │NotYet      │
              ┌────────────┐    ┌────────────┐    │Available)  │
              │PolicyEngine│◄───┤  AuditLog  │    └────────────┘
              │ (USD only) │    │ (JSONL +   │
              └────────────┘    │  fsync)    │
                                └────────────┘
```

## Components

- **`stipend.core.Stipend`** is the composer. CLI, MCP server, and SDK all
  construct one. It owns the policy engine, the audit log, and the active
  backend. It exposes verbs (`pay`, `charge`, `refund`, `policy_check`) and
  query namespaces (`charges`, `audit`).
- **`stipend.policy.PolicyEngine`** evaluates one proposed transaction
  against a loaded `Policy`. It reads `audit.jsonl` on every call to
  compute window totals (no caching, no SQLite materialization). Returns a
  `PolicyDecision`: `allow`, `deny`, or `requires_approval`. v0.1 enforces
  USD only; the `velocity` field is reserved in the schema but not enforced.
- **`stipend.audit.AuditLog`** is an append-only JSONL store at
  `<root>/audit.jsonl`. Writes use `O_APPEND` plus per-line `fsync`; this
  makes concurrent writers from multiple processes (`stipend mcp` + a CLI
  `stipend run` from another shell) safe. Reads are linear scans.
- **`stipend.backends.Backend`** is the ABC. Two concretes:
  - `MockBackend` persists payment state to SQLite (WAL journal mode) and
    runs synchronous-with-delay settlement (no worker thread).
  - `AgentRailBackend` is a stub. Every method raises `NotYetAvailable`.
- **`stipend.rendering.format_receipt_trace`** is the single brand-surface
  formatter. CLI, MCP server, and SDK all call it. Golden-file tests pin
  the byte layout in `tests/golden/`.

## Three entry points, one composer

| Entry point | Lifecycle | Notes |
|---|---|---|
| CLI (`stipend run`, `stipend audit ...`) | per-process; cold-start each invocation | Mock-backend "pending" payments survive only through SQLite, not in-memory state |
| MCP server (`stipend mcp`) | per-server-session; one `Stipend` constructed at startup, reused for the session | Pending payments survive across `tools/call` invocations within one server process |
| SDK (`from stipend import Stipend`) | caller-controlled | Library users own the lifetime |

This is decision **1D** from the engineering review: the lifecycle matches
each entry point's natural shape. There is no global singleton.

## Concurrent state

Two processes can hit `.stipend/state.db` and `.stipend/audit.jsonl` at the
same time (CLI + MCP server is the canonical demo scenario). Design choices
that make this safe:

- SQLite is opened in **WAL** journal mode with a 30s busy timeout. Concurrent
  readers don't block writers and vice versa.
- The audit log uses `O_APPEND` plus per-line `fsync`. The kernel guarantees
  that a single `write()` smaller than `PIPE_BUF` to an `O_APPEND` fd is
  atomic. Each JSONL record stays well under that limit.

This is decision **1B** from the engineering review.

## Brand-surface lock

The trace block — the screencast moment — is the public surface that the
product is most often judged by. It's formatted by exactly one function,
[`format_receipt_trace`](../stipend/rendering.py). CLI / MCP / SDK callers all
go through it. A cross-entry-point test
([`tests/test_rendering_consistency.py`](../tests/test_rendering_consistency.py))
asserts that the symbol the CLI imports is the same object as the SDK uses,
so accidental wrapper drift fails CI. Golden files in
[`tests/golden/`](../tests/golden/) lock the exact bytes for each
receipt status.

If you intentionally change the trace format, regenerate the goldens and
treat the diff as a brand-surface review item.

## What v0.1 explicitly is not

See the `NOT in scope` section of the design doc that produced this code. In
short: not production financial infrastructure; not multi-tenant; not
multi-currency in the engine (the schema is keyed for it but USD is the only
currency accepted at runtime); not real banking integration of any kind.

If you want real fiat rails, that is [AgentRail](https://agentrail.com).
