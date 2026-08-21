"""Tests for `mcps.config`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mcps.config import ConfigError, default_log_file, load_config


def _write(path: Path, content: str) -> None:
    path.write_text(content)
    os.chmod(path, 0o600)


def test_load_minimal_config(tmp_config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_config_path))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
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


def test_invalid_mode(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("")
    os.chmod(path, 0o644)
    with pytest.raises(ConfigError, match="must have mode 600"):
        load_config(path=path, env={}, cli_overrides={})


def test_bad_toml(tmp_config_path: Path) -> None:
    _write(tmp_config_path, "this is not valid = = toml\n")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(path=tmp_config_path, env={}, cli_overrides={})


def test_invalid_log_level(tmp_config_path: Path) -> None:
    _write(tmp_config_path, '[server]\nlog_level = "DEBUG"\n')
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


def test_default_log_file_uses_xdg_state_home(
    tmp_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_config_path / "state"))
    _write(tmp_config_path, "[server]\n")
    config = load_config(path=tmp_config_path, env={}, cli_overrides={})
    assert config.log_file == tmp_config_path / "state" / "mcps" / "mcps.log"


def test_default_log_file_falls_back_to_home(
    tmp_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_config_path))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    _write(tmp_config_path, "[server]\n")
    config = load_config(path=tmp_config_path, env={}, cli_overrides={})
    assert config.log_file == tmp_config_path / ".local" / "state" / "mcps" / "mcps.log"


def test_log_file_setting_overrides_default(
    tmp_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_config_path))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    custom = tmp_config_path / "custom.log"
    _write(tmp_config_path, f'[server]\nlog_file = "{custom}"\n')
    config = load_config(path=tmp_config_path, env={}, cli_overrides={})
    assert config.log_file == custom


def test_env_log_file_overrides_setting(
    tmp_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_config_path))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    custom = tmp_config_path / "from-env.log"
    monkeypatch.setenv("MCPS_SERVER_LOG_FILE", str(custom))
    _write(tmp_config_path, '[server]\nlog_file = "/from/file.log"\n')
    config = load_config(path=tmp_config_path, env=os.environ, cli_overrides={})
    assert config.log_file == custom


def test_cli_log_file_wins_over_env(
    tmp_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_config_path))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
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
