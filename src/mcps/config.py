"""Configuration loading, validation, and override merging.

Precedence (low -> high): file < env vars (`MCPS_*`) < CLI flags.
"""

from __future__ import annotations

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

# Validation: section names and key names are snake_case identifiers.
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")

ENV_KEY_PREFIX = "MCPS_"


class ConfigError(ValueError):
    """Raised when configuration is missing, malformed, or fails validation."""


@dataclass(frozen=True)
class SectionConfig:
    """One integration's parsed configuration."""

    name: str
    data: Mapping[str, str]
    http_timeout: float
    verify_tls: bool

    def credential_values(self) -> list[str]:
        """Return values for keys that look like credentials (for output sanitization)."""
        return [v for k, v in self.data.items() if k not in NON_CREDENTIAL_KEYS]


@dataclass(frozen=True)
class ServerConfig:
    log_file: Path
    log_level: str
    http_timeout: float
    sections: Mapping[str, SectionConfig] = field(default_factory=dict)


def _default_config_path(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    env = os.environ.get("MCPS_CONFIG_PATH")
    if env:
        return Path(env)
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
    if not IDENTIFIER_RE.match(value):
        raise ConfigError(f"invalid {kind} name: {value!r} (must be snake_case)")


def _apply_env_overrides(data: dict, env: Mapping[str, str]) -> None:
    """Overlay `MCPS_<SECTION>_<KEY>` env vars onto the parsed config tree."""
    prefix = ENV_KEY_PREFIX
    for env_key, env_value in env.items():
        if not env_key.startswith(prefix):
            continue
        suffix = env_key[len(prefix) :]
        parts = suffix.lower().split("_", 1)
        if len(parts) != SECTION_KEY_PART_COUNT or not parts[0] or not parts[1]:
            continue
        section_name, key_name = parts
        if section_name == "server":
            data.setdefault("server", {})
            data["server"][key_name] = env_value
        else:
            data.setdefault(section_name, {})
            data[section_name][key_name] = env_value


def _apply_cli_overrides(data: dict, overrides: Mapping[tuple[str, str], str]) -> None:
    for (section_name, key_name), value in overrides.items():
        data.setdefault(section_name, {})
        data[section_name][key_name] = value


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


def _coerce_float(value: object, *, field_name: str) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field_name}: expected number, got {value!r}") from exc


def _parse_section(
    name: str, raw: Mapping[str, object], *, default_timeout: float
) -> SectionConfig:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"section [{name}] must be a table, got {type(raw).__name__}")
    flat: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, (str, int, float, bool)):
            raise ConfigError(
                f"section [{name}] key {key!r}: only strings, numbers, and booleans are supported"
            )
        flat[key] = str(value)
    verify_tls_raw = flat.pop("verify_tls", "true")
    timeout_raw = flat.pop("http_timeout", None)
    if timeout_raw:
        timeout = _coerce_float(timeout_raw, field_name=f"[{name}].http_timeout")
    else:
        timeout = default_timeout
    return SectionConfig(
        name=name,
        data=flat,
        http_timeout=timeout,
        verify_tls=_coerce_bool(verify_tls_raw),
    )


def load_config(
    *,
    path: Path | None,
    env: Mapping[str, str],
    cli_overrides: Mapping[tuple[str, str], str] | None = None,
) -> ServerConfig:
    cli_overrides = cli_overrides or {}
    resolved = _default_config_path(path)
    _ensure_config_security(resolved)
    data = _read_toml(resolved)

    _apply_env_overrides(data, env)
    _apply_cli_overrides(data, cli_overrides)

    server_raw = data.get("server", {})
    if not isinstance(server_raw, Mapping):
        raise ConfigError("[server] must be a table")
    log_level_raw = server_raw.get("log_level", DEFAULT_LOG_LEVEL)
    log_level = str(log_level_raw).upper()
    if log_level not in ALLOWED_LOG_LEVELS:
        raise ConfigError(f"log_level must be one of {ALLOWED_LOG_LEVELS}, got {log_level_raw!r}")
    timeout_raw = server_raw.get("http_timeout", DEFAULT_HTTP_TIMEOUT)
    http_timeout = _coerce_float(timeout_raw, field_name="[server].http_timeout")

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
