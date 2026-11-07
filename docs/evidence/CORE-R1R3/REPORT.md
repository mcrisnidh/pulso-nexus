# CORE v1.0 — Intervención R1/R3 y Frozen definitivo: dictamen de cierre

| Dato | Valor |
|---|---|
| Rama | `feature/core-v1` |
| Base verificada | `7200fcf` (local y remoto idénticos, árbol limpio) |
| Commit | el que contiene este informe |
| Autorización | implementación controlada de `PROPUESTA_CONSOLIDADA.diff` (sha256 `0539985b…`) |

**Lo que no se hizo:**
- SQL corporativo, sistemas productivos, publicación de datos reales, PR o despliegue (`[skip ci]`);
- uso de datos reales: todo es **sintético**, con `stage()` y `RefreshService` en directorios temporales aislados.

## Dictamen

| Ámbito | Dictamen |
|---|---|
| **Ingeniería R1** (rechazo de lo no aprobado, ambiguo o inválido; R1 por línea; D-D, D-E, D-F) | **CERTIFICADA TÉCNICAMENTE en Linux.** Todos los criterios aplicables en PASS. Pendiente la reverificación nativa en ASMXC1200 |
| **Ingeniería R3** (sin `OTHER` implícito; validación de reglas al cargar; bloqueo) | **CERTIFICADA TÉCNICAMENTE en Linux.** Ídem |
| **Frozen definitivo y universo mensual** | Implementado y verificado: el Frozen definitivo determina el universo y las fuentes externas no lo amplían |
| **Aislamiento de `stage()`** | Cerrado y verificado (T5) |
| **Aprobación comercial R1/R3** | **NO OTORGADA.** No hay registro R1 ni reglas R3 v2 reales. La certificación técnica no la sustituye |
| **Operación** | **NO-GO** |

## 1. Cambios

**Código:** 7 archivos, +527/−78 respecto de `7200fcf`. Es el diff consolidado más dos correcciones de ejecución: la construcción explícita de `CoverageInput` y el resumen de excepciones R1 del Portal (§3 y §4).

| Archivo | Cambio |
|---|---|
| `core/rules.py` | Ver detalle debajo de esta tabla |
| `core/coverage.py` | `frozen_universe()` (Frozen definitivo); `_restrict()` (filtro auditable al universo); clasificación estricta en las unidades y en la atribución (embarque `UNKNOWN` bloqueante); fila para todo ITEM del universo; bloques `universe`, `unclassified_customers` y `pending_r1`, que solo existen cuando aplican (el histórico no cambia) |
| `core/refresh.py` | El universo sale del Frozen definitivo **solo con reglas v2**. v1 queda restringido al canal `sintetico` con fuentes `SYNTHETIC`. `UNCLASSIFIED_CUSTOMER` y `PENDING_R1` hacen fallar la validación oficial; en `replay` se registran como `validation.no_go` |
| `core/stage.py` | Ver detalle debajo de esta tabla |
| `core/sources.py`, `core/layouts/core_v1.json` | Columna **opcional** `r1_status` del contrato Portal; los CSV históricos son válidos |
| `cli.py` | `core stage --ledger --approvers` |

Detalle de `core/rules.py`:
- Esquema `nexus.core.rules/2`, con `unclassified_customers: "UNKNOWN"` obligatorio.
- `CoreRules.classify()`: estricta en v2, histórica en v1.
- `_validate_classes()` al cargar: clave en dos clases, clave vacía, con espacio final o no imprimible, y clase sustituida marcada como conservada.

Detalle de `core/stage.py`:
- Tabla R1 v2 verificada contra un **registro autorizado** y una lista de aprobadores (D-D).
- `R1_REVISION_EQUIVALENCE` para muchos-a-uno (D-E).
- Destino exacto en OE965/IN092U del periodo (D-F).
- `valid_identity()` (D-R1-01).
- Estado R1 **por línea**: `MAPPED`, `PORTAL_ONLY` (`PORTAL::<Item ID>`) o `PENDING`, sin cuarentena del archivo completo.
- `check_isolation()` y `_reject_links()`, `O_NOFOLLOW` y rechazo de `store/` previo y de un origen enlazado.

**Sin cambios:** `core/physical/*` (R2), `core/state.py` (Frozen y publicación), `core/timebase.py`, `core/cs.py`, `web/*`, `planning/*` (Pilot-20) y las fórmulas de cobertura.

**Documentación:**
- `docs/decisions-register.md`: V16–V27 vigentes y X14–X18 descartadas, con procedencia.
- `docs/core/NOV2026_REPLAY.md`: §2.3–2.4 con formatos v2 y `stage` con registro.

## 2. Resultados (reproducibles)

