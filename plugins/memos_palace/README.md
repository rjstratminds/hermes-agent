# memos_palace

`memos_palace` is a composite Hermes memory provider:

- `memOS` for semantic recall and durable fact storage
- `MemPalace` for verbatim archive recall and evidence

This directory is the tracked source of truth for the provider. Many live
Hermes installs still load the overlay copy from `~/.hermes/plugins/memos_palace/`,
so after updating this source tree you should also sync the overlay or switch the
runtime loader to prefer the tracked copy.

## Files

- `__init__.py` — provider implementation
- `plugin.yaml` — plugin manifest
- `memos_palace.example.json` — sample local config file

## Required config

The provider reads config from environment variables plus
`$HERMES_HOME/memos_palace.json`.

Minimum useful fields:

- `memos_api_url` for memOS
- `mempalace_mcp_url` for MemPalace
- `owner_user_id` for the canonical identity shared by both systems

Optional but common:

- `memos_namespace`
- `memos_source`
- `memos_server`
- `memos_node`
- `mempalace_wing`
- `mempalace_room`
- `principals_path`

## Install summary

1. Copy this directory to `~/.hermes/plugins/memos_palace/`.
2. Copy `memos_palace.example.json` to `~/.hermes/memos_palace.json` and fill in
   your real values.
3. Set `memory.provider: memos_palace` in `~/.hermes/config.yaml`.
4. Restart the Hermes gateway/process.
5. Verify with a memory query plus an explicit `memos_store` test.

## Packaging

Use `scripts/package_memos_palace.sh` to build a tarball bundle for another
machine such as a MacBook:

```bash
scripts/package_memos_palace.sh
```
