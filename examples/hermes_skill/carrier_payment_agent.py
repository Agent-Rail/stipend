"""Toy carrier-payment agent using Stipend.

Reads a fixed list of invoices, runs each through the Stipend policy + mock
backend, and prints the trace block for each settled payment.

The point of this example is to show how Stipend slots into an existing agent
loop: construct one ``Stipend`` for the session, call its verbs, handle
``PolicyDenied`` and ``ApprovalRequired`` as you would any other exception.
"""

from __future__ import annotations

from pathlib import Path

from stipend import ApprovalRequired, PolicyDenied, Stipend
from stipend.policy import Policy
from stipend.rendering import format_receipt_trace

HERE = Path(__file__).parent

POLICY_YAML = """version: 1
agent: hermes-carrier-bot
limits:
  per_transaction_cap: { USD: 25_000_00 }
  daily_cap:           { USD: 100_000_00 }
recipients:
  allowed: ["carrier-*"]
  blocked: ["sanctioned-*"]
approvals:
  requires_approval_above: { USD: 10_000_00 }
"""

INVOICES = [
    {"carrier": "carrier-acme", "amount_cents": 4_250_00, "memo": "Invoice 14"},
    {"carrier": "carrier-titan", "amount_cents": 12_000_00, "memo": "Invoice 22"},
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

    for inv in INVOICES:
        prompt = f"Pay {inv['carrier']} ${inv['amount_cents'] / 100:,.2f} for {inv['memo']}"
        print("=" * 60)
        try:
            receipt = stipend.pay(
                recipient=inv["carrier"],
                amount_cents=inv["amount_cents"],
                currency="USD",
                memo=inv["memo"],
                prompt=prompt,
            )
            decision = stipend.engine.evaluate(
                inv["amount_cents"], inv["carrier"], "USD"
            )
            print(format_receipt_trace(receipt, decision, prompt=prompt, simulated_eta_seconds=2.4))
        except ApprovalRequired as exc:
            print(f"Approval required for {inv['carrier']} {inv['memo']}: {exc.reason}")
            print(f"Threshold was: ${exc.threshold_cents / 100:,.2f} {exc.currency}")
        except PolicyDenied as exc:
            print(f"Denied: {exc.reason}")


if __name__ == "__main__":
    main()
