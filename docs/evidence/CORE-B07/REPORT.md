# B0-7 — Zona horaria operativa: informe y estado GO/NO-GO

- **Rama:** `feature/core-v1`. Base `f404f96`; el commit es el que contiene este informe.
- Sin PR, sin GitHub Actions (`[skip ci]`; el CI no se dispara en esta rama), sin migraciones ni despliegues.
- Solo datos sintéticos.

## Estado: **GO** (alcance B0-7)

Se mantienen sin cambio:
- el GO condicionado del CORE técnico;
- el NO-GO de la operación corporativa.

## 1. Qué cambió

| Archivo | Cambio |
|---|---|
| `src/nexus/core/timebase.py` (nuevo, puro) | `BusinessCalendar`: mes operativo local, ventana UTC desde la medianoche local, huella de zona. Zona explícita (`zoneinfo`), nunca la del sistema. Contrato import-linter de pureza |
| `src/nexus/core/refresh.py` | `operation.json` opcional (sin él: UTC). Mes vigente, filtro de extractos por mes local, canal operativo y ventana de embarques según el calendario. Vínculo de zona del canal; zona en las entradas de la generación (no UTC), en el Frozen y en la publicación; rechazo de zonas incompatibles |
| `src/nexus/core/state.py` | Vínculo de zona del canal: sellado, escrito una sola vez sin reemplazo. Zona efectiva: vínculo; o UTC si hay historial previo a B0-7; o ninguna |
| `src/nexus/core/coverage.py` | Campo opcional `window_start_utc` (por defecto, medianoche UTC: comportamiento idéntico). **Sin cambios en fórmulas** |
| `pyproject.toml` | Solo el contrato import-linter de `timebase` (12 contratos); sin dependencias nuevas |
| `docs/core/B0-7_ZONA_HORARIA.md` | Configuración, identidad y **procedimiento seguro** |
| `tests/core/test_core_timezone.py`, `tests/core/tz_scenarios.py` (nuevos) | 38 pruebas y el escenario de compatibilidad con huella dorada |

**Sin cambios:**
- interfaz, SQL, autenticación;
- reglas comerciales, contratos de fuentes, Customer Service;
- dependencias y `uv.lock`;
- ninguna prueba existente (`compat/git_diff_scope.txt`).

## 2. Condiciones obligatorias

**1. Identidad de generación**
- Toda generación no UTC incluye en sus entradas `calendar = {business_tz, month, window_utc}`, y `gen_id = SHA-256(entradas)[:32]`.
  - Otra zona, o la misma zona con otras reglas `tzdata`, produce otro `gen_id`.
  - UTC, México y Bogotá dan tres identidades distintas (TZ-17).
- La publicación registra `business_tz` y el `manifest_sha256` dentro de la cadena de hashes (ID-01).
- `publish`/`rollback` rechazan una generación calculada con otra zona (ID-02).
- La llave de idempotencia incluye la zona (ID-03).

**2. Rollback seguro**
- Eliminar o cambiar `operation.json` **no** es un rollback.
- Si el canal tiene Frozen o publicaciones ligados a una zona, CLI y web **no arrancan** con otra zona. El historial queda intacto byte a byte, y basta restaurar la configuración correcta para volver a operar (RB-01).
- Un almacén UTC previo a B0-7 rechaza una zona nueva (RB-02); también se rechaza el cambio entre zonas no UTC (RB-03).
- Procedimiento seguro: directorio nuevo y el anterior como archivo de solo lectura, con el historial CS conservado (RB-05, `docs/core/B0-7_ZONA_HORARIA.md`).
- Defensa en profundidad: aun forzando la omisión del vínculo, un Frozen nunca se reinterpreta y el Refresh no publica (TZ-09).

## 3. Compatibilidad UTC con `f404f96`

