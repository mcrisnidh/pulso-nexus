# Noviembre 2026 — fuentes reales en `replay` (procedimiento ASMXC1200)

Plan autorizado: `PLAN_MINIMO_CORE_NOV2026.md`, con las condiciones C1–C4. Este documento indica **cómo** ejecutar `probe`, `stage` y el replay de noviembre en el laboratorio ASMXC1200.

> **Antes de empezar**
> - Este procedimiento se ejecuta **solo en ASMXC1200**, con copias autorizadas.
> - No se suben a GitHub, al chat ni a ningún servicio externo:
>   - los originales (`R_*.TXT`, `Portal_CAT_*.csv`, `INV_STA*.TXT`, libros con ODBC, MexicoDC, CFDI);
>   - el directorio de datos;
>   - la tabla de homologación;
>   - el archivo de claves de cliente.
> - Lo **único** que se comparte para ajustar el layout es el informe saneado de `probe` (§2).
> - **Ningún paso toca** SQL Server, ASMXC1012, PULSO Web, MAGNUM, AMFT ni el canal operativo.

## 1. Qué hace el código

| Pieza | Qué hace | Qué nunca hace |
|---|---|---|
| `nexus core probe` | Lee un archivo en **modo binario de solo lectura**. Informa su estructura **saneada**: letras → `A`, dígitos → `9`, codificación, saltos de línea, clases y longitudes de línea, conteos y códigos de excepción con su línea física | Imprimir filas, ITEM, clientes, pedidos o cantidades. Escribir junto al origen |
| `nexus core stage` | Toma las copias autorizadas de una carpeta y escribe, en un **directorio privado**: el original copiado por SHA-256 en `originals/` (solo lectura), el CSV del contrato CORE en `inbox/` y el sidecar de procedencia en `provenance/` | Modificar el origen. Escribir dentro de un repositorio git o dentro del origen. Reemplazar un archivo existente. Calcular o publicar |
| `nexus core refresh --channel replay` | Calcula y publica en el almacén **físico propio** del replay (`DATA\replay\store`). La vista se marca `INFORMATIVE_NOT_CERTIFIED` | Escribir en `DATA\store` (operativo y sintético). Producir una publicación oficial |
| Canal `operativo` | Sin cambios: exige embarques físicos con el mismo corte que FG, el mes vigente y fuentes no sintéticas | — |

El sidecar guarda los **tiempos por separado**:
- **documento**: `Date: 11/01/26` de OE965, con precisión de fecha;
- **extracción**: `null`, porque la fuente no la declara;
- **recepción**: reloj de `stage`.

Ninguna hora se toma del nombre de archivo. La marca `_0955` del Portal no se usa.

### Nivel de cada cifra en `replay`

| Cifra | Estado |
|---|---|
| Demanda congelada | PROVISIONAL. Desconocida si hay líneas CAT sin Portal homologado (R1), una cantidad abierta vacía o una fecha Portal ambigua |
| FG asignable | PROVISIONAL, desde IN092U stockroom FG. Un ITEM ausente es **desconocido** («Excludes Zero»). Un negativo es NO CERTIFICABLE |
| Embarcado, Pendiente, Cubierto, Sin cubrir | **BLOCKED_SOURCE**, sin valor. Se muestra «—» con «No certificado» |
| KPI oficial | Nunca en `replay` |

## 2. Procedimiento en ASMXC1200 (PowerShell)

### 2.0 Requisitos previos

- El repositorio está en `C:\Users\ucbaa\pulso-nexus`, en la rama `feature/core-v1`, con este commit.
- Windows necesita la base de zonas `tzdata` para `America/Mexico_City`. **No se agrega al lockfile**: se carga como capa efímera con `--with tzdata==2025.2` en cada comando que usa la zona.
- Sin `tzdata`, `stage` pone IN092U en cuarentena (`TZ_UNAVAILABLE`) y el servicio no arranca. Es un fallo cerrado.

Variables de la sesión:

```powershell
cd C:\Users\ucbaa\pulso-nexus
git fetch origin feature/core-v1
git checkout feature/core-v1
git pull origin feature/core-v1
$LAB  = Join-Path $env:LOCALAPPDATA 'PULSO_NEXUS_LAB'
$SRC  = Join-Path $LAB 'inputs\2026-11'
$DATA = Join-Path $LAB 'replay-2026-11'
$PRIV = Join-Path $LAB 'private'
New-Item -ItemType Directory -Force $SRC, $DATA, $PRIV | Out-Null
```

