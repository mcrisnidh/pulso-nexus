# PULSO NEXUS — PILOT-20: integración funcional offline

> Este documento y el código del repositorio **no contienen datos reales**. El dataset
> corporativo, las reglas con claves reales de cliente y todas las salidas derivadas viven fuera
> del repositorio, en un directorio privado. Los resultados con datos reales se entregan en el
> paquete privado (`REPORT_PILOT20.md`), no aquí.

## 1. Qué es

`nexus.planning` es un paquete nuevo que no modifica E1-A ni E1-B. Contiene:

| Capa | Módulo | Función |
|---|---|---|
| Modelo | `planning/model.py` | `Fact` (valor + estado + procedencia + limitaciones), `SourceRef`, `Decimal` exacto. El estado es un vocabulario cerrado. |
| Reglas | `planning/rules.py` | Reglas comerciales parametrizadas: clases de cliente por **clave exacta**, estados Portal excluidos, stockrooms FG usables y estado de aprobación (CRC). |
| Lectura | `planning/adapters/tables.py` | Tablas delimitadas con SHA-256 por archivo, línea física por fila y corte declarado. Si falta el archivo o una columna, la fuente queda bloqueada sin abortar la corrida. |
| Adaptadores | `planning/adapters/sources.py` | Uno por fuente (ver §3), con identidades exactas: ITEM, cliente con espacios significativos, pedido con ceros iniciales, WO, operación. |
| Layout | `planning/layouts/pilot20_v1.json` | Rutas y columnas requeridas del paquete PILOT-20 v1. Solo estructura. |
| Motor | `planning/engine.py` | Etapas puras por ITEM. No hace E/S ni contiene reglas por número de pieza. |
| Runner | `planning/runner.py` | Orquestación, oráculos, matriz, discrepancias, HTML navegable y manifiesto con digest determinista. |
| CLI | `nexus planning …` | Punto de entrada. Rechaza `--out` dentro del repositorio. |

### Reutilización de E1-A

- `nexus.hashing`: `canonical_json`, `digest_json` y `sha256_bytes`.
- `nexus.sealed`: documentos por ITEM y manifiesto autosellados.
- `nexus.storage.object_store.LocalObjectStore`: copia inmutable y verificada de cada vista por ITEM.
- La CLI existente.

**No se reutilizan** los conectores (allowlist E1-A), los contratos sintéticos ni E1-B, que no conecta SQL.

## 2. Flujo por ITEM

`01_demand → 02_portal_cat → 03_substitution → 04_fg → 05_coverage → 06_work_orders →
07_operations_progress → 08_capacity → 09_shipments_billing → 10_manual_inputs →
11_final_shortage`

Cada etapa entrega:
- hechos (`facts`) con valor, estado, limitaciones y procedencia: fuente, archivo, SHA-256, líneas y corte;
- registros de detalle con su origen.

Estados posibles:

| Estado | Significado |
|---|---|
| `PROVISIONAL_CALCULATED` | Calculado con fuentes de un mismo corte. No certificado. |
| `DIAGNOSTIC_ONLY` | Observación útil. No es un resultado de negocio. |
| `TEMPORAL_MISMATCH` | Exigiría combinar cortes incompatibles. Se entregan componentes, no el total. |
| `BLOCKED_SOURCE` | Falta la fuente. Bloquea solo las etapas que dependen de ella. |
| `BLOCKED_CRC` | Falta la regla comercial aprobada. |
| `NOT_CERTIFIABLE` | La fuente existe pero no permite certificar el valor (p. ej., FG negativo). |
| `UNKNOWN_NOT_ZERO` | Dato ausente o no numérico. Nunca se convierte en 0. |

## 3. Adaptadores y reglas de no-equivalencia (Fase 4)

| Fuente | Adaptador | Regla aplicada por construcción |
|---|---|---|
| Demanda OE965 (4 snapshots) | `backlog` | Corte por nombre de archivo (regex en reglas). Frozen = primer snapshot. Demanda = abierta con entrega ≤ fin del mes del corte, incluidos atrasos. Evolución frente al Frozen sin recalcularlo. Limitación explícita: OE965 **no** es sustituto certificado de SSRS 29 OERAS. |
| Portal CAT | `portal` + `engine.portal_decision` | Estados excluidos parametrizados. DIF solo si `Ordered` y `Shipped` son numéricos. DIF ≤ 0 se excluye. Vacío o `8000(0)` dan `NO_CUANTIFICABLE` (desconocido, no cero). La columna de decisión del proveedor se usa solo como **oráculo**. |
| Sustitución CAT/TUPY | `engine.substitution_stage` | Clases por clave exacta. La clase CAT se sustituye por el Portal y las demás (TUPY, revisión, otras) se conservan. El total combinado es `TEMPORAL_MISMATCH`. |
| FG por snapshot OE965 | `fg_snapshots` | Físico y disponible por corte. Un disponible negativo se conserva y su cobertura es `NOT_CERTIFIABLE`. |
| Inventario posterior | `inventory` | Solo cuentan los stockrooms usables de las reglas (FG). QF/QH/RM/SA se listan aparte. Un ITEM no listado da `UNKNOWN_NOT_ZERO`. **Nunca** se usa como inventario del corte Frozen. |
| WO + routing actual | `work_orders`, `routing` | Etiqueta `ESTADO_ACTUAL@fecha`. La cobertura WO al corte Frozen es `TEMPORAL_MISMATCH`. |
| Producción del mes | `labor_groups` | Agregados por WO/operación/centro/tipo. **Nunca** se suman operaciones distintas como piezas. La operación máxima del routing actual con D>0 es *candidata*, no recepción FG. |
| Correcciones | `corrections` | Se anotan junto a su operación como "ya representadas en producción". No se suman. |
| Ajustes tardíos | `late_adjustments` | Dos relojes (actividad / registro). Se anotan y no son producción adicional. Marcan el conocimiento al cierre como no certificable. |
| Facturación | `billing` | Movimiento contable por serie (con signo). Los embarques físicos quedan como `BLOCKED_SOURCE`. |
| Capacidad | `engine.capacity_stage` | Se muestran estándares crudos. Los códigos `time_basis` no tienen semántica certificada. No hay calendario, por lo que horas disponibles y fechas son `BLOCKED_SOURCE`. |
| Entradas CS | — | `BLOCKED_SOURCE` (fuente no entregada). |
| Faltante final | `engine.item_view` | `BLOCKED_SOURCE`, con la brecha FG-only al corte Frozen como `DIAGNOSTIC_ONLY`. |

