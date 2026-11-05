# PULSO NEXUS — Informe de entrega E1-A

| Campo | Valor |
|---|---|
| Alcance | **Solo E1-A** (plan E0-R2, §9.2 y §10). E1-B y E1-C **no** se iniciaron |
| Rama | `claude/blissful-feynman-obiif0` (repositorio `mcrisnidh/pulso-nexus`, **privado**: verificado antes del push con `private: true` y `visibility: private`) |
| Commit de código auditado | `9cf187f6f2ce75ba9259719117ffefccb84886a4` |
| Commit de evidencia | El commit que contiene este archivo, hijo directo del anterior |
| Fecha de la evidencia (UTC) | 2026-11-05 |
| Entorno de evidencia | Linux 6.18 (contenedor), CPython 3.12.3, uv 0.11.32, pyarrow 26.0.1 |
| Datos | Exclusivamente sintéticos (`scripts/make_synthetic_fixtures.py`, semilla 20261105) |
| Conexiones | Ninguna corporativa. Sin SQL, sin credenciales, sin cambios de infraestructura |
| Estado propuesto | **GO de E1-A** (los 14 criterios se cumplen en el entorno de evidencia), **sujeto a revisión independiente**. Las garantías en Windows/NTFS y la durabilidad física **no** están certificadas (ver §5) |

## 1. Resultado por criterio (A1–A14)

Todos los resultados proceden de una **clonación limpia** del commit `9cf187f`
(`test-results/00_clean_status.txt`). Cada archivo `test-results/*.txt` incluye el
comando exacto, el commit, la fecha y el código de salida.

| ID | Criterio | Resultado | Evidencia |
|---|---|---|---|
| A1 | `uv sync --frozen`, ruff, `mypy --strict src` e `lint-imports` sin errores | **PASS** | `01_uv_sync`, `02_ruff`, `03_mypy`, `04_lint_imports` (6 contratos *kept*, 0 *broken*) |
| A2 | Fronteras: motores y normalizadores sin E/S, red, procesos, reloj ni aleatoriedad. Solo `pipeline` integra | **PASS** | `tests/architecture/test_boundaries.py` (AST + import-linter) |
| A3 | Dependencias prohibidas ausentes (lockfile, pyproject y código) | **PASS** | `tests/architecture/test_forbidden_deps.py`. La única dependencia de ejecución es `pyarrow` |
| A4 | ObjectStore: staging, `fsync`, publicación sin reemplazo y relectura verificada; idempotencia; conflicto ante contenido distinto; `IntegrityError` ante alteración | **PASS** | `tests/unit/test_object_store.py`, `tests/adversarial/test_concurrency.py` (8 procesos × 5 rondas), `tests/adversarial/test_tampering.py`, `tests/architecture/test_no_replace.py` |
| A5 | Caída antes del `fsync`, antes de publicar o antes de la relectura: o ningún objeto o uno válido; restos de staging nunca referenciados | **PASS** | `tests/adversarial/test_crash_injection.py` (`os._exit` real; 3 puntos en `put` y 15 combinaciones en el pipeline). Tabla en `adversarial.txt` |
| A6 | Hash del snapshot igual al calculado de forma independiente; manifiestos sin rutas absolutas ni secretos | **PASS** | `tests/unit/test_snapshots.py`, `tests/architecture/test_manifest_hygiene.py` |
| A7 | Salida conforme al contrato; un cambio de esquema sin versión falla; rechazos con motivo; conservación de filas | **PASS** | `tests/contract/*` (6 mutaciones de esquema más tolerancia; propiedad Hypothesis de conservación, 200 casos) |
| A8 | `logical_hash`: invariante a permutación; sensible a 1 ulp, a filas extra o faltantes y a cambios de tipo; NFC | **PASS** | `tests/unit/test_hashing.py` (Hypothesis) |
| A9 | Dos corridas: `run_id`, marcas y rutas distintos; `result_digest` y `content_hash` idénticos | **PASS** | `tests/reproducibility/test_run_twice.py`; demostración, paso 4 |
| A10 | 1 frente a 8 hilos, y `TZ` y `LC_ALL` distintos: mismo `result_digest` | **PASS** (con observación) | `tests/reproducibility/test_threads_tz.py`: 4 variantes en procesos separados (1/8/3/16 hilos; UTC, America/Mexico_City, Pacific/Chatham, Asia/Kolkata; `C` y `C.UTF-8`); `08_*`, `09_*`. **Observación:** el sandbox no tiene `es_MX.UTF-8`, así que se usaron locales distintos disponibles, según lo aprobado en §10 |
| A11 | `replay` informa nivel y primera discrepancia; snapshot alterado da error de integridad; snapshot ausente da "no reproducible" sin resultado parcial | **PASS** | `tests/reproducibility/test_replay_levels.py` (códigos de salida 3 y 4; la salida solo contiene `error` y `detail`) |
| A12 | Prueba de mutación: el comparador L1 detecta valores fuera de tolerancia | **PASS** | `test_comparator_mutations` (9 mutaciones), `test_float_change_*` |
| A13 | Cobertura de ramas ≥ 90 % en hashing, storage, snapshots, pipeline y engines | **PASS** | `06_coverage_by_package`: hashing 100 %, storage 97 %, snapshots 100 %, pipeline 99 %, engines 100 %. Total del paquete 98,87 % (`05_pytest_cov`) |
| A14 | Solo datos sintéticos con semilla; red bloqueada en pruebas | **PASS** | `tests/unit/test_fixtures_and_network.py` (regeneración byte a byte, `SHA256SUMS` y bloqueo de socket) |