**Copie a mano** en `$SRC` únicamente estos archivos:
- `R_20261101.TXT`;
- `Portal_CAT_2026-11-03_0955.csv`;
- `INV_STA289.TXT`;
- si existen, los `R_2026110x.TXT` posteriores.

Antes de seguir, registre la huella de los originales:

```powershell
Get-FileHash -Algorithm SHA256 (Join-Path $SRC '*') | Format-Table Hash, Path -AutoSize | Out-File (Join-Path $PRIV 'sha256_originales.txt')
```

### 2.1 Pruebas offline en Windows

```powershell
uv sync --frozen
uv run --frozen --with tzdata==2025.2 pytest -m "not sql" -p no:cacheprovider -q
uv run --frozen ruff check .
uv run --frozen mypy --strict src
uv run --frozen lint-imports
```

- Esperado: **607 passed** y 12 skipped. Los 12 skipped son las pruebas Pilot-20 con datos reales.
- En Windows algunas pruebas POSIX de E1-A se omiten por diseño (`posix_only`), así que el número de omitidas puede ser mayor.
- Si algo falla, **no continúe**: comparta la salida, que no contiene datos reales.

### 2.2 Probe: bloqueo R2, solo lectura

```powershell
uv run --frozen nexus core probe --file (Join-Path $SRC 'R_20261101.TXT') --out (Join-Path $PRIV 'probe_R_20261101.json')
uv run --frozen nexus core probe --file (Join-Path $SRC 'Portal_CAT_2026-11-03_0955.csv') --show-headers --out (Join-Path $PRIV 'probe_portal.json')
uv run --frozen --with tzdata==2025.2 nexus core probe --file (Join-Path $SRC 'INV_STA289.TXT') --tz America/Mexico_City --out (Join-Path $PRIV 'probe_INV_STA289.json')
```

Claves de cliente distintas (R3). Se escriben en un archivo **privado** y **no se comparten**:

```powershell
uv run --frozen nexus core probe --file (Join-Path $SRC 'R_20261101.TXT') --keys-out (Join-Path $PRIV 'claves_cliente_nov.csv')
```

Compruebe que los originales siguen intactos:

```powershell
Get-FileHash -Algorithm SHA256 (Join-Path $SRC '*') | Format-Table Hash, Path -AutoSize | Out-File (Join-Path $PRIV 'sha256_tras_probe.txt')
Compare-Object (Get-Content (Join-Path $PRIV 'sha256_originales.txt')) (Get-Content (Join-Path $PRIV 'sha256_tras_probe.txt'))
```

`Compare-Object` sin salida significa que no hubo cambios.

**Qué compartir:** solo los tres `probe_*.json`. Antes de enviarlos, revíselos: deben contener únicamente `A`, `9`, conteos y códigos.

- Si `structure.valid` es `true` en los tres, el layout provisional coincide con el archivo real.
- Si no, el informe indica qué ajustar: clases `unclassified`, `HEADER_NOT_FOUND`, `header_names`, longitudes. El ajuste se hace en una copia de `src/nexus/core/layouts/physical_v1.json` pasada con `--layout`, **sin filas reales**.

`--show-headers` incluye los **nombres de columna** del Portal: son encabezados, no filas. Si la política local los considera sensibles, omita esa opción.

### 2.2-bis Segundo probe (R2, tras el ajuste de lectores)

Repita **solo** los tres probes con el commit que contiene `docs/evidence/CORE-NOV/R2_PROBE_1.md`. Escriba en archivos nuevos: `--out` nunca reemplaza un archivo existente.

```powershell
git fetch origin feature/core-v1
git checkout feature/core-v1
git pull origin feature/core-v1
uv run --frozen nexus core probe --file (Join-Path $SRC 'R_20261101.TXT') --out (Join-Path $PRIV 'probe2_R_20261101.json')
uv run --frozen nexus core probe --file (Join-Path $SRC 'Portal_CAT_2026-11-03_0955.csv') --show-headers --out (Join-Path $PRIV 'probe2_portal.json')
uv run --frozen --with tzdata==2025.2 nexus core probe --file (Join-Path $SRC 'INV_STA289.TXT') --tz America/Mexico_City --out (Join-Path $PRIV 'probe2_INV_STA289.json')
```

Después compruebe que los originales no cambiaron:

```powershell
Get-FileHash -Algorithm SHA256 (Join-Path $SRC '*') | Format-Table Hash, Path -AutoSize | Out-File (Join-Path $PRIV 'sha256_tras_probe2.txt')
Compare-Object (Get-Content (Join-Path $PRIV 'sha256_originales.txt')) (Get-Content (Join-Path $PRIV 'sha256_tras_probe2.txt'))
```

