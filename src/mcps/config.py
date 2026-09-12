"""Configuration loading, validation, and override merging.

Precedence (low -> high): file < env vars (`MCPS_*`) < CLI flags.
"""

from __future__ import annotations

import math
import os
import re
import stat
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HTTP_TIMEOUT = 10.0
DEFAULT_LOG_LEVEL = "INFO"
ALLOWED_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

EXPECTED_CONFIG_MODE = 0o600
EXPECTED_DIRECTORY_MODE = 0o700
SECTION_KEY_PART_COUNT = 2


def default_config_file() -> Path:
    """Return the default config path under the current user's home directory."""
    return Path.home() / ".mcps" / "config.toml"


def default_log_file() -> Path:
    """Return the default log file path under the current user's home directory."""
    return Path.home() / ".mcps" / "logs" / "mcps.log"

# Keys that are part of a section's config but are not credentials.
NON_CREDENTIAL_KEYS = frozenset({"url", "verify_tls", "http_timeout", "log_file", "log_level"})

# Settings recognized inside `[server]`; anything else is a typo worth rejecting.
SERVER_KEYS = frozenset({"log_file", "log_level", "http_timeout"})

# Validation: section names and key names are snake_case identifiers.
IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9_]*")

ENV_KEY_PREFIX = "MCPS_"

# Reserved env vars are consumed by the app itself, never as section overrides.
RESERVED_ENV_KEYS = frozenset({"MCPS_CONFIG_PATH"})


class ConfigError(ValueError):
    """Raised when configuration is missing, malformed, or fails validation."""


@dataclass(frozen=True)
class SectionConfig:
    """One integration's parsed configuration."""

    name: str
    data: Mapping[str, str]
    http_timeout: float
    verify_tls: bool
    redaction_values: tuple[str, ...] = field(default=(), repr=False)

    def credential_values(self) -> list[str]:
        """Return values for keys that look like credentials (for output sanitization)."""
        own_values = [v for k, v in self.data.items() if k not in NON_CREDENTIAL_KEYS]
        return list(dict.fromkeys([*own_values, *self.redaction_values]))


@dataclass(frozen=True)
class ServerConfig:
    log_file: Path
    log_level: str
    http_timeout: float
    sections: Mapping[str, SectionConfig] = field(default_factory=dict)


def _default_config_path(explicit: Path | None, env: Mapping[str, str]) -> Path:
    if explicit is not None:
        return explicit
    value = env.get("MCPS_CONFIG_PATH")
    if value:
        return Path(value)
    return default_config_file()


def _ensure_config_security(path: Path) -> None:
    """Ensure a config file is private on platforms with POSIX permissions."""
    if os.name != "posix":
        return
    try:
        st = os.stat(path)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except PermissionError as exc:
        raise ConfigError(f"config file not accessible: {path}") from exc

    if st.st_uid != os.getuid():
        raise ConfigError(
            f"config file {path} must be owned by the current user (uid {os.getuid()}), "
            f"owned by uid {st.st_uid}."
        )

    mode = stat.S_IMODE(st.st_mode)
    if mode == EXPECTED_CONFIG_MODE:
        return
    try:
        os.chmod(path, EXPECTED_CONFIG_MODE)
    except OSError as exc:
        raise ConfigError(
            f"config file {path} has mode {mode:o} and could not be changed to "
            f"{EXPECTED_CONFIG_MODE:o}: {exc}"
        ) from exc
    try:
        corrected_mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError as exc:
        raise ConfigError(f"could not verify permissions for config file {path}: {exc}") from exc
    if corrected_mode != EXPECTED_CONFIG_MODE:
        raise ConfigError(
            f"config file {path} must have mode {EXPECTED_CONFIG_MODE:o}, "
            f"got {corrected_mode:o} after chmod"
        )


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except PermissionError as exc:
        raise ConfigError(f"config file not readable: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc


def _validate_identifier(kind: str, value: str) -> None:
    if not IDENTIFIER_RE.fullmatch(value):
        raise ConfigError(f"invalid {kind} name: {value!r} (must be snake_case)")


def _set_override(data: dict, section_name: str, key_name: str, value: str) -> None:
    existing = data.get(section_name)
    if existing is not None and not isinstance(existing, dict):
        raise ConfigError(f"section [{section_name}] must be a table")
    data.setdefault(section_name, {})
    data[section_name][key_name] = value


def _apply_env_overrides(data: dict, env: Mapping[str, str]) -> None:
    """Overlay `MCPS_<SECTION>_<KEY>` env vars onto the parsed config tree."""
    prefix = ENV_KEY_PREFIX
    for env_key, env_value in env.items():
        if env_key in RESERVED_ENV_KEYS or not env_key.startswith(prefix):
            continue
        suffix = env_key[len(prefix) :]
        parts = suffix.lower().split("_", 1)
        if len(parts) != SECTION_KEY_PART_COUNT or not parts[0] or not parts[1]:
            continue
        section_name, key_name = parts
        _set_override(data, section_name, key_name, env_value)


def _apply_cli_overrides(data: dict, overrides: Mapping[tuple[str, str], str]) -> None:
    for (section_name, key_name), value in overrides.items():
        _set_override(data, section_name, key_name, value)


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    raise ConfigError(f"expected boolean, got {value!r}")


def _coerce_positive_timeout(value: object, *, field_name: str) -> float:
    """Return a positive, finite timeout, rejecting booleans and empty values."""
    if isinstance(value, bool):
        raise ConfigError(f"{field_name}: expected a positive, finite number")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ConfigError(f"{field_name}: expected a positive, finite number") from None
    if not math.isfinite(number) or number <= 0:
        raise ConfigError(f"{field_name}: expected a positive, finite number")
    return number


def _parse_section(
    name: str, raw: Mapping[str, object], *, default_timeout: float
) -> SectionConfig:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"section [{name}] must be a table, got {type(raw).__name__}")
    if "http_timeout" in raw:
        timeout = _coerce_positive_timeout(raw["http_timeout"], field_name=f"[{name}].http_timeout")
    else:
        timeout = default_timeout
    verify_tls_raw = raw.get("verify_tls", True)
    verify_tls = _coerce_bool(
        verify_tls_raw if isinstance(verify_tls_raw, bool) else str(verify_tls_raw)
    )

    flat: dict[str, str] = {}
    for key, value in raw.items():
        if key in ("http_timeout", "verify_tls"):
            continue
        if not IDENTIFIER_RE.fullmatch(key):
            raise ConfigError(f"section [{name}]: invalid key name {key!r} (must be snake_case)")
        if not isinstance(value, (str, int, float, bool)):
            raise ConfigError(
                f"section [{name}] key {key!r}: only strings, numbers, and booleans are supported"
            )
        flat[key] = str(value)

    return SectionConfig(name=name, data=flat, http_timeout=timeout, verify_tls=verify_tls)


