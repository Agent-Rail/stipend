"""CLI: init, run, audit, policy validate."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stipend.cli import _parse_run_command, app

runner = CliRunner()


@contextlib.contextmanager
def _chdir(target: Path):
    """Like CliRunner.isolated_filesystem; just chdir for the duration."""
    prior = Path.cwd()
    os.chdir(target)
    try:
        yield
    finally:
        os.chdir(prior)


# -------------------------------------------------------------- regex parser

@pytest.mark.parametrize(
    "command,expected",
    [
        ("Pay Acme Logistics $4,250 for invoice 14", ("acme-logistics", 425_000, "invoice 14")),
        ("pay Acme $500", ("acme", 50_000, None)),
        ("PAY carrier-1 $50.25 for fuel", ("carrier-1", 5_025, "fuel")),
        ("Pay foo $1,234.50 for stuff", ("foo", 123_450, "stuff")),
    ],
)
def test_parse_run_command_happy(command: str, expected: tuple[str, int, str | None]) -> None:
    assert _parse_run_command(command) == expected


@pytest.mark.parametrize(
    "command",
    [
        "send some money",
        "Pay foo for stuff",
        "pay $500",
        "",
    ],
)
def test_parse_run_command_rejects_bad_input(command: str) -> None:
    assert _parse_run_command(command) is None


# ----------------------------------------------------------------- init

def test_init_scaffolds_project(tmp_path: Path) -> None:
    with _chdir(tmp_path):
        result = runner.invoke(app, ["init", "demo"])
        assert result.exit_code == 0, result.stdout
        assert Path("demo/policy.yaml").exists()
        assert Path("demo/example.py").exists()
        assert Path("demo/.stipend").exists()


def test_init_refuses_to_overwrite(tmp_path: Path) -> None:
    with _chdir(tmp_path):
        Path("demo").mkdir()
        (Path("demo") / "existing.txt").write_text("x")
        result = runner.invoke(app, ["init", "demo"])
        assert result.exit_code != 0


# ----------------------------------------------------------------- run

def _write_policy(path: Path) -> None:
    path.write_text(
        """version: 1
agent: test
limits:
  per_transaction_cap: { USD: 100_000_00 }
recipients:
  allowed: ["acme-*", "Acme*"]
  blocked: ["sanctioned-*"]
""",
        encoding="utf-8",
    )


def test_run_happy(tmp_path: Path) -> None:
    with _chdir(tmp_path):
        _write_policy(Path("policy.yaml"))
        result = runner.invoke(
            app, ["run", "Pay Acme Logistics $4,250 for invoice 14"]
        )
        assert result.exit_code == 0, result.stdout
        assert "[resolve]" in result.stdout
        assert "settled" in result.stdout


def test_run_denied_exit_code(tmp_path: Path) -> None:
    with _chdir(tmp_path):
        _write_policy(Path("policy.yaml"))
        result = runner.invoke(app, ["run", "Pay sanctioned-co $100"])
        assert result.exit_code == 3, result.stdout


def test_run_unparseable_input(tmp_path: Path) -> None:
    with _chdir(tmp_path):
        _write_policy(Path("policy.yaml"))
        result = runner.invoke(app, ["run", "do something nice"])
        assert result.exit_code == 2


def test_run_no_prompt_flag(tmp_path: Path) -> None:
    """`--no-prompt` omits the originating command from the audit entry."""
    with _chdir(tmp_path):
        _write_policy(Path("policy.yaml"))
        result = runner.invoke(
            app, ["run", "Pay Acme Logistics $100", "--no-prompt"]
        )
        assert result.exit_code == 0
        audit_text = Path(".stipend") / "audit.jsonl"
        assert audit_text.exists()
        first = json.loads(audit_text.read_text(encoding="utf-8").splitlines()[0])
        assert first["prompt"] is None


# ----------------------------------------------------------------- audit

def test_audit_tail_empty(tmp_path: Path) -> None:
    with _chdir(tmp_path):
        Path(".stipend").mkdir()
        result = runner.invoke(app, ["audit", "tail"])
        assert result.exit_code == 0
        assert "no audit entries" in result.stdout


def test_audit_tail_after_run(tmp_path: Path) -> None:
    with _chdir(tmp_path):
        _write_policy(Path("policy.yaml"))
        runner.invoke(app, ["run", "Pay Acme Logistics $100"])
        result = runner.invoke(app, ["audit", "tail", "--last", "1"])
        assert result.exit_code == 0
        # Recipient is normalized to kebab-case by the parser before audit write.
        assert "acme-logistics" in result.stdout


def test_audit_tail_json(tmp_path: Path) -> None:
    with _chdir(tmp_path):
        _write_policy(Path("policy.yaml"))
        runner.invoke(app, ["run", "Pay Acme Logistics $100"])
        result = runner.invoke(app, ["audit", "tail", "--format", "json"])
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, list) and len(parsed) >= 1


def test_audit_prune(tmp_path: Path) -> None:
    with _chdir(tmp_path):
        Path(".stipend").mkdir()
        # Seed an old line manually.
        (Path(".stipend") / "audit.jsonl").write_text(
            json.dumps(
                {
                    "audit_schema_version": 1,
                    "id": "aud_old",
                    "ts": "2020-01-01T00:00:00Z",
                    "agent": "test",
                    "prompt": None,
                    "tool": "pay",
                    "args": {},
                    "policy_decision": "allow",
                    "backend": "mock",
                    "receipt_id": None,
                    "status": "settled",
                    "error": None,
                    "parent_audit_id": None,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        result = runner.invoke(app, ["audit", "prune", "--older-than", "1d"])
        assert result.exit_code == 0
        assert "pruned 1 entries" in result.stdout


def test_audit_prune_bad_duration(tmp_path: Path) -> None:
    with _chdir(tmp_path):
        Path(".stipend").mkdir()
        result = runner.invoke(app, ["audit", "prune", "--older-than", "lots"])
        assert result.exit_code == 2


# -------------------------------------------------------------- policy validate

def test_policy_validate_ok(tmp_path: Path) -> None:
    with _chdir(tmp_path):
        _write_policy(Path("policy.yaml"))
        result = runner.invoke(app, ["policy", "validate", "policy.yaml"])
        assert result.exit_code == 0
        assert "valid" in result.stdout


def test_policy_validate_fails(tmp_path: Path) -> None:
    with _chdir(tmp_path):
        Path("bad.yaml").write_text("version: 1\n", encoding="utf-8")
        result = runner.invoke(app, ["policy", "validate", "bad.yaml"])
        assert result.exit_code == 2
