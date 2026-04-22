#!/usr/bin/env bash
# Update the vendored browser-harness checkout to the latest upstream commit.
#
# Usage:
#   update_vendor.sh           # fetch, show diff between pin and origin/main, prompt
#   update_vendor.sh --latest  # same, but bump pin to origin/main after confirmation
#   update_vendor.sh <sha>     # check out a specific sha and update the pin
#
# The vendor tree at ~/opt/browser-harness is never edited — only checked out
# to different commits. Local extensions live in helpers_hermes.py here.

set -euo pipefail

VENDOR="${VENDOR:-$HOME/opt/browser-harness}"
OVERLAY="$(cd "$(dirname "$0")" && pwd)"
PIN_FILE="$OVERLAY/vendor.pin"

[ -d "$VENDOR/.git" ] || { echo "error: $VENDOR is not a git checkout" >&2; exit 1; }
[ -f "$PIN_FILE" ]    || { echo "error: $PIN_FILE missing" >&2; exit 1; }

current_pin=$(tr -d '[:space:]' < "$PIN_FILE")
git -C "$VENDOR" fetch --quiet origin

target=""
case "${1:-}" in
  --latest) target=$(git -C "$VENDOR" rev-parse origin/main) ;;
  "")       target=$(git -C "$VENDOR" rev-parse origin/main) ;;
  *)        target="$1" ;;
esac

if [ "$current_pin" = "$target" ]; then
  echo "already at $current_pin"
  exit 0
fi

echo "pinned : $current_pin"
echo "target : $target"
echo
echo "--- changes ---"
git -C "$VENDOR" log --oneline "$current_pin..$target" | head -30
echo

if [ "${1:-}" != "--latest" ] && [ -z "${CONFIRM:-}" ]; then
  read -r -p "bump pin and check out $target? [y/N] " reply
  [ "$reply" = "y" ] || [ "$reply" = "Y" ] || { echo "aborted"; exit 0; }
fi

git -C "$VENDOR" checkout --quiet "$target"
printf '%s\n' "$target" > "$PIN_FILE"
echo "pinned to $target"