def load_config(
    *,
    path: Path | None,
    env: Mapping[str, str],
    cli_overrides: Mapping[tuple[str, str], str] | None = None,
) -> ServerConfig:
    cli_overrides = cli_overrides or {}
    resolved = _default_config_path(path, env)
    _ensure_config_security(resolved)
    data = _read_toml(resolved)

    _apply_env_overrides(data, env)
    _apply_cli_overrides(data, cli_overrides)

    server_raw = data.get("server", {})
    if not isinstance(server_raw, Mapping):
        raise ConfigError("[server] must be a table")
    unknown_server_keys = set(server_raw) - SERVER_KEYS
    if unknown_server_keys:
        raise ConfigError(
            f"[server]: unknown setting(s): {', '.join(sorted(unknown_server_keys))}"
        )
    log_level_raw = server_raw.get("log_level", DEFAULT_LOG_LEVEL)
    log_level = str(log_level_raw).upper()
    if log_level not in ALLOWED_LOG_LEVELS:
        raise ConfigError(f"log_level must be one of {ALLOWED_LOG_LEVELS}, got {log_level_raw!r}")
    timeout_raw = server_raw.get("http_timeout", DEFAULT_HTTP_TIMEOUT)
    http_timeout = _coerce_positive_timeout(timeout_raw, field_name="[server].http_timeout")

    log_file_raw = server_raw.get("log_file")
    log_file = Path(str(log_file_raw)).expanduser() if log_file_raw else default_log_file()

    sections: dict[str, SectionConfig] = {}
    for section_name, section_raw in data.items():
        if section_name == "server":
            continue
        _validate_identifier("section", section_name)
        sections[section_name] = _parse_section(
            section_name, section_raw, default_timeout=http_timeout
        )

    return ServerConfig(
        log_file=log_file,
        log_level=log_level,
        http_timeout=http_timeout,
        sections=sections,
    )


# Exposed for tests.
def _reset_for_tests() -> None:
    """No-op kept for symmetry; remove if unused."""
    sys.modules.pop(__name__, None)


_INIT_CONFIG_TEMPLATE = """\
# mcps config - created by `mcps init`.
# Uncomment an integration and fill in values to enable it.
# A configured integration must contain all of its required keys.

[server]
log_file = "~/.mcps/logs/mcps.log"
log_level = "INFO"
http_timeout = 10.0

# [homeassistant]
# url = "http://hass.local:8123"
# token = "..."
# verify_tls = true

# [mealie]
# url = "http://mealie.local:9000"
# api_key = "..."
# verify_tls = true

# [netbox]
# url = "https://netbox.example.com"
# token = "..."
# verify_tls = true

# [nirvana]
# url = "https://api.nirvanahq.com"
# email = "..."
# password = "..."
# verify_tls = true
"""


def init_default_path() -> Path:
    """Return the path `mcps init` writes to by default.

    Honors `MCPS_CONFIG_PATH`; otherwise uses `~/.mcps/config.toml`.
    """
    env = os.environ.get("MCPS_CONFIG_PATH")
    if env:
        return Path(env)
    return default_config_file()


def init_log_directory() -> Path:
    """Create the default mcps directories and secure them on POSIX."""
    log_directory = default_log_file().parent
    for directory in (log_directory.parent, log_directory):
        try:
            directory.mkdir(mode=EXPECTED_DIRECTORY_MODE, parents=True, exist_ok=True)
            if os.name == "posix":
                os.chmod(directory, EXPECTED_DIRECTORY_MODE)
                mode = stat.S_IMODE(directory.stat().st_mode)
                if mode != EXPECTED_DIRECTORY_MODE:
                    raise ConfigError(
                        f"directory {directory} must have mode {EXPECTED_DIRECTORY_MODE:o}, "
                        f"got {mode:o} after chmod"
                    )
        except OSError as exc:
            raise ConfigError(f"could not prepare directory {directory}: {exc}") from exc
    return log_directory


def init_config(path: Path, *, force: bool = False) -> Path:
    """Write a default config template to `path` with mode 0600.

    Raises `ConfigError` if the file exists and `force` is not set.
    Returns the resolved path on success.
    """
    if path.exists() and not force:
        raise ConfigError(f"config file already exists: {path}. Use --force to overwrite.")
    init_log_directory()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_INIT_CONFIG_TEMPLATE, encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"could not create config file {path}: {exc}") from exc
    _ensure_config_security(path)
    return path