Qué revisar en cada informe antes de compartirlo:

| Campo | Para qué sirve |
|---|---|
| `structure.valid` | Si el archivo se acepta |
| `structure.line_balance.balanced` | Comprueba que cada línea física quedó en una clase |
| `structure.line_classes` | Conteo de líneas por clase |
| `structure.flags.reconciliation` | OE965: totales por ITEM y generales frente a lo leído. IN092U: grupos por stockroom con `qty_total_match` |
| `structure.flags.header_diagnosis` | Portal: campos `missing` y columnas no reconocidas, enmascaradas |
| `structure.exceptions` | Códigos de excepción |
| `structure.exception_first_line_by_code` | Línea física del primer caso de cada código |

- **Portal:** `--show-headers` agrega `header_names`, con los **nombres** de las columnas. Son encabezados, no filas. Si su política los considera sensibles, omita la opción y comparta solo `header_diagnosis`. En ese caso el ajuste de alias tomará una ronda más.
- **No ejecutar** `stage`, `refresh`, SQL ni despliegues.

### 2.2-ter Tercer probe (cierre de R2)

Repita **solo** los tres probes con el commit que contiene `docs/evidence/CORE-NOV/R2_FINAL.md`. Use archivos de salida nuevos: `--out` nunca reemplaza un archivo existente.

```powershell
git fetch origin feature/core-v1
git checkout feature/core-v1
git pull origin feature/core-v1
uv run --frozen nexus core probe --file (Join-Path $SRC 'R_20261101.TXT') --out (Join-Path $PRIV 'probe3_R_20261101.json')
uv run --frozen nexus core probe --file (Join-Path $SRC 'Portal_CAT_2026-11-03_0955.csv') --out (Join-Path $PRIV 'probe3_portal.json')
uv run --frozen --with tzdata==2025.2 nexus core probe --file (Join-Path $SRC 'INV_STA289.TXT') --tz America/Mexico_City --out (Join-Path $PRIV 'probe3_INV_STA289.json')
Get-FileHash -Algorithm SHA256 (Join-Path $SRC '*') | Format-Table Hash, Path -AutoSize | Out-File (Join-Path $PRIV 'sha256_tras_probe3.txt')
Compare-Object (Get-Content (Join-Path $PRIV 'sha256_originales.txt')) (Get-Content (Join-Path $PRIV 'sha256_tras_probe3.txt'))
```

El veredicto es `structure.acceptance.verdict`. `structure.line_accounting` es solo un conteo contable de líneas y **no** es un PASS. Los valores esperados están en `docs/evidence/CORE-NOV/R2_FINAL.md`.

**No ejecutar** `stage`, `refresh`, SQL ni despliegues.

### 2.3 Configuración privada (R3 y R1)

Copie la configuración del laboratorio y cree `operation.json`:

```powershell
Copy-Item .\path\a\users.json $DATA      # usuarios del laboratorio (DevAuth sintético; solo replay)
@'
{"schema": "nexus.core.operation/1", "business_tz": "America/Mexico_City"}
'@ | Set-Content -Encoding ascii -NoNewline (Join-Path $DATA 'operation.json')
```

- **`rules.json` (R3).** Lo prepara el dueño del proceso comercial a partir de `claves_cliente_nov.csv`, con las claves **exactas**, espacios incluidos:
  - CAT, TUPY y `CAT_REVIEW`;
  - CATPERK y `98753462 001` en revisión;
  - APTIV y DELPHI como clases **distintas**.
  - Se guarda en `$DATA\rules.json`.
  - Esquema `nexus.core.rules/1`, igual que el sintético de `src/nexus/core/demo.py`.
- **Homologación (R1)**, opcional. Archivo `$PRIV\homologacion_portal_erp.csv` con estas columnas:
  ```
  item_portal,item_erp,status,evidence
  ```
  - Solo las filas `APPROVED` se aplican. Las `PROVISIONAL` se ignoran.
  - La igualdad de texto es exacta, sin normalizar.
  - Si un ITEM Portal tiene dos destinos, se invalida la tabla.
  - Sin tabla, el Portal queda **en cuarentena**, la demanda CAT es desconocida y el resto se ve completo.

### 2.4 Stage

```powershell
uv run --frozen --with tzdata==2025.2 nexus core stage --src $SRC --data $DATA --homologation (Join-Path $PRIV 'homologacion_portal_erp.csv') | Out-File (Join-Path $PRIV 'stage_report.json')
```

