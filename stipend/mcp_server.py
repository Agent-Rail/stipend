"""Hand-rolled stdio MCP server.

Per /plan-eng-review decision **1A=B**, this module implements the MCP
JSON-RPC 2.0 framing directly rather than depending on the official ``mcp``
Python SDK. The wire format is line-delimited JSON over stdin / stdout, one
JSON object per line. Notifications (no ``id`` field) are accepted but never
require a response; requests get exactly one response back.

The dispatch surface implements the subset of MCP needed to drive the five
Stipend tools from Claude Desktop / Cursor / Continue:

- ``initialize`` — handshake.
- ``initialized`` — client confirmation (notification).
- ``tools/list`` — advertise the five tools.
- ``tools/call`` — invoke one of the five tools.
- ``ping`` — health check.

Per decision **1D**, the server constructs one :class:`Stipend` instance at
startup and reuses it for the whole session; pending mock payments survive
between ``tools/call`` invocations within a single ``stipend mcp`` process.

Per decision **3C**, the dispatcher is exposed as a standalone function
:func:`handle_message` so direct-dispatch tests can drive every branch
without going through stdio.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, is_dataclass
from typing import Any, BinaryIO, TextIO

from stipend.audit import AuditEntry
from stipend.core import Stipend
from stipend.errors import (
    ApprovalRequired,
    NotYetAvailable,
    PolicyConfigError,
    PolicyDenied,
)
from stipend.receipt import Receipt

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "stipend"
SERVER_VERSION = "0.1.0"

# JSON-RPC error codes per spec.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Stipend-specific error codes (must be in -32000 .. -32099 range).
POLICY_DENIED_CODE = -32001
APPROVAL_REQUIRED_CODE = -32002
NOT_YET_AVAILABLE_CODE = -32003


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "pay",
        "description": "Send a mock payment to a recipient through the active backend.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipient": {"type": "string"},
                "amount_cents": {"type": "integer", "minimum": 1},
                "currency": {"type": "string", "default": "USD"},
                "memo": {"type": "string", "default": ""},
                "prompt": {"type": ["string", "null"], "default": None},
            },
            "required": ["recipient", "amount_cents"],
        },
    },
    {
        "name": "charge",
        "description": "Pull a mock charge from a source.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "amount_cents": {"type": "integer", "minimum": 1},
                "currency": {"type": "string", "default": "USD"},
                "memo": {"type": "string", "default": ""},
                "prompt": {"type": ["string", "null"], "default": None},
            },
            "required": ["source", "amount_cents"],
        },
    },
    {
        "name": "refund",
        "description": "Refund a prior payment, fully or partially.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "payment_id": {"type": "string"},
                "amount_cents": {"type": ["integer", "null"], "default": None},
                "prompt": {"type": ["string", "null"], "default": None},
            },
            "required": ["payment_id"],
        },
    },
    {
        "name": "policy_check",
        "description": "Run the policy engine without executing the transaction.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "amount_cents": {"type": "integer", "minimum": 1},
                "recipient": {"type": "string"},
                "currency": {"type": "string", "default": "USD"},
            },
            "required": ["amount_cents", "recipient"],
        },
    },
    {
        "name": "audit_search",
        "description": "Search the audit log by agent, status, recipient, or ts range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filter": {"type": "object"},
            },
            "required": ["filter"],
        },
    },
]


def make_stipend(
    *,
    policy_path: str = "policy.yaml",
    backend: str = "mock",
    root: str = ".stipend",
) -> Stipend:
    """Construct a Stipend instance for the MCP server session.

    Raised to module scope so tests can build a Stipend pointed at a temp
    directory without going through the full ``serve_stdio`` entry point.
    """
    return Stipend(policy=policy_path, backend=backend, root=root)


def serve_stdio(
    *,
    policy_path: str = "policy.yaml",
    backend: str = "mock",
    root: str = ".stipend",
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> None:
    """Run the stdio JSON-RPC loop forever.

    Reads one JSON object per line from ``stdin`` and writes responses
    (one JSON object per line) to ``stdout``. ``stderr`` is used for
    operator-visible warnings and the startup banner. The function returns
    when ``stdin`` reaches EOF or on KeyboardInterrupt.
    """
    sin = stdin if stdin is not None else sys.stdin
    sout = stdout if stdout is not None else sys.stdout
    serr = stderr if stderr is not None else sys.stderr

    try:
        stipend = make_stipend(policy_path=policy_path, backend=backend, root=root)
    except PolicyConfigError as exc:
        serr.write(f"stipend mcp: policy error: {exc}\n")
        serr.flush()
        sys.exit(2)

    serr.write(
        f"stipend mcp (v{SERVER_VERSION}) ready on stdio; policy={policy_path}, "
        f"backend={backend}\n"
    )
    serr.flush()

    while True:
        line = sin.readline()
        if not line:
            return  # EOF
        line = line.strip()
        if not line:
            continue
        try:
            request: Any = json.loads(line)
        except json.JSONDecodeError as exc:
            _write(
                sout,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": PARSE_ERROR, "message": f"parse error: {exc}"},
                },
            )
            continue
        try:
            response = handle_message(stipend, request)
        except KeyboardInterrupt:
            return
        if response is not None:
            _write(sout, response)


def handle_message(stipend: Stipend, request: dict[str, Any]) -> dict[str, Any] | None:
    """Dispatch a single JSON-RPC message.

    Returns the response object to write, or ``None`` for notifications.
    Never raises; protocol-level errors are turned into JSON-RPC error
    responses.
    """
    if not isinstance(request, dict):
        return _err(None, INVALID_REQUEST, "expected JSON object")
    if request.get("jsonrpc") != "2.0":
        return _err(request.get("id"), INVALID_REQUEST, "missing or wrong 'jsonrpc' field")
    method = request.get("method")
    if not isinstance(method, str):
        return _err(request.get("id"), INVALID_REQUEST, "missing 'method' string field")
    req_id = request.get("id")
    is_notification = "id" not in request

    if method == "initialize":
        return _ok(
            req_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method in ("initialized", "notifications/initialized"):
        return None  # notifications get no response

    if method == "ping":
        return _ok(req_id, {})

    if method == "tools/list":
        return _ok(req_id, {"tools": TOOL_SCHEMAS})

    if method == "tools/call":
        params = request.get("params") or {}
        if not isinstance(params, dict):
            return _err(req_id, INVALID_PARAMS, "params must be an object")
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            return _err(req_id, INVALID_PARAMS, "missing 'name' string in params")
        if not isinstance(arguments, dict):
            return _err(req_id, INVALID_PARAMS, "'arguments' must be an object")
        return _dispatch_tool(stipend, req_id, name, arguments)

    if is_notification:
        return None
    return _err(req_id, METHOD_NOT_FOUND, f"unknown method: {method}")


def _dispatch_tool(
    stipend: Stipend, req_id: Any, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Translate a ``tools/call`` into a Stipend method invocation."""
    try:
        if name == "pay":
            receipt = stipend.pay(
                recipient=arguments["recipient"],
                amount_cents=int(arguments["amount_cents"]),
                currency=arguments.get("currency", "USD"),
                memo=arguments.get("memo", ""),
                prompt=arguments.get("prompt"),
            )
            return _ok(req_id, _tool_result(_receipt_to_dict(receipt)))
        if name == "charge":
            receipt = stipend.charge(
                source=arguments["source"],
                amount_cents=int(arguments["amount_cents"]),
                currency=arguments.get("currency", "USD"),
                memo=arguments.get("memo", ""),
                prompt=arguments.get("prompt"),
            )
            return _ok(req_id, _tool_result(_receipt_to_dict(receipt)))
        if name == "refund":
            receipt = stipend.refund(
                payment_id=arguments["payment_id"],
                amount_cents=arguments.get("amount_cents"),
                prompt=arguments.get("prompt"),
            )
            return _ok(req_id, _tool_result(_receipt_to_dict(receipt)))
        if name == "policy_check":
            decision = stipend.policy_check(
                amount_cents=int(arguments["amount_cents"]),
                recipient=arguments["recipient"],
                currency=arguments.get("currency", "USD"),
            )
            return _ok(
                req_id,
                _tool_result(
                    {
                        "decision": decision.decision,
                        "reason": decision.reason,
                        "rule": decision.rule,
                    }
                ),
            )
        if name == "audit_search":
            filter_obj = arguments.get("filter") or {}
            if not isinstance(filter_obj, dict):
                return _err(req_id, INVALID_PARAMS, "'filter' must be an object")
            entries = stipend.audit.search(filter_obj)
            return _ok(
                req_id,
                _tool_result(
                    {"entries": [_audit_entry_to_dict(e) for e in entries]}
                ),
            )
        return _err(req_id, METHOD_NOT_FOUND, f"unknown tool: {name}")
    except PolicyDenied as exc:
        return _err(req_id, POLICY_DENIED_CODE, str(exc))
    except ApprovalRequired as exc:
        return _err(req_id, APPROVAL_REQUIRED_CODE, str(exc))
    except NotYetAvailable as exc:
        return _err(req_id, NOT_YET_AVAILABLE_CODE, str(exc))
    except KeyError as exc:
        return _err(req_id, INVALID_PARAMS, f"missing required arg: {exc.args[0]}")
    except (TypeError, ValueError) as exc:
        return _err(req_id, INVALID_PARAMS, str(exc))


def _tool_result(payload: Any) -> dict[str, Any]:
    """Wrap a payload in the MCP ``tools/call`` result envelope."""
    text = json.dumps(payload, default=_json_default, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "isError": False,
    }


def _receipt_to_dict(receipt: Receipt) -> dict[str, Any]:
    return asdict(receipt)


def _audit_entry_to_dict(entry: AuditEntry) -> dict[str, Any]:
    return asdict(entry)


def _ok(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def _write(stream: TextIO | BinaryIO, obj: dict[str, Any]) -> None:
    line = json.dumps(obj, default=_json_default) + "\n"
    if isinstance(stream, (bytes, bytearray)) or (hasattr(stream, "write") and "b" in getattr(
        stream, "mode", ""
    )):
        stream.write(line.encode("utf-8"))  # type: ignore[arg-type]
    else:
        stream.write(line)  # type: ignore[arg-type]
    stream.flush()


def _json_default(value: Any) -> Any:
    """Serialize datetimes and other dataclasses for the wire."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return str(value)
