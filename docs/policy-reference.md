# Policy reference

A Stipend policy is a YAML file validated against
[`stipend/schemas/policy.schema.json`](../stipend/schemas/policy.schema.json).

## Minimal valid policy

```yaml
version: 1
agent: my-agent
```

`version` and `agent` are the only required fields. With no other configuration
the engine allows every transaction (in USD).

## Full example

```yaml
version: 1
agent: dispatch-bot
limits:
  per_transaction_cap: { USD: 25_000_00 }
  daily_cap:           { USD: 250_000_00 }
  monthly_cap:         { USD: 1_000_000_00 }
recipients:
  allowed: ["acme-logistics", "carrier-*"]
  blocked: ["sanctioned-*"]
approvals:
  requires_approval_above: { USD: 10_000_00 }
```

Amounts are integer cents. The underscore separators are YAML syntactic sugar
(`25_000_00` reads as 2,500,000, i.e. $25,000.00).

## Field reference

### `limits`

| Field | Effect |
|---|---|
| `per_transaction_cap` | Single transaction at or above this cap denies. |
| `daily_cap` | If the sum of allowed transactions in the trailing 24h plus the proposed transaction exceeds this cap, deny. |
| `monthly_cap` | Same, but trailing 30 days. |

All are currency-keyed maps. v0.1 only enforces `USD`.

### `recipients.allowed` / `recipients.blocked`

Lists of strings. Each string is matched against the recipient identifier with
`fnmatch` semantics (`*` matches any run of characters, `?` matches one).
Exact strings work too — `"acme-logistics"` matches only `acme-logistics`.

**Tie-break (T13 / Reviewer Concerns #4):** if an allow pattern and a deny
pattern both match the same recipient, **deny wins**.

Concrete example:

```yaml
recipients:
  allowed: ["carrier-*"]
  blocked: ["*-restricted"]
```

A transaction to `carrier-restricted` matches both lists. Decision: `deny`,
rule: `recipient_blocked`.

### `approvals.requires_approval_above`

When a transaction amount is **at or above** the threshold, the engine returns
`requires_approval` instead of `allow`. In v0.1 the SDK raises
`ApprovalRequired` and the CLI exits with code 4. v0.1 does **not** implement
signed-token resubmission; the caller must resolve the approval out-of-band
(human escalation, separate approval system, abort).

### `velocity` (v0.2)

The schema accepts `velocity.max_per_minute` and `velocity.max_per_hour`,
but the v0.1 engine does **not** enforce them. The field exists so that
policies written today remain valid in v0.2. If you set velocity in v0.1,
expect to be surprised in v0.2 when it starts firing.

## Evaluation precedence

Highest precedence first:

1. Currency check (non-USD → `deny`).
2. `per_transaction_cap`.
3. `recipients.blocked` (wins over `allowed`).
4. `recipients.allowed` (if non-empty, anything not matching is denied).
5. `daily_cap`.
6. `monthly_cap`.
7. `approvals.requires_approval_above`.
8. Default: `allow`.

A transaction over the per-transaction cap denies; it does **not** return
`requires_approval` (caps win over thresholds). Approvals are for amounts that
**could** be sent if a human signed off, but caps express a "never under any
circumstance" rule.
