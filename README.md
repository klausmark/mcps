# mcps

Thin stdio MCP server acting as a logging and secret-injection facade for services
that should be made available for AI agents.

The server runs on demand, launched by an MCP host (OpenCode, Hermes Agent,
Codex, etc.). Credentials are read from a local config file and never
appear in tool results.

## Install

```sh
uv sync
```

For development (tests and lint):

```sh
uv sync --extra dev
```

## Configure

Create the default config and log directory:

```sh
uv run mcps init
```

This creates `~/.mcps/config.toml` and prepares `~/.mcps/logs`. On POSIX,
the config file is set to mode `0600`, while `~/.mcps` and its log directory
are set to `0700`. Running `init` again refuses to overwrite the config unless
`--force` is supplied. Use `mcps init --config <path>` or `MCPS_CONFIG_PATH`
to select another config path.

Edit the generated config to enable integrations:

```toml
[server]
log_file = "~/.mcps/logs/mcps.log"
log_level = "INFO"

[homeassistant]
url = "http://hass.local:8123"
token = "..."
verify_tls = false

[mealie]
url = "http://mealie.local:9000"
api_key = "..."

[netbox]
url = "https://netbox.example.com"
token = "..."

[nirvana]
url = "https://api.nirvanahq.com"
email = "..."
password = "..."
```

Override individual values via `MCPS_*` environment variables or CLI flags.
Precedence (low to high): file < env < CLI.

The config path can be overridden with `MCPS_CONFIG_PATH` or `--config`.
The log path can be overridden with `MCPS_SERVER_LOG_FILE` or `--log-file`.
On POSIX, `mcps` automatically corrects the config file to mode `0600` before
reading it and fails if that is not possible or the file has another owner.
On Windows, access is governed by the user's existing Windows ACLs.

## Register with the MCP host

Point your MCP host at `mcps` (or `python -m mcps`). Example for OpenCode
(`opencode.json` or `opencode.jsonc`):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "mcps": {
      "type": "local",
      "command": ["mcps"],
      "enabled": true
    }
  }
}
```

To run as a different user:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "mcps": {
      "type": "local",
      "command": ["sudo", "-u", "mcps", "-n", "mcps"],
      "enabled": true
    }
  }
}
```

## Tools

- `homeassistant_list_entities`, `homeassistant_get_state`, `homeassistant_call_service`
- `mealie_list_recipes`, `mealie_get_recipe`, `mealie_search_recipes`
- `netbox_get` - read an unescaped REST API path below `/api/` with optional query parameters
- `nirvana_list_tasks`, `nirvana_get_task`, `nirvana_complete_task`, `nirvana_add_task`

`mealie_list_recipes` and `mealie_search_recipes` return one page at a time and
accept `limit` (default 20, maximum 100) and `page` (default 1).

`netbox_get` only sends GET requests, refuses redirects, and blocks the NetBox token
administration endpoint. Use a NetBox token belonging to a user with the minimum
required read-only object permissions. Generic API responses can contain sensitive
custom fields or plugin data that `mcps` cannot identify automatically.

Upstream response bodies are capped at 1 MiB; a larger response fails instead of
being truncated. Tool results never contain a configured credential: every parsed
response is redacted, and upstream errors are replaced with a safe message.

## Logs

Flat-text log at `[server].log_file` (default
`~/.mcps/logs/mcps.log`). Falls back to stderr if the file cannot be opened.

## Troubleshoot

- "config file not found" — set `--config` or `MCPS_CONFIG_PATH`.
- "could not be changed to 600" — ensure the current user can change the config file.
- "missing required keys" — your `[section]` lacks `url` or credentials.
