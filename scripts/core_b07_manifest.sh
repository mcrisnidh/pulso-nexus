#!/usr/bin/env bash
# Manifiesto SHA-256 de B0-7: código, pruebas, documentación y evidencia.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=docs/evidence/CORE-B07/MANIFEST.sha256
git ls-files -co --exclude-standard -- src/nexus/core tests/core/test_core_timezone.py \
  tests/core/tz_scenarios.py docs/core/B0-7_ZONA_HORARIA.md docs/evidence/CORE-B07 pyproject.toml \
  scripts/core_b07_manifest.sh | grep -v -e '__pycache__' -e 'MANIFEST.sha256$' | LC_ALL=C sort \
  | xargs sha256sum > "$OUT"
sha256sum -c "$OUT" --quiet && wc -l < "$OUT"
