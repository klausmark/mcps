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
  (precedence: file < env < CLI). The default is `~/.mcps/config.toml`.
  On POSIX, the file is corrected to mode `0600` before it is read and must
  be owned by the running uid; startup fails if either condition cannot be met.
  Windows relies on the user's existing ACLs.
- **Logging**: flat text (one event per line) to
  `~/.mcps/logs/mcps.log`, stderr fallback. `mcps init` prepares `~/.mcps`
  and `~/.mcps/logs` with mode `0700` on POSIX. Levels: WARNING / INFO /
  ERROR / CRITICAL — no DEBUG. Parameter names matching credential patterns
  are logged as `<redacted>`.
- **Tools**: one per logical action per integration, except NetBox's intentionally
  generic read-only API tool. No `secret_*` and no `log_event` tools - by intent.

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
- NetBox: `get` (GET-only access below `/api/`, with token administration blocked)
- Nirvana: `list_tasks`, `get_task`, `complete_task`, `add_task`
