"""Minimal Model Context Protocol client used by Open Computer Use.

Only the client side of the protocol is implemented: initialize, tools/list and
tools/call over stdio or streamable HTTP.
"""

from .client import McpError, McpServerConfig, McpSession, make_tool_name
from .manager import McpToolSet, build_mcp_toolset, probe_mcp_server

__all__ = [
    "McpError",
    "McpServerConfig",
    "McpSession",
    "McpToolSet",
    "build_mcp_toolset",
    "make_tool_name",
    "probe_mcp_server",
]
