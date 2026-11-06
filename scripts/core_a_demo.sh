#!/usr/bin/env bash
# Demostración reproducible del Hito A de CORE v1.0 (solo datos sintéticos, solo loopback).
# Uso: bash scripts/core_a_demo.sh DIRECTORIO_VACÍO_FUERA_DEL_REPO
set -euo pipefail
OUT="${1:?directorio de salida}"
mkdir -p "$OUT"
DATA="$OUT/data"; SHOTS="$OUT/screenshots"; mkdir -p "$SHOTS"
export NEXUS_WEB_SECRET="${NEXUS_WEB_SECRET:-$(python3 -c 'import secrets;print(secrets.token_hex(32))')}"
echo "== 1. Demostración de dos meses (driver web, sin red)"
uv run --frozen python scripts/core_a_demo.py "$DATA" | tee "$OUT/demo.txt"
echo "== 2. Servidor web solo en loopback + navegador headless"
PORT="${PORT:-8765}"
uv run --frozen nexus web --data "$DATA" --channel sintetico --host 127.0.0.1 --port "$PORT" \
  --simulate-start 2031-02-10T21:00:00Z > "$OUT/web.log" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break; sleep 0.2; done
echo "sockets en escucha en el puerto $PORT (desde /proc/net/tcp*):"
python3 - "$PORT" <<'PY' | tee "$OUT/listen.txt"
import socket, struct, sys
port = int(sys.argv[1]); found = []
for fam, path in ((socket.AF_INET, "/proc/net/tcp"), (socket.AF_INET6, "/proc/net/tcp6")):
    try:
        lines = open(path).read().splitlines()[1:]
    except OSError:
        continue
    for line in lines:
        local, state = line.split()[1], line.split()[3]
        addr, p = local.split(":")
        if state != "0A" or int(p, 16) != port:
            continue
        raw = bytes.fromhex(addr)
        raw = b"".join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4))
        found.append(socket.inet_ntop(fam, raw))
print(" ".join(found) or "ninguno")
sys.exit(0 if found and all(a in ("127.0.0.1", "::1") for a in found) else 1)
PY
echo "== 3. Rechazo de host no loopback"
if uv run --frozen nexus web --data "$DATA" --host 0.0.0.0 --port $((PORT+1)) > "$OUT/reject.txt" 2>&1; then
  echo "NO-GO: aceptó 0.0.0.0"; exit 1; fi
cat "$OUT/reject.txt"
node scripts/core_a_browser.js "http://127.0.0.1:$PORT" "$SHOTS" | tee "$OUT/browser.txt"
echo "== OK"
