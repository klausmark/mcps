# Design

Decisions for `mcps` v1 and their rationale. See `AGENTS.md` for project rules.

## Architecture

One stdio MCP server (launched on demand by the MCP host) that proxies LLM
tool calls to upstream HTTP services. Each integration reads its credentials
from config at startup and injects them into outbound requests — the model
never sees a credential value.

## Decisions

- **SDK**: `mcp>=2,<3` with `MCPServer` + `@tool()`. Less code than the v1
  `FastMCP` API and zero hand-rolled JSON-RPC.
- **Auth model**: each integration exports `REQUIRED_KEYS` and an internal
  `apply_auth` callable. Three cases: none (open API — no required keys),
  static (token/api_key/basic), OAuth (stubbed: required keys present but no
  flow yet — out of scope for v1).
- **TLS**: `verify_tls` per integration, default `true`. `false` logs a
  startup warning so it isn't silent.
- **Secret isolation**: parsed responses pass through `sanitize()` which
  recursively replaces any occurrence of a credential string with
  `<redacted>`. A test enumerates tools and asserts no result matches any
  config credential string.
- **Config**: TOML file + `MCPS_*` env vars + CLI flags
  (precedence: file < env < CLI). File must be mode `0600` and owned by the
  running uid; startup fails otherwise.
- **Logging**: flat text (one event per line) to
  `$XDG_STATE_HOME/mcps/mcps.log`, stderr fallback. Levels: WARNING / INFO /
  ERROR / CRITICAL — no DEBUG. Parameter names matching credential patterns
  are logged as `<redacted>`.
- **Tools**: one per logical action per integration. No `secret_*` and no
  `log_event` tools — by intent.

## Out of scope (v1)

- SSH and email integrations (later: SSH uses `StrictHostKeyChecking=accept-new`).
- OAuth flows (only stubbed).
- TCP / Streamable HTTP transport.
- Encryption of the config file.
- DEBUG log level, JSON logging, log rotation (systemd/logrotate handles it).
- Plugin discovery via entry-points.

## v1 tool surface

- HA: `list_entities`, `get_state`, `call_service`
- Mealie: `list_recipes`, `get_recipe`, `search_recipes`
- Nirvana: `list_tasks`, `get_task`, `complete_task`, `add_task`