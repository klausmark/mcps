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


def test_env_server_override(tmp_config_path: Path) -> None:
    _write(tmp_config_path, '[server]\nlog_level = "INFO"\n')
    config = load_config(
        path=tmp_config_path,
        env={"MCPS_SERVER_LOG_LEVEL": "ERROR"},
        cli_overrides={},
    )
    assert config.log_level == "ERROR"


def test_cli_overrides_win_over_env(tmp_config_path: Path) -> None:
    _write(tmp_config_path, '[server]\nlog_level = "INFO"\n')
    config = load_config(
        path=tmp_config_path,
        env={"MCPS_SERVER_LOG_LEVEL": "ERROR"},
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


def test_explicit_path_wins_over_env_path(
    tmp_config_path: Path, tmp_path: Path
) -> None:
    _write(tmp_config_path, '[server]\nlog_level = "WARNING"\n')
    other = tmp_path / "other.toml"
    _write(other, '[server]\nlog_level = "ERROR"\n')
    config = load_config(
        path=tmp_config_path,
        env={"MCPS_CONFIG_PATH": str(other)},
        cli_overrides={},
    )
    assert config.log_level == "WARNING"


def test_env_path_override(tmp_config_path: Path) -> None:
    _write(tmp_config_path, "[server]\n")
    config = load_config(
        path=None,
        env={"MCPS_CONFIG_PATH": str(tmp_config_path)},
        cli_overrides={},
    )
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


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "true", ""])
def test_invalid_server_timeout_is_rejected(tmp_config_path: Path, value: str) -> None:
    _write(tmp_config_path, f"[server]\nhttp_timeout = {value!r}\n")
    if value in ("nan", "inf"):
        _write(tmp_config_path, f"[server]\nhttp_timeout = {value}\n")
    with pytest.raises(ConfigError, match=r"\[server\]\.http_timeout"):
        load_config(path=tmp_config_path, env={}, cli_overrides={})


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "true", ""])
def test_invalid_section_timeout_is_rejected(tmp_config_path: Path, value: str) -> None:
    _write(tmp_config_path, f'[homeassistant]\nurl = "http://x"\ntoken = "y"\nhttp_timeout = {value!r}\n')
    if value in ("nan", "inf"):
        _write(
            tmp_config_path,
            f'[homeassistant]\nurl = "http://x"\ntoken = "y"\nhttp_timeout = {value}\n',
        )
    with pytest.raises(ConfigError, match=r"\[homeassistant\]\.http_timeout"):
        load_config(path=tmp_config_path, env={}, cli_overrides={})


def test_toml_boolean_timeout_is_rejected(tmp_config_path: Path) -> None:
    _write(tmp_config_path, '[server]\nhttp_timeout = true\n[homeassistant]\nurl = "http://x"\ntoken = "y"\nhttp_timeout = false\n')
    with pytest.raises(ConfigError, match=r"\[server\]\.http_timeout"):
        load_config(path=tmp_config_path, env={}, cli_overrides={})


def test_server_unknown_setting_is_rejected(tmp_config_path: Path) -> None:
    _write(tmp_config_path, "[server]\nhttp_timout = 5\n")
    with pytest.raises(ConfigError, match="unknown setting"):
        load_config(path=tmp_config_path, env={}, cli_overrides={})


def test_invalid_section_key_name_is_rejected(tmp_config_path: Path) -> None:
    _write(tmp_config_path, '[homeassistant]\nurl = "http://x"\nBadKey = "y"\n')
    with pytest.raises(ConfigError, match="invalid key name"):
        load_config(path=tmp_config_path, env={}, cli_overrides={})


def test_config_path_env_is_not_a_section(tmp_config_path: Path) -> None:
    _write(tmp_config_path, "[server]\n")
    config = load_config(
        path=None,
        env={"MCPS_CONFIG_PATH": str(tmp_config_path)},
        cli_overrides={},
    )
    assert config.sections == {}


def test_override_on_non_table_section_is_rejected(tmp_config_path: Path) -> None:
    _write(tmp_config_path, 'server = "oops"\n')
    with pytest.raises(ConfigError, match=r"section \[server\] must be a table"):
        load_config(
            path=tmp_config_path,
            env={"MCPS_SERVER_LOG_LEVEL": "INFO"},
            cli_overrides={},
        )


def test_section_name_with_trailing_newline_is_rejected(tmp_config_path: Path) -> None:
    _write(tmp_config_path, '["homeassistant\\n"]\nurl = "http://x"\n')
    with pytest.raises(ConfigError, match="invalid section name"):
        load_config(path=tmp_config_path, env={}, cli_overrides={})


def test_key_with_trailing_newline_is_rejected(tmp_config_path: Path) -> None:
    _write(tmp_config_path, '[homeassistant]\nurl = "http://x"\n"token\\n" = "y"\n')
    with pytest.raises(ConfigError, match="invalid key name"):
        load_config(path=tmp_config_path, env={}, cli_overrides={})
