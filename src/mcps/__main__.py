"""CLI entry point: `python -m mcps` or `mcps` (via project.scripts)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mcps.config import ConfigError, load_config
from mcps.logging_setup import configure_logging
from mcps.server import build_server


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mcps",
        description="Thin stdio MCP server for Home Assistant, Mealie and NirvanaHQ.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to the TOML config file. Defaults to $MCPS_CONFIG_PATH, "
        "$XDG_CONFIG_HOME/mcps/config.toml, or /etc/mcps/config.toml.",
    )
    parser.add_argument("--log-file", type=Path, default=None, help="Override log file path.")
    parser.add_argument("--log-level", default=None, help="Override log level (default INFO).")
    parser.add_argument("--http-timeout", type=float, default=None, help="HTTP timeout in seconds.")
    return parser.parse_args(argv)


def cli_overrides(args: argparse.Namespace) -> dict[tuple[str, str], str]:
    overrides: dict[tuple[str, str], str] = {}
    if args.log_file is not None:
        overrides[("server", "log_file")] = str(args.log_file)
    if args.log_level is not None:
        overrides[("server", "log_level")] = args.log_level
    if args.http_timeout is not None:
        overrides[("server", "http_timeout")] = str(args.http_timeout)
    return overrides


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(
            path=args.config,
            env=os.environ,
            cli_overrides=cli_overrides(args),
        )
    except ConfigError as exc:
        sys.stderr.write(f"mcps: configuration error: {exc}\n")
        return 2

    logger = configure_logging(config.log_file, config.log_level)
    logger.info("server_started version=%s log_file=%s", "0.1.0", str(config.log_file))
    server = build_server(config)
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
