"""Validation of model-supplied URL path parameters."""

import re

from mcps.errors import ToolError

MAX_PARAMETER_LENGTH = 512


def validate_path_parameter(value: str, field_name: str, *, pattern: str = r"[\w-]+") -> str:
    """Accept one identifier, never URL syntax or encoded path separators."""
    if len(value) > MAX_PARAMETER_LENGTH or not re.fullmatch(pattern, value):
        raise ToolError(f"{field_name} has an invalid identifier format")
    return value
