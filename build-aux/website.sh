#!/usr/bin/env bash
# Assemble the GitHub Pages site. Icons and screenshots are copied from data/,
# so the site always shows what the app ships.
#
#   build-aux/website.sh [OUTPUT_DIR]      (default: build/website)
set -euo pipefail

cd "$(dirname "$0")/.."
OUT="${1:-build/website}"
ICON=data/icons/hicolor/scalable/apps/io.github.euvinicios.PodFlow.svg

rm -rf "$OUT"
mkdir -p "$OUT/assets" "$OUT/screenshots"
cp -R website/. "$OUT/"
cp "$ICON" "$OUT/assets/icon.svg"
cp data/screenshots/*.png "$OUT/screenshots/"
if command -v rsvg-convert >/dev/null 2>&1; then
    rsvg-convert -w 180 -h 180 "$ICON" -o "$OUT/assets/icon-180.png"
else
    echo "aviso: rsvg-convert ausente, assets/icon-180.png não foi gerado" >&2
fi
touch "$OUT/.nojekyll"
echo "Site gerado em $OUT"
