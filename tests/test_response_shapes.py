"""Unexpected upstream shapes must raise clear errors, not silent empty results."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from mcp import Client

from mcps.config import SectionConfig, ServerConfig
from mcps.logging_setup import configure_logging
from mcps.server import build_server


def _server(section: SectionConfig, tmp_log_file: Path):
    configure_logging(tmp_log_file, "WARNING")
    return build_server(
        ServerConfig(
            log_file=tmp_log_file,
            log_level="WARNING",
            http_timeout=5.0,
            sections={section.name: section},
        )
    )


async def _call(server, tool: str, args: dict):
    async with Client(server) as client:
        return await client.call_tool(tool, args)


def _install(
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    response: httpx.Response | Callable[[httpx.Request], httpx.Response],
) -> list[httpx.Request]:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return response(request) if callable(response) else response

    mock_http(handler)
    return captured


@pytest.mark.parametrize(
    ("section_fixture", "tool", "args", "response"),
    [
        ("homeassistant_section", "homeassistant_list_entities", {}, httpx.Response(200, json={})),
        (
            "homeassistant_section",
            "homeassistant_list_entities",
            {},
            httpx.Response(200, json=[1, 2]),
        ),
        (
            "homeassistant_section",
            "homeassistant_get_state",
            {"entity_id": "light.kitchen"},
            httpx.Response(200, json=[]),
        ),
        (
            "homeassistant_section",
            "homeassistant_call_service",
            {"domain": "light", "service": "turn_on"},
            httpx.Response(200, json={}),
        ),
        ("mealie_section", "mealie_get_recipe", {"slug": "soup"}, httpx.Response(200, json=42)),
        (
            "mealie_section",
            "mealie_list_recipes",
            {},
            httpx.Response(200, json={"items": "nope"}),
        ),
        (
            "nirvana_section",
            "nirvana_list_tasks",
            {},
            httpx.Response(200, json={"items": {}}),
        ),
        (
            "nirvana_section",
            "nirvana_get_task",
            {"id": "t1"},
            httpx.Response(200, json=[]),
        ),
        (
            "homeassistant_section",
            "homeassistant_list_entities",
            {},
            httpx.Response(204),
        ),
    ],
)
async def test_unexpected_shape_is_an_error(
    *,
    request,
    mock_http,
    tmp_log_file: Path,
    section_fixture,
    tool,
    args,
    response,
) -> None:
    section = request.getfixturevalue(section_fixture)
    _install(mock_http, response)
    result = await _call(_server(section, tmp_log_file), tool, args)
    assert result.is_error
    assert "unexpected" in str(result)


async def test_empty_list_is_a_success(
    homeassistant_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    tmp_log_file: Path,
) -> None:
    _install(mock_http, httpx.Response(200, json=[]))
    result = await _call(_server(homeassistant_section, tmp_log_file), "homeassistant_list_entities", {})
    assert not result.is_error
    assert result.structured_content["result"] == []


async def test_mealie_list_forwards_pagination(
    mealie_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    tmp_log_file: Path,
) -> None:
    captured = _install(mock_http, httpx.Response(200, json={"items": []}))
    await _call(_server(mealie_section, tmp_log_file), "mealie_list_recipes", {"limit": 5, "page": 2})
    assert captured[0].url.params["perPage"] == "5"
    assert captured[0].url.params["page"] == "2"


async def test_mealie_search_forwards_pagination(
    mealie_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    tmp_log_file: Path,
) -> None:
    captured = _install(mock_http, httpx.Response(200, json={"items": []}))
    await _call(
        _server(mealie_section, tmp_log_file),
        "mealie_search_recipes",
        {"query": "soup", "limit": 7, "page": 3},
    )
    assert captured[0].url.params["search"] == "soup"
    assert captured[0].url.params["perPage"] == "7"
    assert captured[0].url.params["page"] == "3"


async def test_mealie_limit_is_enforced_locally(
    mealie_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    tmp_log_file: Path,
) -> None:
    items = [{"slug": f"r{i}"} for i in range(50)]
    _install(mock_http, httpx.Response(200, json={"items": items}))
    result = await _call(_server(mealie_section, tmp_log_file), "mealie_list_recipes", {"limit": 3})
    assert len(result.structured_content["result"]) == 3


@pytest.mark.parametrize(
    "args",
    [{"limit": 0}, {"limit": 101}, {"page": 0}, {"page": -1}],
)
async def test_mealie_invalid_bounds_are_rejected_without_request(
    mealie_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    tmp_log_file: Path,
    args: dict,
) -> None:
    captured = _install(mock_http, httpx.Response(200, json={"items": []}))
    result = await _call(_server(mealie_section, tmp_log_file), "mealie_list_recipes", args)
    assert result.is_error
    assert not captured
