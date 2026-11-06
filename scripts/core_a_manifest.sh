#!/usr/bin/env bash
# Manifiesto SHA-256 del Hito A: código CORE/web, pruebas, scripts, documentación y evidencia.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=docs/evidence/CORE-A/MANIFEST.sha256
git ls-files -co --exclude-standard -- src/nexus/core src/nexus/web src/nexus/pipeline/worker.py \
  src/nexus/cli.py tests/core tests/web tests/architecture/test_boundaries.py scripts/core_a_* \
  docs/core docs/adr/0010-flask-web-minima.md docs/adr/0011-contratos-y-estado-core.md \
  docs/evidence/CORE-A pyproject.toml uv.lock \
  | grep -v -e '__pycache__' -e 'MANIFEST.sha256$' | LC_ALL=C sort | xargs sha256sum > "$OUT"
sha256sum -c "$OUT" --quiet && wc -l < "$OUT"
