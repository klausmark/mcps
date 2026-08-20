"""Tests for `mcps.config`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mcps.config import ConfigError, load_config


def _write(path: Path, content: str) -> None:
    path.write_text(content)
    os.chmod(path, 0o600)


def test_load_minimal_config(tmp_config_path: Path) -> None:
    _write(tmp_config_path, '[server]\nlog_level = "INFO"\n')
    config = load_config(path=tmp_config_path, env={}, cli_overrides={})
    assert config.log_level == "INFO"
    assert config.http_timeout == 10.0
    assert config.log_file is None
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
