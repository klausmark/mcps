"""Validation helpers for upstream response shapes.

Unexpected shapes raise a safe `ToolError` instead of silently returning an
empty result, so a contract change is visible rather than mistaken for no data.
"""

from __future__ import annotations

from typing import Any

from mcps.errors import ToolError


def expect_dict(data: Any, *, label: str) -> dict:
    if not isinstance(data, dict):
        raise ToolError(f"{label} returned an unexpected response shape")
    return data


def expect_list(data: Any, *, label: str) -> list[dict]:
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise ToolError(f"{label} returned an unexpected response shape")
    return data


def expect_items(data: Any, *, label: str) -> list[dict]:
    """Accept a bare list or an `{"items": [...]}` envelope."""
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    return expect_list(data, label=label)
