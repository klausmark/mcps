"""Tests for the Mealie integration."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
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
            sections={"mealie": section},
        )
    )


async def _call(server, name: str, args: dict):
    async with Client(server) as client:
        return await client.call_tool(name, args)


async def test_list_recipes_sends_bearer_auth(
    mealie_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    tmp_log_file: Path,
) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"items": [{"slug": "tikka", "name": "Tikka"}]})

    mock_http(handler)
    server = _server(mealie_section, tmp_log_file)
    await _call(server, "mealie_list_recipes", {"limit": 5})
    assert captured[0].headers.get("authorization") == "Bearer mealie-secret-key"
    assert captured[0].url.params["perPage"] == "5"


async def test_get_recipe_targets_specific_slug(
    mealie_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    tmp_log_file: Path,
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"slug": "tikka", "name": "Tikka"})

    mock_http(handler)
    server = _server(mealie_section, tmp_log_file)
    await _call(server, "mealie_get_recipe", {"slug": "tikka"})
    assert seen == ["/api/recipes/tikka"]


async def test_search_recipes_passes_query(
    mealie_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    tmp_log_file: Path,
) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"items": [{"slug": "tikka"}]})

    mock_http(handler)
    server = _server(mealie_section, tmp_log_file)
    await _call(server, "mealie_search_recipes", {"query": "tikka"})
    assert captured[0].url.params["search"] == "tikka"


async def test_credential_redaction_in_response(
    mealie_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    tmp_log_file: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [{"slug": "tikka", "note": "leaked mealie-secret-key here"}]},
        )

    mock_http(handler)
    server = _server(mealie_section, tmp_log_file)
    result = await _call(server, "mealie_search_recipes", {"query": "tikka"})
    text = str(result)
    assert "mealie-secret-key" not in text
    assert "<redacted>" in text
