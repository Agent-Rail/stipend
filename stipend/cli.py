"""Stipend CLI.

Typer-based. Commands:

- ``stipend init [project-name]`` — scaffold a new Stipend-using project.
- ``stipend run "<nl command>"`` — parse and execute via regex (v0.1).
- ``stipend audit ...`` — view or prune the audit log.
- ``stipend policy validate <path>`` — schema-check a policy YAML.
- ``stipend mcp`` — start the stdio MCP server.

Per /plan-eng-review decisions:

- **1D**: CLI is per-process; each invocation cold-starts the backend and
  reads state from ``.stipend/`` on disk.
- **D3 / T9**: ``stipend run`` accepts ``--no-prompt`` to omit the captured
  prompt from the audit entry. ``stipend audit prune`` removes old entries.
- v0.1 NL parser is regex-only (LLM mode deferred to v0.2).
"""

from __future__ import annotations

import json
import re
import sys
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import typer

from stipend.audit import AuditWriteError
from stipend.core import Stipend
from stipend.errors import ApprovalRequired, NotYetAvailable, PolicyConfigError, PolicyDenied
from stipend.policy import Policy
from stipend.rendering import format_receipt_trace

app = typer.Typer(
    name="stipend",
    help="An allowance for your AI agent.",
    no_args_is_help=True,
    add_completion=False,
)
audit_app = typer.Typer(
    name="audit", help="View or prune the audit log.", no_args_is_help=True
)
policy_app = typer.Typer(
    name="policy", help="Inspect a policy.", no_args_is_help=True
)
app.add_typer(audit_app, name="audit")
app.add_typer(policy_app, name="policy")


# ---------------------------------------------------------------- init

@app.command("init")
def init_cmd(
    name: Annotated[str, typer.Argument(help="Project directory name.")] = "stipend-demo",
) -> None:
    """Scaffold a new project directory with a sample policy and example."""
    target = Path(name)
    if target.exists() and any(target.iterdir()):
        typer.echo(f"error: {target} already exists and is not empty", err=True)
        raise typer.Exit(code=1)
    target.mkdir(parents=True, exist_ok=True)
    (target / ".stipend").mkdir(exist_ok=True)

    (target / "policy.yaml").write_text(_SAMPLE_POLICY_YAML, encoding="utf-8")
    (target / "example.py").write_text(_SAMPLE_EXAMPLE_PY, encoding="utf-8")
    (target / "README.md").write_text(_SAMPLE_PROJECT_README, encoding="utf-8")

    typer.echo(f"Created {target}/")
    typer.echo("  policy.yaml — sample policy")
    typer.echo("  example.py  — minimal SDK demo")
    typer.echo("")
    typer.echo("Next:")
    typer.echo(f"  cd {target}")
    typer.echo('  stipend run "Pay Acme Logistics $4,250 for invoice 14"')


# ----------------------------------------------------------------- run

_RUN_RE = re.compile(
    r"""^\s*
    (?:Pay|pay|PAY)\s+
    (?P<recipient>[A-Za-z0-9][A-Za-z0-9 _-]*?)\s+
    \$\s*
    (?P<amount>[0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)
    (?:\s+for\s+(?P<memo>.+))?
    \s*$""",
    re.VERBOSE,
)