| Evidencia | Resultado |
|---|---|
| Huella dorada (`tz_scenarios.UTC_BASELINE_DIGEST`) **calculada con el código de `f404f96` antes del cambio**: generaciones, Frozen y publicaciones de un escenario de dos meses (Refresh, Current, inválido, tardío, corte nuevo, corte roto, rollback, reinicio, mes 2) | Código B0-7 sin `operation.json` y con `UTC` explícito: **idéntica** (TZ-18) |
| Almacén **real** producido por `f404f96` operado con B0-7 (`compat/legacy_store_continuation.txt`) | Huella = dorada; otra zona rechazada; 0 archivos previos modificados; **ningún** archivo de zona escrito |
| Demostración web de dos meses (`compat/demo_generations_vs_f404f96.txt`) | `gen/`: 27 de 27 y `frozen/`: 4 de 4 archivos **idénticos byte a byte** a la misma demostración con `f404f96` |
| Almacenes nuevos | Único metadato adicional: `calendar/<canal>/binding.json` (vínculo UTC). No cambia generaciones, Frozen ni publicaciones; cierra la carrera UTC contra no UTC (RB-07) |

## 4. Pruebas (`test-results/`)

| Comprobación | Resultado |
|---|---|
| `ruff`, `mypy --strict`, `lint-imports` | OK (12 contratos) |
| Suite `pytest -m "not sql"` | **455 passed, 12 skipped, 0 failed**: las 417 previas sin modificar + 38 nuevas. Los 12 skipped son las pruebas Pilot-20 con datos reales, solo locales |
| `tests/core/test_core_timezone.py` | **38/38** (`pytest_timezone.txt`) |
| Demostración de dos meses | 11/11 |
| Navegador | 33/33 |

Correspondencia de las 38 pruebas:

| Grupo | Casos | Pruebas |
|---|---|---|
| Calendario | TZ-01 mes, TZ-02 año, TZ-03 febrero bisiesto, TZ-04 reglas históricas con horario de verano, TZ-05 independencia de la zona del proceso, TZ-06 zona inválida o esquema estricto | 14 |
| Frozen | TZ-07 Frozen por fecha local, TZ-08 primer extracto del mes nuevo con reinicio, TZ-09 Frozen nunca reinterpretado | 3 |
| Embarques | TZ-10/11 ventana desde medianoche local, TZ-12 reversa que cruza el mes, TZ-13 corte nocturno alineado | 3 |
| Vigencia y canal | TZ-14 vigencia CS y web sin cambios de interfaz, TZ-15 canal operativo | 3 |
| Determinismo y compatibilidad | TZ-16 determinismo, TZ-17 identidades distintas, TZ-18 compatibilidad (instalación nueva y almacén previo a B0-7) | 4 |
| Identidad | ID-01…ID-03 | 3 |
| Rollback seguro | RB-01 eliminar `operation.json`; RB-02 historial UTC; RB-03 cambio entre zonas; RB-04 reinicios y transición de año con Frozen inmutable; RB-05 procedimiento seguro; RB-06 almacén sin historial; RB-07 carrera concurrente con un único vínculo; RB-08 vínculo manipulado | 8 |

## 5. Defecto encontrado y corregido durante la implementación

RB-07 (6 hilos con zonas distintas) detectó una carrera en el vínculo de zona: un proceso perdedor podía continuar con una zona distinta de la registrada. Era justo la colisión que prohíbe la condición 1.

Se corrigió así:
- la verificación del vínculo ocurre **siempre** después de intentar registrarlo;
- todo almacén nuevo registra su zona antes de crear estado, incluido UTC.

Verificado con 40 repeticiones de la carrera antes de la corrección (21 fallos) y con la prueba RB-07 después.

## 6. Limitaciones

- La web sigue **mostrando** horas en UTC: cambiar la interfaz quedó fuera del alcance.
- Los instantes sin zona en fuentes reales (p. ej., fechas locales de `R_*.TXT`) se rechazan. Asignarles la zona corresponderá al parser físico de cada fuente, cuando se cierre su contrato oficial.
- En Windows, `zoneinfo` requiere `tzdata`, a resolver en el despliegue del Hito B. Si no hay base de zonas, el servicio no arranca (fallo cerrado).

## 7. Rollback

Ver `docs/core/B0-7_ZONA_HORARIA.md`.

| Caso | Procedimiento |
|---|---|
| Instalación que solo operó en UTC | `git revert` del commit |
| Instalación que operó con zona no UTC | No revertir el código sobre ese directorio: conservarlo como archivo de solo lectura y operar con un directorio nuevo |
