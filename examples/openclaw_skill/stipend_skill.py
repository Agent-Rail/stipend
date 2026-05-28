"""OpenClaw-style skill: per-task budget via Stipend charges.

The agent is allotted a daily charge cap. Each task pulls a small charge from
the budget; the daily cap stops runaway tool-use spend.
"""

from __future__ import annotations

from pathlib import Path

from stipend import PolicyDenied, Stipend
from stipend.policy import Policy

HERE = Path(__file__).parent

POLICY_YAML = """version: 1
agent: openclaw-task-bot
limits:
  per_transaction_cap: { USD: 5_00 }
  daily_cap:           { USD: 50_00 }
recipients:
  allowed: ["tool-*"]
"""

TASKS = [
    ("tool-search", 100, "web search"),
    ("tool-llm",    250, "completion"),
    ("tool-search", 100, "web search"),
    ("tool-image",  500, "image gen"),
]


def main() -> None:
    policy_path = HERE / "policy.yaml"
    if not policy_path.exists():
        policy_path.write_text(POLICY_YAML, encoding="utf-8")

    stipend = Stipend(
        policy=Policy.load(str(policy_path)),
        backend="mock",
        root=str(HERE / ".stipend"),
    )

    total = 0
    for source, cents, memo in TASKS:
        try:
            r = stipend.charge(source=source, amount_cents=cents, currency="USD", memo=memo)
            total += cents
            print(f"OK  {source:<14}  {memo:<14}  ${cents / 100:.2f}  receipt={r.id}")
        except PolicyDenied as exc:
            print(f"DENY {source:<14}  {memo:<14}  ${cents / 100:.2f}  {exc.reason}")

    print(f"\nTotal spent: ${total / 100:.2f}")


if __name__ == "__main__":
    main()
