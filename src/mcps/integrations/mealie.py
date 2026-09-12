"""Mealie recipe API integration."""

from __future__ import annotations

from collections.abc import Mapping

import httpx
from mcp.server import MCPServer

from mcps.config import SectionConfig
from mcps.errors import ToolError
from mcps.http_client import request_json, warn_if_tls_disabled
from mcps.logging_setup import log_call

NAME = "mealie"
REQUIRED_KEYS = ("url", "api_key")


def _apply_auth(client: httpx.Client, data: Mapping[str, str]) -> None:
    client.headers["Authorization"] = f"Bearer {data['api_key']}"


def _call(section: SectionConfig, method: str, path: str, **kwargs) -> object:
    return request_json(section, method, path, apply_auth=_apply_auth, **kwargs)


def register(server: MCPServer, section: SectionConfig) -> None:
    warn_if_tls_disabled(section)

    @server.tool(name="mealie_list_recipes", description="List Mealie recipes.")
    @log_call("mealie_list_recipes")
    def list_recipes(limit: int = 20) -> list[dict]:
        """List recipes, capped at `limit` entries (default 20)."""
        if limit < 1:
            raise ToolError("limit must be >= 1")
        data = _call(section, "GET", "/api/recipes", params={"perPage": limit, "page": 1})
        if isinstance(data, dict) and "items" in data:
            items = data["items"]
            return items if isinstance(items, list) else []
        if isinstance(data, list):
            return data[:limit]
        return []

    @server.tool(name="mealie_get_recipe", description="Get one Mealie recipe by slug.")
    @log_call("mealie_get_recipe")
    def get_recipe(slug: str) -> dict:
        """Fetch a recipe by slug (e.g. `chicken-tikka-masala`)."""
        return _call(section, "GET", f"/api/recipes/{slug}")

    @server.tool(name="mealie_search_recipes", description="Search Mealie recipes by query.")
    @log_call("mealie_search_recipes")
    def search_recipes(query: str) -> list[dict]:
        """Search recipes whose title or summary contains `query`."""
        data = _call(section, "GET", "/api/recipes", params={"search": query})
        if isinstance(data, dict) and "items" in data:
            items = data["items"]
            return items if isinstance(items, list) else []
        if isinstance(data, list):
            return data
        return []
