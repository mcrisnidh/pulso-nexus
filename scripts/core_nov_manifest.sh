#!/usr/bin/env bash
# Manifiesto SHA-256 de noviembre 2026: código, pruebas, documentación y evidencia.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=docs/evidence/CORE-NOV/MANIFEST.sha256
git ls-files -co --exclude-standard -- src/nexus/core src/nexus/web src/nexus/cli.py \
  tests/core/physical_fixtures.py tests/core/test_core_physical.py tests/core/test_core_stage.py \
  tests/core/test_core_november.py tests/core/test_core_portability.py \
  tests/web/test_core_web_replay.py tests/architecture/test_no_real_sources.py \
  docs/core/NOV2026_REPLAY.md docs/evidence/CORE-NOV pyproject.toml .gitignore \
  scripts/core_nov_manifest.sh | grep -v -e '__pycache__' -e 'MANIFEST.sha256$' | LC_ALL=C sort \
  | xargs -d '\n' sha256sum > "$OUT"
sha256sum -c "$OUT" --quiet && wc -l < "$OUT"
