#!/usr/bin/env bash
# Demostración end-to-end de E1-A sobre un almacén temporal, con datos SINTÉTICOS.
# Sin red, sin SQL, sin credenciales. Sale con un código distinto de cero ante cualquier
# desviación del comportamiento esperado.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
NEXUS=(uv run --frozen nexus)
WORK="$(mktemp -d "${TMPDIR:-/tmp}/nexus-e1a-demo.XXXXXX")"
STORE="$WORK/store"
AS_OF="2026-12-31T00:00:00Z"
cleanup() { chmod -R u+w "$WORK" 2>/dev/null || true; rm -rf "$WORK"; }
trap cleanup EXIT

field() { uv run --frozen python -c 'import json,sys; d=json.load(sys.stdin); print(d[sys.argv[1]])' "$1"; }
step() { printf '\n== %s\n' "$*"; }

step "1. Regenerar fixtures sintéticos y verificar SHA256SUMS"
uv run --frozen python scripts/make_synthetic_fixtures.py --out "$WORK/fixtures"
cmp "$WORK/fixtures/input_v1.csv" tests/fixtures/synthetic/input_v1.csv
cmp "$WORK/fixtures/input_v1_invalid.csv" tests/fixtures/synthetic/input_v1_invalid.csv
(cd tests/fixtures/synthetic && sha256sum -c SHA256SUMS)

step "2. nexus snapshot"
SNAP_JSON="$("${NEXUS[@]}" snapshot --store "$STORE" --path tests/fixtures/synthetic/input_v1.csv)"
echo "$SNAP_JSON"
SNAP="$(echo "$SNAP_JSON" | field snapshot_id)"

step "3. nexus run (dos veces, mismo input_manifest, hilos/TZ distintos)"
RUN_A_JSON="$(NEXUS_THREADS=1 TZ=UTC LC_ALL=C "${NEXUS[@]}" run --store "$STORE" --snapshot "$SNAP" --as-of "$AS_OF")"
RUN_B_JSON="$(NEXUS_THREADS=8 TZ=America/Mexico_City LC_ALL=C.UTF-8 "${NEXUS[@]}" run --store "$STORE" --snapshot "$SNAP" --as-of "$AS_OF" --trigger scheduled)"
RUN_A="$(echo "$RUN_A_JSON" | field run_id)"
RUN_B="$(echo "$RUN_B_JSON" | field run_id)"
DIG_A="$(echo "$RUN_A_JSON" | field result_digest)"
DIG_B="$(echo "$RUN_B_JSON" | field result_digest)"
IN_A="$(echo "$RUN_A_JSON" | field input_digest)"
IN_B="$(echo "$RUN_B_JSON" | field input_digest)"
echo "run A: $RUN_A  result_digest=$DIG_A  input_digest=$IN_A"
echo "run B: $RUN_B  result_digest=$DIG_B  input_digest=$IN_B"

step "4. Comprobación: run_id distintos; result_digest e input_digest iguales"
[[ "$RUN_A" != "$RUN_B" ]] || { echo "FALLO: run_id iguales"; exit 1; }
[[ "$DIG_A" == "$DIG_B" ]] || { echo "FALLO: result_digest distintos"; exit 1; }
[[ "$IN_A" == "$IN_B" ]] || { echo "FALLO: input_digest distintos"; exit 1; }
echo "OK"

step "5. nexus replay --level L3 y nexus verify --all"
"${NEXUS[@]}" replay --store "$STORE" --run-id "$RUN_A" --level L3
"${NEXUS[@]}" verify --store "$STORE" --all

step "6. Alterar 1 byte de un artefacto: verify y replay deben fallar cerrado"
ARTIFACT="$(find "$STORE/objects/runs/$RUN_A/generation" -name 'synthetic_output-*.parquet' | head -1)"
chmod u+w "$ARTIFACT"
uv run --frozen python - "$ARTIFACT" <<'PY'
import sys
p = sys.argv[1]
data = bytearray(open(p, "rb").read())
data[len(data) // 2] ^= 0x01
open(p, "wb").write(bytes(data))
PY
set +e
"${NEXUS[@]}" verify --store "$STORE" --all; VERIFY_RC=$?
"${NEXUS[@]}" replay --store "$STORE" --run-id "$RUN_A" --level L3; REPLAY_RC=$?
set -e
echo "verify exit=$VERIFY_RC (esperado 5) · replay exit=$REPLAY_RC (esperado 3)"
[[ $VERIFY_RC -eq 5 && $REPLAY_RC -eq 3 ]] || { echo "FALLO: la alteración no se detectó"; exit 1; }

step "7. La corrida B no fue afectada y sigue reproduciendo en L3"
"${NEXUS[@]}" replay --store "$STORE" --run-id "$RUN_B" --level L3 >/dev/null
echo "OK"

printf '\nDEMO E1-A: COMPLETADA SIN DESVIACIONES\n'
