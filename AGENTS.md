# AGENTS.md

Guidance for AI agents and humans working on this codebase.

## Project

`mcps` is a thin stdio MCP server acting as a logging and secret-injection
facade for services exposed to AI agents.

## Stack

- Python 3.11+ (uses stdlib `tomllib`)
- `uv` for dependency management
- `mcp>=2.0,<3` (official SDK; `MCPServer` with `@tool()` decorator)
- `httpx` for outbound HTTP; `MockTransport` for tests
- Dev: `pytest`, `pytest-asyncio`, `ruff`

## Layout

```
src/mcps/
  __init__.py
  __main__.py          # CLI entry (`python -m mcps`)
  server.py            # MCPServer + register_all()
  config.py            # file/env/CLI merging + validation
  logging_setup.py     # flat-text logger + log_call decorator
  http_client.py       # per-integration httpx.Client + bounded reader
  redaction.py         # credential redaction for responses, logs, and errors
  errors.py            # ToolError: public, credential-free tool failures
  responses.py         # response-shape validation (expect_dict/list/items)
  validation.py        # URL path-parameter validation
  integrations/
    __init__.py        # register_all()
    homeassistant.py
    mealie.py
    netbox.py
    nirvana.py
tests/
  test_*.py           # shared behavior and security coverage
  integrations/       # integration-specific tests
docs/
  DESIGN.md
```

## Code style

- All code, comments, docstrings and log messages: **English**.
- Functions small and focused; descriptive names.
- Comments explain *why*, not *what*.
- Validate configuration early: startup failures use precise `ConfigError`
  messages; tool-call failures use safe `ToolError` messages.
- Minimize side effects.
- YAGNI: don't add abstractions, layers, or fallbacks for a hypothetical future;
  solve the problem at hand, not its imagined extensions.
- DRY: keep shared HTTP behavior in `http_client.py`.
- DEBUG logging is permitted only when explicitly justified. Treat each DEBUG
  call as a deliberate decision.

## Project rules (hard)

- **Never expose secrets to the model.**
- Tool results must never contain a credential value. Defenses:
  - every parsed response passes through `sanitize()` against all configured
    credentials, including NirvanaHQ's encoded Basic-auth token
  - tool errors carry a safe `ToolError` message, never the upstream or
    exception text (which may echo headers); the message is also redacted
    against every configured credential
  - `log_call` records only the exception type, never its message
  - a test asserts no tool result matches any credential from config
- Log redaction: parameter names equal to `token`, `password`, `api_key`,
  `apikey`, `secret`, or `credential`, or ending in `_token` or `_key`, are
  matched case-insensitively and logged as `<redacted>`.
- Default config file: `~/.mcps/config.toml`. On POSIX, correct it to `0600`
  before reading; it must be owned by the running uid. Refuse to start if the
  mode cannot be corrected or ownership differs. Windows relies on existing ACLs.
- Logging target: `~/.mcps/logs/mcps.log`, with fallback to stderr and a startup
  message. `mcps init` prepares `~/.mcps` and `~/.mcps/logs` as `0700` on POSIX.
- Setting precedence (low to high): file < env (`MCPS_*`) < corresponding CLI
  flag. CLI overrides exist only for `[server].log_file`, `[server].log_level`,
  and `[server].http_timeout`. Env naming is `MCPS_<SECTION>_<KEY>`, uppercase
  with underscores.
- The config path uses `--config`, then `MCPS_CONFIG_PATH`, then
  `~/.mcps/config.toml`.
- TLS verification per integration via `verify_tls`; when disabled, log a
  startup DEBUG line (deliberately not WARNING — see `docs/DESIGN.md`).
- Upstream bodies are read through the bounded reader in `http_client.py`;
  oversized responses fail instead of being truncated.
- Integration settings are validated before the server starts: unknown
  sections, unknown settings, non-snake_case names, empty required values,
  invalid or path-prefixed URLs, and non-positive/non-finite timeouts are
  rejected with `ConfigError`.
- New integration modules must export `NAME` and `REQUIRED_KEYS` and
  implement `register(server, section_config)`. Add them to
  `integrations/__init__.py::INTEGRATIONS` and write tests under
  `tests/integrations/`.

## Working agreements

- Run relevant tests after changes. Before committing, run `uv run pytest` and
  `uv run ruff check src tests`.
- Keep changes scoped to the assigned task, including necessary tests and
  documentation.
- Document public interface changes in `README.md` or `docs/DESIGN.md`.
- Keep each commit focused on one logical change.
- Never amend, rebase, squash, drop, or otherwise rewrite commits made by
  another agent.

## Common tasks

- Install:       `uv sync`
- Dev setup:     `uv sync --extra dev`
- Test:          `uv run pytest`
- Lint:          `uv run ruff check src tests`
- Run:           `uv run python -m mcps --config path/to/config.toml`
- Inspect via MCP Inspector: `uv run mcp dev src/mcps/server.py`

## Forbidden

- `print()` — use the logger (stdout is the MCP channel).
- `urllib` / `requests` — use `httpx`.
