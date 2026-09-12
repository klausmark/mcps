"""Read-only Garmin Connect integration.

Garmin has no public consumer API. This integration uses the unofficial
`garminconnect` SDK, which handles SSO login, MFA, and OAuth token refresh and
persists tokens to a local store that `mcps` keeps private.

Authentication is deliberately lazy: the MCP server never prompts (stdin is the
MCP transport). Tokens must be bootstrapped once with `mcps garmin-login`.
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import re
import stat
import threading
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from mcps.config import ConfigError, SectionConfig
from mcps.errors import ToolError
from mcps.logging_setup import log_call
from mcps.redaction import REDACTION_MARKER, sanitize
from mcps.responses import expect_dict, expect_list

NAME = "garmin"
REQUIRED_KEYS = ("email", "password")
ALLOWED_KEYS = ("email", "password", "token_store")

DEFAULT_TOKEN_STORE = "~/.mcps/garmin"
TOKEN_FILE_NAME = "garmin_tokens.json"

MAX_ACTIVITY_LIMIT = 100
MAX_BODY_BATTERY_DAYS = 31
MAX_EMAIL_LENGTH = 254
MAX_RESPONSE_BYTES = 1024 * 1024
EXPECTED_DIRECTORY_MODE = 0o700
EXPECTED_FILE_MODE = 0o600

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ACTIVITY_ID_RE = re.compile(r"^[0-9]{1,20}$")
SENSITIVE_KEY_RE = re.compile(r"(token|password|secret|credential|cookie|ticket)", re.IGNORECASE)

AUTH_HINT = "Garmin Connect authentication failed; run `mcps garmin-login` and try again."
TOKEN_STORE_ERROR = "Garmin Connect token store could not be secured."
REQUEST_FAILED = "Garmin Connect request failed."
RESOURCE_NOT_FOUND = "Garmin Connect resource was not found."
RATE_LIMITED = "Garmin Connect rate limit exceeded; try again later."
UNEXPECTED_SHAPE = "Garmin Connect returned an unexpected response shape."
RESPONSE_TOO_LARGE = "Garmin Connect response exceeds the 1 MiB limit."


class GarminError(ToolError):
    """Safe error that contains no upstream response or credential details."""


def _silence_sdk_logging() -> None:
    """Keep the SDK's DEBUG/WARNING output (which may contain bodies) out of mcps logs."""
    for name in ("garminconnect", "garminconnect.client"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.CRITICAL)
        logger.propagate = False
        if not any(isinstance(handler, logging.NullHandler) for handler in logger.handlers):
            logger.addHandler(logging.NullHandler())


def _resolve_token_store(section: SectionConfig) -> tuple[Path, Path]:
    """Return `(directory, token_file)` for the configured token store."""
    raw = section.data.get("token_store", DEFAULT_TOKEN_STORE).strip()
    if raw.startswith(("{", "[")):
        raise ConfigError(
            "section [garmin] token_store must be a directory path, not inline token JSON"
        )
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ConfigError("section [garmin] token_store must be an absolute path")
    if ".." in path.parts:
        raise ConfigError("section [garmin] token_store must not contain '..'")
    if path.suffix.lower() == ".json":
        return path.parent, path
    return path, path / TOKEN_FILE_NAME


def _reject_symlink_components(path: Path) -> None:
    """Reject symlinks on the path below the user's home directory.

    Component checks stop at the home directory so a symlinked home or system
    mount is not rejected; the token directory and file are always checked.
    """
    home = Path.home()
    components = [path]
    try:
        relative = path.relative_to(home)
    except ValueError:
        pass
    else:
        components = [
            home.joinpath(*relative.parts[: index + 1]) for index in range(len(relative.parts))
        ]
    for component in components:
        if component.is_symlink():
            raise ConfigError(
                f"Garmin Connect token store path must not contain symlinks: {component}"
            )