@app.command("run")
def run_cmd(
    command: Annotated[str, typer.Argument(help='Natural language command, e.g. "Pay Acme $4,250 for invoice 14"')],
    policy_path: Annotated[
        str, typer.Option("--policy", help="Policy YAML path.")
    ] = "policy.yaml",
    backend: Annotated[
        str, typer.Option("--backend", help="Backend: mock or agentrail.")
    ] = "mock",
    no_prompt: Annotated[
        bool,
        typer.Option(
            "--no-prompt",
            help="Do NOT record the originating prompt in the audit log.",
        ),
    ] = False,
) -> None:
    """Parse and execute a natural-language command (v0.1: regex only)."""
    parsed = _parse_run_command(command)
    if parsed is None:
        typer.echo(
            'error: could not parse command. v0.1 expects: '
            '"Pay <recipient> $<amount> for <memo>"',
            err=True,
        )
        raise typer.Exit(code=2)

    recipient, amount_cents, memo = parsed

    try:
        stipend = Stipend(policy=policy_path, backend=backend)
    except PolicyConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from None

    prompt_for_audit = None if no_prompt else command

    # Recreate the trace block the spec locks. To compute it we re-evaluate
    # the policy first (so the trace shows the same decision the dispatcher
    # used) and then call the backend through the composer.
    decision = stipend.engine.evaluate(amount_cents, recipient, "USD")
    try:
        receipt = stipend.pay(
            recipient=recipient,
            amount_cents=amount_cents,
            currency="USD",
            memo=memo or "",
            prompt=prompt_for_audit,
        )
    except PolicyDenied as exc:
        # Render the trace with the denied receipt-shaped stub.
        from stipend.receipt import Receipt

        stub = Receipt(
            id=None,
            status="denied",
            recipient=recipient,
            amount_cents=amount_cents,
            currency="USD",
            memo=memo or "",
            backend="mock",
            counterparty_id=None,
            policy_reason=str(exc),
        )
        sys.stdout.write(
            format_receipt_trace(stub, decision, prompt=command if not no_prompt else None)
        )
        raise typer.Exit(code=3) from None
    except ApprovalRequired as exc:
        from stipend.receipt import Receipt

        stub = Receipt(
            id=None,
            status="requires_approval",
            recipient=recipient,
            amount_cents=amount_cents,
            currency="USD",
            memo=memo or "",
            backend="mock",
            counterparty_id=None,
            policy_reason=str(exc),
        )
        sys.stdout.write(
            format_receipt_trace(stub, decision, prompt=command if not no_prompt else None)
        )
        raise typer.Exit(code=4) from None
    except NotYetAvailable as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=5) from None
    except AuditWriteError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=6) from None

    sys.stdout.write(
        format_receipt_trace(
            receipt,
            decision,
            prompt=command if not no_prompt else None,
            simulated_eta_seconds=stipend.backend.simulated_eta_seconds  # type: ignore[attr-defined]
            if backend == "mock"
            else None,
        )
    )


def _parse_run_command(command: str) -> tuple[str, int, str | None] | None:
    """Parse the v0.1 supported NL pattern via regex.

    The recipient is normalized to lowercase kebab-case so the demo input
    ``"Pay Acme Logistics $4,250 for invoice 14"`` produces the recipient
    ``acme-logistics`` that the locked brand-surface trace block (and the
    sample policy's allow list) expects.
    """
    match = _RUN_RE.match(command)
    if match is None:
        return None
    recipient_raw = match.group("recipient").strip()
    recipient = _normalize_recipient(recipient_raw)
    amount_text = match.group("amount").replace(",", "")
    try:
        dollars = float(amount_text)
    except ValueError:
        return None
    amount_cents = round(dollars * 100)
    memo = (match.group("memo") or None)
    return recipient, amount_cents, memo


def _normalize_recipient(name: str) -> str:
    """Normalize a natural-language recipient phrase to an id-like slug.

    Lowercases and replaces runs of whitespace with single hyphens. Keeps
    existing hyphens and alphanumerics. This is intentionally simple; v0.2's
    LLM-mode NL parser will resolve recipients against a counterparty
    directory and skip this regex step entirely.
    """
    return re.sub(r"\s+", "-", name.strip().lower())


# ---------------------------------------------------------------- audit

@audit_app.command("tail")
def audit_tail(
    last: Annotated[int, typer.Option("--last", help="Number of entries to show.")] = 10,
    output_format: Annotated[
        str, typer.Option("--format", help="table or json.")
    ] = "table",
    root: Annotated[
        str, typer.Option("--root", help="Directory holding .stipend/.")
    ] = ".stipend",
) -> None:
    """Show the most recent audit entries."""
    from stipend.audit import AuditLog

    log = AuditLog(root)
    entries = log.tail(last)
    if not entries:
        typer.echo("no audit entries")
        return
    if output_format == "json":
        sys.stdout.write(
            json.dumps([_entry_as_dict(e) for e in entries], indent=2) + "\n"
        )
        return
    # table
    for e in entries:
        typer.echo(
            f"{e.ts}  {e.tool:<13}  {e.policy_decision:<18}  {e.status:<18}  "
            f"{e.args.get('recipient', '')}"
        )


