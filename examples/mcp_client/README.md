# MCP client example

Stipend ships a stdio MCP server (`stipend mcp`). Any MCP-compatible client can drive
it: Claude Desktop, Cursor, Continue, OpenClaw, Hermes.

## Claude Desktop

Add the snippet from [`claude_desktop_config.json`](./claude_desktop_config.json) to
your `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows), then restart Claude Desktop.

The five tools (`pay`, `charge`, `refund`, `policy_check`, `audit_search`) appear in
Claude's tool palette. Ask Claude to "pay Acme Logistics $4,250" and watch the
trace render inline.

Make sure `stipend` is on the PATH the Claude Desktop process inherits. If your
shell config is not loaded, point `command` at an absolute path
(e.g. `/usr/local/bin/stipend`).

## Cursor and Continue

Both have native MCP support via similar JSON configs. Point them at
`stipend mcp` with a `--policy` flag if you keep `policy.yaml` outside the
current working directory.

## Custom client

The MCP server speaks line-delimited JSON-RPC 2.0 over stdio. Any client that can
spawn a subprocess and exchange JSON messages can drive it. See
`stipend/mcp_server.py` for the dispatched methods and the tool schemas.
