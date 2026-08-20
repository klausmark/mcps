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

## Configure

Create a config file (default `$XDG_CONFIG_HOME/mcps/config.toml`) owned by
the running user with mode `0600`:

```toml
[server]
log_file = "~/.local/state/mcps/mcps.log"
log_level = "INFO"

[homeassistant]
url = "http://hass.local:8123"
token = "..."
verify_tls = false

[mealie]
url = "http://mealie.local:9000"
api_key = "..."

[nirvana]
url = "https://api.nirvanahq.com"
email = "..."
password = "..."
```

Override individual values via `MCPS_*` environment variables or CLI flags.
Precedence (low to high): file < env < CLI.

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
- `nirvana_list_tasks`, `nirvana_get_task`, `nirvana_complete_task`, `nirvana_add_task`

## Logs

Flat-text log at `[server].log_file` (default
`$XDG_STATE_HOME/mcps/mcps.log`). Falls back to stderr if the file cannot
be opened.

## Troubleshoot

- "config file not found" — set `--config` or `MCPS_CONFIG_PATH`.
- "must have mode 0600" — `chmod 600 <config>`.
- "missing required keys" — your `[section]` lacks `url` or credentials.