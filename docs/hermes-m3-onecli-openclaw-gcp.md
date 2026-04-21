# Hermes M3 OneCli on Openclaw-Gcp

This note documents the Hermes M3 deployment where provider and MCP traffic is
sent through OneCli running on `openclaw-gcp` over Tailscale.

## Routing Contract

- Hermes loads the OneCli proxy environment from
  `~/.config/onecli/hermes-m3-proxy.env`.
- The proxy endpoint must be
  `openclaw-gcp.tailc13f7e.ts.net:10255`.
- Do not use `openclaw-gcp.tailc13f7e.ts.net:10254`; that is the OneCli API
  endpoint, not the outbound provider proxy.
- The gateway CA is stored under `~/.config/onecli/certs/`.
- Telegram delivery stays local and bypasses the proxy through `NO_PROXY`.

The Hermes CLI, gateway process, and cron runner all load this OneCli proxy env
after the normal Hermes dotenv files so the remote proxy overrides stale local
proxy values.

## MCP Credential Ownership

Notion, Granola, Grain, and Zoom should not keep local Hermes OAuth clients,
refresh tokens, bearer tokens, or static API secrets. Their MCP server entries
should be URL/process configuration only. OneCli owns API key and OAuth secret
injection for those services.

If Hermes starts prompting with messages such as `Connect with Notion MCP` or
`Connect with Grain MCP`, check for local MCP auth metadata:

```bash
python - <<'PY'
import yaml
from pathlib import Path

cfg = yaml.safe_load(Path("~/.hermes/config.yaml").expanduser().read_text())
for name in ("notion", "granola", "grain", "zoom"):
    entry = (cfg.get("mcp_servers") or {}).get(name) or {}
    print(name, "auth=", entry.get("auth", "none"),
          "has_oauth=", "oauth" in entry,
          "has_headers=", "headers" in entry)
PY
```

Expected output for those four servers is `auth= none`, `has_oauth= False`, and
`has_headers= False`.

## Local Secret Cleanup

Remove stale local credentials for the OneCli-managed services:

```bash
rm -f ~/.hermes/notion_token.json
rm -f ~/.hermes/notion_oauth_pending.json
rm -f ~/.hermes/grain_token.json
rm -f ~/.hermes/grain_manual_oauth_pending.json
rm -f ~/.hermes/grain_manual_token_exchange.json
rm -f ~/.hermes/grain_refresh_pending.json
rm -f ~/.hermes/granola_token.json
rm -f ~/.hermes/granola_oauth_pending.json
rm -f ~/.hermes/zoom_client_credentials_token.json
rm -f ~/.hermes/hermes_m3.zoom.OAuth.cred.txt
rm -f ~/.hermes/mcp-tokens/notion*.json
rm -f ~/.hermes/mcp-tokens/grain*.json
rm -f ~/.hermes/mcp-tokens/granola*.json
rm -f ~/.hermes/mcp-tokens/zoom*.json
```

Also inspect old config backups before keeping them, because they may contain
provider keys, MCP bearer tokens, or OAuth client secrets:

```bash
ls ~/.hermes/config*.bak* ~/.hermes/config.*backup* 2>/dev/null
```

Delete backups that contain superseded secrets after confirming they are no
longer needed.

## Retired Local OneCli Artifacts

This setup no longer needs a local OneCli runtime. Remove old local artifacts if
present:

```bash
rm -rf ~/.hermes/onecli
rm -f ~/.local/bin/hermes-onecli
rm -rf ~/src/onecli
rm -rf ~/.hermes/skills/mcp/hermes-onecli-proxy
```

If a previous local Docker stack exists, stop it with its compose file before
removing the source directory:

```bash
docker compose -f ~/src/onecli/docker/docker-compose.yml down -v --remove-orphans
```

## Verification

Confirm the handoff package and provider proxy:

```bash
source ~/.config/onecli/hermes-m3-proxy.env
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H 'Authorization: Bearer placeholder' \
  https://api.openai.com/v1/models
```

An HTTP `200` response confirms the provider path is reaching OpenAI through the
OneCli proxy. A `407`, TLS error, or connection refusal usually means the proxy
env file, Tailscale route, or CA bundle needs attention.

Restart the Hermes gateway after changing MCP config or proxy settings:

```bash
hermes gateway restart
```
