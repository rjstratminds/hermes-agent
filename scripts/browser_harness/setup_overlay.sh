#!/usr/bin/env bash
# Bootstrap the Hermes browser-harness overlay at ~/.hermes/browser_harness.
#
# What this does, all idempotent:
#   1. Seeds the overlay dir with helpers_hermes.py, README.md, update_vendor.sh
#      (leaves existing files alone; the agent's edits to helpers_hermes.py
#      are preserved).
#   2. Writes vendor.pin with the current HEAD of ~/opt/browser-harness.
#   3. (Ubuntu snap Chromium only) creates ~/.config/chromium symlink,
#      ~/.local/bin/chromium wrapper, and user-level .desktop override so
#      Chromium always launches with --remote-debugging-port=9222.
#
# Vendor (browser-harness) must already exist at $VENDOR (default
# ~/opt/browser-harness). Clone it first:
#   git clone https://github.com/browser-use/browser-harness ~/opt/browser-harness
#   cd ~/opt/browser-harness && uv sync

set -euo pipefail

VENDOR="${VENDOR:-$HOME/opt/browser-harness}"
OVERLAY="${OVERLAY:-$HOME/.hermes/browser_harness}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

[ -d "$VENDOR/.git" ] || { echo "error: $VENDOR is not a git checkout of browser-harness" >&2; exit 1; }

mkdir -p "$OVERLAY/skills"

# 1. Seed overlay files (only if missing — don't clobber agent edits).
for f in helpers_hermes.py README.md update_vendor.sh; do
  if [ ! -e "$OVERLAY/$f" ]; then
    cp "$SCRIPT_DIR/$f" "$OVERLAY/$f"
    [ "$f" = "update_vendor.sh" ] && chmod +x "$OVERLAY/$f"
    echo "seeded $OVERLAY/$f"
  fi
done

# 2. Pin to current vendor HEAD if no pin yet.
if [ ! -f "$OVERLAY/vendor.pin" ]; then
  git -C "$VENDOR" rev-parse HEAD > "$OVERLAY/vendor.pin"
  echo "pinned vendor at $(cat "$OVERLAY/vendor.pin")"
fi

# 3. Chromium launch config — only if snap Chromium is the one in use.
if [ -x /snap/bin/chromium ] && [ ! -e "$HOME/.config/chromium" ] && [ -d "$HOME/snap/chromium/common/chromium" ]; then
  ln -s "$HOME/snap/chromium/common/chromium" "$HOME/.config/chromium"
  echo "symlinked ~/.config/chromium -> snap profile"
fi

if [ -x /snap/bin/chromium ] && [ ! -e "$HOME/.local/bin/chromium" ]; then
  mkdir -p "$HOME/.local/bin"
  cat > "$HOME/.local/bin/chromium" <<'WRAP'
#!/usr/bin/env bash
# Chromium wrapper installed by Hermes browser-harness integration.
# Ensures Chromium always starts with --remote-debugging-port=9222 so
# browser-harness can attach via CDP. Call /snap/bin/chromium directly to
# bypass. Already-present --remote-debugging-port* in argv takes precedence.
for arg in "$@"; do
  case "$arg" in --remote-debugging-port*) exec /snap/bin/chromium "$@" ;; esac
done
exec /snap/bin/chromium --remote-debugging-port=9222 "$@"
WRAP
  chmod +x "$HOME/.local/bin/chromium"
  echo "installed ~/.local/bin/chromium wrapper"
fi

if [ -x /snap/bin/chromium ] \
   && [ -f /var/lib/snapd/desktop/applications/chromium_chromium.desktop ] \
   && [ ! -e "$HOME/.local/share/applications/chromium_chromium.desktop" ]; then
  mkdir -p "$HOME/.local/share/applications"
  sed \
    -e 's|^Exec=/snap/bin/chromium %U$|Exec=/snap/bin/chromium --remote-debugging-port=9222 %U|' \
    -e 's|^Exec=/snap/bin/chromium$|Exec=/snap/bin/chromium --remote-debugging-port=9222|' \
    -e 's|^Exec=/snap/bin/chromium --incognito$|Exec=/snap/bin/chromium --remote-debugging-port=9222 --incognito|' \
    -e 's|^Exec=/snap/bin/chromium --temp-profile$|Exec=/snap/bin/chromium --remote-debugging-port=9222 --temp-profile|' \
    /var/lib/snapd/desktop/applications/chromium_chromium.desktop \
    > "$HOME/.local/share/applications/chromium_chromium.desktop"
  command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications/" || true
  echo "installed user-level chromium_chromium.desktop override"
fi

echo "overlay ready at $OVERLAY"
echo "next: launch Chromium (it will pick up --remote-debugging-port=9222),"
echo "then run: cd $VENDOR && uv run python run.py --doctor"
