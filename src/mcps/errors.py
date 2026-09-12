"""Errors whose messages are safe to return to MCP clients."""


class ToolError(RuntimeError):
    """A public error containing no upstream details or credential values."""
