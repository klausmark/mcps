"""Mealie recipe API integration."""

from __future__ import annotations

from collections.abc import Mapping

import httpx
from mcp.server import MCPServer

from mcps.config import SectionConfig
from mcps.errors import ToolError
from mcps.http_client import request_json, warn_if_tls_disabled
from mcps.logging_setup import log_call
from mcps.responses import expect_dict, expect_items
from mcps.validation import validate_path_parameter

NAME = "mealie"
REQUIRED_KEYS = ("url", "api_key")

DEFAULT_RECIPE_LIMIT = 20
MAX_RECIPE_LIMIT = 100


def _apply_auth(client: httpx.Client, data: Mapping[str, str]) -> None:
    client.headers["Authorization"] = f"Bearer {data['api_key']}"


def _call(section: SectionConfig, method: str, path: str, **kwargs) -> object:
    return request_json(section, method, path, apply_auth=_apply_auth, **kwargs)


def _validate_pagination(limit: int, page: int) -> None:
    if not 1 <= limit <= MAX_RECIPE_LIMIT:
        raise ToolError(f"limit must be between 1 and {MAX_RECIPE_LIMIT}")
    if page < 1:
        raise ToolError("page must be >= 1")


def register(server: MCPServer, section: SectionConfig) -> None:
    warn_if_tls_disabled(section)

    @server.tool(name="mealie_list_recipes", description="List Mealie recipes.")
    @log_call("mealie_list_recipes", credentials=section.credential_values())
    def list_recipes(limit: int = DEFAULT_RECIPE_LIMIT, page: int = 1) -> list[dict]:
        """List one page of recipes, capped at `limit` entries (max 100)."""
        _validate_pagination(limit, page)
        data = _call(section, "GET", "/api/recipes", params={"perPage": limit, "page": page})
        return expect_items(data, label="Mealie")[:limit]

    @server.tool(name="mealie_get_recipe", description="Get one Mealie recipe by slug.")
    @log_call("mealie_get_recipe", credentials=section.credential_values())
    def get_recipe(slug: str) -> dict:
        """Fetch a recipe by slug (e.g. `chicken-tikka-masala`)."""
        validate_path_parameter(slug, "slug")
        return expect_dict(_call(section, "GET", f"/api/recipes/{slug}"), label="Mealie")

    @server.tool(name="mealie_search_recipes", description="Search Mealie recipes by query.")
    @log_call("mealie_search_recipes", credentials=section.credential_values())
    def search_recipes(
        query: str, limit: int = DEFAULT_RECIPE_LIMIT, page: int = 1
    ) -> list[dict]:
        """Search recipes by query, returning at most `limit` results (max 100)."""
        _validate_pagination(limit, page)
        data = _call(
            section,
            "GET",
            "/api/recipes",
            params={"search": query, "perPage": limit, "page": page},
        )
        return expect_items(data, label="Mealie")[:limit]