**Totales:** 212 pruebas, 212 superadas, 0 fallos, 0 errores, 0 omitidas (`test-results/junit.xml`).

Ningún umbral se redujo y ninguna prueba se marcó como `skip` o `xfail` para obtener PASS.
Los marcadores `posix_only` solo omiten pruebas en Windows; en Linux se ejecutaron todas.

## 2. Precisiones de la autorización (sección 10)

| Precisión | Implementación | Evidencia |
|---|---|---|
| 1. Compatibilidad Windows/NTFS | Todas las llamadas de SO están aisladas en `storage/_fs.py`. Claves portables a NTFS. Garantías separadas por plataforma | `PLATFORM_GUARANTEES.md`; `test_non_portable_keys_rejected` (19 casos) |
| 2. Sin sobrescrituras ni sustituciones concurrentes | `os.link` (POSIX) o `MoveFileExW` sin reemplazo (Windows). Prohibidos `os.replace` y `shutil.move`. Fallo cerrado en un FS sin enlaces duros | `test_concurrency.py`, `test_no_replace.py`, `test_unsupported_filesystem_fails_closed`, archivo plantado, enlaces simbólicos |
| 3. Contradicción de P11 | Una sola entrada vigente en `docs/decisions-register.md`. E1-A sin SQL | Registro de decisiones; `test_forbidden_deps.py` |

## 3. Pruebas adversariales (resumen)

Ver `adversarial.txt` para la salida completa (43 pruebas, todas superadas).

| Ataque o fallo | Resultado observado |
|---|---|
| 8 procesos escriben el mismo contenido a la vez (5 rondas) | Todos terminan bien. Un único archivo final válido y sin restos de staging |
| 8 procesos escriben contenidos distintos bajo la misma clave (5 rondas) | Exactamente 1 ganador y 7 `ImmutableConflictError`. El contenido final es el del ganador |
| Caída (`os._exit`) antes de `fsync` o antes de publicar | No hay objeto final. El temporal queda en staging y no está referenciado |
| Caída antes de la relectura | El objeto final existe y es válido |
| Caída en cada una de las 5 escrituras del pipeline | `verify` sin fallos. Las corridas incompletas se informan y `replay` las rechaza (`NotReproducibleError`). Una corrida posterior funciona en L3 |
| Archivo plantado en el destino | `ImmutableConflictError`; el archivo plantado queda intacto |
| Sustitución posterior a la escritura (nuevo inodo) | `IntegrityError` al leer |
| Artefacto sustituido por otro archivo válido | `verify` falla y `replay` lanza `IntegrityError` |
| Destino o componente de ruta convertido en enlace simbólico | Rechazado; el destino del enlace no se toca |
| Manifiesto re-sellado de forma incoherente (`run_id`, `input_digest`, `result_digest`, `logical_hash`, esquema) | `IntegrityError` |
| Manifiesto re-sellado de forma coherente con otra versión de motor, otras huellas de contrato o snapshots duplicados | Rechazado (`ConfigurationError` o `ContractError`) |
| `as_of` re-sellado de forma coherente | `replay` da nivel `NONE` frente a los artefactos guardados |
| Texto con inyección SQL o HTML en los datos | Se trata como dato: rechazo por tipo o aceptación como cadena. Nunca se ejecuta |

## 4. Demostración end-to-end (`scripts/e1a_demo.sh` → `demo.log`)

