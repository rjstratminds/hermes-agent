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
- `memos_top_k`
- `memos_memory_limit`
- `memos_query_context_depth`
- `memos_max_item_chars`
- `memos_add_retries`
- `memos_dedup_enabled`
- `memos_include_assistant`
- `memos_max_message_chars`
- `memos_min_user_chars`
- `memos_skip_vague_adds`
- `memos_skip_event_logs`
- `memos_event_log_penalty`
- `memos_typed_memory_boost`
- `memos_recent_dedup_window_secs`
- `mempalace_wing`
- `mempalace_room`
- `mempalace_limit`
- `mempalace_fallback_score_threshold`
- `mempalace_use_on_weak_coverage`
- `principals_path`

## OpenClaw Parity

On 2026-04-24 this provider was aligned against the live OpenClaw
`memos-local-plugin` running on `openclaw-gcp`.

That parity sync brought over:

- the same practical recall/store caps (`top_k=5`, `memory_limit=5`,
  `query_context_depth=4`, `max_item_chars=220`, `max_message_chars=3500`)
- vague-query expansion using recent turn context before memOS recall
- OpenClaw-style reranking weights (`event_log_penalty=0.35`,
  `typed_memory_boost=1.25`)
- the same MemPalace fallback shape (`mempalace_limit=4`,
  `mempalace_fallback_score_threshold=0.62`)

MemPalace parity also depends on the runtime carrying the HTTPS proxy / CA env.
On Hermes Spark this is done by loading the OneCLI env file from the service
unit so the provider does not hit the MCP endpoint with a direct
`Bearer placeholder` request.

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
