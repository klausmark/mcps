"""Tests for `mcps.http_client`."""

from __future__ import annotations

from typing import Any

import httpx

from mcps.config import SectionConfig
from mcps.http_client import make_client, sanitize


def test_sanitize_replaces_credential_string() -> None:
    data = {"note": "the token is TOPSECRET and password is HIDDEN"}
    out = sanitize(data, ["TOPSECRET", "HIDDEN"])
    assert out == {"note": "the token is <redacted> and password is <redacted>"}


def test_sanitize_is_recursive() -> None:
    data = {"a": [{"b": "TOPSECRET"}, "TOPSECRET"], "c": ("x", "TOPSECRET")}
    out = sanitize(data, ["TOPSECRET"])
    assert out == {"a": [{"b": "<redacted>"}, "<redacted>"], "c": ("x", "<redacted>")}


def test_sanitize_no_credentials_is_noop() -> None:
    data = {"x": 1, "y": ["a", "b"]}
    assert sanitize(data, []) == data


def test_sanitize_empty_string_credentials_are_skipped() -> None:
    assert sanitize("hello", [""]) == "hello"


def test_make_client_sets_verify_false() -> None:
    section = SectionConfig(name="x", data={"url": "http://x"}, http_timeout=1.0, verify_tls=False)
    seen: dict[str, Any] = {}

    def fake_apply(client, _data):
        seen["auth"] = True

    client = make_client(section, base_url="http://x", apply_auth=fake_apply)
    try:
        # httpx stores verify on the transport; we can probe via _transport.
        assert isinstance(client._transport, httpx.HTTPTransport)
        assert client._transport._pool._ssl_context.verify_mode.name.startswith("CERT_NONE")
        assert seen.get("auth") is True
    finally:
        client.close()


def test_make_client_calls_apply_auth_with_data() -> None:
    section = SectionConfig(
        name="x",
        data={"url": "http://x", "token": "abc"},
        http_timeout=2.0,
        verify_tls=True,
    )

    received: dict = {}

    def apply_auth(client, data):
        received["data"] = dict(data)

    client = make_client(section, apply_auth=apply_auth)
    try:
        assert received["data"]["token"] == "abc"
    finally:
        client.close()


def test_make_client_without_apply_auth() -> None:
    section = SectionConfig(name="x", data={"url": "http://x"}, http_timeout=2.0, verify_tls=True)
    client = make_client(section)
    try:
        assert "Authorization" not in client.headers
    finally:
        client.close()