def _enforce_owner_and_mode(path: Path, *, kind: str, mode: int) -> None:
    try:
        st = path.lstat()
    except OSError as exc:
        raise ConfigError(
            f"Garmin Connect token {kind} {path} is not accessible: {exc}"
        ) from exc
    if stat.S_ISLNK(st.st_mode):
        raise ConfigError(f"Garmin Connect token {kind} {path} must not be a symlink")
    if st.st_uid != os.getuid():
        raise ConfigError(
            f"Garmin Connect token {kind} {path} must be owned by the current user "
            f"(uid {os.getuid()}), owned by uid {st.st_uid}"
        )
    current_mode = stat.S_IMODE(st.st_mode)
    if current_mode == mode:
        return
    try:
        os.chmod(path, mode)
    except OSError as exc:
        raise ConfigError(
            f"Garmin Connect token {kind} {path} has mode {current_mode:o} and could not "
            f"be changed to {mode:o}: {exc}"
        ) from exc
    if stat.S_IMODE(path.lstat().st_mode) != mode:
        raise ConfigError(
            f"Garmin Connect token {kind} {path} could not be secured to mode {mode:o}"
        )


def _secure_directory(path: Path, *, create: bool) -> None:
    """Create (optionally) and enforce ownership/mode on the token directory."""
    _reject_symlink_components(path)
    if create:
        try:
            path.mkdir(mode=EXPECTED_DIRECTORY_MODE, parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(
                f"could not create Garmin Connect token directory {path}: {exc}"
            ) from exc
    elif not path.exists():
        return
    if os.name != "posix":
        return
    _enforce_owner_and_mode(path, kind="directory", mode=EXPECTED_DIRECTORY_MODE)


def _secure_token_file(path: Path, *, required: bool) -> None:
    if path.is_symlink():
        raise ConfigError(f"Garmin Connect token file {path} must not be a symlink")
    if os.name != "posix":
        return
    if not path.exists():
        if required:
            raise ConfigError(f"Garmin Connect token file {path} was not created")
        return
    _enforce_owner_and_mode(path, kind="file", mode=EXPECTED_FILE_MODE)


def _validate_section(section: SectionConfig) -> None:
    email = section.data["email"].strip()
    password = section.data["password"]
    if not email or "@" not in email or any(character.isspace() for character in email):
        raise ConfigError("section [garmin] email must be an address without whitespace")
    if len(email) > MAX_EMAIL_LENGTH:
        raise ConfigError("section [garmin] email is too long")
    if not password.strip():
        raise ConfigError("section [garmin] password must not be empty")
    if not section.verify_tls:
        raise ConfigError(
            "section [garmin] verify_tls=false is not supported: the Garmin SDK cannot "
            "disable TLS verification"
        )
    token_dir, _ = _resolve_token_store(section)
    _secure_directory(token_dir, create=False)


def _make_sdk(section: SectionConfig, prompt_mfa: Callable[[], str] | None = None) -> Garmin:
    """Build the SDK client; the single choke point for SDK construction."""
    _silence_sdk_logging()
    return Garmin(
        email=section.data["email"],
        password=section.data["password"],
        prompt_mfa=prompt_mfa,
        verify_login=True,
    )


def _runtime_credentials(sdk: Garmin) -> list[str]:
    """Return generated tokens/cookies so results can be redacted against them."""
    client = getattr(sdk, "client", None)
    if client is None:
        return []
    values: list[str] = []
    for attribute in ("di_token", "di_refresh_token", "jwt_web", "csrf_token"):
        value = getattr(client, attribute, None)
        if isinstance(value, str) and value:
            values.append(value)
    session = getattr(client, "cs", None)
    cookies = getattr(session, "cookies", None)
    if cookies is not None:
        for cookie in cookies:
            value = getattr(cookie, "value", None)
            if isinstance(value, str) and value:
                values.append(value)
    return values


def _redact_sensitive_keys(data: Any) -> Any:
    """Replace values under credential-like upstream keys with a marker."""
    if isinstance(data, dict):
        return {
            key: (
                REDACTION_MARKER
                if isinstance(key, str) and SENSITIVE_KEY_RE.search(key)
                else _redact_sensitive_keys(value)
            )
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [_redact_sensitive_keys(item) for item in data]
    return data


def _ensure_serializable_size(data: Any) -> None:
    try:
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        raise GarminError(UNEXPECTED_SHAPE) from None
    if len(encoded.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise GarminError(RESPONSE_TOO_LARGE)


class _GarminClient:
    """Lazily authenticates and serializes access to the Garmin SDK."""

    def __init__(self, section: SectionConfig) -> None:
        self._section = section
        self._token_dir, self._token_file = _resolve_token_store(section)
        self._lock = threading.Lock()
        self._sdk: Garmin | None = None

    def call_dict(self, method_name: str, *args: Any, **kwargs: Any) -> dict:
        data = self._call(method_name, *args, **kwargs)
        return expect_dict(data, label="Garmin Connect")

    def call_list(self, method_name: str, *args: Any, **kwargs: Any) -> list[dict]:
        data = self._call(method_name, *args, **kwargs)
        return expect_list(data, label="Garmin Connect")

    def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            sdk = self._ensure_authenticated()
            try:
                result = getattr(sdk, method_name)(*args, **kwargs)
            except GarminConnectNotFoundError:
                raise GarminError(RESOURCE_NOT_FOUND) from None
            except GarminConnectTooManyRequestsError:
                raise GarminError(RATE_LIMITED) from None
            except GarminConnectAuthenticationError:
                self._sdk = None
                raise GarminError(AUTH_HINT) from None
            except Exception:
                raise GarminError(REQUEST_FAILED) from None
            credentials = [*self._section.credential_values(), *_runtime_credentials(sdk)]
            data = sanitize(_redact_sensitive_keys(result), credentials)
            _ensure_serializable_size(data)
            return data

    def _ensure_authenticated(self) -> Garmin:
        if self._sdk is not None:
            return self._sdk
        try:
            _secure_directory(self._token_dir, create=True)
            _secure_token_file(self._token_file, required=False)
        except ConfigError:
            raise GarminError(TOKEN_STORE_ERROR) from None
        try:
            sdk = _make_sdk(self._section)
            sdk.login(str(self._token_file))
        except GarminConnectTooManyRequestsError:
            raise GarminError(RATE_LIMITED) from None
        except GarminConnectAuthenticationError:
            raise GarminError(AUTH_HINT) from None
        except Exception:
            raise GarminError(REQUEST_FAILED) from None
        try:
            _secure_token_file(self._token_file, required=True)
        except ConfigError:
            raise GarminError(TOKEN_STORE_ERROR) from None
        self._sdk = sdk
        return sdk


def login_interactively(section: SectionConfig) -> None:
    """Authenticate interactively and persist tokens for the MCP server."""
    _validate_section(section)
    token_dir, token_file = _resolve_token_store(section)
    _secure_directory(token_dir, create=True)
    _secure_token_file(token_file, required=False)
    try:
        sdk = _make_sdk(section, prompt_mfa=_prompt_mfa)
        sdk.login(str(token_file))
    except GarminConnectTooManyRequestsError:
        raise GarminError(RATE_LIMITED) from None
    except GarminConnectAuthenticationError:
        raise GarminError("Garmin Connect authentication failed.") from None
    except Exception:
        raise GarminError(REQUEST_FAILED) from None
    _secure_token_file(token_file, required=True)


def _prompt_mfa() -> str:
    return getpass.getpass("Garmin MFA code: ")


def _validate_date(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise GarminError(f"{field_name} must be a date in YYYY-MM-DD format")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise GarminError(f"{field_name} must be a real date in YYYY-MM-DD format") from None
    return value


def _validate_date_range(start_date: str, end_date: str | None) -> tuple[str, str]:
    start = _validate_date(start_date, "start_date")
    end = _validate_date(end_date if end_date is not None else start_date, "end_date")
    span = date.fromisoformat(end) - date.fromisoformat(start)
    if span.days < 0:
        raise GarminError("start_date must not be after end_date")
    if span.days >= MAX_BODY_BATTERY_DAYS:
        raise GarminError(f"date range must not exceed {MAX_BODY_BATTERY_DAYS} days")
    return start, end


def _validate_activity_window(start: int, limit: int) -> tuple[int, int]:
    if isinstance(start, bool) or not isinstance(start, int) or start < 0:
        raise GarminError("start must be a non-negative integer")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit < 1
        or limit > MAX_ACTIVITY_LIMIT
    ):
        raise GarminError(f"limit must be an integer between 1 and {MAX_ACTIVITY_LIMIT}")
    return start, limit


def _validate_activity_id(activity_id: str) -> str:
    if (
        not isinstance(activity_id, str)
        or not ACTIVITY_ID_RE.fullmatch(activity_id)
        or not activity_id.strip("0")
    ):
        raise GarminError("activity_id must be a positive numeric identifier")
    return activity_id


def register(server: MCPServer, section: SectionConfig) -> None:
    _validate_section(section)
    client = _GarminClient(section)
    annotations = ToolAnnotations(read_only_hint=True, open_world_hint=True)

    @server.tool(
        name="garmin_get_daily_summary",
        description="Return the Garmin Connect daily activity summary for one date.",
        annotations=annotations,
    )
    @log_call("garmin_get_daily_summary", credentials=section.credential_values())
    def garmin_get_daily_summary(date: str) -> dict:
        """Fetch steps, calories, distance, and intensity minutes for one day."""
        return client.call_dict("get_stats", _validate_date(date, "date"))

    @server.tool(
        name="garmin_get_sleep",
        description="Return Garmin Connect sleep data for one date.",
        annotations=annotations,
    )
    @log_call("garmin_get_sleep", credentials=section.credential_values())
    def garmin_get_sleep(date: str) -> dict:
        """Fetch sleep stages and summary for one night, reported by wake date."""
        return client.call_dict("get_sleep_data", _validate_date(date, "date"))

    @server.tool(
        name="garmin_get_heart_rate",
        description="Return Garmin Connect heart-rate data for one date.",
        annotations=annotations,
    )
    @log_call("garmin_get_heart_rate", credentials=section.credential_values())
    def garmin_get_heart_rate(date: str) -> dict:
        """Fetch resting heart rate and available samples for one day."""
        return client.call_dict("get_heart_rates", _validate_date(date, "date"))

    @server.tool(
        name="garmin_get_stress",
        description="Return Garmin Connect stress data for one date.",
        annotations=annotations,
    )
    @log_call("garmin_get_stress", credentials=section.credential_values())
    def garmin_get_stress(date: str) -> dict:
        """Fetch the daily stress summary and samples for one day."""
        return client.call_dict("get_stress_data", _validate_date(date, "date"))

    @server.tool(
        name="garmin_get_body_battery",
        description="Return Garmin Connect Body Battery data for a date range.",
        annotations=annotations,
    )
    @log_call("garmin_get_body_battery", credentials=section.credential_values())
    def garmin_get_body_battery(start_date: str, end_date: str | None = None) -> list[dict]:
        """Fetch Body Battery values for at most 31 days, inclusive."""
        safe_start, safe_end = _validate_date_range(start_date, end_date)
        return client.call_list("get_body_battery", safe_start, safe_end)

    @server.tool(
        name="garmin_list_activities",
        description="List recent Garmin Connect activities, newest first.",
        annotations=annotations,
    )
    @log_call("garmin_list_activities", credentials=section.credential_values())
    def garmin_list_activities(start: int = 0, limit: int = 20) -> list[dict]:
        """List activities with an offset and a limit of at most 100."""
        safe_start, safe_limit = _validate_activity_window(start, limit)
        return client.call_list("get_activities", start=safe_start, limit=safe_limit)

    @server.tool(
        name="garmin_get_activity",
        description="Return the summary of one Garmin Connect activity.",
        annotations=annotations,
    )
    @log_call("garmin_get_activity", credentials=section.credential_values())
    def garmin_get_activity(activity_id: str) -> dict:
        """Fetch one activity by its numeric Garmin identifier."""
        return client.call_dict("get_activity", _validate_activity_id(activity_id))
