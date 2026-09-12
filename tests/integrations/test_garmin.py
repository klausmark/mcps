"""Tests for the Garmin Connect integration.

The `garminconnect` SDK is replaced with an in-process fake, so no test touches
the network or requires a Garmin account.
"""

from __future__ import annotations

import logging
import os
import stat
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)
from mcp import Client

from mcps.__main__ import main
from mcps.config import ConfigError, SectionConfig, ServerConfig
from mcps.integrations import garmin
from mcps.server import build_server

TOOL_NAMES = {
    "garmin_get_daily_summary",
    "garmin_get_sleep",
    "garmin_get_heart_rate",
    "garmin_get_stress",
    "garmin_get_body_battery",
    "garmin_list_activities",
    "garmin_get_activity",
}


class FakeGarmin:
    """Stand-in for `garminconnect.Garmin` that records calls."""

    instances: list[FakeGarmin] = []
    responses: dict[str, object] = {}
    failures: dict[str, Exception] = {}
    login_failure: Exception | None = None
    needs_mfa = False
    write_token_file = True

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        prompt_mfa=None,
        verify_login: bool = True,
    ) -> None:
        self.email = email
        self.password = password
        self.prompt_mfa = prompt_mfa
        self.verify_login = verify_login
        self.logins: list[str | None] = []
        self.calls: list[tuple[str, tuple, dict]] = []
        self.mfa_code: str | None = None
        self.client = SimpleNamespace(
            di_token="access-token-sentinel",
            di_refresh_token="refresh-token-sentinel",
            jwt_web=None,
            csrf_token="csrf-token-sentinel",
            cs=SimpleNamespace(cookies=[SimpleNamespace(value="cookie-sentinel")]),
        )
        FakeGarmin.instances.append(self)

    def login(self, tokenstore: str | None = None) -> tuple[None, None]:
        if FakeGarmin.login_failure is not None:
            raise FakeGarmin.login_failure
        if FakeGarmin.needs_mfa:
            if self.prompt_mfa is None:
                raise GarminConnectAuthenticationError(
                    "MFA Required but no prompt_mfa mechanism supplied"
                )
            self.mfa_code = self.prompt_mfa()
        self.logins.append(tokenstore)
        if FakeGarmin.write_token_file and tokenstore:
            Path(tokenstore).write_text('{"token": "stored"}')
            os.chmod(tokenstore, 0o644)
        return (None, None)

    def _record(self, name: str, args: tuple = (), kwargs: dict | None = None):
        self.calls.append((name, args, kwargs or {}))
        if name in FakeGarmin.failures:
            raise FakeGarmin.failures[name]
        return FakeGarmin.responses.get(name, {})

    def get_stats(self, cdate: str):
        return self._record("get_stats", (cdate,))

    def get_sleep_data(self, cdate: str):
        return self._record("get_sleep_data", (cdate,))

    def get_heart_rates(self, cdate: str):
        return self._record("get_heart_rates", (cdate,))

    def get_stress_data(self, cdate: str):
        return self._record("get_stress_data", (cdate,))

    def get_body_battery(self, startdate: str, enddate: str | None = None):
        return self._record("get_body_battery", (startdate, enddate))

    def get_activities(
        self,
        start: int = 0,
        limit: int = 20,
        activitytype: str | None = None,
        activitysubtype: str | None = None,
    ):
        return self._record("get_activities", (), {"start": start, "limit": limit})

    def get_activity(self, activity_id: str):
        return self._record("get_activity", (activity_id,))


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> type[FakeGarmin]:
    monkeypatch.setattr(garmin, "Garmin", FakeGarmin)
    FakeGarmin.instances = []
    FakeGarmin.responses = {}
    FakeGarmin.failures = {}
    FakeGarmin.login_failure = None
    FakeGarmin.needs_mfa = False
    FakeGarmin.write_token_file = True
    return FakeGarmin


def _build(section: SectionConfig, tmp_log_file: Path):
    config = ServerConfig(
        log_file=tmp_log_file,
        log_level="WARNING",
        http_timeout=5.0,
        sections={"garmin": section},
    )
    return build_server(config)


async def _call(server, name: str, arguments: dict | None = None):
    async with Client(server) as client:
        return await client.call_tool(name, arguments or {})


def _token_file(section: SectionConfig) -> Path:
    return Path(section.data["token_store"]) / "garmin_tokens.json"


