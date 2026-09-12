"""Credential redaction shared by responses, logs, and public error messages."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

REDACTION_MARKER = "<redacted>"


def compile_credentials(values: Iterable[str]) -> re.Pattern[str] | None:
    """Compile one longest-first regex matching every non-empty credential."""
    unique = sorted({value for value in values if value}, key=len, reverse=True)
    if not unique:
        return None
    return re.compile("|".join(re.escape(value) for value in unique))


def safe_marker(pattern: re.Pattern[str] | None) -> str:
    """Return a redaction marker that cannot itself contain a credential."""
    if pattern is None:
        return REDACTION_MARKER
    return pattern.sub("", REDACTION_MARKER)


def redact_text(text: str, values: Iterable[str], *, marker: str = "") -> str:
    """Remove credentials from free text, e.g. a public error message."""
    pattern = compile_credentials(values)
    if pattern is None:
        return text
    return pattern.sub(pattern.sub("", marker), text)


def sanitize(data: Any, credentials: Iterable[str]) -> Any:
    """Recursively replace any occurrence of a credential with the marker.

    Defense in depth: even if an upstream server echoes a token in its JSON
    body, the model never sees the value.
    """
    pattern = compile_credentials(credentials)
    if pattern is None:
        return data
    marker = safe_marker(pattern)

    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {redact(key): redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(redact(item) for item in value)
        if isinstance(value, str):
            return pattern.sub(marker, value)
        if isinstance(value, bool):
            return marker if pattern.search("true" if value else "false") else value
        if value is None:
            return marker if pattern.search("null") else value
        if isinstance(value, (int, float)) and pattern.search(str(value)):
            return marker
        return value

    return redact(data)