Sin R1, omita `--homologation`. Revise `stage_report.json`, que no contiene filas:
- Cada archivo queda `STAGED`, `QUARANTINED` (con `reason` y códigos) o `DUPLICATE_CONTENT` (por ejemplo `R_20261001(3)`/`(4)`).
- Los `.xlsx` y `MF_*` aparecen solo como **ignorados**.

Repita la comparación de hashes de §2.2: el origen debe seguir intacto.

### 2.5 Replay de noviembre

```powershell
$env:NEXUS_WEB_SECRET = uv run --frozen python -c "import secrets;print(secrets.token_hex(32))"
uv run --frozen --with tzdata==2025.2 nexus core refresh --data $DATA --channel replay --actor laboratorio
uv run --frozen --with tzdata==2025.2 nexus core status  --data $DATA --channel replay
uv run --frozen --with tzdata==2025.2 nexus web --data $DATA --channel replay --host 127.0.0.1 --port 8765
```

Abra `http://127.0.0.1:8765` en el mismo equipo. Debe verse:
- el aviso «Vista informativa (replay): no oficial y no certificada»;
- «Corte FG» con «Embarques: no disponibles»;
- Frozen 29 OERAS del «1 nov 2026 · fecha del documento, sin hora · Frozen provisional»;
- «—» con «No certificado» en Embarcado, Pendiente, Cubierto y Sin cubrir.

**No capture pantallas con datos reales** para compartirlas.

### 2.6 Rollback local

1. Detenga la web con Ctrl+C.
2. El directorio `$DATA` es **desechable**: `Remove-Item -Recurse $DATA` deja el laboratorio como estaba.
3. Los originales de `$SRC` nunca se modificaron.
4. La demostración sintética sigue en su propio directorio.

Para volver a un corte anterior **dentro** del replay:

```powershell
uv run --frozen --with tzdata==2025.2 nexus core rollback --data $DATA --channel replay --to <gen_id>
```

## 3. Garantías comprobadas por pruebas (sintéticas)

| Condición | Pruebas |
|---|---|
| **C1 Frozen inmutable** | `test_core_november.py` nv30–nv36:<br>• identidades y SHA independientes del original y del CSV;<br>• evidencia del primer archivo;<br>• Frozen PROVISIONAL con precisión de fecha;<br>• un extracto posterior no lo reemplaza;<br>• un archivo distinto de la misma fecha genera la alerta `FROZEN_PRECEDENCE_AMBIGUOUS`;<br>• precedencia ambigua antes del registro: no publica;<br>• la sustitución es rechazada y una manipulación da `IntegrityError` |
| **C2 Aislamiento del replay** | nv40–nv45:<br>• almacén físico propio;<br>• cada almacén rechaza claves de otro canal;<br>• una generación replay copiada byte a byte al almacén operativo o sintético no se publica;<br>• un manifiesto falsificado con canal `operativo` es rechazado;<br>• el rollback replay no altera la cadena sintética;<br>• CS separado (`test_core_web_replay.py` wr04) |
| **C3 Desconocido ≠ 0** | nv10–nv15:<br>• contrato: todo hecho desconocido tiene `value=null`;<br>• totales con `known_sum=null` y `blocked_by`;<br>• unidades sin «atendida 0»;<br>• Shipped vacío, fechas `A(B)`, cantidad abierta vacía, FG ausente y FG negativo.<br>Web wr01–wr03: Junta, filas, filtros y detalle |
| **C4 Identidades exactas** | nv20–nv21, po06, po08:<br>• CAT y TUPY independientes;<br>• APTIV ≠ DELPHI;<br>• otra clave por espacios no es CAT;<br>• sin asociación por guiones, mayúsculas o parecido;<br>• una homologación PROVISIONAL nunca se aplica |
| **Portabilidad (Δ6)** | `test_core_portability.py`:<br>• huella dorada `400e36ce…` con escritura de texto estilo Windows (CRLF emulado);<br>• prueba estática sin `write_text` ni `open(...,'w')` en `nexus.core` |
| **Probe y stage** | `test_core_stage.py`:<br>• hash, mtime y permisos del origen intactos;<br>• informes sin valores sintéticos;<br>• sin escritura en git;<br>• sin reemplazos;<br>• primera recepción conservada |
| **Higiene** | `tests/architecture/test_no_real_sources.py`: ningún `R_*.TXT`, `Portal_CAT_*`, `INV_STA*`, `*.xlsx` ni `MF_*` versionado o pendiente. Además, `.gitignore` |
