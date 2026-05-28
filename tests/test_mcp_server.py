"""MCP server: direct-dispatch + subprocess smoke (T8 / 3C).

Decision 3C splits coverage into two shapes:

- Direct dispatch through ``handle_message`` for branch coverage of every
  tool path. Fast and cheap to write.

- One or two subprocess smoke tests that spawn ``python -m stipend mcp``
  with a tmp policy and exchange real stdio JSON-RPC, validating that the
  hand-rolled framing actually works end-to-end.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from stipend.core import Stipend
from stipend.mcp_server import (
    APPROVAL_REQUIRED_CODE,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    POLICY_DENIED_CODE,
    TOOL_SCHEMAS,
    handle_message,
    serve_stdio,
)

# ---------------------------------------------------------- fixtures

def _policy_path(tmp_path: Path) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(
        """version: 1
agent: test
limits:
  per_transaction_cap: { USD: 50_000_00 }
recipients:
  allowed: ["acme-*"]
  blocked: ["sanctioned-*"]
approvals:
  requires_approval_above: { USD: 30_000_00 }
""",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def stipend(tmp_path: Path) -> Stipend:
    return Stipend(
        policy=str(_policy_path(tmp_path)),
        backend="mock",
        root=str(tmp_path / ".stipend"),
    )


# ----------------------------------------------------- handshake / meta

def test_initialize(stipend: Stipend) -> None:
    resp = handle_message(
        stipend, {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    )
    assert resp is not None
    assert resp["result"]["protocolVersion"]
    assert resp["result"]["serverInfo"]["name"] == "stipend"


def test_initialized_notification(stipend: Stipend) -> None:
    resp = handle_message(stipend, {"jsonrpc": "2.0", "method": "initialized"})
    assert resp is None  # notifications get no response


def test_ping(stipend: Stipend) -> None:
    resp = handle_message(stipend, {"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert resp == {"jsonrpc": "2.0", "id": 2, "result": {}}


def test_tools_list(stipend: Stipend) -> None:
    resp = handle_message(
        stipend, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
    )
    assert resp is not None
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {"pay", "charge", "refund", "policy_check", "audit_search"}


# ----------------------------------------------------------- protocol errors

def test_non_dict_request(stipend: Stipend) -> None:
    resp = handle_message(stipend, [])  # type: ignore[arg-type]
    assert resp is not None and resp["error"]["code"] == INVALID_REQUEST


def test_missing_jsonrpc_field(stipend: Stipend) -> None:
    resp = handle_message(stipend, {"id": 1, "method": "ping"})
    assert resp is not None and resp["error"]["code"] == INVALID_REQUEST


def test_missing_method(stipend: Stipend) -> None:
    resp = handle_message(stipend, {"jsonrpc": "2.0", "id": 1})
    assert resp is not None and resp["error"]["code"] == INVALID_REQUEST


def test_unknown_method(stipend: Stipend) -> None:
    resp = handle_message(
        stipend, {"jsonrpc": "2.0", "id": 1, "method": "totally/unknown"}
    )
    assert resp is not None and resp["error"]["code"] == METHOD_NOT_FOUND


# ------------------------------------------------------------ pay tool

def _call(stipend: Stipend, name: str, arguments: dict) -> dict:
    resp = handle_message(
        stipend,
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert resp is not None
    return resp


def test_call_pay_happy(stipend: Stipend) -> None:
    resp = _call(
        stipend,
        "pay",
        {"recipient": "acme-1", "amount_cents": 1_000_00, "memo": "x"},
    )
    assert "result" in resp
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["status"] == "settled"
    assert payload["recipient"] == "acme-1"


def test_call_pay_denied(stipend: Stipend) -> None:
    resp = _call(
        stipend, "pay", {"recipient": "sanctioned-co", "amount_cents": 100}
    )
    assert resp["error"]["code"] == POLICY_DENIED_CODE


def test_call_pay_requires_approval(stipend: Stipend) -> None:
    resp = _call(
        stipend, "pay", {"recipient": "acme-1", "amount_cents": 40_000_00}
    )
    assert resp["error"]["code"] == APPROVAL_REQUIRED_CODE


def test_call_pay_missing_arg(stipend: Stipend) -> None:
    resp = _call(stipend, "pay", {"amount_cents": 100})
    assert resp["error"]["code"] == INVALID_PARAMS


def test_call_unknown_tool(stipend: Stipend) -> None:
    resp = _call(stipend, "obliterate", {})
    assert resp["error"]["code"] == METHOD_NOT_FOUND


# ------------------------------------------------------- charge / refund

def test_call_charge(stipend: Stipend) -> None:
    resp = _call(
        stipend, "charge", {"source": "acme-1", "amount_cents": 500, "memo": "x"}
    )
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["status"] == "settled"


def test_call_refund(stipend: Stipend) -> None:
    paid = _call(
        stipend, "pay", {"recipient": "acme-1", "amount_cents": 500, "memo": "x"}
    )
    paid_id = json.loads(paid["result"]["content"][0]["text"])["id"]
    refund = _call(stipend, "refund", {"payment_id": paid_id})
    payload = json.loads(refund["result"]["content"][0]["text"])
    assert payload["status"] == "settled"


# ------------------------------------------------- policy_check / audit_search

def test_call_policy_check(stipend: Stipend) -> None:
    resp = _call(
        stipend,
        "policy_check",
        {"amount_cents": 100, "recipient": "acme-1"},
    )
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["decision"] == "allow"


def test_call_audit_search(stipend: Stipend) -> None:
    _call(
        stipend,
        "pay",
        {"recipient": "acme-1", "amount_cents": 100, "memo": "x"},
    )
    resp = _call(stipend, "audit_search", {"filter": {"agent": "test"}})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert len(payload["entries"]) >= 1


def test_call_audit_search_bad_filter_key(stipend: Stipend) -> None:
    resp = _call(stipend, "audit_search", {"filter": {"nope": "x"}})
    assert resp["error"]["code"] == INVALID_PARAMS


def test_call_audit_search_filter_not_object(stipend: Stipend) -> None:
    resp = _call(stipend, "audit_search", {"filter": "string"})
    assert resp["error"]["code"] == INVALID_PARAMS


# ------------------------------------------------------- tool schemas

def test_tool_schemas_are_self_consistent() -> None:
    for tool in TOOL_SCHEMAS:
        assert "name" in tool
        assert "description" in tool
        assert tool["inputSchema"]["type"] == "object"
        assert "required" in tool["inputSchema"]


# ------------------------------------------------------- in-process stdio

def test_stdio_loop_handles_request_and_eof(tmp_path: Path) -> None:
    """Drive serve_stdio with a string-based stdin/stdout to test framing."""
    policy = _policy_path(tmp_path)
    request = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"
    )
    stdin = io.StringIO(request)
    stdout = io.StringIO()
    stderr = io.StringIO()
    serve_stdio(
        policy_path=str(policy),
        backend="mock",
        root=str(tmp_path / ".stipend"),
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
    )
    lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1
    resp = json.loads(lines[0])
    assert resp == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_stdio_loop_handles_parse_error(tmp_path: Path) -> None:
    policy = _policy_path(tmp_path)
    stdin = io.StringIO("this is not json\n")
    stdout = io.StringIO()
    stderr = io.StringIO()
    serve_stdio(
        policy_path=str(policy),
        backend="mock",
        root=str(tmp_path / ".stipend"),
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
    )
    lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1
    err = json.loads(lines[0])
    assert err["error"]["code"] == PARSE_ERROR


# ----------------------------------------------------------- subprocess smoke

def test_subprocess_round_trip(tmp_path: Path) -> None:
    """Real subprocess: spawn `python -m stipend mcp`, exchange JSON-RPC."""
    policy = _policy_path(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get(
        "PYTHONPATH", ""
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "stipend",
            "mcp",
            "--policy",
            str(policy),
            "--root",
            str(tmp_path / ".stipend"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    try:
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "pay",
                    "arguments": {
                        "recipient": "acme-1",
                        "amount_cents": 100,
                        "memo": "smoke",
                    },
                },
            },
        ]
        assert proc.stdin is not None
        for m in msgs:
            proc.stdin.write(json.dumps(m) + "\n")
        proc.stdin.flush()
        proc.stdin.close()

        assert proc.stdout is not None
        stdout_text = proc.stdout.read()
        if proc.stderr is not None:
            proc.stderr.read()  # drain to avoid pipe buffer fill
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        # Close all pipes to avoid ResourceWarning under strict warnings filter.
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None and not stream.closed:
                stream.close()

    output_lines = [line for line in stdout_text.splitlines() if line.strip()]
    parsed = [json.loads(line) for line in output_lines]
    assert len(parsed) >= 3
    init_resp = next(p for p in parsed if p.get("id") == 1)
    assert init_resp["result"]["serverInfo"]["name"] == "stipend"
    tools_resp = next(p for p in parsed if p.get("id") == 2)
    names = {t["name"] for t in tools_resp["result"]["tools"]}
    assert "pay" in names
    pay_resp = next(p for p in parsed if p.get("id") == 3)
    payload = json.loads(pay_resp["result"]["content"][0]["text"])
    assert payload["status"] == "settled"
