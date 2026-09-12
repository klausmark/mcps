"""Flat-text logging setup and the `log_call` decorator used on every tool."""

from __future__ import annotations

import functools
import inspect
import logging
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from mcps.errors import ToolError

LOGGER_NAME = "mcps"

ALLOWED_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
DEFAULT_LEVEL = "INFO"

CREDENTIAL_PARAM_RE = re.compile(
    r"(token|password|api_key|apikey|secret|credential|.*_token|.*_key)",
    re.IGNORECASE,
)


def is_credential_key(name: str) -> bool:
    return bool(CREDENTIAL_PARAM_RE.fullmatch(name))


class _PlainFormatter(logging.Formatter):
    """Format records as: `<timestamp> <LEVEL> <category> <message>`."""

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        level = record.levelname.ljust(5)
        category = getattr(record, "category", "system").ljust(7)
        return f"{ts} {level} {category} {record.getMessage()}"


class ResilientFileHandler(logging.FileHandler):
    """FileHandler that survives log rotation, deletion, and missing parent dirs.

    On every emit, the file's `(st_dev, st_ino)` is compared against the handle
    we opened with. If the path no longer exists — or has been replaced by a
    new inode (the typical logrotate pattern) — the stream is closed, the
    parent directory is recreated if needed, and the file is reopened.

    Hard `OSError` during reopen is dropped on the floor: the next emit will
    retry. This avoids cascading failures when the log target is temporarily
    unavailable (e.g. filesystem unmounted).
    """

    def __init__(self, path: Path, **kwargs: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(path, **kwargs)
        self._cache_identity()

    def _cache_identity(self) -> None:
        st = os.fstat(self.stream.fileno())
        self._dev = st.st_dev
        self._ino = st.st_ino

    def _reopen(self) -> None:
        if self.stream is not None:
            self.stream.close()
        Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
        self.stream = self._open()
        self._cache_identity()

    def _ensure_open(self) -> None:
        try:
            st = os.stat(self.baseFilename)
        except FileNotFoundError:
            self._reopen()
            return
        if (st.st_dev, st.st_ino) != (self._dev, self._ino):
            self._reopen()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._ensure_open()
        except OSError:
            # Best-effort: surface once to stderr, then drop the record.
            # The next emit will retry.
            sys.stderr.write(
                f"mcps: log file {self.baseFilename} became unavailable; "
                "dropping record until next successful reopen\n"
            )
            return
        super().emit(record)


def configure_logging(path: Path, level: str) -> logging.Logger:
    """Configure the root `mcps` logger to write flat text to `path` or stderr."""
    normalized = level.upper()
    if normalized not in ALLOWED_LEVELS:
        raise ValueError(f"log level must be one of {ALLOWED_LEVELS}, got {level!r}")
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(normalized)
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
    handler = _open_handler(path)
    handler.setFormatter(_PlainFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def _open_handler(path: Path) -> logging.Handler:
    try:
        return ResilientFileHandler(path, encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"mcps: cannot open log file {path}: {exc}; falling back to stderr\n")
        return logging.StreamHandler(sys.stderr)


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def _preview(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, bool):
        return repr(value)
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return f"str({len(value)})"
    if isinstance(value, (list, tuple)):
        return f"{type(value).__name__}({len(value)})"
    if isinstance(value, dict):
        return f"dict({len(value)})"
    return type(value).__name__


def _redact_summary(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    param_names: tuple[str, ...],
) -> str:
    parts: list[str] = []
    for index, value in enumerate(args):
        name = param_names[index] if index < len(param_names) else f"arg{index}"
        parts.append(_render_kv(name, value))
    for key, value in kwargs.items():
        parts.append(_render_kv(key, value))
    return " ".join(parts) if parts else "-"


def _render_kv(name: str, value: Any) -> str:
    if is_credential_key(name):
        return f"{name}=<redacted>"
    return f"{name}={_preview(value)}"


P = ParamSpec("P")
R = TypeVar("R")


def log_call(tool_name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator that logs every invocation: args (redacted), duration, success/failure."""

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        params = tuple(inspect.signature(fn).parameters.keys())

        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            logger = get_logger()
            start = time.monotonic()
            summary = _redact_summary(args, kwargs, params)
            logger.info(
                "tool %s args=%s",
                tool_name,
                summary,
                extra={"category": "tool"},
            )
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                duration_ms = int((time.monotonic() - start) * 1000)
                logger.error(
                    "tool %s ok=false duration_ms=%d error_type=%s",
                    tool_name,
                    duration_ms,
                    type(exc).__name__,
                    extra={"category": "tool"},
                )
                if isinstance(exc, ToolError):
                    raise
                # Unexpected exceptions can include credentials in their messages.
                raise ToolError("Tool call failed") from None
            else:
                duration_ms = int((time.monotonic() - start) * 1000)
                logger.info(
                    "tool %s ok=true duration_ms=%d",
                    tool_name,
                    duration_ms,
                    extra={"category": "tool"},
                )
                return result

        return wrapper

    return decorator
