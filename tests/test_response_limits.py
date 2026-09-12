"""Bounded streaming behavior of the shared HTTP reader."""

from __future__ import annotations

import gzip
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from mcp import Client

from mcps.config import SectionConfig, ServerConfig
from mcps.errors import ToolError
from mcps.http_client import MAX_RESPONSE_BYTES, read_bounded_body
from mcps.logging_setup import configure_logging
from mcps.server import build_server

REQUEST = httpx.Request("GET", "http://test/api")


class RecordingStream(httpx.SyncByteStream):
    """Yields chunks and records how many were consumed before closing."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.yielded = 0
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self._chunks:
            self.yielded += 1
            yield chunk

    def close(self) -> None:
        self.closed = True


def _response(
    chunks: list[bytes] | None = None,
    *,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[httpx.Response, RecordingStream | None]:
    stream = RecordingStream(chunks) if chunks is not None else None
    response = httpx.Response(
        200, content=content, stream=stream, headers=headers, request=REQUEST
    )
    return response, stream


def test_body_under_limit_is_returned() -> None:
    response, _ = _response([b"a" * 10])
    assert read_bounded_body(response, label="x") == b"a" * 10


def test_body_exactly_at_limit_is_allowed() -> None:
    response, stream = _response([b"a" * MAX_RESPONSE_BYTES])
    body = read_bounded_body(response, label="x")
    assert len(body) == MAX_RESPONSE_BYTES
    assert stream is not None
    assert stream.yielded == 1


def test_body_over_limit_stops_reading_early() -> None:
    stream_chunks = [b"a" * MAX_RESPONSE_BYTES, b"b", b"c"]
    response, stream = _response(stream_chunks)
    with pytest.raises(ToolError, match="1 MiB"):
        read_bounded_body(response, label="x")
    assert stream is not None
    assert stream.yielded == 2


def test_declared_too_large_is_rejected_before_reading() -> None:
    response, stream = _response(
        [b"a"], headers={"Content-Length": str(MAX_RESPONSE_BYTES + 1)}
    )
    with pytest.raises(ToolError, match="1 MiB"):
        read_bounded_body(response, label="x")
    assert stream is not None
    assert stream.yielded == 0


def test_single_oversized_chunk_is_rejected_before_copying() -> None:
    response, stream = _response([b"a" * (MAX_RESPONSE_BYTES + 1)])
    with pytest.raises(ToolError, match="1 MiB"):
        read_bounded_body(response, label="x")
    assert stream is not None
    assert stream.yielded == 1


def test_misleading_small_content_length_is_still_enforced() -> None:
    response, _ = _response(
        [b"a" * MAX_RESPONSE_BYTES, b"b"], headers={"Content-Length": "1"}
    )
    with pytest.raises(ToolError, match="1 MiB"):
        read_bounded_body(response, label="x")


def test_valid_content_length_is_accepted() -> None:
    response, _ = _response([b"abc"], headers={"Content-Length": "3"})
    assert read_bounded_body(response, label="x") == b"abc"


def test_identity_content_encoding_is_accepted() -> None:
    response, _ = _response([b"abc"], headers={"Content-Encoding": "identity"})
    assert read_bounded_body(response, label="x") == b"abc"


def test_compressed_body_is_refused() -> None:
    compressed = gzip.compress(b"a" * (MAX_RESPONSE_BYTES + 1))
    response, _ = _response(
        content=compressed,
        headers={"Content-Encoding": "gzip", "Content-Length": str(len(compressed))},
    )
    with pytest.raises(ToolError, match="Content-Encoding"):
        read_bounded_body(response, label="x")


@pytest.mark.parametrize(
    "value",
    ["not-a-number", "-1", "+10", " 10", "10 ", "", "0x10", "1.5"],
)
def test_malformed_content_length_is_rejected(value: str) -> None:
    response, _ = _response([b"a"], headers={"Content-Length": value})
    with pytest.raises(ToolError, match="invalid Content-Length"):
        read_bounded_body(response, label="x")


async def test_integration_response_over_limit_is_rejected(
    homeassistant_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    tmp_log_file: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))

    mock_http(handler)
    configure_logging(tmp_log_file, "WARNING")
    config = ServerConfig(
        log_file=tmp_log_file,
        log_level="WARNING",
        http_timeout=5.0,
        sections={"homeassistant": homeassistant_section},
    )
    async with Client(build_server(config)) as client:
        result = await client.call_tool("homeassistant_list_entities", {})
    assert result.is_error
    assert "1 MiB" in str(result)


async def test_response_is_closed_when_limit_is_exceeded(
    homeassistant_section: SectionConfig,
    mock_http: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    tmp_log_file: Path,
) -> None:
    captured: list[httpx.Response] = []

    def handler(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(
            200, content=b"x" * (MAX_RESPONSE_BYTES + 1), request=request
        )
        captured.append(response)
        return response

    mock_http(handler)
    configure_logging(tmp_log_file, "WARNING")
    config = ServerConfig(
        log_file=tmp_log_file,
        log_level="WARNING",
        http_timeout=5.0,
        sections={"homeassistant": homeassistant_section},
    )
    async with Client(build_server(config)) as client:
        result = await client.call_tool("homeassistant_list_entities", {})
    assert result.is_error
    assert captured and captured[0].is_closed
