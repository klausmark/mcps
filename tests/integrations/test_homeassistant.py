"""Tests for the Home Assistant integration."""

from __future__ import annotations

from collections.abc import Callable

import httpx
from mcp import Client

from mcps.config import SectionConfig, ServerConfig
from mcps.logging_setup import configure_logging
from mcps.server import build_server


def _server(section: SectionConfig):
    configure_logging(None, "WARNING")
    return build_server(
        ServerConfig(
            log_file=None,
            log_level="WARNING",
            http_timeout=5.0,
            sections={"homeassistant": section},
        )
    )


async def _call(server, name: str, args: dict):
    async with Client(server) as client:
        return await client.call_tool(name, args)


async def test_list_entities_sends_bearer_auth(
    homeassistant_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[{"entity_id": "light.kitchen", "state": "on"}])

    mock_http(handler)
    server = _server(homeassistant_section)
    await _call(server, "homeassistant_list_entities", {})
    assert captured, "expected at least one request"
    assert captured[0].headers.get("authorization") == "Bearer ha-secret-token"
    assert str(captured[0].url).startswith("http://hass.local:8123/api/states")


async def test_list_entities_filters_by_domain(
    homeassistant_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"entity_id": "light.kitchen", "state": "on"},
                {"entity_id": "switch.kettle", "state": "off"},
            ],
        )

    mock_http(handler)
    server = _server(homeassistant_section)
    result = await _call(server, "homeassistant_list_entities", {"domain": "light"})
    assert len(result.structured_content["result"]) == 1
    assert result.structured_content["result"][0]["entity_id"] == "light.kitchen"


async def test_get_state_targets_specific_entity(
    homeassistant_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"entity_id": "light.kitchen", "state": "on"})

    mock_http(handler)
    server = _server(homeassistant_section)
    await _call(server, "homeassistant_get_state", {"entity_id": "light.kitchen"})
    assert seen == ["/api/states/light.kitchen"]


async def test_call_service_posts_to_service_endpoint(
    homeassistant_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[{"entity_id": "light.kitchen", "state": "on"}])

    mock_http(handler)
    server = _server(homeassistant_section)
    await _call(
        server,
        "homeassistant_call_service",
        {"domain": "light", "service": "turn_on", "data": {"entity_id": "light.kitchen"}},
    )
    assert captured[0].method == "POST"
    assert captured[0].url.path == "/api/services/light/turn_on"


async def test_upstream_error_does_not_leak_credential(
    homeassistant_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Even if an error body contains the token, we must not echo it to the model.
        return httpx.Response(
            502,
            text="upstream failed with token=ha-secret-token",
        )

    mock_http(handler)
    server = _server(homeassistant_section)
    result = await _call(server, "homeassistant_get_state", {"entity_id": "light.kitchen"})
    assert result.is_error is True
    text = "".join(block.text for block in result.content if hasattr(block, "text"))
    assert "ha-secret-token" not in text