async def test_registers_all_tools_as_read_only(
    garmin_section: SectionConfig, fake_sdk: type[FakeGarmin], tmp_log_file: Path
) -> None:
    server = _build(garmin_section, tmp_log_file)
    async with Client(server) as client:
        listed = await client.list_tools()
    assert {tool.name for tool in listed.tools} == TOOL_NAMES
    for tool in listed.tools:
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.open_world_hint is True


async def test_authentication_is_lazy_and_reused(
    garmin_section: SectionConfig, fake_sdk: type[FakeGarmin], tmp_log_file: Path
) -> None:
    server = _build(garmin_section, tmp_log_file)
    assert fake_sdk.instances == []

    await _call(server, "garmin_get_daily_summary", {"date": "2026-09-01"})
    assert len(fake_sdk.instances) == 1
    assert fake_sdk.instances[0].logins == [str(_token_file(garmin_section))]

    await _call(server, "garmin_get_daily_summary", {"date": "2026-09-02"})
    assert len(fake_sdk.instances) == 1
    assert len(fake_sdk.instances[0].logins) == 1


@pytest.mark.parametrize(
    ("tool", "arguments", "method", "expected_args", "expected_kwargs"),
    [
        ("garmin_get_daily_summary", {"date": "2026-08-01"}, "get_stats", ("2026-08-01",), {}),
        ("garmin_get_sleep", {"date": "2026-08-02"}, "get_sleep_data", ("2026-08-02",), {}),
        ("garmin_get_heart_rate", {"date": "2026-08-03"}, "get_heart_rates", ("2026-08-03",), {}),
        ("garmin_get_stress", {"date": "2026-08-04"}, "get_stress_data", ("2026-08-04",), {}),
        (
            "garmin_get_body_battery",
            {"start_date": "2026-08-01", "end_date": "2026-08-05"},
            "get_body_battery",
            ("2026-08-01", "2026-08-05"),
            {},
        ),
        (
            "garmin_get_body_battery",
            {"start_date": "2026-08-01"},
            "get_body_battery",
            ("2026-08-01", "2026-08-01"),
            {},
        ),
        (
            "garmin_list_activities",
            {"start": 5, "limit": 10},
            "get_activities",
            (),
            {"start": 5, "limit": 10},
        ),
        ("garmin_list_activities", {}, "get_activities", (), {"start": 0, "limit": 20}),
        ("garmin_get_activity", {"activity_id": "12345"}, "get_activity", ("12345",), {}),
    ],
)
async def test_tool_maps_to_sdk_call(
    *,
    garmin_section: SectionConfig,
    fake_sdk: type[FakeGarmin],
    tmp_log_file: Path,
    tool: str,
    arguments: dict,
    method: str,
    expected_args: tuple,
    expected_kwargs: dict,
) -> None:
    list_methods = {"get_body_battery", "get_activities"}
    fake_sdk.responses[method] = [{"ok": True}] if method in list_methods else {"ok": True}
    result = await _call(_build(garmin_section, tmp_log_file), tool, arguments)
    assert not result.is_error
    assert fake_sdk.instances[0].calls == [(method, expected_args, expected_kwargs)]


@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("garmin_get_daily_summary", {"date": "2026-13-01"}, "YYYY-MM-DD"),
        ("garmin_get_daily_summary", {"date": "01-01-2026"}, "YYYY-MM-DD"),
        ("garmin_get_sleep", {"date": "2026-02-30"}, "real date"),
        ("garmin_get_heart_rate", {"date": "not-a-date"}, "YYYY-MM-DD"),
        ("garmin_get_stress", {"date": ""}, "YYYY-MM-DD"),
        (
            "garmin_get_body_battery",
            {"start_date": "2026-01-02", "end_date": "2026-01-01"},
            "after",
        ),
        (
            "garmin_get_body_battery",
            {"start_date": "2026-01-01", "end_date": "2026-03-01"},
            "31",
        ),
        ("garmin_list_activities", {"start": -1}, "start"),
        ("garmin_list_activities", {"limit": 0}, "limit"),
        ("garmin_list_activities", {"limit": 101}, "limit"),
        ("garmin_get_activity", {"activity_id": "0"}, "positive"),
        ("garmin_get_activity", {"activity_id": "-1"}, "positive"),
        ("garmin_get_activity", {"activity_id": "12/34"}, "positive"),
        ("garmin_get_activity", {"activity_id": ""}, "positive"),
        ("garmin_get_activity", {"activity_id": "9" * 21}, "positive"),
    ],
)
async def test_invalid_input_is_rejected_before_the_sdk(
    *,
    garmin_section: SectionConfig,
    fake_sdk: type[FakeGarmin],
    tmp_log_file: Path,
    tool: str,
    arguments: dict,
    message: str,
) -> None:
    result = await _call(_build(garmin_section, tmp_log_file), tool, arguments)
    assert result.is_error
    assert message in str(result)
    assert fake_sdk.instances == []


