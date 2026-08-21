"""Tests for `mcps.config`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mcps.config import ConfigError, default_config_file, default_log_file, load_config


def _write(path: Path, content: str) -> None:
    path.write_text(content)
    os.chmod(path, 0o600)


def test_load_minimal_config(tmp_config_path: Path, fake_home: Path) -> None:
    _write(tmp_config_path, '[server]\nlog_level = "INFO"\n')
    config = load_config(path=tmp_config_path, env={}, cli_overrides={})
    assert config.log_level == "INFO"
    assert config.http_timeout == 10.0
    assert config.log_file == default_log_file()
    assert config.sections == {}


def test_env_overrides_section_keys(tmp_config_path: Path) -> None:
    _write(
        tmp_config_path,
        '[homeassistant]\nurl = "http://old"\ntoken = "old-token"\n',
    )
    config = load_config(
        path=tmp_config_path,
        env={"MCPS_HOMEASSISTANT_URL": "http://new", "MCPS_HOMEASSISTANT_TOKEN": "new-token"},
        cli_overrides={},
    )
    ha = config.sections["homeassistant"]
    assert ha.data["url"] == "http://new"
    assert ha.data["token"] == "new-token"


def test_netbox_env_overrides_and_section_settings(tmp_config_path: Path) -> None:
    _write(
        tmp_config_path,
        '[server]\nhttp_timeout = 8\n[netbox]\nurl = "https://old"\ntoken = "old-token"\n',
    )
    config = load_config(
        path=tmp_config_path,
        env={
            "MCPS_NETBOX_URL": "https://new",
            "MCPS_NETBOX_TOKEN": "new-token",
            "MCPS_NETBOX_VERIFY_TLS": "false",
            "MCPS_NETBOX_HTTP_TIMEOUT": "3.5",
        },
        cli_overrides={},
    )
    netbox = config.sections["netbox"]
    assert netbox.data == {"url": "https://new", "token": "new-token"}
    assert netbox.verify_tls is False
    assert netbox.http_timeout == 3.5


def test_cli_overrides_win_over_env(tmp_config_path: Path) -> None:
    _write(tmp_config_path, '[server]\nlog_level = "INFO"\n')
    config = load_config(
        path=tmp_config_path,
        env={"MCPS_LOG_LEVEL": "ERROR"},
        cli_overrides={("server", "log_level"): "WARNING"},
    )
    assert config.log_level == "WARNING"


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config file not found"):
        load_config(path=tmp_path / "nope.toml", env={}, cli_overrides={})


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are not available")
def test_invalid_mode_is_corrected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("")
    os.chmod(path, 0o644)
    load_config(path=path, env={}, cli_overrides={})
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are not available")
def test_invalid_mode_fails_when_chmod_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text("")
    os.chmod(path, 0o644)

    def fail_chmod(path: Path, mode: int) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(os, "chmod", fail_chmod)
    with pytest.raises(ConfigError, match="could not be changed to 600"):
        load_config(path=path, env={}, cli_overrides={})


def test_bad_toml(tmp_config_path: Path) -> None:
    _write(tmp_config_path, "this is not valid = = toml\n")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(path=tmp_config_path, env={}, cli_overrides={})


def test_debug_log_level_is_valid(tmp_config_path: Path) -> None:
    _write(tmp_config_path, '[server]\nlog_level = "DEBUG"\n')
    config = load_config(path=tmp_config_path, env={}, cli_overrides={})
    assert config.log_level == "DEBUG"


def test_invalid_log_level(tmp_config_path: Path) -> None:
    _write(tmp_config_path, '[server]\nlog_level = "TRACE"\n')
    with pytest.raises(ConfigError, match="log_level must be one of"):
        load_config(path=tmp_config_path, env={}, cli_overrides={})


def test_verify_tls_default_is_true(tmp_config_path: Path) -> None:
    _write(tmp_config_path, '[homeassistant]\nurl = "http://x"\ntoken = "y"\n')
    config = load_config(path=tmp_config_path, env={}, cli_overrides={})
    assert config.sections["homeassistant"].verify_tls is True


def test_verify_tls_explicit_false(tmp_config_path: Path) -> None:
    _write(
        tmp_config_path,
        '[homeassistant]\nurl = "http://x"\ntoken = "y"\nverify_tls = "false"\n',
    )
    config = load_config(path=tmp_config_path, env={}, cli_overrides={})
    assert config.sections["homeassistant"].verify_tls is False


def test_credential_values_excludes_url_and_flags(tmp_config_path: Path) -> None:
    _write(
        tmp_config_path,
        '[homeassistant]\nurl = "http://x"\ntoken = "y"\nverify_tls = "false"\n',
    )
    config = load_config(path=tmp_config_path, env={}, cli_overrides={})
    ha = config.sections["homeassistant"]
    assert ha.credential_values() == ["y"]


def test_env_path_override(tmp_config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_config_path, "[server]\n")
    monkeypatch.setenv("MCPS_CONFIG_PATH", str(tmp_config_path))
    config = load_config(path=None, env={}, cli_overrides={})
    assert config.log_level == "INFO"


def test_default_paths_use_mcps_home_directory(
    tmp_config_path: Path, fake_home: Path
) -> None:
    _write(tmp_config_path, "[server]\n")
    config = load_config(path=tmp_config_path, env={}, cli_overrides={})
    assert default_config_file() == fake_home / ".mcps" / "config.toml"
    assert config.log_file == fake_home / ".mcps" / "logs" / "mcps.log"


def test_log_file_setting_overrides_default(
    tmp_config_path: Path,
) -> None:
    custom = tmp_config_path / "custom.log"
    _write(tmp_config_path, f'[server]\nlog_file = "{custom}"\n')
    config = load_config(path=tmp_config_path, env={}, cli_overrides={})
    assert config.log_file == custom


def test_env_log_file_overrides_setting(
    tmp_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = tmp_config_path / "from-env.log"
    monkeypatch.setenv("MCPS_SERVER_LOG_FILE", str(custom))
    _write(tmp_config_path, '[server]\nlog_file = "/from/file.log"\n')
    config = load_config(path=tmp_config_path, env=os.environ, cli_overrides={})
    assert config.log_file == custom


def test_cli_log_file_wins_over_env(
    tmp_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from_env = tmp_config_path / "from-env.log"
    from_cli = tmp_config_path / "from-cli.log"
    monkeypatch.setenv("MCPS_SERVER_LOG_FILE", str(from_env))
    _write(tmp_config_path, "[server]\n")
    config = load_config(
        path=tmp_config_path,
        env=os.environ,
        cli_overrides={("server", "log_file"): str(from_cli)},
    )
    assert config.log_file == from_cli
