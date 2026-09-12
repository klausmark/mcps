# AGENTS.md

Guidance for AI agents and humans working on this codebase.

## Project

`mcps` is a thin stdio MCP server acting as a logging and secret-injection
facade for services exposed to AI agents.

## Stack

- Python 3.12+ (uses stdlib `tomllib`; the Garmin Connect SDK requires 3.12)
- `uv` for dependency management
- `mcp>=2.0,<3` (official SDK; `MCPServer` with `@tool()` decorator)
- `httpx` for outbound HTTP; `MockTransport` for tests
- `garminconnect` (pinned) only inside `integrations/garmin.py`; its transitive
  `requests`/`curl_cffi` use is a deliberate, audited exception to the
  `httpx`-only rule
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
    garmin.py
    homeassistant.py
    mealie.py
    netbox.py
    nirvana.py
tests/
  test_*.py           # shared behavior and security coverage
  integrations/       # integration-specific tests (test_garmin.py fakes the SDK)
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
    credentials, including NirvanaHQ's encoded Basic-auth token and Garmin's
    generated OAuth tokens/cookies
  - values under credential-like JSON keys (`token`, `password`, `secret`,
    `credential`, `cookie`, `ticket`) are replaced with `<redacted>` in Garmin
    results, even when the value is not a known credential
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
- Garmin authentication never runs at startup and never prompts on stdin.
  Tokens are bootstrapped only by `mcps garmin-login`; on POSIX, the token
  directory must be `0700`, the token file `0600`, owned by the running uid,
  with no symlinks. The server uses cached tokens only and reports a safe
  error telling the operator to re-run `garmin-login`.
- New integration modules must export `NAME` and `REQUIRED_KEYS` and
  implement `register(server, section_config)`, plus `ALLOWED_KEYS` if they
  accept optional settings. Add them to
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
- Garmin login:  `uv run python -m mcps --config path/to/config.toml garmin-login`
- Inspect via MCP Inspector: `uv run mcp dev src/mcps/server.py`

## Forbidden

- `print()` — use the logger (stdout is the MCP channel).
- `urllib` / `requests` — use `httpx`, except the pinned `garminconnect` SDK
  used only by `integrations/garmin.py`.
