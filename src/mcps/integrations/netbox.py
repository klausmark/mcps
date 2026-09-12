"""Read-only NetBox REST integration."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

import httpx
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from mcps.config import ConfigError, SectionConfig
from mcps.errors import ToolError
from mcps.http_client import make_client, sanitize, warn_if_tls_disabled
from mcps.logging_setup import log_call

NAME = "netbox"
REQUIRED_KEYS = ("url", "token")

MAX_PATH_LENGTH = 512
MAX_RESPONSE_BYTES = 1024 * 1024
API_PATH = re.compile(r"^/api/[A-Za-z0-9._~/-]*$")

QueryValue = str | int | bool | list[str]
JsonValue = dict[str, Any] | list[Any]


class NetBoxError(ToolError):
    """Safe error that contains no upstream response or credential details."""


def _validate_section(section: SectionConfig) -> None:
    token = section.data["token"]
    if not token or any(character.isspace() for character in token):
        raise ConfigError("section [netbox] token must be non-empty and contain no whitespace")

    value = section.data["url"]
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL:
        raise ConfigError("section [netbox] url must be a valid HTTPS origin") from None

    if (
        url.scheme != "https"
        or not url.host
        or url.userinfo
        or url.path not in ("", "/")
        or url.query
        or url.fragment
    ):
        raise ConfigError(
            "section [netbox] url must be an HTTPS origin without credentials, "
            "path, query, or fragment"
        )


def _validate_api_path(path: str) -> str:
    if len(path) > MAX_PATH_LENGTH or not API_PATH.fullmatch(path):
        raise NetBoxError("path must be an unescaped absolute path below /api/")
    if any(segment in (".", "..") for segment in path.split("/")):
        raise NetBoxError("path traversal is not allowed")
    if path == "/api/users/tokens" or path.startswith("/api/users/tokens/"):
        raise NetBoxError("the NetBox token administration endpoint is not available")
    return path


def _apply_auth(client: httpx.Client, data: Mapping[str, str]) -> None:
    token = data["token"]
    scheme = "Bearer" if token.startswith("nbt_") else "Token"
    client.headers.update(
        {
            "Accept": "application/json",
            "Authorization": f"{scheme} {token}",
        }
    )


def _read_response_body(response: httpx.Response) -> bytes:
    if response.is_redirect:
        raise NetBoxError("NetBox returned a redirect, which was refused")
    if response.is_error:
        raise NetBoxError(f"NetBox returned HTTP {response.status_code}")

    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) > MAX_RESPONSE_BYTES:
        raise NetBoxError("NetBox response exceeds the 1 MiB limit")

    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > MAX_RESPONSE_BYTES:
            raise NetBoxError("NetBox response exceeds the 1 MiB limit")
    return bytes(body)


def _parse_json_object_or_array(body: bytes) -> JsonValue:
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise NetBoxError("NetBox returned invalid JSON") from None
    if not isinstance(data, (dict, list)):
        raise NetBoxError("NetBox returned an unsupported JSON value")
    return data


def _get(
    section: SectionConfig,
    path: str,
    query: Mapping[str, QueryValue] | None = None,
) -> JsonValue:
    safe_path = _validate_api_path(path)
    try:
        with (
            make_client(section, base_url=section.data["url"], apply_auth=_apply_auth) as client,
            client.stream("GET", safe_path, params=query) as response,
        ):
            body = _read_response_body(response)
    except NetBoxError:
        raise
    except (httpx.HTTPError, ValueError):
        raise NetBoxError("NetBox request failed") from None

    data = _parse_json_object_or_array(body)
    return sanitize(data, section.credential_values())


def register(server: MCPServer, section: SectionConfig) -> None:
    _validate_section(section)
    warn_if_tls_disabled(section)

    @server.tool(
        name="netbox_get",
        description="Perform a read-only GET request against the configured NetBox REST API.",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
    )
    @log_call("netbox_get")
    def netbox_get(path: str, query: dict[str, QueryValue] | None = None) -> JsonValue:
        """Read an unescaped NetBox REST API path beginning with `/api/`."""
        return _get(section, path, query)