async def test_body_battery_accepts_a_full_31_day_range(
    garmin_section: SectionConfig, fake_sdk: type[FakeGarmin], tmp_log_file: Path
) -> None:
    fake_sdk.responses["get_body_battery"] = [{"date": "2026-01-31"}]
    result = await _call(
        _build(garmin_section, tmp_log_file),
        "garmin_get_body_battery",
        {"start_date": "2026-01-01", "end_date": "2026-01-31"},
    )
    assert not result.is_error
    assert fake_sdk.instances[0].calls == [
        ("get_body_battery", ("2026-01-01", "2026-01-31"), {})
    ]


def test_activity_window_rejects_booleans() -> None:
    with pytest.raises(garmin.GarminError):
        garmin._validate_activity_window(True, 5)
    with pytest.raises(garmin.GarminError):
        garmin._validate_activity_window(0, True)


def test_activity_window_accepts_upper_bound() -> None:
    assert garmin._validate_activity_window(0, 100) == (0, 100)


@pytest.mark.parametrize(
    ("tool", "arguments", "method", "is_list"),
    [
        ("garmin_get_daily_summary", {"date": "2026-09-01"}, "get_stats", False),
        ("garmin_get_sleep", {"date": "2026-09-01"}, "get_sleep_data", False),
        ("garmin_get_heart_rate", {"date": "2026-09-01"}, "get_heart_rates", False),
        ("garmin_get_stress", {"date": "2026-09-01"}, "get_stress_data", False),
        (
            "garmin_get_body_battery",
            {"start_date": "2026-09-01"},
            "get_body_battery",
            True,
        ),
        ("garmin_list_activities", {}, "get_activities", True),
        ("garmin_get_activity", {"activity_id": "12345"}, "get_activity", False),
    ],
)
async def test_every_tool_redacts_all_credentials(
    *,
    garmin_section: SectionConfig,
    fake_sdk: type[FakeGarmin],
    tmp_log_file: Path,
    tool: str,
    arguments: dict,
    method: str,
    is_list: bool,
) -> None:
    record = {
        "email": "garmin-user@example.com",
        "note": "access=access-token-sentinel refresh=refresh-token-sentinel",
        "cookie_note": "cookie-sentinel",
        "token_store_path": garmin_section.data["token_store"],
        "nested": [{"password": "garmin-secret-pw", "csrf": "csrf-token-sentinel"}],
    }
    fake_sdk.responses[method] = [record] if is_list else record
    result = await _call(_build(garmin_section, tmp_log_file), tool, arguments)
    text = str(result)
    assert not result.is_error
    assert "<redacted>" in text
    sentinels = {
        "garmin-user@example.com",
        "garmin-secret-pw",
        "access-token-sentinel",
        "refresh-token-sentinel",
        "csrf-token-sentinel",
        "cookie-sentinel",
        garmin_section.data["token_store"],
    }
    for sentinel in sentinels:
        assert sentinel not in text


async def test_credential_like_keys_are_redacted_even_when_unknown(
    garmin_section: SectionConfig, fake_sdk: type[FakeGarmin], tmp_log_file: Path
) -> None:
    fake_sdk.responses["get_stats"] = {
        "access_token": "unconfigured-token-value",
        "refreshToken": "unconfigured-refresh-value",
        "cookie": "unconfigured-cookie-value",
        "ticket": "unconfigured-ticket-value",
        "steps": 1200,
    }
    result = await _call(
        _build(garmin_section, tmp_log_file),
        "garmin_get_daily_summary",
        {"date": "2026-09-01"},
    )
    text = str(result)
    assert not result.is_error
    assert "unconfigured-token-value" not in text
    assert "unconfigured-refresh-value" not in text
    assert "unconfigured-cookie-value" not in text
    assert "unconfigured-ticket-value" not in text
    assert "1200" in text


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (GarminConnectAuthenticationError("secret auth detail"), "garmin-login"),
        (GarminConnectTooManyRequestsError("secret rate detail"), "rate limit"),
        (GarminConnectNotFoundError("secret not found detail"), "was not found"),
        (GarminConnectConnectionError("secret connection detail"), "request failed"),
        (ValueError("secret value detail"), "request failed"),
    ],
)
async def test_sdk_failures_map_to_safe_messages(
    garmin_section: SectionConfig,
    fake_sdk: type[FakeGarmin],
    tmp_log_file: Path,
    failure: Exception,
    message: str,
) -> None:
    fake_sdk.failures["get_stats"] = failure
    result = await _call(
        _build(garmin_section, tmp_log_file),
        "garmin_get_daily_summary",
        {"date": "2026-09-01"},
    )
    text = str(result)
    assert result.is_error
    assert message in text
    assert "secret" not in text