| Suite | Resultado | Archivo |
|---|---|---|
| `pytest -m "not sql"` | **736 passed, 12 skipped, 0 failed** (12 skipped = Pilot-20 con datos reales, solo locales) | `test-results/pytest_not_sql.txt` |
| 455 pruebas originales | Todas en verde, **sin cambios** (`git diff 7200fcf -- tests` solo toca pruebas de las fases de noviembre; ver §4) | — |
| Batería adversarial R1/R3 (62 casos) | **74/74 PASS**: 62 casos más las parametrizaciones de D-D/D-E y 3 nuevas (D-F, solo-Portal, confinamiento v1). Los **16 FAIL de `7200fcf` → PASS** | `test-results/pytest_r1r3_frozen_isolation.txt` |
| Controles puros del Frozen definitivo y del universo | **34/34**: los 33 previos más «externos sembrados», que antes era implícito | ídem |
| T1–T3 (`RefreshService`) | **8/8** | ídem |
| T4–T5 (`stage()`: R1 por línea y aislamiento) | **13/13**, sin omisiones en Linux | ídem |
| Huella dorada histórica `400e36ce…` y portabilidad | **42/42** (constante sin cambio) | `test-results/pytest_golden_tz_portability.txt` |
| Demostración de dos meses / navegador | **11/11** / **33/33** | `test-results/demo_two_months.txt`, `test-results/browser_33.txt` |
| `ruff`, `ruff format`, `mypy --strict`, import-linter | Sin errores; **14 contratos KEPT** | `test-results/static_checks.txt` |

Reproducción:

```bash
uv run --frozen pytest -m "not sql" -p no:cacheprovider -q
uv run --frozen pytest -p no:cacheprovider -v tests/core/test_cert_r1_r3.py tests/core/test_core_frozen_universe.py tests/core/test_core_stage_isolation.py
```

## 3. Matriz de defectos

| Defecto | Estado | Prueba de cierre |
|---|---|---|
| D-R3-01 (crítica): `OTHER` implícito, demanda duplicada en silencio | **Cerrado** | R3-03 ×3, R3-04; F6; T1 `unknown_customer_blocks…` |
| D-R1-01 (alta): identidades con espacios o caracteres de control | **Cerrado** | R1-09 ×3, R1-10 ×3 |
| D-R1-02 (media): `APPROVED` sin evidencia | **Cerrado** (registro autorizado) | R1-11 ×7, R1-11b; T4 `tampered_ledger…` |
| D-R3-02 (media): clave en dos clases aceptada al cargar | **Cerrado** | R3-10, R3-11 |
| D-R3-03 (media): claves vacías o con espacio final | **Cerrado** | R3-14 ×3 |
| D-R3-04 (baja): sustituida conservada | **Cerrado** | R3-13 |
| P-STG-01/02: enlaces, escapes y `store/` en `stage()` | **Cerrado** | T5 (9 pruebas) |
| ITEM congelado con solo líneas futuras perdido | **Cerrado** | F9 |
| Portal completo en cuarentena (perdía ITEM solo-Portal) | **Cerrado** (R1 por línea) | `test_nv13`, `test_st16`, T4 |
| Universo por unión directa | **Cerrado** (Frozen definitivo) | F1–F10b, T3 |
| **Nuevo, hallado y cerrado:** ITEM solo-CAT eliminado aunque hubiera pendientes R1 | **Cerrado** (pertenencia pendiente) | F10b, `test_nv13` |
| **Nuevo, hallado y cerrado:** el informe de `stage` decía `UNMAPPED_ITEM` en líneas ya homologadas | **Cerrado** | T4 |
| Pendientes técnicos | Ver detalle debajo de esta tabla | — |

Pendientes técnicos:
- Reverificación nativa en Windows (ASMXC1200).
- Las pruebas de enlaces simbólicos se omiten en Windows: los junctions se rechazan por código (`is_junction`), pero no se probaron allí.

## 4. Pruebas existentes modificadas (todas de las fases de noviembre; ninguna de las 455 originales)

Motivo común:
- **[K-3]** líneas pendientes en vez de cuarentena;
- **[K-4/D-B]** las reglas v2 son obligatorias con fuentes reales;
- **[V22]** universo = Frozen definitivo (`QZXITEM3`, solo CAT, sale);
- **[D-D]** tabla R1 v2;
- **[P-STG-02]** `stage` rechaza un `store/` previo.

Cada cambio está anotado en la propia prueba.

| Archivo | Pruebas | Cambio |
|---|---|---|
| `physical_fixtures.py` | — | Reglas v2 con clases explícitas (`OTHER` explícito); tabla R1 v2, registro y aprobadores; `fx.r1(lab)` |
| `test_core_november.py` | nv11, nv12, nv13, nv21, nv43 y las llamadas a `stage` | nv12: el ITEM solo-CAT sale y se cuenta. nv13: `PENDING` bloqueante en lugar de cuarentena. nv21: la fila PROVISIONAL queda `PENDING`. nv11: el FG ausente se prueba con QZXITEM1. nv43: el segundo extracto se prepara antes del `store/` |
| `test_core_stage.py` | st16, st20 y las llamadas a `stage` | st16: líneas `PENDING` conservadas. st20: CLI con `--ledger` y `--approvers` |
| `test_core_physical.py` | po01, po02, po08 | El lector recibe la resolución R1 ya verificada; po08 pasa a la tabla v2 |
| `test_core_web_replay.py` | wr01, wr02 | Universo de 2 ITEM; el total parcial «—» se prueba en FG asignable |

