"""Tests for `mcps.logging_setup`."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcps.logging_setup import (
    ALLOWED_LEVELS,
    configure_logging,
    get_logger,
    is_credential_key,
    log_call,
)


def get_logger_handlers():
    return get_logger().handlers


def test_is_credential_key_matches_known_patterns() -> None:
    assert is_credential_key("token")
    assert is_credential_key("api_key")
    assert is_credential_key("auth_token")
    assert is_credential_key("password")
    assert is_credential_key("secret")
    assert not is_credential_key("name")
    assert not is_credential_key("limit")
    assert not is_credential_key("domain")


def test_configure_logging_rejects_debug(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="log level"):
        configure_logging(tmp_path / "x.log", "DEBUG")


def test_configure_logging_creates_file(tmp_path: Path) -> None:
    log_path = tmp_path / "subdir" / "mcps.log"
    logger = configure_logging(log_path, "INFO")
    logger.info("hello")
    for handler in logger.handlers:
        handler.flush()
    content = log_path.read_text()
    assert "INFO" in content
    assert "hello" in content


def test_configure_logging_falls_back_to_stderr(tmp_path: Path, capsys) -> None:
    """An unwritable path must fall back to stderr rather than crash."""
    # Use a path that cannot be created (a file treated as a parent directory).
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    bad_path = blocker / "mcps.log"
    logger = configure_logging(bad_path, "INFO")
    logger.info("hello")
    captured = capsys.readouterr()
    assert "cannot open log file" in captured.err
    assert "hello" in captured.err


def test_log_call_redacts_credential_args(tmp_path: Path) -> None:
    log_path = tmp_path / "log"
    configure_logging(log_path, "INFO")

    @log_call("fake_tool")
    def fake(token: str, name: str) -> str:
        return f"{name}={token}"

    fake(token="TOPSECRET", name="alice")
    for handler in get_logger_handlers():
        handler.flush()
    content = log_path.read_text()
    assert "TOPSECRET" not in content
    assert "<redacted>" in content
    assert "name=alice" in content or "name=str(5)" in content


def test_log_call_logs_duration_and_error(tmp_path: Path) -> None:
    log_path = tmp_path / "log"
    configure_logging(log_path, "INFO")

    @log_call("boom")
    def boom() -> None:
        raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError, match="kaboom"):
        boom()
    for handler in get_logger_handlers():
        handler.flush()
    content = log_path.read_text()
    assert "boom" in content
    assert "ok=false" in content
    assert "kaboom" in content


def test_log_call_records_ok_true_on_success(tmp_path: Path) -> None:
    log_path = tmp_path / "log"
    configure_logging(log_path, "INFO")

    @log_call("ok_tool")
    def ok_tool() -> str:
        return "x"

    ok_tool()
    content = log_path.read_text()
    assert "ok=true" in content
    assert "ok_tool" in content


def test_levels_constant() -> None:
    assert "DEBUG" not in ALLOWED_LEVELS
    assert "INFO" in ALLOWED_LEVELS
