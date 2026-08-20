"""Flat-text logging setup and the `log_call` decorator used on every tool."""

from __future__ import annotations

import functools
import inspect
import logging
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

LOGGER_NAME = "mcps"

ALLOWED_LEVELS = ("WARNING", "INFO", "ERROR", "CRITICAL")
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


def configure_logging(path: Path | None, level: str) -> logging.Logger:
    """Configure the root `mcps` logger to write flat text to file or stderr."""
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


def _open_handler(path: Path | None) -> logging.Handler:
    if path is None:
        return logging.StreamHandler(sys.stderr)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return logging.FileHandler(path, encoding="utf-8")
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
                    "tool %s ok=false duration_ms=%d error=%r",
                    tool_name,
                    duration_ms,
                    exc,
                    extra={"category": "tool"},
                )
                raise
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
