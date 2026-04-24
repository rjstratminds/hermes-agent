#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_DIR="$ROOT_DIR/plugins/memos_palace"
OUT_DIR="${1:-$ROOT_DIR/dist}"
VERSION="$(date +%Y%m%d-%H%M%S)"
STAGE_DIR="$OUT_DIR/memos_palace-$VERSION"
ARCHIVE="$OUT_DIR/memos_palace-$VERSION.tar.gz"

mkdir -p "$OUT_DIR"
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"

cp -R "$PLUGIN_DIR" "$STAGE_DIR/memos_palace"

cat >"$STAGE_DIR/INSTALL.txt" <<'EOF'
Install on the target machine:

1. mkdir -p ~/.hermes/plugins
2. cp -R memos_palace ~/.hermes/plugins/memos_palace
3. cp ~/.hermes/plugins/memos_palace/memos_palace.example.json ~/.hermes/memos_palace.json
4. Edit ~/.hermes/memos_palace.json with the target machine's endpoints and owner_user_id
5. Set memory.provider: memos_palace in ~/.hermes/config.yaml
6. Restart Hermes or the Hermes gateway process
7. Ask a known memory question and run an explicit memos_store check

On macOS launchd installs, make sure the launched Hermes process sees the same
MEMOS_*/MEMPALACE_* environment or that ~/.hermes/memos_palace.json contains the
full config.
EOF

tar -C "$OUT_DIR" -czf "$ARCHIVE" "$(basename "$STAGE_DIR")"
rm -rf "$STAGE_DIR"

printf '%s\n' "$ARCHIVE"
