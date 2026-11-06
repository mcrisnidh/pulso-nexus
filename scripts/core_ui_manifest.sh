#!/usr/bin/env bash
# Manifiesto SHA-256 de la adenda UI/UX: interfaz, pruebas web, scripts y evidencia.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=docs/evidence/CORE-UI/MANIFEST.sha256
git ls-files -co --exclude-standard -- src/nexus/web tests/web scripts/core_a_browser.js \
  scripts/core_a_demo.py scripts/core_a_demo.sh scripts/core_ui_screens.js scripts/core_ui_manifest.sh \
  docs/evidence/CORE-UI | grep -v -e '__pycache__' -e 'MANIFEST.sha256$' | LC_ALL=C sort | xargs sha256sum > "$OUT"
sha256sum -c "$OUT" --quiet && wc -l < "$OUT"
