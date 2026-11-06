#!/usr/bin/env bash
# Genera docs/evidence/E1-B/MANIFEST.sha256: SHA-256 de todos los archivos versionados
# (git ls-files), excluido el propio manifiesto. Orden determinista (LC_ALL=C).
# No modifica el manifiesto congelado de E1-A.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
OUT="docs/evidence/E1-B/MANIFEST.sha256"
git ls-files -z | LC_ALL=C sort -z | grep -zv "^${OUT}\$" | xargs -0 sha256sum > "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
echo "$(wc -l < "$OUT") archivos en $OUT"
