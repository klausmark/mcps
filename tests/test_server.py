"""Tests for `mcps.server` and the integration registration layer."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from mcp import Client

from mcps.config import ConfigError, SectionConfig, ServerConfig
from mcps.logging_setup import configure_logging
from mcps.server import build_server


def _build(config: ServerConfig):
    configure_logging(None, "WARNING")
    return build_server(config)


async def _list_tool_names(server) -> list[str]:
    async with Client(server) as client:
        listed = await client.list_tools()
    return [tool.name for tool in listed.tools]


async def _call_tool(server, name: str, arguments: dict | None = None):
    async with Client(server) as client:
        return await client.call_tool(name, arguments or {})


def test_build_server_returns_mcpserver(server_config: ServerConfig) -> None:
    server = _build(server_config)
    assert server is not None


async def test_homeassistant_tools_registered(server_config: ServerConfig) -> None:
    server = _build(server_config)
    names = await _list_tool_names(server)
    assert "homeassistant_list_entities" in names
    assert "homeassistant_get_state" in names
    assert "homeassistant_call_service" in names


async def test_absent_sections_produce_no_tools() -> None:
    config = ServerConfig(log_file=None, log_level="WARNING", http_timeout=5.0, sections={})
    server = _build(config)
    names = await _list_tool_names(server)
    assert not any(name.startswith(("homeassistant_", "mealie_", "nirvana_")) for name in names)


def test_missing_required_key_raises_configerror() -> None:
    section = SectionConfig(
        name="homeassistant",
        data={"url": "http://x"},  # missing token
        http_timeout=5.0,
        verify_tls=True,
    )
    config = ServerConfig(
        log_file=None,
        log_level="WARNING",
        http_timeout=5.0,
        sections={"homeassistant": section},
    )
    with pytest.raises(ConfigError, match="missing required keys"):
        _build(config)


async def test_no_secret_or_log_event_tools_exposed(server_config: ServerConfig) -> None:
    """Hard invariant: no tool may expose secrets or be a logging tool."""
    server = _build(server_config)
    names = await _list_tool_names(server)
    for name in names:
        assert "secret" not in name.lower(), f"forbidden tool name: {name}"
        assert "log_event" not in name.lower(), f"forbidden tool name: {name}"


async def test_tool_result_never_contains_credential_value(
    server_config: ServerConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    """If the upstream echoes a credential in the body, it must not leak to the model."""
    secret = "ha-secret-token"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/states":
            return httpx.Response(
                200,
                json=[
                    {"entity_id": "light.kitchen", "note": f"token={secret}"},
                ],
            )
        return httpx.Response(404, json={"error": "not found"})

    mock_http(handler)
    server = _build(server_config)
    result = await _call_tool(server, "homeassistant_list_entities", {})
    text = str(result)
    assert secret not in text
    assert "<redacted>" in text


async def test_all_integrations_register_when_all_sections_present() -> None:
    ha = SectionConfig(
        name="homeassistant",
        data={"url": "http://ha", "token": "t"},
        http_timeout=5.0,
        verify_tls=True,
    )
    me = SectionConfig(
        name="mealie",
        data={"url": "http://me", "api_key": "k"},
        http_timeout=5.0,
        verify_tls=True,
    )
    ni = SectionConfig(
        name="nirvana",
        data={"url": "https://ni", "email": "u@x", "password": "p"},
        http_timeout=5.0,
        verify_tls=True,
    )
    config = ServerConfig(
        log_file=None,
        log_level="WARNING",
        http_timeout=5.0,
        sections={"homeassistant": ha, "mealie": me, "nirvana": ni},
    )
    server = _build(config)
    names = await _list_tool_names(server)
    expected = {
        "homeassistant_list_entities",
        "homeassistant_get_state",
        "homeassistant_call_service",
        "mealie_list_recipes",
        "mealie_get_recipe",
        "mealie_search_recipes",
        "nirvana_list_tasks",
        "nirvana_get_task",
        "nirvana_complete_task",
        "nirvana_add_task",
    }
    assert expected.issubset(set(names))
