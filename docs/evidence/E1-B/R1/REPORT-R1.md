# E1-B-R1: correcciones de la revisión independiente R1

> **Estado: CANDIDATO E1-B-R1 para revisión independiente.** No se declara GO.
> No se inicia E1-C. No se crea `e1a-baseline`. No se conecta infraestructura corporativa.
> No se modifican los datos congelados de E1-A.

| Dato | Valor |
|---|---|
| Hallazgos atendidos | NX-E1B-R1-01 (vigencia del claim) y NX-E1B-R1-02 (reconciliación completa) |
| Candidato revisado (original) | código `531107c`, evidencia `f30debd` |
| Commit de reproducción (sin correcciones) | `e1620ce`: CI run [42200620979](https://github.com/mcrisnidh/pulso-nexus/actions/runs/42200620979) |
| **Commit del candidato E1-B-R1** | **`37be314517ce3c547a8a07ea54054b698fb51836`** |
| **CI del candidato E1-B-R1** | **[42201190314](https://github.com/mcrisnidh/pulso-nexus/actions/runs/42201190314): 6/6 jobs en verde** |
| Commit de evidencia de la reproducción | `729a1ba` (solo documentación) |

## 1. Secuencia seguida (puntos 1–8 de la revisión)

1. **Núcleo E1-A sin modificaciones.** Solo cambian módulos de E1-B: `src/nexus/metadata/` (procedimientos, repositorios) y `src/nexus/pipeline/reconcile.py`. Ver §6.
2. **Reproducción contra el candidato original.**
   - Copias congeladas en `tests/regress/` con SHA-256 idéntico al publicado en el informe original (§9): `procedures_531107c.sql` = `aae97c35…711`, `reconcile_531107c.py` = `00f0c3b4…43d`.
   - Commit `e1620ce`: añade solo las sondas, sin corregir nada.
3. **Correcciones mínimas** (commit `37be314`). Ver §2 y §3.
4. **Pruebas dirigidas primero.** El paso CI "Pruebas dirigidas R1 … antes de la suite" es bloqueante y se ejecuta antes de B1–B16 contra SQL Server real. Resultado: **18/18 en cada celda**.
5. **B1–B16 completos** en las 4 configuraciones SQL, más E1-A Linux y Windows. Ver §4.
6. **Sin reducciones.** Se mantienen umbrales, repeticiones (20) y pruebas adversariales y de mutación. B15 y la sonda compartida se **endurecen**: exigen SHA-256 exacto y que no se registre nada.
7. Informe, resultados, commits y manifiesto actualizados (este documento y `MANIFEST.sha256`).
8. **Ubicación de los registros.** Ver §5.

## 2. NX-E1B-R1-01: vigencia del claim

**Defecto (original `531107c`).** `usp_acquire_lease`:

- tomaba `@now = SYSUTCDATETIME()` al inicio;
- después preparaba la fila de lease y esperaba bloqueos;
- validaba `claim_expires_at > @now` con esa hora ya obsoleta.

Un claim que vencía durante la espera podía adquirir el lease.

**Corrección mínima (`0002_procedures.sql`, `usp_acquire_lease`):**

- Se elimina `@now`.
- `claim_expires_at` se lee bajo `UPDLOCK, HOLDLOCK` y se compara con `SYSUTCDATETIME()` en una sentencia **posterior** a la obtención del bloqueo.
- Se vuelve a comprobar **tras el último punto de espera** (el `UPDATE` de la fila de lease) y antes de conceder. Si falla, `THROW 50011` con `XACT_ABORT` revierte toda la transacción, incluido el `UPDATE` del lease.
- Prueba estática de regresión: `test_acquire_lease_checks_claim_validity_after_lock_waits`.

**Sonda contra SQL Server real (`tests/sql/test_r1_01_claim_validity.py`).**

- Retrasa deliberadamente la adquisición con un bloqueo real de otra sesión, comprobado en `sys.dm_exec_requests`.
- El retraso dura hasta después del vencimiento del claim, medido con la hora del **servidor**.
- Se prueban dos puntos de espera: la fila de la solicitud (`request_row`) y la fila de lease (`lease_row`).

| Celda | Original `531107c` (request_row / lease_row) | Corregido (request_row / lease_row) |
|---|---|---|
| RCSI OFF · RC | lease **concedido** con claim vencido / **concedido** | `CLAIM_INVALID` (1.ª comprobación) / `CLAIM_INVALID` (1.ª) |
| RCSI OFF · SNAPSHOT | concedido / concedido | `CLAIM_INVALID` (1.ª) / `CLAIM_INVALID` (1.ª) |
| RCSI ON · RC | concedido / concedido | `CLAIM_INVALID` (1.ª) / `CLAIM_INVALID` (**2.ª**: "vencido durante la adquisición") |
| RCSI ON · SNAPSHOT | concedido / concedido | `CLAIM_INVALID` (1.ª) / `CLAIM_INVALID` (**2.ª**) |

Con RCSI ON, la lectura previa no se bloquea y la espera ocurre en el `UPDATE` de la fila de lease. Solo la **segunda** comprobación detecta allí el vencimiento: queda demostrado que es necesaria.

En todos los casos corregidos se verifica que no hay efectos inconsistentes:

- 0 intentos;
- 0 generaciones;
- 0 eventos `LEASE_ACQUIRED`;
- el lease sigue libre;
- la solicitud sigue `CLAIMED` con 0 intentos;
- `usp_recover_expired` la devuelve a `PENDING` **sin consumir intento**, con 1 evento `CLAIM_RECOVERED`.

Evidencia:

- `ci-run-42201190314/<celda>/evidence.json` (clave `R1_01`).
- `reproduction-run-42200620979/<celda>/r1_01_evidence.json`: en el commit sin corregir, las 2 pruebas de producción fallan en las 4 celdas; 424 pasan.

## 3. NX-E1B-R1-02: reconciliación completa

**Defecto (original `531107c`).** La reconciliación tenía dos huecos:

- Solo examinaba `runs/<run_id>/generation/*.parquet`; quedaban fuera `run_record`, `input_manifest`, snapshots y claves desconocidas.
- Sus entradas `UNREGISTERED_FILE` no llevaban SHA-256.

Reproducido por `tests/unit/test_r1_02_reconcile_original_gaps.py`, que afirma ambos defectos sobre la copia congelada.

**Corrección (`src/nexus/pipeline/reconcile.py`, módulo de E1-B).**

Examina **todos** los objetos del almacén (tipos `ARTIFACT`, `INPUT_MANIFEST`, `RUN_RECORD`, `SNAPSHOT_MANIFEST`, `SNAPSHOT_CONTENT`, `UNKNOWN_OBJECT`) y los restos de staging. Para cada uno calcula:

- **Identidad:** SHA-256 y tamaño de los bytes en disco.
- **Integridad**, según el tipo de objeto:
  - el sello del documento autosellado;
  - el hash declarado en el nombre del artefacto;
  - la entrada del `run_record`;
  - el `content_sha256` del manifiesto del snapshot.
- **Procedencia** cuando está disponible:
  - corrida, contrato, `input_digest` y `result_digest`;
  - `logical_hash` de la entrada del registro;
  - snapshot, origen, conector y `captured_at`.

**Estado por corrida:**

- `COMPLETE_STORE_RUN`;
- `INCOMPLETE_RUN`: sin `run_record`;
- `CORRUPT_RUN_RECORD`;
- `INCONSISTENT_RUN`: referencias incompletas, por ejemplo un artefacto citado ausente o con otro hash, un `.parquet` no citado, o un `input_manifest` ausente, corrupto o con otro digest.

**Referencias de SQL que no casan con el almacén** (`sql_reference_issues`). Se obtienen con los nuevos procedimientos **de solo lectura** `usp_list_generations` y `usp_list_snapshot_refs`:

- generación sellada cuyo `run_record` no está disponible;
- digests que no coinciden;
- corrida inconsistente;
- `snapshot_ref` sin snapshot o con otro hash.

**Sigue siendo solo informe.** La reconciliación:

- nunca elimina, mueve ni altera archivos;
- nunca registra filas automáticamente.

La única escritura sigue siendo la transición auditada `PRESENT → MISSING/CORRUPT` de filas de artefacto (R-B), ya existente.

**Pruebas:**

- `tests/unit/test_r1_02_reconcile_complete.py` (10 pruebas). Cubren:
  - almacén coherente;
  - cada tipo de objeto sin fila, con identidad y procedencia;
  - `run_record` ausente o corrupto;
  - `input_manifest` ausente o corrupto;
  - referencias incompletas (citado ausente, alterado o no citado);
  - snapshots corruptos;
  - referencias de SQL sin correspondencia;
  - staging.

  En cada una se exige que los SHA-256 de todo el almacén y el staging queden idénticos, y que no se registre ninguna fila.
- Contra SQL Server real (`tests/sql/test_b15_reconcile.py` y la sonda `probe_reconcile`):
  - R-A con BACKUP/RESTORE real informa artefactos, `run_record`, `input_manifest` y snapshot.
  - R-C informa **todos** los objetos de la corrida caída.
  - R-B informa `GENERATION_RUN_INCONSISTENT`.
  - Todas exigen el SHA-256 exacto de cada archivo informado y que no se registre ni elimine ninguna fila.
- La mutación M3 (reconciliación que borra) **sigue detectada**.

## 4. Resultados CI del candidato E1-B-R1 (run 42201190314, commit `37be314`)

| Job | Pruebas dirigidas R1 | Suite completa | Cobertura |
|---|---|---|---|
| SQL · RCSI OFF · READ COMMITTED | 18/18 | **441 passed** | 91.85 % |
| SQL · RCSI OFF · SNAPSHOT | 18/18 | **441 passed** | 91.92 % |
| SQL · RCSI ON · READ COMMITTED | 18/18 | **441 passed** | 91.85 % |
| SQL · RCSI ON · SNAPSHOT | 18/18 | **441 passed** | 91.92 % |
| E1-A Linux | n/a | 326 passed (sin SQL); A13 por paquete; TOCTOU 6/6; reproducibilidad 33 + 33; demostración E1-A | ≥ 90 % por paquete |
| E1-A Windows | n/a | 321 passed, 5 skipped (garantías POSIX declaradas en E1-A, pendientes en Windows); TOCTOU 6/6 | informativa |

**B1–B16 siguen en PASS en las 4 celdas:**

- B1: `result_digest` igual con y sin SQL.
- B5: 20 repeticiones de 16 × 200, sin reclamaciones duplicadas en el cliente ni en `run_event`.
- B8: 7/7 procedimientos responden `FENCED`.
- B12: los candidatos A, B y C son conformes; se mantiene A.
- B13: hueco de servidor > 5000 ms.
- B14: 1 reintento de interbloqueo sin efectos duplicados.
- B15: R-A, R-B y R-C ampliados.
- B16: M1, M2 × 7 y M3 detectados; M1b registrado como equivalente.

## 5. Ubicación de los registros (punto 8)

- **Versionados en esta carpeta:**
  - `ci-run-42201190314/<celda>/job.log`: candidato E1-B-R1, 4 celdas SQL y E1-A Linux.
  - `reproduction-run-42200620979/<celda>/job.log`: reproducción.
  - `evidence.json` y `SUMMARY.json` extraídos de esos registros.
  - Del candidato original: `../ci-run-42159129132/`.
- **No versionados (están en GitHub Actions):**
  - El registro del job **E1-A Windows** de cada ejecución. Del de R1 se transcriben aquí sus resultados: 321 passed, 5 skipped y TOCTOU 6/6.
  - Los *artifacts* de cada celda: `junit.xml`, `pytest.log`, `r1_targeted.log`, `deadlocks_*.json`, `server_info.json`, `sql_image_digest.txt`, sondas TLS y `*.jsonl`. Están en la página de cada ejecución (retención de 30 días). El proxy de este entorno impide descargarlos, así que la evidencia versionada procede de los registros de los jobs.
  - Los registros de las ejecuciones intermedias de depuración (runs 15–33): solo en GitHub Actions.
- Los registros versionados son los de GitHub Actions, con los secretos ya enmascarados (`***`).

## 6. Núcleo E1-A sin modificar

Comparado contra `62c5600` (código) y `1721bdc` (evidencia):

- `docs/evidence/E1-A/`: sin cambios en E1-B ni en R1. La única diferencia respecto de `1721bdc` es `CLOSURE.md`, añadido en el cierre autorizado de E1-A (`88853db`), antes de E1-B.
- R1 modifica exclusivamente: `src/nexus/metadata/sql/0002_procedures.sql`, `src/nexus/metadata/{repository,memory,sqlserver}.py` y `src/nexus/pipeline/reconcile.py`.
- Fuera de `src/nexus/metadata/`, `pipeline/worker.py` y `pipeline/reconcile.py`: solo `cli.py` cambió, con los comandos aditivos de E1-B y ningún cambio en R1.
- Única prueba E1-A tocada en E1-B: `test_forbidden_deps.py` (`pyodbc` solo en `nexus.metadata`); ningún cambio en R1.

## 7. Riesgos residuales (actualizados)

Siguen vigentes los del informe original (§5): P17, P12, P15, valores de lease de producción, coste de `usp_recover_expired`, identidades de E1-C, administrador privilegiado, mutante equivalente M1b y SQL solo en Linux.

Se añaden:

1. **Coste de la reconciliación.** Lee y calcula el hash de **todos** los objetos (coste O(bytes del almacén)). Con almacenes grandes necesitará muestreo, paralelismo o una marca de verificación incremental; no se ha medido.
2. **Alcance de `sql_reference_issues`.** Las generaciones selladas sin `store_run_id` (no producidas por el worker) no se pueden contrastar con el almacén y quedan fuera de esa verificación.
3. **Vigencia de la segunda comprobación de R1-01.** Se evalúa justo antes de las inserciones finales de la misma transacción. Entre esa comprobación y el `COMMIT` solo quedan inserciones sin espera de bloqueos ajenos, porque las filas implicadas son nuevas o ya están bloqueadas por la transacción. Aun así, un claim podría vencer en esos milisegundos; no se considera explotable porque la recuperación del claim exige el mismo bloqueo de fila.

## 8. SHA-256 de los artefactos modificados en R1 (árbol de `37be314`)

```
e2fc2bee93d85aae1498afce32d4d09f7c8ebe91f08d8aa0da31dcc446ce6012  src/nexus/metadata/sql/0002_procedures.sql
294b199e2b07de531f345dd4eafa639c30bb8fcd02b8b1695fabc03b79b87eb8  src/nexus/metadata/repository.py
dff236c488dfe1956ce0f8c68f2be8f6a58142436e362e4d1e2b0b2dfd3cbae5  src/nexus/metadata/memory.py
bd20d5c7c26910a1d4b3e46052bd5121500f8405e2604309717c17c798f83a5f  src/nexus/metadata/sqlserver.py
5af2bd33798e4f92dc7ae2956605d0d547a2f83ea07024e52f5064d1afabaa10  src/nexus/pipeline/reconcile.py
cd9877e3717fd97bd131b2601e686a5db38788af64fc2f1b89d96ed3b84f0215  .github/workflows/ci.yml
f8e05ced08e3cd9a962ab5638259f992e2e39968f3d8685a4e63e1cf65dd1617  tests/sql/test_r1_01_claim_validity.py
d3889ff57da72da2295fd0b3da678536662ee2577eda1105572b87579e84215e  tests/sql/test_b15_reconcile.py
1f4f0f13ee4856218eed41edc13e729bd657e437659dd654c3db53afa5023add  tests/sql/sql_probes.py
5fad024972e96f3fd30810b98804b4f42dff8aebe50bdc8818e41a68646ea1ee  tests/sql/sql_variants.py
774b937c8ee1d8048c5f75eb98d9b68ef57db29ee414ea968427090db25caa2a  tests/unit/test_r1_02_reconcile_complete.py
1debc2738acb8c239a87ea897825b9c3ed5e189e40ad6db6f19c788c78b7cf5d  tests/unit/test_r1_02_reconcile_original_gaps.py
cc508ef3f28eea7c0852d3d5c7a55c7879fde6cf29f658ce9b70a6ebee835ae5  tests/unit/test_sql_static.py
e26742520a18b722f4d461b355a7470ee2fb123e6728894d44b1b8132a6727ef  tests/unit/test_worker_reconcile_memory.py
aae97c35d37bcde6139cba932c2baba6fbc62bd889d36b41d04a97a2f87fc711  tests/regress/procedures_531107c.sql
00f0c3b47f87fe8a4829930f0da10891a3e8cbba478f685838ab85c8a736243d  tests/regress/reconcile_531107c.py
```
