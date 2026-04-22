# Browser-Harness Integration

Snapshot: 2026-04-22

## Overview

Hermes wraps [`browser-use/browser-harness`](https://github.com/browser-use/browser-harness)
as an opt-in toolset named `browser_harness`. The harness is the "self-healing"
CDP-over-websocket driver for the user's *real* Chrome/Chromium — the agent
writes Python calling `helpers.py` primitives, and when a primitive is missing
it adds it to a Hermes-local `helpers_hermes.py` rather than mutating vendored
code. Upstream updates stay clean.

## Layout

The integration keeps vendor code and Hermes code fully decoupled:

```text
~/opt/browser-harness/              ← vendor, READ-ONLY (git checkout pinned)
  helpers.py                        ← upstream primitive surface, untouched
  daemon.py, admin.py, run.py       ← daemon + CLI, untouched

~/.hermes/browser_harness/          ← Hermes-owned overlay, agent-editable
  helpers_hermes.py                 ← Hermes extensions + goto() override
  skills/<host>/*.md                ← agent-authored domain skills (host-scoped)
  vendor.pin                        ← pinned upstream commit SHA
  update_vendor.sh                  ← fetch-diff-bump helper
  README.md                         ← layout + invariants

~/.hermes/hermes-agent/
  tools/browser_harness_tool.py     ← adapter (this PR)
  toolsets.py                       ← registers `browser_harness` toolset
```

Upstream `goto(url)` returns a `domain_skills` list by scanning
`<vendor>/domain-skills/<host>/*.md`. The Hermes `goto` wrapper in
`helpers_hermes.py` additionally scans `~/.hermes/browser_harness/skills/<host>/`
and merges filenames in (de-duped, vendor wins on name collisions) so
Hermes-authored skills surface alongside upstream's.

## Invariants

1. **Vendor is read-only.** Never edit `~/opt/browser-harness/helpers.py` —
   upstream updates would conflict. When the agent needs a missing primitive,
   it appends to `~/.hermes/browser_harness/helpers_hermes.py`.
2. **Vendor updates go through `update_vendor.sh`.** The script fetches
   `origin/main`, shows the diff between the current pin and the target,
   prompts before advancing, and updates `vendor.pin`.
3. **Socket is system-wide.** `BU_NAME=default` (the adapter default) lets
   other local agents (Claude Code, Codex) attach to the same daemon at
   `/tmp/bu-default.sock`. Use a distinct `session` arg only for isolated
   remote sub-browsers.

## Tool surface

Single tool, `browser_harness`, auto-registered by
`tools/browser_harness_tool.py` via the discovery hook in `tools/registry.py`.

```text
browser_harness(code: str, timeout: float = 120, session: str | None = None) -> str
```

- `code` — Python executed inside the harness subprocess with
  `from helpers import *` (upstream) and `from helpers_hermes import *`
  (overlay) both pre-imported.
- `timeout` — seconds before abort (max 600).
- `session` — overrides `BU_NAME` for isolated daemons; defaults to
  `default` so calls share the local-browser daemon.

Return is a JSON string with `exit_code`, `stdout`, `stderr`, `vendor`,
`overlay`, `bu_name`.

Registered in `toolsets.py`:

```python
"browser_harness": {
    "description": "Drive the user's real Chrome via browser-harness (CDP).
                    Single self-healing tool: the agent writes Python
                    calling helpers.py primitives, and adds missing ones
                    to helpers_hermes.py.",
    "tools": ["browser_harness"],
    "includes": [],
}
```

## Transport / daemon

Every call spawns `uv run --project ~/opt/browser-harness python run.py` with
code on stdin. Upstream's `run.py` calls `ensure_daemon()` before `exec`, so
the daemon auto-starts on first call and is reused on subsequent calls via
its Unix socket at `/tmp/bu-{BU_NAME}.sock`. The daemon is long-lived;
Hermes does not stop it between tool calls.

## Local-browser bootstrap

Chromium/Chrome must be launched with `--remote-debugging-port=9222`. On this
host the auto-launch is wired up so a cold-started Chromium is always CDP-ready:

- `~/.local/bin/chromium` — wrapper shadows `/snap/bin/chromium` on `PATH`,
  prepends `--remote-debugging-port=9222`. Idempotent if the flag is already
  in argv.
- `~/.local/share/applications/chromium_chromium.desktop` — user-level XDG
  override copied from the snap-provided entry with the flag added to all
  four `Exec=` lines (main, New Window, New Incognito Window, Temp Profile).

To bypass (e.g., for testing), call `/snap/bin/chromium` directly.

## Chromium-snap gotchas

Two quirks of Ubuntu's snap Chromium vs. browser-harness's assumptions:

1. **Profile path.** Snap stores its profile at
   `/home/rj/snap/chromium/common/chromium/`, which isn't in
   `daemon.py`'s `PROFILES` list. Fixed by a symlink at
   `~/.config/chromium` → the snap path. Chromium's own writes are
   unaffected; browser-harness's *reads* of `DevToolsActivePort` resolve
   through the alias.
2. **Stale `DevToolsActivePort`.** Snap Chromium doesn't refresh the
   `DevToolsActivePort` file on restart when launched with
   `--remote-debugging-port=9222`, leaving the file's websocket UUID
   pointing at a dead session. The adapter works around this in
   `_live_cdp_ws()`: if `http://127.0.0.1:<port>/json/version` responds,
   the fresh `webSocketDebuggerUrl` is passed to the subprocess via
   `BU_CDP_WS`, which `daemon.py` honors before consulting the file.
   No vendor edits required.

A `browser-harness --doctor` run shows `chrome running` + (after first
`run.py` call) `daemon alive`.

## Availability gate

`_browser_harness_check()` returns True iff all of:

- `~/opt/browser-harness/` contains `run.py`, `helpers.py`, `daemon.py`, `admin.py`
- `~/.hermes/browser_harness/helpers_hermes.py` exists
- `uv` is on `PATH`

The tool is hidden from the model when any of these fail.

## Setup (reproducible)

Overlay seeds and the bootstrap script ship in `scripts/browser_harness/`:

```text
scripts/browser_harness/
  helpers_hermes.py      ← seed copy (agent edits its runtime copy, not this)
  README.md              ← seed copy
  update_vendor.sh       ← vendor bump helper
  setup_overlay.sh       ← idempotent bootstrap (overlay + Chromium launcher)
```

From a fresh machine:

```bash
# 1. Vendor
mkdir -p ~/opt && git clone https://github.com/browser-use/browser-harness ~/opt/browser-harness
cd ~/opt/browser-harness && uv sync

# 2. Overlay + Chromium launch config (idempotent; Ubuntu snap wrapper + XDG override)
scripts/browser_harness/setup_overlay.sh

# 3. First-time CDP attach
# Launch Chromium (now auto-gets --remote-debugging-port=9222), then:
cd ~/opt/browser-harness && uv run python run.py --doctor   # should show daemon FAIL
uv run python run.py --setup                                # walks chrome://inspect checkbox if needed
```

`setup_overlay.sh` is safe to re-run; it never clobbers existing overlay
files (preserves agent edits to `helpers_hermes.py`) and only installs the
Chromium wrapper / desktop override when they don't already exist.

## Prompt-injection mitigations

This tool drives the user's real logged-in browser and the agent can
self-edit its own primitive surface — both properties make prompt
injection a realistic concern (any page the agent reads is an untrusted
input channel). Three hardenings are in place:

1. **Anti-injection framing in the tool description.** The
   `browser_harness` schema explicitly tells the model that page content,
   titles, alt text, form values, CDP responses, and screenshot OCR are
   *data* — not instructions — and that money-moving, message-sending,
   destructive, and permission-changing actions require user confirmation
   regardless of what page text says.

2. **Gated self-editing.** Writes to
   `~/.hermes/browser_harness/helpers_hermes.py` and
   `~/.hermes/browser_harness/skills/` are blocked by
   `agent/file_safety.py` by default. Two ways to opt in:
   - **Process-scoped**: `HERMES_BROWSER_HARNESS_ALLOW_SELF_EDIT=1`
   - **Session-scoped (recommended)**: `/bh-self-edit` in the CLI, in
     any gateway platform that takes slash commands (Telegram, Discord,
     Slack, etc.), flips the gate on for *just that session*. Sent again
     to flip it off. Backed by in-memory state in
     `tools/approval.py::_session_bh_self_edit`, so it doesn't persist
     across Hermes restarts.

   This neutralizes the worst injection scenario — a page convincing the
   agent to persist a backdoor by appending a helper — while preserving
   self-healing as an explicit user opt-in. The usual flow: agent
   proposes a new primitive, writes blocked, agent asks the user "can I
   extend helpers_hermes?", user replies `/bh-self-edit` in Telegram (or
   whichever channel they're in), agent retries.

3. **Audit log.** Every `browser_harness` call is appended as a JSON line
   to `~/.hermes/logs/browser_harness/YYYY-MM-DD.jsonl` (UTC day-rolled)
   with timestamp, `BU_NAME`, code length + leading 2000 chars, timeout,
   exit code, and stdout/stderr sizes. Failures to write the audit log
   never fail the call.

Additional mitigations not yet wired (recommended follow-ups):

- **Dedicated Chromium profile** (not the user's daily driver) — biggest
  containment win. `setup_overlay.sh` could grow a `--agent-profile` mode
  that launches Chromium with `--user-data-dir=~/chromium-agent` so the
  agent only has access to sites the user explicitly logs into there.
- **Per-session CDP rate limiting** — integrate with Hermes' existing
  tool-budget system so the adapter refuses after N calls or N bytes of
  page content in a single session.
- **Targeted-extraction preference** — enforced at review time rather
  than runtime; the anti-injection framing already nudges toward
  `document.querySelector(...)` over full-page `get_text()`.

## Cloud / remote browsers (not yet wired)

`BROWSER_USE_API_KEY` is only needed for `start_remote_daemon(...)` —
parallel sub-browsers or headless servers. The default local path needs no
credentials. When we wire cloud mode: candidate integration is OneCLI as the
credential store, either by subprocess env injection (simple, key visible in
adapter memory) or by routing browser-harness's HTTP calls through the
OneCLI gateway (requires upstream `BROWSER_USE_BASE_URL` support —
contribution deferred).

## Open follow-ups

- Optional: surface `--doctor`, `--update`, `--setup` as a companion
  diagnostic tool, or leave them for the agent to invoke via `terminal`.
- Cloud-mode wiring + `BROWSER_USE_API_KEY` sourcing from OneCLI.
