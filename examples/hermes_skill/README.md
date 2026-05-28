# Hermes skill: carrier payment agent

A toy agent that pays a carrier through Stipend after looking up an invoice.
This is the most likely shape AgentRail's logistics design partners will use, so
keep it as the load-bearing example if you ever have to cut down to one.

## Run

```
pip install stipend
cd examples/hermes_skill
python carrier_payment_agent.py
```

The script writes a sample `policy.yaml` next to itself if one does not exist,
constructs a `Stipend` against the mock backend, and processes two carrier
invoices: one under the per-transaction cap and one that triggers approval.

You should see the trace block for the first payment and an `ApprovalRequired`
exception for the second.
