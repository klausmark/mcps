"""Exercise public error boundaries through the MCP client."""

from pathlib import Path

import httpx
import pytest
from mcp import Client

from mcps.config import ServerConfig
from mcps.logging_setup import configure_logging
from mcps.server import build_server


@pytest.mark.parametrize(
    ("integration", "tool", "arguments"),
    [
        ("homeassistant", "homeassistant_get_state", {"entity_id": "light.kitchen"}),
        ("mealie", "mealie_get_recipe", {"slug": "soup"}),
        ("nirvana", "nirvana_get_task", {"id": "task-1"}),
        ("netbox", "netbox_get", {"path": "/api/status/"}),
    ],
)
@pytest.mark.parametrize("failure", ["connect", "header", "client", "json", "redirect"])
async def test_errors_do_not_expose_credentials(
    *,
    integration, tool, arguments, failure, request, mock_http, monkeypatch, tmp_log_file: Path
) -> None:
    section = request.getfixturevalue(f"{integration}_section")
    secret = section.credential_values()[-1]

    def fail_client(*args, **kwargs):
        raise ValueError(f"client failed with {secret}")

    def handler(outbound):
        if failure == "connect":
            raise httpx.ConnectError(f"connection failed with {secret}", request=outbound)
        if failure == "header":
            raise httpx.LocalProtocolError(f"Illegal header value: {secret}", request=outbound)
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": f"https://example.org/{secret}"})
        return httpx.Response(200, content=f"invalid JSON containing {secret}")

    if failure == "client":
        monkeypatch.setattr("mcps.http_client.httpx.Client", fail_client)
    else:
        mock_http(handler)
    configure_logging(tmp_log_file, "INFO")
    config = ServerConfig(tmp_log_file, "INFO", 5.0, {integration: section})
    async with Client(build_server(config)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.is_error
    assert secret not in str(result)
    assert secret not in tmp_log_file.read_text()
