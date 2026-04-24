# memos_palace MacBook Install

Date: 2026-04-24

This runbook installs the same `memos_palace` provider used on `rj-spark` onto a
MacBook Hermes install.

## Goal

Make Hermes on macOS use the same composite memory stack:

- `memOS` for semantic recall and durable fact storage
- `MemPalace` for verbatim archive recall and evidence
- shared canonical identity via the same `owner_user_id`

## Package

Build a portable tarball from the tracked source tree:

```bash
cd /path/to/hermes-agent
scripts/package_memos_palace.sh
```

That outputs a tarball under `dist/`, containing:

- `memos_palace/`
- `INSTALL.txt`
- `memos_palace.example.json`

## Target machine layout

On the MacBook, place the provider here:

```bash
~/.hermes/plugins/memos_palace/
```

Create this config file:

```bash
~/.hermes/memos_palace.json
```

Set the Hermes config to use the provider:

```yaml
memory:
  provider: memos_palace
```

## Example config

Use the included example file as the starting point:

```json
{
  "memos_api_url": "https://YOUR-MEMOS-ENDPOINT",
  "mempalace_mcp_url": "https://YOUR-MEMPALACE-MCP-ENDPOINT",
  "owner_user_id": "rj@stratminds.vc"
}
```

If you want the same private namespace behavior as `rj-spark`, keep the same
canonical `owner_user_id` on both machines.

## macOS service note

If Hermes runs under `launchd`, do not rely on your interactive shell env being
present. Prefer storing the provider config in `~/.hermes/memos_palace.json`.

If you must rely on env vars, make sure the launch agent explicitly sets:

- `MEMOS_API_URL`
- `MEMPALACE_MCP_URL`
- `MEMOS_OWNER_USER_ID`
- any TLS/proxy variables required by your MemPalace path

The existing Hermes FAQ already documents that macOS `launchd` runs with a
minimal `PATH`; the same caution applies to provider env and certificate paths.

## Verification

After restart, verify all three paths:

1. Run a known personal-memory query that should hit memOS.
2. Run a verbatim/evidence-style query that should fall back to MemPalace.
3. Run an explicit store request and confirm it succeeds.

Recommended smoke checks:

```bash
python - <<'PY'
from plugins.memos_palace import MemosPalaceProvider
provider = MemosPalaceProvider()
print("available:", provider.is_available())
print("memos enabled:", provider._memos_enabled)
print("mempalace enabled:", provider._mempalace_enabled)
PY
```

## Current known caveat

The tracked source now lives in `plugins/memos_palace/`, but many current Hermes
installs still load memory providers from `~/.hermes/plugins/`. Keep the target
machine's overlay copy in sync with this tracked source until the runtime loader
is changed to prefer the tracked copy.
