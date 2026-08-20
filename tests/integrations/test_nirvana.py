"""Tests for the NirvanaHQ integration."""

from __future__ import annotations

import base64
import json
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
            sections={"nirvana": section},
        )
    )


async def _call(server, name: str, args: dict):
    async with Client(server) as client:
        return await client.call_tool(name, args)


def _expected_basic(email: str, password: str) -> str:
    raw = f"{email}:{password}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


async def test_list_tasks_sends_basic_auth(
    nirvana_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"items": [{"id": "t1", "name": "Buy milk"}]})

    mock_http(handler)
    server = _server(nirvana_section)
    await _call(server, "nirvana_list_tasks", {})
    assert captured[0].headers.get("authorization") == _expected_basic(
        "user@example.com", "nirvana-secret-pw"
    )


async def test_complete_task_posts_to_completed_endpoint(
    nirvana_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"id": "t1", "status": "completed"})

    mock_http(handler)
    server = _server(nirvana_section)
    await _call(server, "nirvana_complete_task", {"id": "t1"})
    assert captured[0].method == "POST"
    assert captured[0].url.path == "/2.0/tasks/t1/completed"


async def test_add_task_posts_payload(
    nirvana_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        body = json.loads(request.content)
        assert body == {"name": "Buy milk", "bucket": "Today"}
        return httpx.Response(201, json={"id": "t2", "name": body["name"]})

    mock_http(handler)
    server = _server(nirvana_section)
    await _call(server, "nirvana_add_task", {"name": "Buy milk", "bucket": "Today"})
    assert captured[0].url.path == "/2.0/tasks"


async def test_credential_redaction(
    nirvana_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [{"id": "t1", "note": "leaked nirvana-secret-pw here"}]},
        )

    mock_http(handler)
    server = _server(nirvana_section)
    result = await _call(server, "nirvana_list_tasks", {})
    text = str(result)
    assert "nirvana-secret-pw" not in text
    assert "<redacted>" in text