@audit_app.command("prune")
def audit_prune(
    older_than: Annotated[
        str,
        typer.Option(
            "--older-than", help="Duration, e.g. 7d, 24h, 30m."
        ),
    ] = "7d",
    root: Annotated[
        str, typer.Option("--root", help="Directory holding .stipend/.")
    ] = ".stipend",
) -> None:
    """Remove audit entries older than a duration."""
    from stipend.audit import AuditLog

    delta = _parse_duration(older_than)
    if delta is None:
        typer.echo(
            f"error: could not parse duration {older_than!r}; expected e.g. 7d, 24h, 30m",
            err=True,
        )
        raise typer.Exit(code=2)
    log = AuditLog(root)
    removed = log.prune(delta)
    typer.echo(f"pruned {removed} entries older than {older_than}")


def _parse_duration(spec: str) -> timedelta | None:
    """Parse a short duration like ``7d``, ``24h``, ``30m``."""
    spec = spec.strip().lower()
    if not spec:
        return None
    unit = spec[-1]
    try:
        value = int(spec[:-1])
    except ValueError:
        return None
    if unit == "d":
        return timedelta(days=value)
    if unit == "h":
        return timedelta(hours=value)
    if unit == "m":
        return timedelta(minutes=value)
    if unit == "s":
        return timedelta(seconds=value)
    return None


def _entry_as_dict(e: object) -> dict[str, object]:
    """Best-effort dict shape for JSON output."""
    from dataclasses import asdict

    return asdict(e)  # type: ignore[arg-type]


# ---------------------------------------------------------------- policy

@policy_app.command("validate")
def policy_validate(
    path: Annotated[str, typer.Argument(help="Path to policy YAML.")],
) -> None:
    """Validate a policy YAML against the schema."""
    try:
        Policy.load(path)
    except PolicyConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from None
    typer.echo(f"{path}: valid")


# ------------------------------------------------------------------- mcp

@app.command("mcp")
def mcp_cmd(
    policy_path: Annotated[
        str, typer.Option("--policy", help="Policy YAML path.")
    ] = "policy.yaml",
    backend: Annotated[
        str, typer.Option("--backend", help="Backend: mock or agentrail.")
    ] = "mock",
    root: Annotated[
        str, typer.Option("--root", help="Directory holding .stipend/.")
    ] = ".stipend",
) -> None:
    """Start the stdio MCP server."""
    from stipend.mcp_server import serve_stdio

    serve_stdio(policy_path=policy_path, backend=backend, root=root)


# --------------------------------------------------------------- samples

_SAMPLE_POLICY_YAML = """version: 1
agent: demo-agent
limits:
  per_transaction_cap: { USD: 25_000_00 }
  daily_cap:           { USD: 250_000_00 }
recipients:
  allowed: ["acme-logistics", "carrier-*"]
  blocked: ["sanctioned-*"]
approvals:
  requires_approval_above: { USD: 10_000_00 }
"""

_SAMPLE_EXAMPLE_PY = '''"""Minimal SDK demo. Run with `python example.py`."""

from stipend import Stipend

s = Stipend(policy="policy.yaml", backend="mock")

receipt = s.pay(
    recipient="acme-logistics",
    amount_cents=425_000,
    currency="USD",
    memo="Invoice 14",
)

print(f"Sent (mock). Receipt id: {receipt.id}")

for entry in s.audit.tail(5):
    print(entry.ts, entry.tool, entry.policy_decision, entry.status)
'''

_SAMPLE_PROJECT_README = """# Stipend demo project

```
stipend run "Pay Acme Logistics $4,250 for invoice 14"
```

Or via the SDK:

```
python example.py
```

See [stipend on GitHub](https://github.com/agent-rail/stipend) for docs.
"""
