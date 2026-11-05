#!/usr/bin/env bash
# Evidencia E1-A-R1 en una clonación limpia (opcionalmente con core.autocrlf=true).
# Uso: scripts/r1_evidence.sh <repo> <commit> <dir_salida> <dir_trabajo> [autocrlf: true|false]
set -uo pipefail
SRC_REPO="$1"; COMMIT="$2"; OUT="$3"; WORK="$4"; AUTOCRLF="${5:-false}"
export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS
rm -rf "$WORK"; mkdir -p "$OUT"
git -c core.autocrlf="$AUTOCRLF" clone -q "$SRC_REPO" "$WORK" && git -C "$WORK" -c core.autocrlf="$AUTOCRLF" checkout -q "$COMMIT"
git -C "$WORK" config core.autocrlf "$AUTOCRLF"
cd "$WORK"
SUMMARY="$OUT/summary.tsv"; : > "$SUMMARY"
run() {
  local id="$1"; shift
  local log="$OUT/$id.txt"
  { echo "\$ $*"; echo "# commit: $(git rev-parse HEAD)  core.autocrlf=$(git config core.autocrlf)  fecha_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"; echo; } > "$log"
  bash -c "$*" >> "$log" 2>&1; local rc=$?
  echo "# exit_code: $rc" >> "$log"
  printf '%s\t%s\t%s\n' "$id" "$rc" "$*" >> "$SUMMARY"
}
run 00_clean_status "git status --porcelain && git rev-parse HEAD && git log -1 --format=%s"
run 01_fixture_bytes "(cd tests/fixtures/synthetic && sha256sum -c --strict SHA256SUMS) && git ls-files --eol tests/fixtures/synthetic/"
run 02_uv_sync "uv sync --frozen --python 3.12"
run 03_ruff "uv run ruff check . && uv run ruff format --check ."
run 04_mypy "uv run mypy --strict src"
run 05_lint_imports "uv run lint-imports"
run 06_pytest_cov "uv run pytest -p no:cacheprovider -ra --cov=nexus --cov-branch --cov-report=term-missing --cov-fail-under=90 --junitxml=$OUT/junit.xml"
run 07_coverage_by_package "for p in 'src/nexus/hashing.py' 'src/nexus/storage/*' 'src/nexus/snapshots/*' 'src/nexus/pipeline/*' 'src/nexus/engines/*'; do echo \"== \$p\"; uv run coverage report --include=\"\$p\" --fail-under=90 | tail -1 || exit 1; done"
run 08_repro_adv_arch "uv run pytest -p no:cacheprovider tests/reproducibility tests/adversarial tests/architecture -v"
run 09_repro_threads1_utc_C "NEXUS_THREADS=1 TZ=UTC LC_ALL=C uv run pytest -p no:cacheprovider tests/reproducibility -v"
run 10_repro_threads8_mx_cutf8 "NEXUS_THREADS=8 TZ=America/Mexico_City LC_ALL=C.UTF-8 uv run pytest -p no:cacheprovider tests/reproducibility -v"
run 11_demo "bash scripts/e1a_demo.sh"
run 12_toctou_probes "uv run pytest -p no:cacheprovider tests/adversarial/test_toctou.py -v -s"
run 13_toctou_x20 "for i in \$(seq 1 20); do uv run pytest -p no:cacheprovider -q -s tests/adversarial/test_toctou.py 2>&1 | grep -E 'swaps=|passed|failed' | tr '\n' ' '; echo; done"
run 14_post_status "git status --porcelain --untracked-files=no && echo 'arbol limpio tras las pruebas'"
cat "$SUMMARY" | cut -f1,2
