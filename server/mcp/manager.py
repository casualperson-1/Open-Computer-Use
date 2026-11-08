"""Connect MCP servers for a run and expose their tools to the agent."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from agent.providers.base import ToolCall, ToolSpec

from .client import McpError, McpServerConfig, McpSession, make_tool_name


@dataclass
class McpToolSet:
    specs: List[ToolSpec] = field(default_factory=list)
    routes: Dict[str, Tuple[McpSession, str]] = field(default_factory=dict)
    sessions: List[McpSession] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)

    def owns(self, name: str) -> bool:
        return name in self.routes

    async def execute(self, call: ToolCall) -> Tuple[str, bool]:
        route = self.routes.get(call.name)
        if route is None:
            return f"Unknown MCP tool '{call.name}'.", True
        session, tool_name = route
        try:
            return await session.call_tool(tool_name, call.arguments or {})
        except McpError as exc:
            return f"MCP tool '{tool_name}' failed: {exc}", True
        except Exception as exc:  # pragma: no cover - defensive
            return f"MCP tool '{tool_name}' failed: {exc}", True

    async def close(self) -> None:
        for session in self.sessions:
            await session.close()
        self.sessions.clear()


async def build_mcp_toolset(
    configs: List[McpServerConfig], timeout: float = 20.0
) -> McpToolSet:
    toolset = McpToolSet()
    used: set = set()
    for config in configs:
        session = McpSession(config)
        try:
            await asyncio.wait_for(session.connect(timeout=timeout), timeout=timeout + 5)
        except Exception as exc:
            toolset.failures.append(f"{config.name}: {exc}")
            await session.close()
            continue

        toolset.sessions.append(session)
        for tool in session.tools:
            name = make_tool_name(config.name, tool["name"], used)
            used.add(name)
            schema = tool.get("inputSchema")
            if not isinstance(schema, dict) or not schema:
                schema = {"type": "object", "properties": {}}
            description = str(
                tool.get("description") or f"MCP tool {tool['name']} from {config.name}."
            )
            toolset.routes[name] = (session, str(tool["name"]))
            toolset.specs.append(
                ToolSpec(
                    name=name,
                    description=f"{description} (MCP server: {config.name})",
                    parameters=schema,
                )
            )
    return toolset


async def probe_mcp_server(
    config: McpServerConfig, timeout: float = 20.0
) -> List[Dict[str, str]]:
    """Connect once, list the tools, and disconnect."""
    session = McpSession(config)
    try:
        await asyncio.wait_for(session.connect(timeout=timeout), timeout=timeout + 5)
        return [
            {
                "name": str(tool["name"]),
                "description": str(tool.get("description") or "")[:300],
            }
            for tool in session.tools
        ]
    finally:
        await session.close()


__all__: List[str] = [
    "McpToolSet",
    "build_mcp_toolset",
    "probe_mcp_server",
]
