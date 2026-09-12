"""Exercise public error boundaries through the MCP client."""

from pathlib import Path

import httpx
import pytest
from mcp import Client

from mcps.config import SectionConfig, ServerConfig
from mcps.logging_setup import configure_logging
from mcps.server import build_server


def _homeassistant_section(secret: str) -> SectionConfig:
    return SectionConfig(
        name="homeassistant",
        data={"url": "http://x", "token": secret},
        http_timeout=5.0,
        verify_tls=True,
    )


def _unexpected_failure(*_args: object, **_kwargs: object) -> None:
    raise ValueError("boom")


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


@pytest.mark.parametrize(
    ("secret", "failure", "arguments"),
    [
        ("failed", "connect", {"entity_id": "light.kitchen"}),
        ("invalid", "json", {"entity_id": "light.kitchen"}),
        ("401", "http", {"entity_id": "light.kitchen"}),
        ("unexpected", "shape", {"entity_id": "light.kitchen"}),
        ("invalid", "path", {"entity_id": "../bad"}),
    ],
)
async def test_generated_error_messages_do_not_reveal_credentials(
    *,
    secret: str,
    failure: str,
    arguments: dict,
    mock_http,
    tmp_log_file: Path,
) -> None:
    section = _homeassistant_section(secret)

    def handler(outbound: httpx.Request) -> httpx.Response:
        if failure == "connect":
            raise httpx.ConnectError("connection failed", request=outbound)
        if failure == "http":
            return httpx.Response(401, json={})
        if failure == "shape":
            return httpx.Response(200, json="not-a-container")
        return httpx.Response(200, content="invalid JSON")

    mock_http(handler)
    configure_logging(tmp_log_file, "INFO")
    config = ServerConfig(tmp_log_file, "INFO", 5.0, {"homeassistant": section})
    async with Client(build_server(config)) as client:
        result = await client.call_tool("homeassistant_get_state", arguments)

    assert result.is_error
    assert secret not in str(result)
    assert secret not in tmp_log_file.read_text()


async def test_unexpected_exception_is_generic_and_credential_free(
    mock_http,
    monkeypatch: pytest.MonkeyPatch,
    tmp_log_file: Path,
) -> None:
    secret = "failed"
    section = _homeassistant_section(secret)
    mock_http(lambda outbound: httpx.Response(200, json={}))
    monkeypatch.setattr("mcps.integrations.homeassistant.expect_dict", _unexpected_failure)
    configure_logging(tmp_log_file, "INFO")
    config = ServerConfig(tmp_log_file, "INFO", 5.0, {"homeassistant": section})
    async with Client(build_server(config)) as client:
        result = await client.call_tool(
            "homeassistant_get_state", {"entity_id": "light.kitchen"}
        )

    assert result.is_error
    assert secret not in str(result)
    assert "ValueError" not in str(result)
    assert "boom" not in str(result)
    assert secret not in tmp_log_file.read_text()
