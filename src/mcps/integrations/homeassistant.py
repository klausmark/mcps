"""Home Assistant REST integration."""

from __future__ import annotations

from collections.abc import Mapping

import httpx
from mcp.server import MCPServer

from mcps.config import SectionConfig
from mcps.http_client import request_json, warn_if_tls_disabled
from mcps.logging_setup import log_call

NAME = "homeassistant"
REQUIRED_KEYS = ("url", "token")


def _apply_auth(client: httpx.Client, data: Mapping[str, str]) -> None:
    client.headers["Authorization"] = f"Bearer {data['token']}"


def _call(section: SectionConfig, method: str, path: str, **kwargs) -> object:
    return request_json(section, method, path, apply_auth=_apply_auth, **kwargs)


def register(server: MCPServer, section: SectionConfig) -> None:
    warn_if_tls_disabled(section)

    @server.tool(
        name="homeassistant_list_entities",
        description="List Home Assistant entity states.",
    )
    @log_call("homeassistant_list_entities")
    def list_entities(domain: str | None = None) -> list[dict]:
        """List entity states, optionally filtered by domain (e.g. `light`, `switch`)."""
        data = _call(section, "GET", "/api/states")
        if not isinstance(data, list):
            return []
        if domain is None:
            return data
        prefix = f"{domain.lower()}."
        return [
            entity
            for entity in data
            if isinstance(entity, dict) and str(entity.get("entity_id", "")).startswith(prefix)
        ]

    @server.tool(
        name="homeassistant_get_state",
        description="Get a single entity state by id.",
    )
    @log_call("homeassistant_get_state")
    def get_state(entity_id: str) -> dict:
        """Get the state of one entity, e.g. `light.kitchen`."""
        return _call(section, "GET", f"/api/states/{entity_id}")

    @server.tool(
        name="homeassistant_call_service",
        description="Call a Home Assistant service.",
    )
    @log_call("homeassistant_call_service")
    def call_service(domain: str, service: str, data: dict | None = None) -> list[dict]:
        """Call `domain.service` with optional service data; returns the affected states."""
        return _call(section, "POST", f"/api/services/{domain}/{service}", json=data or {})
