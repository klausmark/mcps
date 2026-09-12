"""CLI startup behavior: configuration failures exit cleanly without a traceback."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mcps.__main__ import main


def _write_config(path: Path, content: str) -> None:
    path.write_text(content)
    os.chmod(path, 0o600)


@pytest.mark.parametrize(
    "content",
    [
        '[home_assistant]\nurl = "http://x"\ntoken = "y"\n',
        '[server]\nhttp_timout = 5\n',
        '[homeassistant]\nurl = "http://x"\n',
    ],
)
def test_invalid_config_exits_with_code_2(
    tmp_path: Path, fake_home: Path, capsys: pytest.CaptureFixture[str], content: str
) -> None:
    path = tmp_path / "config.toml"
    _write_config(path, content)
    code = main(["--config", str(path)])
    captured = capsys.readouterr()
    assert code == 2
    assert "configuration error" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_valid_config_hits_server_run(
    tmp_path: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "config.toml"
    _write_config(path, '[server]\nlog_level = "WARNING"\n[homeassistant]\nurl = "http://x"\ntoken = "y"\n')
    started: list[bool] = []

    def fake_run(self, *, transport: str) -> None:  # noqa: ANN001
        started.append(transport == "stdio")

    monkeypatch.setattr("mcps.server.MCPServer.run", fake_run)
    assert main(["--config", str(path)]) == 0
    assert started == [True]
