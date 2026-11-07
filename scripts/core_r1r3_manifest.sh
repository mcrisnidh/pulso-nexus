#!/usr/bin/env bash
# Manifiesto SHA-256 de la intervención R1/R3 y Frozen definitivo.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=docs/evidence/CORE-R1R3/MANIFEST.sha256
git ls-files -co --exclude-standard -- src/nexus/core src/nexus/cli.py tests/core tests/web \
  docs/decisions-register.md docs/core/NOV2026_REPLAY.md docs/evidence/CORE-R1R3 \
  scripts/core_r1r3_manifest.sh | grep -v -e '__pycache__' -e 'MANIFEST.sha256$' | LC_ALL=C sort \
  | xargs -d '\n' sha256sum > "$OUT"
sha256sum -c "$OUT" --quiet && wc -l < "$OUT"
