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

From a fresh machine:

```bash
# 1. Vendor
mkdir -p ~/opt && git clone https://github.com/browser-use/browser-harness ~/opt/browser-harness
cd ~/opt/browser-harness && uv sync

# 2. Overlay
mkdir -p ~/.hermes/browser_harness/skills
git -C ~/opt/browser-harness rev-parse HEAD > ~/.hermes/browser_harness/vendor.pin
# ...then copy helpers_hermes.py, README.md, update_vendor.sh from this repo's
# scripts/browser_harness/ (TODO: move overlay seeds into the repo).

# 3. Chromium launch flag (Ubuntu snap example)
# Install ~/.local/bin/chromium wrapper and
# ~/.local/share/applications/chromium_chromium.desktop override.

# 4. First-time attach: relaunch Chromium with the flag, then:
cd ~/opt/browser-harness && uv run python run.py --doctor   # should show daemon FAIL
uv run python run.py --setup                                # walks you through chrome://inspect checkbox if needed
```

## Cloud / remote browsers (not yet wired)

`BROWSER_USE_API_KEY` is only needed for `start_remote_daemon(...)` —
parallel sub-browsers or headless servers. The default local path needs no
credentials. When we wire cloud mode: candidate integration is OneCLI as the
credential store, either by subprocess env injection (simple, key visible in
adapter memory) or by routing browser-harness's HTTP calls through the
OneCLI gateway (requires upstream `BROWSER_USE_BASE_URL` support —
contribution deferred).

## Open follow-ups

- Move overlay seeds (`helpers_hermes.py`, `README.md`, `update_vendor.sh`)
  into `hermes-agent/scripts/browser_harness/` so overlay setup is
  reproducible from this repo alone.
- Optional: surface `--doctor`, `--update`, `--setup` as a companion
  diagnostic tool, or leave them for the agent to invoke via `terminal`.
- Cloud-mode wiring + `BROWSER_USE_API_KEY` sourcing from OneCLI.
