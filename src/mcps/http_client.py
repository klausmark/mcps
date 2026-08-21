"""Shared httpx client factory and response sanitization helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import httpx

from mcps.config import SectionConfig
from mcps.logging_setup import get_logger

AuthApplier = Callable[[httpx.Client, Mapping[str, str]], None]


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
    if apply_auth is not None:
        apply_auth(client, section.data)
    return client


def sanitize(data: Any, credentials: list[str]) -> Any:
    """Recursively replace any occurrence of a credential string with `<redacted>`.

    Defense in depth: even if an upstream server echoes a token in its JSON
    body, the model never sees the value.
    """
    if not credentials:
        return data
    if isinstance(data, dict):
        return {
            sanitize(key, credentials): sanitize(value, credentials)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [sanitize(item, credentials) for item in data]
    if isinstance(data, tuple):
        return tuple(sanitize(item, credentials) for item in data)
    if isinstance(data, str):
        out = data
        for cred in credentials:
            if cred and cred in out:
                out = out.replace(cred, "<redacted>")
        return out
    return data


def warn_if_tls_disabled(section: SectionConfig) -> None:
    """Caller invokes this at integration registration time when verify_tls is off."""
    if not section.verify_tls:
        get_logger().warning(
            "integration %s verify_tls=false: TLS certificate verification is disabled",
            section.name,
        )
