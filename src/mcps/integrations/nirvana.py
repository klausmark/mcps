"""NirvanaHQ API integration."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import replace

import httpx
from mcp.server import MCPServer

from mcps.config import SectionConfig
from mcps.http_client import request_json, warn_if_tls_disabled
from mcps.logging_setup import log_call
from mcps.responses import expect_dict, expect_items
from mcps.validation import validate_path_parameter

NAME = "nirvana"
REQUIRED_KEYS = ("url", "email", "password")


def basic_token(data: Mapping[str, str]) -> str:
    """Return the actual credential sent on the wire, also needed for redaction."""
    raw = f"{data['email']}:{data['password']}".encode()
    return base64.b64encode(raw).decode("ascii")


def _apply_auth(client: httpx.Client, data: Mapping[str, str]) -> None:
    client.headers["Authorization"] = f"Basic {basic_token(data)}"


def _call(section: SectionConfig, method: str, path: str, **kwargs) -> object:
    return request_json(section, method, path, apply_auth=_apply_auth, **kwargs)


def register(server: MCPServer, section: SectionConfig) -> None:
    section = replace(
        section, redaction_values=(*section.redaction_values, basic_token(section.data))
    )
    warn_if_tls_disabled(section)

    @server.tool(name="nirvana_list_tasks", description="List NirvanaHQ tasks.")
    @log_call("nirvana_list_tasks")
    def list_tasks() -> list[dict]:
        """Return all tasks in the user's NirvanaHQ account."""
        return expect_items(_call(section, "GET", "/2.0/tasks"), label="NirvanaHQ")

    @server.tool(name="nirvana_get_task", description="Get one NirvanaHQ task by id.")
    @log_call("nirvana_get_task")
    def get_task(id: str) -> dict:
        """Fetch a single task by its NirvanaHQ id."""
        validate_path_parameter(id, "id")
        return expect_dict(_call(section, "GET", f"/2.0/tasks/{id}"), label="NirvanaHQ")

    @server.tool(name="nirvana_complete_task", description="Mark a NirvanaHQ task as completed.")
    @log_call("nirvana_complete_task")
    def complete_task(id: str) -> dict:
        """Complete (close) a task by id."""
        validate_path_parameter(id, "id")
        return expect_dict(_call(section, "POST", f"/2.0/tasks/{id}/completed"), label="NirvanaHQ")

    @server.tool(name="nirvana_add_task", description="Add a task to NirvanaHQ.")
    @log_call("nirvana_add_task")
    def add_task(name: str, bucket: str | None = None) -> dict:
        """Create a new task; optionally place it in a named bucket."""
        payload: dict = {"name": name}
        if bucket is not None:
            payload["bucket"] = bucket
        return expect_dict(_call(section, "POST", "/2.0/tasks", json=payload), label="NirvanaHQ")