**Adaptaciones de la batería R1/R3** (registradas en su docstring):
- D-D, en R1-01…06, 09…11, 16, 17;
- D-E, en R1-15;
- reglas v2;
- R3-10 rechaza al cargar.

Las aserciones de rechazo se mantienen.

**Correcciones de mis propias pruebas durante la ejecución (transparencia):**
- T3 sembraba las filas FG externas en un segundo grupo FG. IN092U lo rechazó correctamente como `GROUP_STOCKROOM_REPEATED`; las filas se movieron al grupo FG.
- T4 omitía en su expectativa la fila PROVISIONAL del fixture.
- S4/S5 comparaban contra nombres fijos; ahora derivan los externos de los datos sembrados.

Ninguna corrección relaja una regla del código.

**Corrección de código frente a una prueba original:** `tests/architecture/test_no_replace.py`, que no se modificó, señaló `dataclasses.replace(...)`. Se sustituyó por una construcción explícita del `CoverageInput`.

## 5. Evidencia: el Frozen definitivo determina el universo

`test-results/universe_evidence_synthetic.json`, con un laboratorio sintético con y sin externos sembrados:
- **Externos sembrados:**
  - IN092U: `QZXEXT01` y `QZXITEM3`;
  - OE965 actual: `QZXEXT02`, una línea CAT nueva para `QZXITEM1` y una línea CAT para `QZXITEM3`;
  - Portal actual: un Item ID nuevo sin R1.
- **Construcción:** 3 ITEM ERP originales → 2 líneas CAT retiradas → 1 ITEM solo-CAT fuera → 2 ITEM conservados → 1 ITEM Portal válido (ya presente en el ERP) → **universo = 2**.
- **Con externos:** universo **2**, construcción idéntica, filas y totales **idénticos**, Frozen idéntico. Los externos aparecen solo en `universe.excluded_records` (FG: 2). El CAT del OE965 actual **no** reintroduce `QZXITEM3` ni cambia la demanda de `QZXITEM1`.
- **ITEM congelado ausente del extracto posterior:** conserva su pertenencia (T3).

## 6. Evidencia: aislamiento y bloqueo de publicación

**Aislamiento (T5):**
- `stage()` en un subproceso con `HOME`, `TMPDIR`, `LOCALAPPDATA` y `cwd` temporales.
- La huella del directorio padre (origen y archivos privados) y `git status` del repositorio no cambian.
- Solo se crea `data_dir/{originals,inbox,provenance}`.
- Rechazos **antes de escribir**, sin cambios fuera:
  - `inbox`, `originals` o `provenance` enlazados;
  - un enlace anidado en `inbox/<fuente>`;
  - `data_dir` enlazado o con un componente padre enlazado;
  - un archivo de origen enlazado (no se sigue ni se copia);
  - un archivo privado (registro) enlazado;
  - `store/` existente;
  - salida dentro del repositorio.

**Bloqueo:**
- T1: `UNCLASSIFIED_CUSTOMER` y `PENDING_R1` → sin publicación oficial; con todo clasificado, publica.
- T2: reglas v1 con fuentes reales → rechazo en `replay` **y** en `sintetico`, porque el canal no elude la separación. v1 con el histórico sintético sigue publicando.
- `replay`: `validation.no_go` y alcance `INFORMATIVE_NOT_CERTIFIED`, sin promoción.

## 7. Dependencias comerciales para operar con datos reales

| # | Dependencia | Efecto mientras falte |
|---|---|---|
| CD-1 | **R3:** reglas v2 con decisión CAT / no-CAT aprobada para las 84 claves del ERP Frozen de noviembre | `UNCLASSIFIED_CUSTOMER`: sin publicación oficial |
| CD-2 | **R1:** registro autorizado con una decisión por cada Item ID Portal válido, más `R1_REVISION_EQUIVALENCE` donde haya muchos-a-uno | `PENDING_R1`: sin publicación oficial; los ITEM solo-CAT quedan con pertenencia pendiente |
| CD-3 | Equivalencia funcional 29 OERAS ↔ OE965 (`NOT_ESTABLISHED`) | El Frozen definitivo con OE965 es provisional |
| CD-4 | Embarques físicos certificados | Embarcado, Pendiente y Cubierto quedan `BLOCKED_SOURCE` |
| CD-5 | K-5, si se desea: CAT = 0 cuando el Portal no tiene líneas para un ITEM conservado | Hoy «no determinable», sin cambio de fórmula |
| CD-6 | Operación: persistencia y respaldo de CS, autenticación corporativa, aislamiento del host | **NO-GO operativo** |

**Cantidades de noviembre:** siguen **pendientes**. Los TXT reales no están en este entorno; se obtienen con `universe_report_v2.py` en ASMXC1200, que solo da conteos.
