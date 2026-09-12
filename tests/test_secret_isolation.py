"""Every tool must redact all configured credentials and the actual Basic token."""

import httpx
import pytest
from mcp import Client

from mcps.config import ServerConfig
from mcps.integrations.nirvana import basic_token
from mcps.server import build_server


@pytest.mark.parametrize(
    ("tool", "arguments", "shape"),
    [
        ("homeassistant_list_entities", {}, "list"),
        ("homeassistant_get_state", {"entity_id": "light.kitchen"}, "dict"),
        ("homeassistant_call_service", {"domain": "light", "service": "turn_on"}, "list"),
        ("mealie_list_recipes", {}, "items"),
        ("mealie_get_recipe", {"slug": "soup"}, "dict"),
        ("mealie_search_recipes", {"query": "soup"}, "items"),
        ("netbox_get", {"path": "/api/status/"}, "dict"),
        ("nirvana_list_tasks", {}, "items"),
        ("nirvana_get_task", {"id": "task-1"}, "dict"),
        ("nirvana_complete_task", {"id": "task-1"}, "dict"),
        ("nirvana_add_task", {"name": "test task"}, "dict"),
    ],
)
async def test_all_tools_redact_all_credentials(
    *, tool, arguments, shape, request, mock_http, tmp_log_file
) -> None:
    sections = {
        name: request.getfixturevalue(f"{name}_section")
        for name in ("homeassistant", "mealie", "netbox", "nirvana")
    }
    credentials = [value for section in sections.values() for value in section.credential_values()]
    credentials.append(basic_token(sections["nirvana"].data))
    record = {f"echo-{value}": {"note": f"Authorization: {value}"} for value in credentials}
    response = record if shape == "dict" else [record]
    if shape == "items":
        response = {"items": [record]}
    mock_http(lambda request: httpx.Response(200, json=response))
    config = ServerConfig(tmp_log_file, "WARNING", 5.0, sections)
    async with Client(build_server(config)) as client:
        result = await client.call_tool(tool, arguments)
    assert not result.is_error
    assert "<redacted>" in str(result)
    for credential in credentials:
        assert credential not in str(result)
