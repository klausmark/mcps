"""Tests for the `init` config command and CLI subcommand."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from mcps.__main__ import main
from mcps.config import ConfigError, init_config, init_default_path, load_config


def test_init_creates_file(tmp_path: Path, fake_home: Path) -> None:
    target = tmp_path / "config.toml"
    result = init_config(target)
    assert result == target
    assert target.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are not available")
def test_init_sets_mode_0600(tmp_path: Path, fake_home: Path) -> None:
    target = tmp_path / "config.toml"
    init_config(target)
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership is not available")
def test_init_owned_by_current_user(tmp_path: Path, fake_home: Path) -> None:
    target = tmp_path / "config.toml"
    init_config(target)
    assert target.stat().st_uid == os.getuid()


def test_init_creates_parent_directory(tmp_path: Path, fake_home: Path) -> None:
    target = tmp_path / "nested" / "dir" / "config.toml"
    init_config(target)
    assert target.exists()


def test_init_refuses_to_overwrite(tmp_path: Path, fake_home: Path) -> None:
    target = tmp_path / "config.toml"
    init_config(target)
    with pytest.raises(ConfigError, match="already exists"):
        init_config(target)


def test_init_force_overwrites(tmp_path: Path, fake_home: Path) -> None:
    target = tmp_path / "config.toml"
    init_config(target)
    target.write_text("custom content")
    init_config(target, force=True)
    assert "[server]" in target.read_text()
    assert "custom content" not in target.read_text()


def test_init_writes_loadable_toml(tmp_path: Path, fake_home: Path) -> None:
    target = tmp_path / "config.toml"
    init_config(target)
    # In the default template every integration is commented out, so only
    # the [server] section is parsed.
    config = load_config(path=target, env={}, cli_overrides={})
    assert config.log_level == "INFO"
    assert config.sections == {}


def test_init_default_path_honors_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCPS_CONFIG_PATH", str(tmp_path / "from-env.toml"))
    assert init_default_path() == tmp_path / "from-env.toml"


def test_init_default_path_without_env(
    monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    monkeypatch.delenv("MCPS_CONFIG_PATH", raising=False)
    assert init_default_path() == fake_home / ".mcps" / "config.toml"


def test_init_creates_log_directory(tmp_path: Path, fake_home: Path) -> None:
    init_config(tmp_path / "config.toml")
    assert (fake_home / ".mcps" / "logs").is_dir()


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are not available")
def test_init_secures_default_directories(tmp_path: Path, fake_home: Path) -> None:
    base = fake_home / ".mcps"
    logs = base / "logs"
    logs.mkdir(parents=True)
    os.chmod(base, 0o755)
    os.chmod(logs, 0o755)

    init_config(tmp_path / "config.toml")

    assert stat.S_IMODE(base.stat().st_mode) == 0o700
    assert stat.S_IMODE(logs.stat().st_mode) == 0o700


def test_cli_init_creates_file(
    tmp_path: Path, fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "config.toml"
    code = main(["init", "--config", str(target)])
    assert code == 0
    assert target.exists()
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600
    captured = capsys.readouterr()
    assert "created config" in captured.err
    assert str(target) in captured.err
    assert str(fake_home / ".mcps" / "logs") in captured.err


def test_cli_init_refuses_existing(
    tmp_path: Path, fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "config.toml"
    target.write_text("already here")
    os.chmod(target, 0o600)
    code = main(["init", "--config", str(target)])
    assert code == 2
    assert target.read_text() == "already here"
    captured = capsys.readouterr()
    assert "already exists" in captured.err


def test_cli_init_force_overwrites(tmp_path: Path, fake_home: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text("old content")
    os.chmod(target, 0o600)
    code = main(["init", "--config", str(target), "--force"])
    assert code == 0
    assert "[server]" in target.read_text()


def test_cli_help_lists_init(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "init" in captured.out
