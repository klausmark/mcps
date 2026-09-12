# AGENTS.md

Guidance for AI agents and humans working on this codebase.

## Project

`mcps` is a thin stdio MCP server acting as a logging and secret-injection
facade in front of AI relevant systems.

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
  conftest.py
  test_config.py
  test_logging.py
  test_http_client.py
  test_server.py
  test_cli.py
  test_init.py
  test_integration_validation.py
  test_path_parameters.py
  test_response_limits.py
  test_response_shapes.py
  test_secret_isolation.py
  test_tool_errors.py
  integrations/
    test_homeassistant.py
    test_mealie.py
    test_netbox.py
    test_nirvana.py
docs/
  DESIGN.md
```

## Code style

- All code, comments, docstrings and log messages: **English**.
- Functions small and focused; descriptive names.
- Comments explain *why*, not *what*.
- Early validation: fail fast at startup with precise `ConfigError` / `ToolError`.
- Minimize side effects.
- YAGNI: don't add abstractions, layers, or fallbacks for a hypothetical future;
  solve the problem at hand, not its imagined extensions.
- DRY: shared HTTP factory in `http_client.py`; each integration is only
  `register(server, section_config)`.
- DEBUG logging is permitted only when explicitly justified. Do not scatter speculative DEBUG calls; treat each one as a deliberate decision.

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
- Log redaction: parameter names matching `token|password|api_key|secret|
  credential|*_token|*_key` are logged as `<redacted>`.
- Default config file: `~/.mcps/config.toml`. On POSIX, correct it to `0600`
  before reading; it must be owned by the running uid. Refuse to start if the
  mode cannot be corrected or ownership differs. Windows relies on existing ACLs.
- Logging target: `~/.mcps/logs/mcps.log`, fallback to stderr with a startup
  warning. `mcps init` prepares `~/.mcps` and `~/.mcps/logs` as `0700` on POSIX.
- Config precedence (low to high): file < env (`MCPS_*`) < CLI flags. Env
  naming: `MCPS_<SECTION>_<KEY>`, uppercase, underscore.
- TLS verification per integration via `verify_tls`; when disabled, log a
  startup DEBUG line (deliberately not WARNING — see `docs/DESIGN.md`).
- Upstream bodies are read through the bounded reader in `http_client.py`;
  oversized responses fail instead of being truncated.
- Integration settings are validated before the server starts: unknown
  sections, non-snake_case keys, empty required values, invalid URLs, and
  non-positive/non-finite timeouts are rejected with `ConfigError`.
- New integration modules must export `NAME` and `REQUIRED_KEYS` and
  implement `register(server, section_config)`. Add them to
  `integrations/__init__.py::INTEGRATIONS` and write tests under
  `tests/integrations/`.

## Working agreements

- Run tests before committing (`uv run pytest`; lint with `uv run ruff check src tests`).
- Do not modify files outside your assigned scope.
- Do not change public interfaces without documenting it.
- Keep commits small.
- Never rewrite another agent's commits.

## Common tasks

- Install:       `uv sync`
- Dev setup:     `uv sync --extra dev`
- Test:          `uv run pytest`
- Lint:          `uv run ruff check src tests`
- Run:           `uv run python -m mcps --config path/to/config.toml`
- Inspect via MCP Inspector: `uv run mcp dev src/mcps/server.py`

## Forbidden

- Any code path that returns a credential value to the model.
- `print()` — use the logger (stdout is the MCP channel).
- `urllib` / `requests` — use `httpx`.