@pytest.mark.parametrize(
    ("login_failure", "message"),
    [
        (
            GarminConnectAuthenticationError("MFA Required but no prompt_mfa mechanism supplied"),
            "garmin-login",
        ),
        (GarminConnectTooManyRequestsError("rate detail"), "rate limit"),
        (GarminConnectConnectionError("connection detail"), "request failed"),
    ],
)
async def test_login_failures_map_to_safe_messages(
    garmin_section: SectionConfig,
    fake_sdk: type[FakeGarmin],
    tmp_log_file: Path,
    login_failure: Exception,
    message: str,
) -> None:
    fake_sdk.login_failure = login_failure
    result = await _call(
        _build(garmin_section, tmp_log_file),
        "garmin_get_daily_summary",
        {"date": "2026-09-01"},
    )
    text = str(result)
    assert result.is_error
    assert message in text
    assert "prompt_mfa" not in text
    assert "detail" not in text


async def test_server_login_never_prompts_for_mfa(
    garmin_section: SectionConfig,
    fake_sdk: type[FakeGarmin],
    monkeypatch: pytest.MonkeyPatch,
    tmp_log_file: Path,
) -> None:
    fake_sdk.needs_mfa = True

    def forbidden_prompt() -> str:
        raise AssertionError("the MCP server must never prompt for MFA")

    monkeypatch.setattr(garmin.getpass, "getpass", forbidden_prompt)
    result = await _call(
        _build(garmin_section, tmp_log_file),
        "garmin_get_daily_summary",
        {"date": "2026-09-01"},
    )
    assert result.is_error
    assert "garmin-login" in str(result)


@pytest.mark.parametrize("value", [None, "text", 42, [1, 2]])
async def test_unexpected_dict_shapes_are_rejected(
    garmin_section: SectionConfig,
    fake_sdk: type[FakeGarmin],
    tmp_log_file: Path,
    value: object,
) -> None:
    fake_sdk.responses["get_stats"] = value
    result = await _call(
        _build(garmin_section, tmp_log_file),
        "garmin_get_daily_summary",
        {"date": "2026-09-01"},
    )
    assert result.is_error
    assert "unexpected response shape" in str(result)


@pytest.mark.parametrize("value", [None, {"a": 1}, [None], ["x"], [{"ok": True}, 1]])
async def test_unexpected_list_shapes_are_rejected(
    garmin_section: SectionConfig,
    fake_sdk: type[FakeGarmin],
    tmp_log_file: Path,
    value: object,
) -> None:
    fake_sdk.responses["get_activities"] = value
    result = await _call(_build(garmin_section, tmp_log_file), "garmin_list_activities", {})
    assert result.is_error
    assert "unexpected response shape" in str(result)


async def test_empty_activity_list_is_accepted(
    garmin_section: SectionConfig, fake_sdk: type[FakeGarmin], tmp_log_file: Path
) -> None:
    fake_sdk.responses["get_activities"] = []
    result = await _call(_build(garmin_section, tmp_log_file), "garmin_list_activities", {})
    assert not result.is_error


async def test_oversized_response_is_rejected(
    garmin_section: SectionConfig, fake_sdk: type[FakeGarmin], tmp_log_file: Path
) -> None:
    fake_sdk.responses["get_stats"] = {"blob": "x" * (1024 * 1024 + 1)}
    result = await _call(
        _build(garmin_section, tmp_log_file),
        "garmin_get_daily_summary",
        {"date": "2026-09-01"},
    )
    assert result.is_error
    assert "1 MiB" in str(result)


