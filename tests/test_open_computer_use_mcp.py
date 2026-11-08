"""MCP client tests against local fake servers (no external network)."""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent.providers.base import ToolCall
from mcp.client import McpServerConfig, McpSession, make_tool_name
from mcp.manager import build_mcp_toolset

STDIO_SERVER = r'''
import json
import sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message.get("method")
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1.0"},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "echo",
                    "description": "Echo the text back",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                }
            ]
        }
    elif method == "tools/call":
        text = str((message.get("params") or {}).get("arguments", {}).get("text", ""))
        result = {"content": [{"type": "text", "text": "echo:" + text}], "isError": False}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}) + "\n")
    sys.stdout.flush()
'''


async def test_stdio_toolset_lists_and_calls_tools():
    config = McpServerConfig(
        id="fake",
        name="Fake",
        transport="stdio",
        command=sys.executable,
        args=["-u", "-c", STDIO_SERVER],
    )
    toolset = await build_mcp_toolset([config], timeout=5)
    try:
        assert toolset.failures == []
        assert [spec.name for spec in toolset.specs] == ["mcp__fake__echo"]
        assert toolset.specs[0].parameters["required"] == ["text"]

        text, is_error = await toolset.execute(
            ToolCall(id="1", name="mcp__fake__echo", arguments={"text": "hello"})
        )
        assert text == "echo:hello"
        assert is_error is False
    finally:
        await toolset.close()


def test_tool_names_are_namespaced_and_unique():
    used = set()
    first = make_tool_name("My Server", "read-file", used)
    used.add(first)
    second = make_tool_name("My Server", "read-file", used)
    assert first == "mcp__my_server__read-file"
    assert second != first
    assert len(second) <= 64


class _McpHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        message = json.loads(self.rfile.read(length) or b"{}")
        if "id" not in message:
            self.send_response(202)
            self.end_headers()
            return
        method = message.get("method")
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "http-fake", "version": "1.0"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "ping",
                        "description": "Ping the server",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
            }
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": "pong"}], "isError": False}
        else:
            result = {}
        body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


async def test_http_session_lists_and_calls_tools():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _McpHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    config = McpServerConfig(
        id="http",
        name="HTTP fake",
        transport="http",
        url=f"http://127.0.0.1:{port}/mcp",
    )
    session = McpSession(config)
    try:
        await session.connect(timeout=5)
        assert [tool["name"] for tool in session.tools] == ["ping"]
        text, is_error = await session.call_tool("ping", {})
        assert text == "pong"
        assert is_error is False
    finally:
        await session.close()
        server.shutdown()
