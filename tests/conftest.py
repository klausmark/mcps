"""Shared test fixtures."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from mcps.config import SectionConfig, ServerConfig


@pytest.fixture
def tmp_config_path(tmp_path: Path) -> Path:
    """A writable config file path with mode 0600 set."""
    path = tmp_path / "config.toml"
    path.touch()
    os.chmod(path, 0o600)
    return path


@pytest.fixture
def tmp_log_file(tmp_path: Path) -> Path:
    """A log file path under `tmp_path` so tests never touch the real home."""
    return tmp_path / "mcps.log"


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect `Path.home()` so tests cannot touch the real home directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


@pytest.fixture
def mock_http(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Callable[[httpx.Request], httpx.Response]], None]:
    """Install an httpx.MockTransport on the module used by `make_client`.

    Usage in a test::

        def test_something(mock_http):
            def handler(request):
                return httpx.Response(200, json={"ok": True})
            mock_http(handler)
            ...

    Tests that need to inspect requests should close over a list inside `handler`.
    """

    def install(handler: Callable[[httpx.Request], httpx.Response]) -> None:
        transport = httpx.MockTransport(handler)
        original_client = httpx.Client

        def patched_client(*args, **kwargs):
            kwargs.setdefault("transport", transport)
            return original_client(*args, **kwargs)

        monkeypatch.setattr("mcps.http_client.httpx.Client", patched_client)

    return install


@pytest.fixture
def homeassistant_section() -> SectionConfig:
    return SectionConfig(
        name="homeassistant",
        data={"url": "http://hass.local:8123", "token": "ha-secret-token"},
        http_timeout=5.0,
        verify_tls=True,
    )


@pytest.fixture
def mealie_section() -> SectionConfig:
    return SectionConfig(
        name="mealie",
        data={"url": "http://mealie.local:9000", "api_key": "mealie-secret-key"},
        http_timeout=5.0,
        verify_tls=True,
    )


@pytest.fixture
def netbox_section() -> SectionConfig:
    return SectionConfig(
        name="netbox",
        data={"url": "https://netbox.example", "token": "nbt_key.netbox-secret"},
        http_timeout=5.0,
        verify_tls=True,
    )


@pytest.fixture
def nirvana_section() -> SectionConfig:
    return SectionConfig(
        name="nirvana",
        data={
            "url": "https://api.nirvanahq.com",
            "email": "user@example.com",
            "password": "nirvana-secret-pw",
        },
        http_timeout=5.0,
        verify_tls=True,
    )


@pytest.fixture
def garmin_section(fake_home: Path) -> SectionConfig:
    return SectionConfig(
        name="garmin",
        data={
            "email": "garmin-user@example.com",
            "password": "garmin-secret-pw",
            "token_store": str(fake_home / ".mcps" / "garmin"),
        },
        http_timeout=5.0,
        verify_tls=True,
    )


@pytest.fixture
def server_config(
    homeassistant_section: SectionConfig,
    tmp_log_file: Path,
) -> ServerConfig:
    return ServerConfig(
        log_file=tmp_log_file,
        log_level="WARNING",
        http_timeout=5.0,
        sections={"homeassistant": homeassistant_section},
    )
