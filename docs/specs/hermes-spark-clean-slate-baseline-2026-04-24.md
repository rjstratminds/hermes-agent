# Hermes Spark Clean-Slate Baseline

Date: 2026-04-24

## Purpose

This document defines the baseline to treat as the starting point if Hermes
Spark is reimplemented on a clean system.

It is intentionally split into two layers:

1. **GitHub baseline** — what must exist in the tracked `hermes-agent` fork
2. **Host-local operational baseline** — what still lives outside GitHub and
   must be recreated on the target machine

The goal is to stop treating the current machine as the only source of truth.

## GitHub baseline

Repository:

- fork: `rjstratminds/hermes-agent`
- PR branch carrying the current memory-provider baseline:
  `rj/memos-palace-plugin-2026-04-24`
- PR: `#2`

Tracked artifacts that are part of the clean-slate baseline:

- `plugins/memos_palace/`
  - canonical tracked source for the Hermes Spark composite memory provider
  - includes the schema-drift fix for MemOS `/product/search`
  - includes review fixes for prefetch caching and memOS disabled-store handling
  - includes OpenClaw parity behavior:
    - `top_k = 5`
    - `memory_limit = 5`
    - `query_context_depth = 4`
    - `max_item_chars = 220`
    - `include_assistant = false`
    - `max_message_chars = 3500`
    - `min_user_chars = 80`
    - `skip_vague_adds = true`
    - `skip_event_logs = true`
    - `event_log_penalty = 0.35`
    - `typed_memory_boost = 1.25`
    - `mempalace_limit = 4`
    - `mempalace_fallback_score_threshold = 0.62`
    - vague-query expansion from recent turn context before memOS recall
- `plugins/memos_palace/memos_palace.example.json`
  - portable config template for another host
- `plugins/memos_palace/README.md`
  - provider usage + parity note
- `docs/memos-palace-macbook-install-2026-04-24.md`
  - portable install/runbook
- `docs/specs/hermes-spark-memory-retrieval-rca-2026-04-21.md`
  - RCA plus follow-on OpenClaw parity alignment
- `docs/specs/onecli-mcp-memory-integration-2026-04-19.md`
  - tracked-home note, OneCLI integration state, and service-env requirement

## Host-local operational baseline

These items are required on the machine even though they are not yet fully
owned by the repo:

### 1. Hermes home

- `HERMES_HOME=/home/rj/.hermes`
- active config file: `~/.hermes/config.yaml`
- active memory provider: `memory.provider: memos_palace`

### 2. Runtime overlay copy

Current runtime still loads the user overlay copy:

- `~/.hermes/plugins/memos_palace/`

This must be kept in sync with `plugins/memos_palace/` until loader precedence
is changed to prefer the tracked copy.

### 3. Provider config

Active local config file:

- `~/.hermes/memos_palace.json`

Current required fields:

- `memos_api_url = https://openclaw-gcp.tailc13f7e.ts.net/memos`
- `mempalace_mcp_url = https://openclaw-gcp.tailc13f7e.ts.net/mempalace/mcp`
- `owner_user_id = rj@stratminds.vc`
- `principals_path = /home/rj/.hermes/memos_principals.json`

Current behavior knobs:

- `memos_top_k = 5`
- `memos_memory_limit = 5`
- `memos_query_context_depth = 4`
- `memos_max_item_chars = 220`
- `memos_add_retries = 1`
- `memos_dedup_enabled = true`
- `memos_include_assistant = false`
- `memos_max_message_chars = 3500`
- `memos_min_user_chars = 80`
- `memos_skip_vague_adds = true`
- `memos_skip_event_logs = true`
- `memos_event_log_penalty = 0.35`
- `memos_typed_memory_boost = 1.25`
- `memos_recent_dedup_window_secs = 900`
- `mempalace_limit = 4`
- `mempalace_fallback_score_threshold = 0.62`
- `mempalace_use_on_weak_coverage = false`

### 4. Principal mapping

Required local file:

- `~/.hermes/memos_principals.json`

Current critical mapping:

- `rj@stratminds.vc -> rj@stratminds.vc`
- `1643294159 -> rj@stratminds.vc`
- `telegram:1643294159 -> rj@stratminds.vc`

This is what keeps Hermes Spark scoped to the canonical owner identity.

### 5. Service unit

Current service:

- `~/.config/systemd/user/hermes-gateway.service`

Current required characteristics:

- `ExecStart=/home/rj/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway run --replace`
- `WorkingDirectory=/home/rj/.hermes/hermes-agent`
- `EnvironmentFile=-/home/rj/.config/onecli/hermes-spark-proxy.env`
- `Environment="HERMES_HOME=/home/rj/.hermes"`

The `EnvironmentFile` line is required for MemPalace parity. Without it,
`memos_palace` will hit the MCP HTTPS endpoint directly with
`Authorization: Bearer placeholder` and receive `401 Unauthorized`.

### 6. OneCLI env handoff

Required local file:

- `~/.config/onecli/hermes-spark-proxy.env`

This file must provide the proxy/TLS runtime env used by the service:

- `HTTP_PROXY`
- `HTTPS_PROXY`
- `ONECLI_PROXY_URL`
- `SSL_CERT_FILE`
- `REQUESTS_CA_BUNDLE`

### 7. Python runtime

Current service runtime:

- `/home/rj/.hermes/hermes-agent/venv`

Minimum expectation for the runtime environment:

- Hermes package dependencies installed
- `httpx` present for `memos_palace`
- normal service restart path works through systemd user service

## External authority baseline

The clean-slate design assumes these remain external authorities, not rebuilt
locally on the Hermes host:

- memOS authority: `openclaw-gcp`
- MemPalace authority / MCP endpoint: `openclaw-gcp`

Canonical remote endpoints:

- `https://openclaw-gcp.tailc13f7e.ts.net/memos`
- `https://openclaw-gcp.tailc13f7e.ts.net/mempalace/mcp`

The baseline is **client-of-authority**, not **replicated authority**.

## What "clean-slate reimplementation" should mean

If rebuilding Hermes Spark from zero, the target should be considered correct
only when all of the following are true:

1. The tracked `plugins/memos_palace/` code from GitHub is installed.
2. The runtime overlay copy is either:
   - synced from the tracked copy, or
   - eliminated by changing loader precedence.
3. `~/.hermes/memos_palace.json` carries the same memOS / MemPalace endpoint
   and behavior knobs.
4. `~/.hermes/memos_principals.json` maps platform IDs to
   `rj@stratminds.vc`.
5. `hermes-gateway.service` imports the OneCLI env file.
6. A post-restart smoke check confirms:
   - provider is available
   - memOS recall returns expected food-sensitivity memories
   - MemPalace path reaches the MCP endpoint through the proxy path

## Known remaining non-baselined items

These are still outside the clean-slate GitHub baseline:

- the exact local `~/.hermes/config.yaml`
- the OneCLI env file contents
- the user service unit itself
- the loader-precedence rule that still prefers `~/.hermes/plugins/`
- unrelated local operational patches such as the Notion MCP transport patch

## Recommended next step

If the intent is to make GitHub the true baseline for future rebuilds, the next
follow-up should be:

1. check in a deployment-owned template for `hermes-gateway.service`
2. check in a redacted template for `memos_palace.json`
3. switch runtime loading to prefer the tracked `plugins/memos_palace/`
4. add one reproducible smoke script that verifies memOS and MemPalace access
   after a fresh install
