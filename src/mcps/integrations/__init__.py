"""Integration modules that register MCP tools for each upstream service."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

import httpx
from mcp.server import MCPServer

from mcps.config import ConfigError, SectionConfig, ServerConfig

from . import homeassistant, mealie, netbox, nirvana

INTEGRATIONS = (
    homeassistant,
    mealie,
    netbox,
    nirvana,
)


def register_all(server: MCPServer, config: ServerConfig) -> None:
    """Register every integration whose config section is present and valid."""
    known = {module.NAME for module in INTEGRATIONS}
    unknown = set(config.sections) - known
    if unknown:
        raise ConfigError(f"unknown integration section(s): {', '.join(sorted(unknown))}")

    for module in INTEGRATIONS:
        if section := config.sections.get(module.NAME):
            _validate_section(module.NAME, module.REQUIRED_KEYS, section)

    credentials = [
        value for section in config.sections.values() for value in section.credential_values()
    ]
    if section := config.sections.get(nirvana.NAME):
        credentials.append(nirvana.basic_token(section.data))

    for module in INTEGRATIONS:
        section = config.sections.get(module.NAME)
        if section is None:
            continue
        module.register(server, replace(section, redaction_values=tuple(credentials)))


def _validate_section(
    section_name: str, required: Iterable[str], section: SectionConfig
) -> None:
    missing = [key for key in required if key not in section.data]
    if missing:
        raise ConfigError(
            f"section [{section_name}] is missing required keys: {', '.join(missing)}"
        )
    for key in required:
        value = section.data[key]
        if not value.strip():
            raise ConfigError(f"section [{section_name}] key {key!r} must not be empty")
        if key == "url":
            _validate_url(section_name, value)
        elif any(character in value for character in "\r\n"):
            raise ConfigError(
                f"section [{section_name}] key {key!r} contains an invalid header character"
            )


def _validate_url(section_name: str, value: str) -> None:
    try:
        url = httpx.URL(value)
    except (httpx.InvalidURL, ValueError):
        raise ConfigError(f"section [{section_name}] url is not a valid URL") from None
    if (
        url.scheme not in ("http", "https")
        or not url.host
        or url.userinfo
        or url.query
        or url.fragment
    ):
        raise ConfigError(
            f"section [{section_name}] url must be an HTTP(S) URL without "
            "credentials, query, or fragment"
        )
