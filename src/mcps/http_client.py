"""Shared httpx client factory and response sanitization helpers."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from mcps.config import SectionConfig
from mcps.errors import ToolError
from mcps.logging_setup import get_logger

AuthApplier = Callable[[httpx.Client, Mapping[str, str]], None]

# Upstream responses larger than this are refused rather than truncated.
MAX_RESPONSE_BYTES = 1024 * 1024


def _limit_label(max_bytes: int) -> str:
    return f"{max_bytes // (1024 * 1024)} MiB"


def read_bounded_body(
    response: httpx.Response, *, label: str, max_bytes: int = MAX_RESPONSE_BYTES
) -> bytes:
    """Stream a response body, refusing redirects, errors, and oversized payloads.

    `Content-Length` is checked first for an early rejection, but the actual
    decoded bytes are counted while streaming, so a missing or misleading length
    still cannot exceed the limit.
    """
    if response.is_redirect:
        raise ToolError(f"{label} returned a redirect, which was refused")
    if response.is_error:
        raise ToolError(f"{label} returned HTTP {response.status_code}")

    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            raise ToolError(f"{label} returned an invalid Content-Length") from None
        if declared > max_bytes:
            raise ToolError(f"{label} response exceeds the {_limit_label(max_bytes)} limit")

    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ToolError(f"{label} response exceeds the {_limit_label(max_bytes)} limit")
    return bytes(body)


def make_client(
    section: SectionConfig,
    *,
    base_url: str | None = None,
    apply_auth: AuthApplier | None = None,
) -> httpx.Client:
    """Build an httpx.Client with TLS/timeout from config and optional auth applier.

    The caller owns the returned client and is responsible for closing it (use as
    a context manager or call `.close()`).
    """
    kwargs: dict[str, Any] = {
        "verify": section.verify_tls,
        "timeout": section.http_timeout,
    }
    if base_url is not None:
        kwargs["base_url"] = base_url
    client = httpx.Client(**kwargs)
    try:
        if apply_auth is not None:
            apply_auth(client, section.data)
    except Exception:
        client.close()
        raise
    return client


def request_json(
    section: SectionConfig,
    method: str,
    path: str,
    *,
    apply_auth: AuthApplier,
    **kwargs: Any,
) -> Any:
    """Request JSON without exposing transport, header, or parsing error details."""
    try:
        with (
            make_client(section, base_url=section.data["url"], apply_auth=apply_auth) as client,
            client.stream(method, path, **kwargs) as response,
        ):
            body = read_bounded_body(response, label=section.name)
    except ToolError:
        raise
    except (httpx.HTTPError, ValueError):
        raise ToolError(f"{section.name} request failed") from None
    if not body:
        return None
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ToolError(f"{section.name} returned invalid JSON") from None
    return sanitize(data, section.credential_values())


def sanitize(data: Any, credentials: list[str]) -> Any:
    """Recursively replace any occurrence of a credential string with `<redacted>`.

    Defense in depth: even if an upstream server echoes a token in its JSON
    body, the model never sees the value.
    """
    values = sorted({value for value in credentials if value}, key=len, reverse=True)
    if not values:
        return data
    # Match once, longest first: overlapping credentials must not reveal suffixes
    # or cause a later replacement to modify an earlier redaction marker.
    pattern = re.compile("|".join(re.escape(value) for value in values))

    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {redact(key): redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(redact(item) for item in value)
        if isinstance(value, str):
            return pattern.sub("<redacted>", value)
        if isinstance(value, (int, float)) and pattern.search(str(value)):
            return "<redacted>"
        return value

    return redact(data)


def warn_if_tls_disabled(section: SectionConfig) -> None:
    """Caller invokes this at integration registration time when verify_tls is off."""
    if not section.verify_tls:
        get_logger().debug(
            "integration %s verify_tls=false: TLS certificate verification is disabled",
            section.name,
        )