## 4. Instalación y ejecución (local, offline)

Requisitos: Python 3.12 y `uv`. No hay dependencias nuevas.

```bash
uv sync --frozen
```

Extraer el dataset en un directorio privado fuera del repositorio:

```bash
mkdir -p ~/pulso_private/pilot20 && chmod 700 ~/pulso_private
```

```bash
unzip PULSO_NEXUS_PILOT20_DATASET_v1_20261106.zip -d ~/pulso_private/pilot20/dataset
```

Verificación del proveedor:

```bash
python3 -I ~/pulso_private/pilot20/dataset/PULSO_NEXUS_PILOT20_DATASET_v1_20261106/tools/verify_integrated.py
```

Reglas: copiar `tests/planning/synthetic_pilot.py::RULES` como plantilla y escribir las claves
exactas reales en `~/pulso_private/pilot20/rules_pilot20.json` (esquema `nexus.planning.rules/1`).

Corrida completa, o limitada a ITEM concretos con `--item X` (repetible):

```bash
uv run nexus planning --dataset ~/pulso_private/pilot20/dataset/PULSO_NEXUS_PILOT20_DATASET_v1_20261106 --rules ~/pulso_private/pilot20/rules_pilot20.json --out ~/pulso_private/pilot20/outputs/run
```

Salidas en `--out`:
- `index.html`: selector de ITEM y etapas navegables, sin red.
- `items/<ITEM>.json`: vista sellada por ITEM. Copia inmutable en `store/`.
- `matrix.csv`: matriz consolidada con valor y estado por columna.
- `discrepancies.json` y `discrepancies.md`: fuentes, duplicados, campos no certificables, clasificación de clientes, Portal, FG negativo, series y oráculos.
- `run_manifest.json`: manifiesto sellado con `run_digest`, determinista y sin marcas de tiempo.

Pruebas:

Sintéticas (CI y local):

```bash
uv run pytest tests/planning
```

Sobre datos reales (solo local; se omiten si no están las variables):

```bash
NEXUS_PILOT20_DATASET=~/pulso_private/pilot20/dataset/PULSO_NEXUS_PILOT20_DATASET_v1_20261106 NEXUS_PILOT20_RULES=~/pulso_private/pilot20/rules_pilot20.json NEXUS_PILOT20_WORKDIR=~/pulso_private/pilot20/work uv run pytest tests/planning -v
```

| Validación del mandato (Fase 5) | Prueba sintética | Prueba real |
|---|---|---|
| 1. 20 ITEM individuales | `test_v1_*` | `test_r1_*` |
| 2. El motor conecta fuentes | `test_v2_*` | `test_r2_*` (oráculos del dataset) |
| 3. Sin duplicar demanda/WO/operaciones/correcciones | `test_v3_*` | `test_r3_*` |
| 4. WO con actividad del mes trazables | `test_v4_*` | `test_r4_*` |
| 5. Candidatas en operación final ≠ FG | `test_v5_*` | `test_r5_*` |
| 6. Cortes temporales | `test_v6_*` | `test_r6_*` |
| 7. Procedencia y estado en todo cálculo | `test_v7_*` | `test_r7_*` |
| 8. Fuentes ausentes → bloqueos localizados | `test_v8_*` | `test_r8_*` |
| 9. Determinismo | `test_v9_*` | `test_r9_*` |
| 10. Mismo código para todos los ITEM | `test_v10_*` (AST sin literales) | `test_r10_*` |

## 5. Rollback

- **Código**: el paquete es aditivo. Para retirarlo, revertir el commit de PILOT-20 (`git revert <sha>`). E1-A y E1-B no dependen de `nexus.planning`.
- **Datos y salidas**: borrar el directorio privado:

```bash
rm -rf ~/pulso_private/pilot20
```

  No hay escrituras en SQL, AS400, PULSO/MAGNUM ni en servicios externos.

## 6. Integración futura en NEXUS (pendiente, no implementado)

No hay una UI de NEXUS montada en este entorno. Lo entregado es el contrato de integración:
- `item_view()` es puro y recibe un `Bundle`. Un conector aprobado (E2) podría poblar el `Bundle` desde snapshots en lugar de CSV.
- Las reglas pasarían a ser un CRC versionado y aprobado (`approval: CRC_APPROVED`). Mientras no lo sean, toda vista lo declara.
- Los estados y la procedencia ya son serializables (JSON canónico sellado), aptos para una generación de E1-B/E1-C.
