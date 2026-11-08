"""MCP over stdio and streamable HTTP (JSON-RPC 2.0), using only stdlib + httpx."""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "open-computer-use", "version": "0.2.0"}
_TOOL_SAFE = re.compile(r"[^a-zA-Z0-9_-]+")


class McpError(RuntimeError):
    """Raised when an MCP server cannot be reached or answers with an error."""


@dataclass
class McpServerConfig:
    id: str
    name: str
    transport: str = "stdio"  # stdio | http
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True


def _error_text(error: Any) -> str:
    if isinstance(error, dict):
        return str(error.get("message") or error.get("data") or error)
    return str(error)


def make_tool_name(server_name: str, tool_name: str, used: Optional[set] = None) -> str:
    """Build a provider-safe, namespaced tool name (max 64 chars)."""
    server = _TOOL_SAFE.sub("_", (server_name or "mcp").strip().lower()).strip("_") or "mcp"
    tool = _TOOL_SAFE.sub("_", str(tool_name or "tool")).strip("_") or "tool"
    base = f"mcp__{server}__{tool}"[:64]
    name = base
    index = 2
    while used is not None and name in used:
        suffix = f"__{index}"
        name = base[: 64 - len(suffix)] + suffix
        index += 1
    return name


class _StdioTransport:
    def __init__(self, command: str, args: List[str], env: Dict[str, str]) -> None:
        self.command = command
        self.args = [str(arg) for arg in args]
        self.env = {str(key): str(value) for key, value in (env or {}).items()}
        self.process: Optional[asyncio.subprocess.Process] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._next_id = 0
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._stderr = ""

    async def start(self) -> "_StdioTransport":
        loop = asyncio.get_running_loop()
        try:
            self.process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **self.env},
            )
        except FileNotFoundError as exc:
            raise McpError(f"Command not found: {self.command}") from exc
        except OSError as exc:
            raise McpError(f"Could not start {self.command}: {exc}") from exc
        self._reader_task = loop.create_task(self._read_loop())
        self._stderr_task = loop.create_task(self._read_stderr())
        return self

    async def _read_loop(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            if "id" in message and ("result" in message or "error" in message):
                pending = self._pending.pop(str(message["id"]), None)
                if pending is not None and not pending.done():
                    if message.get("error"):
                        pending.set_exception(McpError(_error_text(message["error"])))
                    else:
                        pending.set_result(message.get("result") or {})
            # Server-initiated requests (sampling, roots) are not supported.
        detail = self._stderr.strip()
        for pending in self._pending.values():
            if not pending.done():
                pending.set_exception(
                    McpError(
                        f"{self.command} exited"
                        + (f": {detail[-300:]}" if detail else "")
                    )
                )
        self._pending.clear()

    async def _read_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        while True:
            chunk = await process.stderr.readline()
            if not chunk:
                break
            self._stderr = (self._stderr + chunk.decode("utf-8", "replace"))[-2000:]

    async def _write(self, payload: Dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise McpError("MCP stdio process is not running.")
        try:
            self.process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise McpError("MCP server closed its input.") from exc

    async def request(
        self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 60.0
    ) -> Dict[str, Any]:
        if self.process is None:
            await self.start()
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[str(request_id)] = future
        await self._write(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        )
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(str(request_id), None)
            raise McpError(
                f"{self.command} did not answer {method} within {timeout:.0f}s."
            ) from exc

    async def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def close(self) -> None:
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
        if self.process is not None and self.process.returncode is None:
            try:
                if self.process.stdin is not None:
                    self.process.stdin.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()


class _HttpTransport:
    def __init__(self, url: str, headers: Dict[str, str]) -> None:
        self.url = url
        self.headers = {str(key): str(value) for key, value in (headers or {}).items()}
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=True
        )
        self._session_id: Optional[str] = None
        self._next_id = 0

    async def start(self) -> "_HttpTransport":
        return self

    def _request_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.headers,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def request(
        self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 60.0
    ) -> Dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        try:
            response = await self._client.post(
                self.url, json=payload, headers=self._request_headers(), timeout=timeout
            )
        except httpx.HTTPError as exc:
            raise McpError(f"Could not reach {self.url}: {exc}") from exc
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        if response.status_code >= 400:
            raise McpError(
                f"MCP server error {response.status_code}: {response.text[:300]}"
            )
        if response.status_code == 202 or not response.content:
            raise McpError(f"MCP server did not answer {method}.")
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            return self._parse_sse(response.text, request_id)
        try:
            data = response.json()
        except ValueError as exc:
            raise McpError("MCP server returned invalid JSON.") from exc
        if isinstance(data, list):
            data = next(
                (item for item in data if str(item.get("id")) == str(request_id)), None
            )
            if data is None:
                raise McpError(f"MCP server did not answer {method}.")
        if data.get("error"):
            raise McpError(_error_text(data["error"]))
        return data.get("result") or {}

    def _parse_sse(self, text: str, request_id: int) -> Dict[str, Any]:
        for event in text.split("\n\n"):
            data_lines = [
                line[5:].strip()
                for line in event.splitlines()
                if line.startswith("data:")
            ]
            if not data_lines:
                continue
            try:
                message = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                continue
            if str(message.get("id")) != str(request_id):
                continue
            if message.get("error"):
                raise McpError(_error_text(message["error"]))
            return message.get("result") or {}
        raise McpError("MCP server sent no matching response.")

    async def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        try:
            await self._client.post(
                self.url,
                json={"jsonrpc": "2.0", "method": method, "params": params or {}},
                headers=self._request_headers(),
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            raise McpError(f"Could not reach {self.url}: {exc}") from exc

    async def close(self) -> None:
        await self._client.aclose()


class McpSession:
    """One initialized MCP connection with its tool catalog."""

    def __init__(self, config: McpServerConfig) -> None:
        self.config = config
        if config.transport == "http":
            self.transport: Any = _HttpTransport(config.url, config.headers)
        else:
            self.transport = _StdioTransport(config.command, config.args, config.env)
        self.tools: List[Dict[str, Any]] = []
        self.server_info: Dict[str, Any] = {}

    async def connect(self, timeout: float = 20.0) -> "McpSession":
        await self.transport.start()
        result = await self.transport.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            timeout=timeout,
        )
        self.server_info = result.get("serverInfo") or {}
        await self.transport.notify("notifications/initialized")

        tools: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            page = await self.transport.request(
                "tools/list", {"cursor": cursor} if cursor else {}, timeout=timeout
            )
            tools.extend(tool for tool in page.get("tools") or [] if isinstance(tool, dict))
            cursor = page.get("nextCursor")
            if not cursor:
                break
        self.tools = [tool for tool in tools if tool.get("name")]
        return self

    async def call_tool(
        self, name: str, arguments: Optional[Dict[str, Any]], timeout: float = 120.0
    ) -> tuple[str, bool]:
        result = await self.transport.request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=timeout,
        )
        parts: List[str] = []
        for item in result.get("content") or []:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == "text":
                parts.append(str(item.get("text") or ""))
            elif kind == "image":
                parts.append("[image data returned by MCP tool]")
            elif kind == "resource":
                resource = item.get("resource") or {}
                parts.append(str(resource.get("text") or resource.get("uri") or "[resource]"))
            else:
                parts.append(json.dumps(item)[:1000])
        if result.get("structuredContent") is not None:
            parts.append(json.dumps(result["structuredContent"])[:4000])
        text = "\n".join(part for part in parts if part).strip() or "(no output)"
        return text[:8000], bool(result.get("isError"))

    async def close(self) -> None:
        try:
            await self.transport.close()
        except Exception:
            pass
