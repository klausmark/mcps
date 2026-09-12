"""Tests for the read-only NetBox integration."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from mcp import Client

from mcps.config import ConfigError, SectionConfig, ServerConfig
from mcps.integrations.netbox import MAX_RESPONSE_BYTES, NetBoxError, _validate_api_path
from mcps.logging_setup import configure_logging, get_logger
from mcps.server import build_server


def _server(section: SectionConfig, tmp_log_file: Path, log_level: str = "WARNING"):
    configure_logging(tmp_log_file, log_level)
    return build_server(
        ServerConfig(
            log_file=tmp_log_file,
            log_level=log_level,
            http_timeout=5.0,
            sections={"netbox": section},
        )
    )


async def _call(server, args: dict):
    async with Client(server) as client:
        return await client.call_tool("netbox_get", args)


async def test_tool_is_marked_read_only(
    netbox_section: SectionConfig,
    tmp_log_file: Path,
) -> None:
    async with Client(_server(netbox_section, tmp_log_file)) as client:
        tools = await client.list_tools()

    tool = next(tool for tool in tools.tools if tool.name == "netbox_get")
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.open_world_hint is True


async def test_get_uses_bearer_auth_and_forwards_query(
    netbox_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    tmp_log_file: Path,
) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"count": 1, "results": [{"id": 7}]})

    mock_http(handler)
    result = await _call(
        _server(netbox_section, tmp_log_file),
        {"path": "/api/dcim/devices/", "query": {"limit": 1, "status": ["active"]}},
    )

    assert result.structured_content["result"]["results"][0]["id"] == 7
    assert len(captured) == 1
    assert captured[0].method == "GET"
    assert captured[0].url.path == "/api/dcim/devices/"
    assert captured[0].url.params["limit"] == "1"
    assert captured[0].url.params.get_list("status") == ["active"]
    assert captured[0].headers["Authorization"] == "Bearer nbt_key.netbox-secret"
    assert captured[0].headers["Accept"] == "application/json"


async def test_legacy_token_uses_token_auth(
    netbox_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    tmp_log_file: Path,
) -> None:
    section = SectionConfig(
        name="netbox",
        data={"url": netbox_section.data["url"], "token": "legacy-secret"},
        http_timeout=5.0,
        verify_tls=True,
    )
    authorization: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        authorization.append(request.headers["Authorization"])
        return httpx.Response(200, json=[])

    mock_http(handler)
    await _call(_server(section, tmp_log_file), {"path": "/api/status/"})
    assert authorization == ["Token legacy-secret"]


@pytest.mark.parametrize(
    "path",
    [
        "https://attacker.example/api/dcim/devices/",
        "//attacker.example/api/dcim/devices/",
        "/login/",
        "/api/../login/",
        "/api/%2e%2e/login/",
        "/api/dcim/devices/?limit=1",
        "/api/dcim\\devices/",
        "/api/users/tokens/",
        "/api/users/tokens/1/",
        "/api//users/tokens/",
        "/api/users//tokens/",
        "/api/users/tokens.json",
        "/api/" + "a" * 508,
    ],
)
def test_validate_api_path_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(NetBoxError):
        _validate_api_path(path)


async def test_redirect_is_refused(
    netbox_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    tmp_log_file: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://attacker.example/collect"})

    mock_http(handler)
    result = await _call(_server(netbox_section, tmp_log_file), {"path": "/api/status/"})
    assert result.is_error is True
    assert "redirect" in str(result).lower()


async def test_response_size_is_limited(
    netbox_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    tmp_log_file: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))

    mock_http(handler)
    result = await _call(_server(netbox_section, tmp_log_file), {"path": "/api/status/"})
    assert result.is_error is True
    assert "1 MiB" in str(result)


@pytest.mark.parametrize("body", [b"not-json", b"42"])
async def test_invalid_or_scalar_json_is_rejected(
    body: bytes,
    netbox_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    tmp_log_file: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    mock_http(handler)
    result = await _call(_server(netbox_section, tmp_log_file), {"path": "/api/status/"})
    assert result.is_error is True


async def test_response_credentials_are_redacted_in_values_and_keys(
    netbox_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    tmp_log_file: Path,
) -> None:
    token = netbox_section.data["token"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={f"key-{token}": {"value": f"Bearer {token}"}})

    mock_http(handler)
    result = await _call(_server(netbox_section, tmp_log_file), {"path": "/api/status/"})
    assert token not in str(result)
    assert "<redacted>" in str(result)


async def test_upstream_failure_does_not_expose_token_in_result_or_log(
    netbox_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    tmp_log_file: Path,
) -> None:
    token = netbox_section.data["token"]

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed with {token}", request=request)

    mock_http(handler)
    result = await _call(
        _server(netbox_section, tmp_log_file, "INFO"),
        {"path": "/api/status/"},
    )
    for handler_instance in get_logger().handlers:
        handler_instance.flush()

    assert result.is_error is True
    assert token not in str(result)
    assert token not in tmp_log_file.read_text()


@pytest.mark.parametrize(
    ("url", "token"),
    [
        ("http://netbox.example", "token"),
        ("https://user:pass@netbox.example", "token"),
        ("https://netbox.example/api", "token"),
        ("https://netbox.example?x=1", "token"),
        ("https://netbox.example", ""),
        ("https://netbox.example", "has whitespace"),
    ],
)
def test_invalid_section_is_rejected_at_startup(
    url: str,
    token: str,
    tmp_log_file: Path,
) -> None:
    section = SectionConfig(
        name="netbox",
        data={"url": url, "token": token},
        http_timeout=5.0,
        verify_tls=True,
    )
    with pytest.raises(ConfigError, match=r"section \[netbox\]"):
        _server(section, tmp_log_file)
