# Hermes ⇆ browser-harness overlay

Hermes-specific state for the vendored [browser-harness](https://github.com/browser-use/browser-harness)
install at `~/opt/browser-harness/`.

## Layout

```
~/opt/browser-harness/            ← vendor, READ-ONLY (git checkout)
  helpers.py, daemon.py, ...

~/.hermes/browser_harness/        ← THIS directory, Hermes-owned
  helpers_hermes.py               ← agent-editable extensions
  skills/                         ← agent-authored domain skills
  .env                            ← secrets (generated from Hermes config)
  vendor.pin                      ← pinned upstream commit
  update_vendor.sh                ← run to bump the pin
```

## Invariants

- **Vendor is read-only.** `helpers.py` in `~/opt/browser-harness/` must never be
  edited in place — that would conflict with upstream updates. When the agent
  discovers a missing primitive mid-task, it adds it to `helpers_hermes.py`.
- **Updates happen via `update_vendor.sh`.** It fetches upstream, shows the
  diff between the current pin and `origin/main`, and only advances the pin
  after a smoke test (`page_info()` round-trip) passes.
- **Socket scope is system-wide.** The browser-harness daemon listens on
  `/tmp/bu-{BU_NAME}.sock`. Default `BU_NAME=default` lets other local agents
  (Claude Code, Codex) attach to the same real-browser daemon.
