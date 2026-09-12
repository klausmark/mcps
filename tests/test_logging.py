"""Tests for `mcps.logging_setup`."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import pytest

from mcps.errors import ToolError
from mcps.logging_setup import (
    ALLOWED_LEVELS,
    ResilientFileHandler,
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


def test_configure_logging_accepts_debug(tmp_path: Path) -> None:
    logger = configure_logging(tmp_path / "x.log", "DEBUG")
    assert logger.level == logging.DEBUG


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

    with pytest.raises(ToolError, match="Tool call failed"):
        boom()
    for handler in get_logger_handlers():
        handler.flush()
    content = log_path.read_text()
    assert "boom" in content
    assert "ok=false" in content
    assert "RuntimeError" in content
    assert "kaboom" not in content


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
    assert "DEBUG" in ALLOWED_LEVELS
    assert "INFO" in ALLOWED_LEVELS


def test_resilient_handler_creates_parent_dir(tmp_path: Path) -> None:
    log_path = tmp_path / "deep" / "nested" / "mcps.log"
    handler = ResilientFileHandler(log_path, encoding="utf-8")
    try:
        assert log_path.parent.is_dir()
    finally:
        handler.close()


def test_resilient_handler_recreates_file_after_unlink(tmp_path: Path) -> None:
    log_path = tmp_path / "mcps.log"
    logger = configure_logging(log_path, "INFO")
    logger.info("first")
    for h in logger.handlers:
        h.flush()
    assert "first" in log_path.read_text()

    log_path.unlink()
    assert not log_path.exists()

    logger.info("second")
    for h in logger.handlers:
        h.flush()
    assert log_path.exists()
    content = log_path.read_text()
    assert "first" not in content
    assert "second" in content


def test_resilient_handler_reopens_after_rotation(tmp_path: Path) -> None:
    log_path = tmp_path / "mcps.log"
    logger = configure_logging(log_path, "INFO")
    logger.info("before")
    for h in logger.handlers:
        h.flush()

    rotated = tmp_path / "mcps.log.1"
    os.rename(log_path, rotated)
    log_path.write_text("")  # logrotate creates a fresh empty file

    logger.info("after")
    for h in logger.handlers:
        h.flush()

    assert "after" in log_path.read_text()
    assert "before" not in log_path.read_text()
    assert "before" in rotated.read_text()


def test_resilient_handler_recreates_parent_dir(tmp_path: Path) -> None:
    log_dir = tmp_path / "logdir"
    log_path = log_dir / "mcps.log"
    logger = configure_logging(log_path, "INFO")
    logger.info("seed")
    for h in logger.handlers:
        h.flush()

    shutil.rmtree(log_dir)
    assert not log_dir.exists()

    logger.info("reborn")
    for h in logger.handlers:
        h.flush()

    assert log_dir.is_dir()
    assert log_path.exists()
    assert "reborn" in log_path.read_text()


def test_resilient_handler_drops_record_when_reopen_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    log_path = tmp_path / "mcps.log"
    logger = configure_logging(log_path, "INFO")
    logger.info("before")
    for h in logger.handlers:
        h.flush()

    # Make the handler believe the file is gone so the next emit triggers a reopen.
    log_path.unlink()

    # Force _reopen to fail so the next emit must drop the record.
    def boom(self: ResilientFileHandler) -> None:
        raise OSError("simulated")

    monkeypatch.setattr(ResilientFileHandler, "_reopen", boom)

    logger.info("after")
    for h in logger.handlers:
        h.flush()
    captured = capsys.readouterr()
    assert "became unavailable" in captured.err

    # The "after" record must not be in the file (and the file should not have
    # been recreated since the reopen was forced to fail).
    assert not log_path.exists() or "after" not in log_path.read_text()
