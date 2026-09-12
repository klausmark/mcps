"""Home Assistant REST integration."""

from __future__ import annotations

from collections.abc import Mapping

import httpx
from mcp.server import MCPServer

from mcps.config import SectionConfig
from mcps.http_client import request_json, warn_if_tls_disabled
from mcps.logging_setup import log_call
from mcps.responses import expect_dict, expect_list
from mcps.validation import validate_path_parameter

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
        entities = expect_list(_call(section, "GET", "/api/states"), label="Home Assistant")
        if domain is None:
            return entities
        prefix = f"{domain.lower()}."
        return [
            entity
            for entity in entities
            if str(entity.get("entity_id", "")).startswith(prefix)
        ]

    @server.tool(
        name="homeassistant_get_state",
        description="Get a single entity state by id.",
    )
    @log_call("homeassistant_get_state")
    def get_state(entity_id: str) -> dict:
        """Get the state of one entity, e.g. `light.kitchen`."""
        validate_path_parameter(entity_id, "entity_id", pattern=r"[a-z0-9_]+\.[a-z0-9_]+")
        state = _call(section, "GET", f"/api/states/{entity_id}")
        return expect_dict(state, label="Home Assistant")

    @server.tool(
        name="homeassistant_call_service",
        description="Call a Home Assistant service.",
    )
    @log_call("homeassistant_call_service")
    def call_service(domain: str, service: str, data: dict | None = None) -> list[dict]:
        """Call `domain.service` with optional service data; returns the affected states."""
        validate_path_parameter(domain, "domain", pattern=r"[a-z0-9_]+")
        validate_path_parameter(service, "service", pattern=r"[a-z0-9_]+")
        affected = _call(section, "POST", f"/api/services/{domain}/{service}", json=data or {})
        return expect_list(affected, label="Home Assistant")
