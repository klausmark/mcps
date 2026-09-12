"""Integration modules that register MCP tools for each upstream service."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from mcp.server import MCPServer

from mcps.config import ConfigError, ServerConfig

from . import homeassistant, mealie, netbox, nirvana

INTEGRATIONS = (
    homeassistant,
    mealie,
    netbox,
    nirvana,
)


def register_all(server: MCPServer, config: ServerConfig) -> None:
    """Register every integration whose config section is present and valid."""
    for module in INTEGRATIONS:
        if section := config.sections.get(module.NAME):
            _validate_required(module.NAME, module.REQUIRED_KEYS, section)

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


def _validate_required(section_name: str, required: Iterable[str], section) -> None:
    missing = [key for key in required if key not in section.data]
    if missing:
        raise ConfigError(
            f"section [{section_name}] is missing required keys: {', '.join(missing)}"
        )
