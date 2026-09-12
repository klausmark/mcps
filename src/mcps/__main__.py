"""CLI entry point: `python -m mcps` or `mcps` (via project.scripts)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mcps.config import (
    ConfigError,
    ServerConfig,
    default_log_file,
    init_config,
    init_default_path,
    load_config,
)
from mcps.errors import ToolError
from mcps.integrations import garmin
from mcps.logging_setup import configure_logging
from mcps.server import build_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcps",
        description="Thin stdio MCP server for Home Assistant, Mealie, NetBox, "
        "NirvanaHQ and Garmin Connect.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to the TOML config file. Defaults to $MCPS_CONFIG_PATH, "
        "or ~/.mcps/config.toml.",
    )
    parser.add_argument("--log-file", type=Path, default=None, help="Override log file path.")
    parser.add_argument("--log-level", default=None, help="Override log level (default INFO).")
    parser.add_argument("--http-timeout", type=float, default=None, help="HTTP timeout in seconds.")

    sub = parser.add_subparsers(dest="command")
    init = sub.add_parser("init", help="Create a config file with default values.")
    init.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Target path. Defaults to MCPS_CONFIG_PATH or ~/.mcps/config.toml.",
    )
    init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing config file.",
    )
    sub.add_parser(
        "garmin-login",
        help="Authenticate with Garmin Connect and store tokens securely.",
    )

    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def cli_overrides(args: argparse.Namespace) -> dict[tuple[str, str], str]:
    overrides: dict[tuple[str, str], str] = {}
    if args.log_file is not None:
        overrides[("server", "log_file")] = str(args.log_file)
    if args.log_level is not None:
        overrides[("server", "log_level")] = args.log_level
    if args.http_timeout is not None:
        overrides[("server", "http_timeout")] = str(args.http_timeout)
    return overrides


def _run_init(args: argparse.Namespace) -> int:
    path = args.config if args.config is not None else init_default_path()
    try:
        init_config(path, force=args.force)
    except ConfigError as exc:
        sys.stderr.write(f"mcps: {exc}\n")
        return 2
    sys.stderr.write(
        f"mcps: created config at {path}.\n"
        f"      Prepared log directory at {default_log_file().parent}.\n"
        f"      Edit it to add credentials, then run `mcps`.\n"
    )
    return 0


def _run_garmin_login(config: ServerConfig) -> int:
    section = config.sections.get(garmin.NAME)
    if section is None:
        sys.stderr.write("mcps: configuration error: no [garmin] section configured\n")
        return 2
    try:
        garmin.login_interactively(section)
    except (ConfigError, ToolError) as exc:
        sys.stderr.write(f"mcps: {exc}\n")
        return 2
    sys.stderr.write(
        "mcps: Garmin authentication completed and tokens were stored securely.\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "init":
        return _run_init(args)

    try:
        config = load_config(
            path=args.config,
            env=os.environ,
            cli_overrides=cli_overrides(args),
        )
    except ConfigError as exc:
        sys.stderr.write(f"mcps: configuration error: {exc}\n")
        return 2

    if args.command == "garmin-login":
        return _run_garmin_login(config)

    logger = configure_logging(config.log_file, config.log_level)
    try:
        server = build_server(config)
    except ConfigError as exc:
        sys.stderr.write(f"mcps: configuration error: {exc}\n")
        return 2
    logger.info("server_started version=%s log_file=%s", "0.1.0", str(config.log_file))
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
