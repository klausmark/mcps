"""Reject endpoint-changing tool arguments before making an HTTP request."""

import httpx
import pytest
from mcp import Client

from mcps.config import ServerConfig
from mcps.server import build_server


@pytest.mark.parametrize(
    ("integration", "tool", "arguments", "parameter"),
    [
        ("homeassistant", "homeassistant_get_state", {}, "entity_id"),
        ("homeassistant", "homeassistant_call_service", {"service": "turn_on"}, "domain"),
        ("homeassistant", "homeassistant_call_service", {"domain": "light"}, "service"),
        ("mealie", "mealie_get_recipe", {}, "slug"),
        ("nirvana", "nirvana_get_task", {}, "id"),
        ("nirvana", "nirvana_complete_task", {}, "id"),
    ],
)
@pytest.mark.parametrize(
    "value",
    ["", ".", "..", "../users", "a/b", "a\\b", "a?x=1", "a#fragment", "%2e%2e",
     "a%2fb", "a%252fb", "a\n", "a" * 513],
)
async def test_invalid_path_parameters_never_reach_upstream(
    *, integration, tool, arguments, parameter, value, request, mock_http, tmp_log_file
) -> None:
    seen = []

    def handler(outbound):
        seen.append(outbound)
        return httpx.Response(200, json={})

    mock_http(handler)
    section = request.getfixturevalue(f"{integration}_section")
    config = ServerConfig(tmp_log_file, "WARNING", 5.0, {integration: section})
    async with Client(build_server(config)) as client:
        result = await client.call_tool(tool, {**arguments, parameter: value})
    assert result.is_error
    assert "identifier format" in str(result)
    assert not seen


async def test_unicode_recipe_slug_stays_in_one_path_segment(
    mealie_section, mock_http, tmp_log_file
) -> None:
    seen = []

    def handler(outbound):
        seen.append(outbound.url.path)
        return httpx.Response(200, json={"slug": "rød-grød"})

    mock_http(handler)
    config = ServerConfig(tmp_log_file, "WARNING", 5.0, {"mealie": mealie_section})
    async with Client(build_server(config)) as client:
        result = await client.call_tool("mealie_get_recipe", {"slug": "rød-grød"})
    assert not result.is_error
    assert seen == ["/api/recipes/rød-grød"]
