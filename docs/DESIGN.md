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
- **TLS**: `verify_tls` per integration, default `true`. `false` emits a
  startup DEBUG log line (not WARNING — see "Known limitations" below).
- **Secret isolation**: parsed responses pass through `sanitize()`, which
  removes every configured credential value, including NirvanaHQ's encoded
  Basic-auth token, from strings, keys, and JSON numbers. A test enumerates
  every tool and asserts no result matches any config credential string.
- **Safe tool errors**: integrations raise `ToolError` with a fixed,
  credential-free message. `log_call` records only the exception type, and an
  unexpected exception is converted to a generic `ToolError`, so upstream
  headers or exception text can never reach the model or the log.
- **Bounded responses**: bodies are read through a shared streaming reader
  capped at 1 MiB (declared `Content-Length` plus actual decoded bytes).
  Oversized responses fail instead of being truncated, and the response and
  client are always closed.
- **Startup validation**: before the server starts, unknown sections,
  non-snake_case keys, empty required values, invalid URLs, and
  non-positive/non-finite timeouts are rejected with `ConfigError`. The CLI
  reports these errors on stderr with exit code 2 and no traceback.
- **Path-parameter validation**: model-supplied values placed in URL paths
  (entity ids, slugs, task ids, domains, services) are validated as single
  identifiers, so they cannot inject path traversal or query separators.
- **Response shapes**: expected list/dict shapes are asserted (with `items`
  envelopes accepted where the API uses them); an unexpected shape raises a
  safe `ToolError` instead of silently returning an empty result.
- **Config**: TOML file + `MCPS_*` env vars + CLI flags
  (precedence: file < env < CLI). The default is `~/.mcps/config.toml`.
  On POSIX, the file is corrected to mode `0600` before it is read and must
  be owned by the running uid; startup fails if either condition cannot be met.
  Windows relies on the user's existing ACLs.
- **Logging**: flat text (one event per line) to
  `~/.mcps/logs/mcps.log`, stderr fallback. `mcps init` prepares `~/.mcps`
  and `~/.mcps/logs` with mode `0700` on POSIX. Levels: DEBUG / INFO /
  WARNING / ERROR / CRITICAL — DEBUG permitted only when explicitly
  justified (see `AGENTS.md`). Parameter names matching credential patterns
  are logged as `<redacted>`.
- **Tools**: one per logical action per integration, except NetBox's intentionally
  generic read-only API tool. No `secret_*` and no `log_event` tools - by intent.
  Mealie recipe listings are paginated: `limit` (default 20, max 100) and `page`
  (default 1), enforced locally as well as forwarded upstream.

## Out of scope (v1)

- SSH and email integrations (later: SSH uses `StrictHostKeyChecking=accept-new`).
- OAuth flows (only stubbed).
- TCP / Streamable HTTP transport.
- Encryption of the config file.
- JSON logging, log rotation (systemd/logrotate handles it).
- Plugin discovery via entry-points.

## v1 tool surface

- HA: `list_entities`, `get_state`, `call_service`
- Mealie: `list_recipes`, `get_recipe`, `search_recipes`
- NetBox: `get` (GET-only access below `/api/`, with token administration blocked)
- Nirvana: `list_tasks`, `get_task`, `complete_task`, `add_task`

## Known limitations

- **Multi-process log interleaving.** `logging.Handler`'s per-handler lock
  protects concurrent writes within a single `mcps` process, so concurrent
  tool calls in one process never interleave. Multiple `mcps` processes
  writing to the same log file (e.g. two MCP hosts open at once) are not
  inter-process synchronised: their writes may interleave at the line level.
  POSIX `write(2)` to a regular file is atomic for payloads up to `PIPE_BUF`
  (typically 4096 bytes), so ordinary log lines remain atomic; only
  unusually long lines (larger than `PIPE_BUF`) may interleave mid-line. The
  file itself is never destroyed or truncated. Point each host at a distinct
  log file via `--log-file` if this matters.
- **`verify_tls=false` is now DEBUG, not WARNING.** A user who disables TLS
  verification is no longer scolded by default; the line is still emitted at
  startup and is visible by raising the log level to DEBUG. This is by
  intent: a stdio MCP server is typically spawned per host connection, so a
  per-process warning was perceived as log spam.