def test_concurrent_first_calls_log_in_only_once(
    garmin_section: SectionConfig, fake_sdk: type[FakeGarmin]
) -> None:
    client = garmin._GarminClient(garmin_section)
    results: list[dict] = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            results.append(client.call_dict("get_stats", "2026-09-01"))
        except Exception as exc:  # pragma: no cover - failure path assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len(results) == 4
    assert len(fake_sdk.instances) == 1
    assert len(fake_sdk.instances[0].logins) == 1


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are not available")
async def test_token_directory_and_file_are_secured(
    garmin_section: SectionConfig, fake_sdk: type[FakeGarmin], tmp_log_file: Path
) -> None:
    await _call(
        _build(garmin_section, tmp_log_file),
        "garmin_get_daily_summary",
        {"date": "2026-09-01"},
    )
    token_dir = Path(garmin_section.data["token_store"])
    token_file = _token_file(garmin_section)
    assert stat.S_IMODE(token_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are not available")
def test_token_file_symlink_is_rejected(
    garmin_section: SectionConfig, fake_sdk: type[FakeGarmin]
) -> None:
    token_dir = Path(garmin_section.data["token_store"])
    token_dir.mkdir(parents=True)
    os.chmod(token_dir, 0o700)
    target = token_dir / "elsewhere.json"
    target.write_text("{}")
    os.chmod(target, 0o600)
    _token_file(garmin_section).symlink_to(target)
    with pytest.raises(ConfigError, match="symlink"):
        garmin.login_interactively(garmin_section)


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are not available")
def test_token_directory_symlink_is_rejected(
    garmin_section: SectionConfig, fake_sdk: type[FakeGarmin], tmp_path: Path
) -> None:
    token_dir = Path(garmin_section.data["token_store"])
    token_dir.parent.mkdir(parents=True)
    real = tmp_path / "real-garmin"
    real.mkdir(mode=0o700)
    token_dir.symlink_to(real)
    with pytest.raises(ConfigError, match="symlink"):
        garmin.login_interactively(garmin_section)


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are not available")
def test_token_file_mode_correction_failure_is_rejected(
    garmin_section: SectionConfig, fake_sdk: type[FakeGarmin], monkeypatch: pytest.MonkeyPatch
) -> None:
    token_dir = Path(garmin_section.data["token_store"])
    token_dir.mkdir(parents=True)
    os.chmod(token_dir, 0o700)
    token_file = _token_file(garmin_section)
    token_file.write_text("{}")
    os.chmod(token_file, 0o644)

    def fail_chmod(path: str, mode: int) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(os, "chmod", fail_chmod)
    with pytest.raises(ConfigError, match="could not be changed"):
        garmin.login_interactively(garmin_section)


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership is not available")
def test_token_file_with_foreign_owner_is_rejected(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = fake_home / "garmin_tokens.json"
    path.write_text("{}")
    os.chmod(path, 0o600)
    monkeypatch.setattr(os, "getuid", lambda: os.stat(path).st_uid + 1)
    with pytest.raises(ConfigError, match="owned by"):
        garmin._secure_token_file(path, required=True)


async def test_token_file_not_created_is_rejected(
    garmin_section: SectionConfig, fake_sdk: type[FakeGarmin], tmp_log_file: Path
) -> None:
    fake_sdk.write_token_file = False
    result = await _call(
        _build(garmin_section, tmp_log_file),
        "garmin_get_daily_summary",
        {"date": "2026-09-01"},
    )
    assert result.is_error
    assert "token store" in str(result)


@pytest.mark.parametrize("token_store", ["relative/tokens", "~/../tokens"])
def test_invalid_token_store_paths_are_rejected(
    fake_home: Path, token_store: str
) -> None:
    section = SectionConfig(
        name="garmin",
        data={"email": "u@example.com", "password": "p", "token_store": token_store},
        http_timeout=5.0,
        verify_tls=True,
    )
    with pytest.raises(ConfigError):
        garmin._validate_section(section)


def test_sdk_logging_is_silenced_and_does_not_propagate(
    garmin_section: SectionConfig, fake_sdk: type[FakeGarmin]
) -> None:
    garmin._make_sdk(garmin_section)
    for name in ("garminconnect", "garminconnect.client"):
        sdk_logger = logging.getLogger(name)
        assert sdk_logger.level == logging.CRITICAL
        assert sdk_logger.propagate is False


def test_inline_token_json_is_rejected(fake_home: Path) -> None:
    section = SectionConfig(
        name="garmin",
        data={
            "email": "u@example.com",
            "password": "p",
            "token_store": '{"refresh_token": "x"}',
        },
        http_timeout=5.0,
        verify_tls=True,
    )
    with pytest.raises(ConfigError, match="inline token JSON"):
        garmin._validate_section(section)


def test_verify_tls_false_is_rejected(fake_home: Path) -> None:
    section = SectionConfig(
        name="garmin",
        data={"email": "u@example.com", "password": "p"},
        http_timeout=5.0,
        verify_tls=False,
    )
    with pytest.raises(ConfigError, match="verify_tls"):
        garmin._validate_section(section)


@pytest.mark.parametrize("email", ["", "no-at-sign", "with space@example.com"])
def test_invalid_email_is_rejected(fake_home: Path, email: str) -> None:
    section = SectionConfig(
        name="garmin",
        data={"email": email, "password": "p"},
        http_timeout=5.0,
        verify_tls=True,
    )
    with pytest.raises(ConfigError, match="email"):
        garmin._validate_section(section)


def test_empty_password_is_rejected(fake_home: Path) -> None:
    section = SectionConfig(
        name="garmin",
        data={"email": "u@example.com", "password": "   "},
        http_timeout=5.0,
        verify_tls=True,
    )
    with pytest.raises(ConfigError, match="password"):
        garmin._validate_section(section)


def _write_garmin_config(path: Path, token_store: str) -> None:
    path.write_text(
        "[server]\n"
        'log_level = "WARNING"\n'
        "[garmin]\n"
        'email = "garmin-user@example.com"\n'
        'password = "garmin-secret-pw"\n'
        f'token_store = "{token_store}"\n'
    )
    os.chmod(path, 0o600)


def test_garmin_login_cli_stores_tokens_securely(
    fake_home: Path,
    fake_sdk: type[FakeGarmin],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token_store = fake_home / ".mcps" / "garmin"
    config_path = tmp_path / "config.toml"
    _write_garmin_config(config_path, str(token_store))

    code = main(["--config", str(config_path), "garmin-login"])

    captured = capsys.readouterr()
    assert code == 0
    assert "tokens were stored securely" in captured.err
    assert "garmin-secret-pw" not in captured.err
    assert captured.out == ""
    token_file = token_store / "garmin_tokens.json"
    assert token_file.exists()
    if os.name == "posix":
        assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


def test_garmin_login_cli_prompts_for_mfa(
    fake_home: Path,
    fake_sdk: type[FakeGarmin],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_sdk.needs_mfa = True
    prompts: list[str] = []
    monkeypatch.setattr(
        garmin.getpass, "getpass", lambda prompt: prompts.append(prompt) or "123456"
    )
    config_path = tmp_path / "config.toml"
    _write_garmin_config(config_path, str(fake_home / ".mcps" / "garmin"))

    code = main(["--config", str(config_path), "garmin-login"])

    assert code == 0
    assert prompts == ["Garmin MFA code: "]
    assert fake_sdk.instances[0].mfa_code == "123456"


def test_garmin_login_cli_reports_failure_without_details(
    fake_home: Path,
    fake_sdk: type[FakeGarmin],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_sdk.login_failure = GarminConnectAuthenticationError("very-secret-detail")
    config_path = tmp_path / "config.toml"
    _write_garmin_config(config_path, str(fake_home / ".mcps" / "garmin"))

    code = main(["--config", str(config_path), "garmin-login"])

    captured = capsys.readouterr()
    assert code == 2
    assert "authentication failed" in captured.err
    assert "very-secret-detail" not in captured.err
    assert "garmin-secret-pw" not in captured.err
    assert captured.out == ""


def test_garmin_login_cli_reports_rate_limit_safely(
    fake_home: Path,
    fake_sdk: type[FakeGarmin],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_sdk.login_failure = GarminConnectTooManyRequestsError("rate-limit-detail")
    config_path = tmp_path / "config.toml"
    _write_garmin_config(config_path, str(fake_home / ".mcps" / "garmin"))

    code = main(["--config", str(config_path), "garmin-login"])

    captured = capsys.readouterr()
    assert code == 2
    assert "rate limit" in captured.err
    assert "rate-limit-detail" not in captured.err


def test_garmin_login_cli_requires_a_garmin_section(
    fake_home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[server]\nlog_level = "WARNING"\n')
    os.chmod(config_path, 0o600)

    code = main(["--config", str(config_path), "garmin-login"])

    captured = capsys.readouterr()
    assert code == 2
    assert "no [garmin] section" in captured.err
    assert captured.out == ""