1. Fixtures regenerados byte a byte y `SHA256SUMS` verificado (`input_v1.csv: OK`, `input_v1_invalid.csv: OK`).
2. Se crea el snapshot `snap-2ca212db…`.
3. Corrida A (1 hilo, UTC, `C`) y corrida B (8 hilos, America/Mexico_City, `C.UTF-8`, `--trigger scheduled`):
   - `run_id` distintos: `run-ed88c34d…` y `run-1cb4b46b…`.
   - `result_digest` idéntico en ambas: `19b016710d2c9ae4…`.
   - `input_digest` idéntico en ambas: `1fb723a4805e1a81…`.
4. `replay --level L3` da L3, y `verify --all` da `ok: true`.
5. Se altera 1 byte de un artefacto: `verify` sale con código 5 y `replay` con código 3 (fallo cerrado).
6. La corrida B no queda afectada y sigue reproduciéndose en L3.

## 5. Limitaciones y riesgos residuales

1. **Windows/NTFS no certificado.** El diseño usa APIs no reemplazantes, pero solo se demostró en Linux. Windows no permite `fsync` de directorio, así que la durabilidad del rename en NTFS queda pendiente. El job de Windows en CI es informativo y su resultado no se había observado al redactar este informe.
2. **Durabilidad ante corte eléctrico no demostrada.** Se ejecuta `fsync`, pero ninguna prueba simula una pérdida de energía real.
3. **Administrador privilegiado.**
   - Los manifiestos autosellados solo detectan corrupción accidental y manipulaciones incoherentes.
   - Una manipulación totalmente coherente (manifiestos, artefactos y hashes) **no es detectable en E1-A**.
   - La firma con custodia externa y el anclaje (P16) son de E1-C.
   - El modo solo lectura disuade, pero no impide.
4. **Red bloqueada solo en el proceso de pruebas.** Los procesos hijos (CLI) no tienen bloqueo de red. Las pruebas AST impiden que motores y normalizadores importen módulos de red. El pipeline invoca `git` localmente para identificar el código.
5. **Replay entre versiones de componentes no disponible.** E1-A solo ejecuta la versión vigente del normalizador y del motor. Un manifiesto con otras versiones da error de configuración en lugar de una comparación L1.
6. **L3 depende de la versión de pyarrow.** El campo `created_by` de Parquet incluye la versión. Una actualización degrada a L2, lo cual es esperado y queda documentado en ADR-0003.
7. **Escala.** La serialización se hace en memoria y `logical_hash` recorre las filas en Python. Es adecuado para los fixtures sintéticos (5 000 filas), pero no se ha dimensionado para volúmenes corporativos.
8. **Normalizador sintético.** No admite registros CSV multilínea (los rechaza con motivo). No contiene reglas comerciales: el motor `synthetic_sum` **no** es un motor de negocio.
9. **Retención y recolección no implementadas.** Los restos de staging y las corridas incompletas se **informan** (`verify`), pero no se eliminan.
10. **Concurrencia entre máquinas o sobre SMB no probada.** La concurrencia se probó entre procesos del mismo host.
11. **Locale `es_MX.UTF-8` no disponible** en el sandbox (ver A10).
12. **Todo lo relativo a SQL Server** (E1-B) y a certificación, publicación e identidades (E1-C) queda pendiente y no autorizado.

## 6. Archivos respecto al plan §9.3

Se crearon todos los archivos de §9.3. Además existen los siguientes, que dan soporte a
las precisiones de §10 o separan responsabilidades sin ampliar el alcance:

- `src/nexus/storage/_fs.py` (precisiones 1 y 2).
- `src/nexus/sealed.py` (sellado de manifiestos).
- `src/nexus/connectors/base.py`.
- `src/nexus/pipeline/compute.py`, compartido por `run` y `replay`.
- `src/nexus/pipeline/verify.py`, que implementa `nexus verify` de §9.4.
- `scripts/make_manifest.sh`.
- Pruebas: `test_concurrency.py`, `test_resealed_tampering.py`, `test_no_replace.py`, `test_boundaries.py`, `test_sealed.py` y `test_manifest_and_cli.py`.

## 7. Reproducción desde una clonación limpia

Requisitos: `git`, `uv` y Python 3.12. Los comandos están en `README.md`, sección
"Reproducir desde una clonación limpia". La integridad de los archivos se comprueba con:

```bash
sha256sum -c docs/evidence/E1-A/MANIFEST.sha256
```

`MANIFEST.sha256` lista el SHA-256 de todos los archivos versionados del commit de
evidencia (excepto él mismo). Se genera con `scripts/make_manifest.sh`.
