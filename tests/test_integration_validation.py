"""Startup validation of integration sections before tools are registered."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcps.config import ConfigError, SectionConfig, ServerConfig
from mcps.server import build_server


def _section(name: str, data: dict[str, str]) -> SectionConfig:
    return SectionConfig(name=name, data=data, http_timeout=5.0, verify_tls=True)


def _build(tmp_log_file: Path, sections: dict[str, SectionConfig]):
    config = ServerConfig(
        log_file=tmp_log_file,
        log_level="WARNING",
        http_timeout=5.0,
        sections=sections,
    )
    return build_server(config)


def test_unknown_section_is_rejected(tmp_log_file: Path) -> None:
    with pytest.raises(ConfigError, match="unknown integration section"):
        _build(tmp_log_file, {"home_assistant": _section("home_assistant", {})})


def test_server_without_integrations_builds(tmp_log_file: Path) -> None:
    assert _build(tmp_log_file, {}) is not None


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("homeassistant", {"url": "http://x", "token": "   "}),
        ("homeassistant", {"url": "", "token": "y"}),
        ("mealie", {"url": "http://x", "api_key": ""}),
        ("nirvana", {"url": "https://x", "email": "u@x", "password": ""}),
        ("netbox", {"url": "https://netbox.example", "token": ""}),
    ],
)
def test_empty_required_values_are_rejected(
    tmp_log_file: Path, name: str, data: dict[str, str]
) -> None:
    with pytest.raises(ConfigError, match="must not be empty"):
        _build(tmp_log_file, {name: _section(name, data)})


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not-a-url",
        "ftp://example.com",
        "https://user:pass@example.com",
        "https://example.com?x=1",
        "https://example.com#frag",
        "https://example.com/proxy",
        "https://example.com/proxy/",
        "https://example.com/api",
    ],
)
def test_invalid_urls_are_rejected(tmp_log_file: Path, url: str) -> None:
    with pytest.raises(ConfigError, match="url"):
        _build(
            tmp_log_file,
            {"homeassistant": _section("homeassistant", {"url": url, "token": "y"})},
        )


@pytest.mark.parametrize(
    "url",
    ["https://example.com", "https://example.com/", "http://localhost:8123"],
)
def test_origin_url_is_accepted(tmp_log_file: Path, url: str) -> None:
    assert (
        _build(
            tmp_log_file,
            {"homeassistant": _section("homeassistant", {"url": url, "token": "y"})},
        )
        is not None
    )


def test_local_http_url_is_accepted(tmp_log_file: Path) -> None:
    assert (
        _build(
            tmp_log_file,
            {"homeassistant": _section("homeassistant", {"url": "http://hass:8123", "token": "y"})},
        )
        is not None
    )


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("homeassistant", {"url": "https://example.com/proxy", "token": "y"}),
        ("mealie", {"url": "https://example.com/proxy", "api_key": "y"}),
        ("nirvana", {"url": "https://example.com/proxy", "email": "u@x", "password": "y"}),
        ("netbox", {"url": "https://example.com/proxy", "token": "y"}),
    ],
)
def test_url_path_prefix_is_rejected_for_every_integration(
    tmp_log_file: Path, name: str, data: dict[str, str]
) -> None:
    with pytest.raises(ConfigError, match="url"):
        _build(tmp_log_file, {name: _section(name, data)})


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("homeassistant", {"url": "http://x", "token": "y", "http_timout": "5"}),
        ("mealie", {"url": "http://x", "api_key": "y", "api_keey": "z"}),
    ],
)
def test_unknown_settings_are_rejected(
    tmp_log_file: Path, name: str, data: dict[str, str]
) -> None:
    with pytest.raises(ConfigError, match="unknown setting"):
        _build(tmp_log_file, {name: _section(name, data)})


@pytest.mark.parametrize("token", ["bad\ntoken", "bad\rtoken"])
def test_header_control_characters_are_rejected(tmp_log_file: Path, token: str) -> None:
    with pytest.raises(ConfigError, match="invalid header character"):
        _build(
            tmp_log_file,
            {"homeassistant": _section("homeassistant", {"url": "http://x", "token": token})},
        )
