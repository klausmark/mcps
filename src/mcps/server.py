"""Server assembly: build MCPServer and run it on stdio."""

from __future__ import annotations

from mcp.server import MCPServer

from mcps.config import ServerConfig
from mcps.integrations import register_all


def build_server(config: ServerConfig) -> MCPServer:
    """Construct the MCP server with all enabled integrations registered."""
    server = MCPServer("mcps")
    register_all(server, config)
    return server
