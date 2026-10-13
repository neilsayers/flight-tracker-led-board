#!/usr/bin/env bash
# Download airline logos (named by ICAO airline code, e.g. BAW.png) from the public Jxck-S/airline-logos collection.
#   ./scripts/fetch-logos.sh [destination-folder]      (default: ./logos)
# Existing files are never overwritten, so your own replacements are safe. Needs git and an internet connection.
set -euo pipefail
DEST="${1:-$(cd "$(dirname "$0")/.." && pwd)/logos}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

git clone -q --depth 1 --filter=blob:none --sparse https://github.com/Jxck-S/airline-logos.git "$TMP/repo"
git -C "$TMP/repo" sparse-checkout set flightaware_logos radarbox_logos custom_logos

mkdir -p "$DEST"
added=0
for folder in flightaware_logos radarbox_logos custom_logos; do   # first match wins
  for f in "$TMP/repo/$folder"/*.png; do
    [ -e "$f" ] || continue
    code="$(basename "$f" .png | tr 'a-z' 'A-Z')"
    [[ "$code" =~ ^[A-Z0-9]{3}$ ]] || continue                  # ICAO airline codes only
    [ -e "$DEST/$code.png" ] && continue
    cp "$f" "$DEST/$code.png"
    added=$((added + 1))
  done
done
echo "Added $added logos to $DEST ($(ls "$DEST"/*.png | wc -l | tr -d ' ') in total)."
echo "These are the airlines' trademarks, included for identification only. Please don't publish them."
