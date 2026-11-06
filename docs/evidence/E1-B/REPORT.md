# Informe de evidencia E1-B: persistencia de metadatos en SQL Server efímero

> **Estado: CANDIDATO ÚNICO para revisión independiente.** No se declara GO.
> No se crea la etiqueta `e1a-baseline`. No se inicia E1-C, E2 ni etapas posteriores.

| Dato | Valor |
|---|---|
| Rama | `claude/blissful-feynman-obiif0` |
| Commit del candidato (código y pruebas) | `531107cff65631c971187af151cecda02cda67ff` |
| Ejecución CI del candidato | `https://github.com/mcrisnidh/pulso-nexus/actions/runs/42159129132` (run `42159129132`) |
| Propuesta implementada | `docs/proposals/E1-B.md` revisada en `4e46eaf` (criterios B1–B16 sin cambios) |
| Línea base E1-A | congelada: código `62c5600`, evidencia `1721bdc` (no modificadas) |
| Motor | SQL Server 2022 Developer Edition (64-bit), RTM-CU14 16.0.4135.4, contenedor oficial `mcr.microsoft.com/mssql/server:2022-CU14-ubuntu-22.04` |
| Digest de la imagen | `sha256:c1aa8afe9b06eab64c9774a4802dcd032205d1be785b1fd51e1c0151e7586b74` |
| Cliente | Microsoft ODBC Driver 18 `18.7.1.1-1`, `pyodbc 5.3.0`, Python 3.12 |
| Entorno | contenedores efímeros de GitHub Actions (`ubuntu-24.04`); solo `127.0.0.1`; nombre `nexus-sql` resuelto en `/etc/hosts` del runner |
| Datos | exclusivamente sintéticos |

## 1. Resultado global

| Job (CI bloqueante) | Resultado |
|---|---|
| E1-A Linux (A1–A14, `-m "not sql"`, A13 por paquete) | PASS: 310 passed, 111 deselected (SQL); A13 por paquete ≥ 90 %; TOCTOU 6/6; reproducibilidad 33/33 en dos configuraciones de hilos/TZ/locale; demostración E1-A |
| E1-A Windows (`-m "not sql"`) | PASS: 305 passed, 5 skipped (garantías POSIX declaradas de E1-A, pendientes en Windows), 111 deselected; TOCTOU 6/6 |
| E1-B SQL · RCSI OFF · sesión READ COMMITTED | PASS: 421 passed; cobertura 91.86 % |
| E1-B SQL · RCSI OFF · sesión SNAPSHOT | PASS: 421 passed; cobertura 91.93 % |
| E1-B SQL · RCSI ON · sesión READ COMMITTED | PASS: 421 passed; cobertura 91.86 % |
| E1-B SQL · RCSI ON · sesión SNAPSHOT | PASS: 421 passed; cobertura 91.93 % |

Cada celda SQL ejecuta la suite completa (unitaria, contrato, reproducibilidad, adversarial, arquitectura y `tests/sql`) con cobertura de ramas total ≥ 90 %.

## 1.1 Línea base E1-A congelada

Verificado con `git diff` contra `62c5600` (código) y `1721bdc` (evidencia):

- `docs/evidence/E1-A/` no tiene cambios después de `1721bdc`, salvo `CLOSURE.md`, que se añadió en el cierre autorizado de E1-A (`88853db`), antes de E1-B.
- En `src/`, fuera de los módulos nuevos (`nexus/metadata/`, `pipeline/worker.py`, `pipeline/reconcile.py`), solo cambia `cli.py`. Los cambios son aditivos: comandos `enqueue`, `work` y `reconcile`, más la reubicación de un import. El núcleo E1-A (hashing, storage, snapshots, contracts, normalize, engines, `pipeline/run`, compare, replay y verify) no se modificó. Las deudas DD-1 y DD-2 no se abordaron porque no se tocó `storage/`.
- La única prueba E1-A modificada es `tests/architecture/test_forbidden_deps.py`: `pyodbc` pasa a estar permitido **solo** en `nexus.metadata`, como fijaba la propuesta (B2).

## 2. Criterios B1–B16

Las evidencias por criterio están en `ci-run-42159129132/<celda>/evidence.json`, extraídas de los registros completos `ci-run-42159129132/<celda>/job.log`. Los secretos aparecen enmascarados (`***`) por GitHub Actions.

| ID | Prueba(s) | Evidencia (las 4 celdas) | Estado |
|---|---|---|---|
| B1 | Suite E1-A (jobs Linux/Windows); `test_b01_result_digest_identical_with_and_without_sql` | `result_digest` sin SQL = con SQL (`19b016710d2c9ae4…`) e `input_digest` igual, en las 4 celdas | PASS |
| B2 | ruff, `mypy --strict`, import-linter; `test_forbidden_deps.py`, `test_only_metadata_imports_pyodbc` | Job E1-A Linux en verde | PASS |
| B3 | `test_b03_*` (8 sentencias denegadas, `SELECT`/`ALTER` incluidos), control positivo de EXECUTE, append-only también para el propietario | 4 celdas | PASS |
| B4 | `test_b04a/b/c_*`; sondas `scripts/e1b_tls_probe.py` | Guard de bootstrap y de runtime (incluida `PULSO_MAGNUM_STAGE` simulada en local). TLS: sin CA → fallo de certificado; con CA → `encrypt_option=TRUE`; `127.0.0.1` fuera del SAN → fallo. El validador rechaza las degradaciones | PASS |
| B5 | `test_b05_claim_exclusivity_16x200` × 20 repeticiones | 0 violaciones, 0 claims duplicados (cliente) y 0 `CLAIMED` duplicados en `run_event` (auditoría independiente); 200/200 `COMPLETED`; p50 ≤ 0,05 s por solicitud | PASS |
| B6 | `test_b06_concurrent_enqueue_same_key` × 20 | 16 encoladores concurrentes, una sola solicitud | PASS |
| B7 | `test_b07_single_lease_and_claim_release` × 20; claim vencido | Un lease ganador; los perdedores liberan; tokens estrictamente crecientes; el claim vencido no consume intento | PASS |
| B8 | `test_b08_zombie_*`, `test_b08_exceptions_*` | Los 7 procedimientos con token responden `FENCED` sin modificar filas; excepciones con sus controles | PASS |
| B9 | `test_b09_sigkill_each_stage_then_bounded_retry` × 6 etapas | SIGKILL real; intentos ≤ máximo; 1 generación sellada; 1 `COMPLETED`; intentos previos `FAILED` | PASS |
| B10 | `test_b10a` (attention por timeout), `test_b10b` (SIGKILL del cliente, `KILL` en servidor) | 2 bloqueos activos durante la transacción y 0 después (`sys.dm_tran_locks`); datos sin cambios; conexión descartada | PASS |
| B11 | `test_b11_ambiguous_commit_never_duplicates` | Respuesta perdida tras COMMIT en 6 procedimientos: cada efecto aparece exactamente 1 vez | PASS |
| B12 | (a) `test_b12_hint_candidate_conformance` A/B/C; (b) B5–B11 en cada celda; (c) `test_sql_static.py` | Matriz de la §3; sin `NOLOCK`/`READUNCOMMITTED`; `READPAST` solo en el `UPDATE` de la reclamación; nunca `ROWLOCK`+`READCOMMITTEDLOCK` | PASS |
| B13 | `test_b13_server_clock_only`, `test_b13_lease_lost_by_latency_is_classified` | Ningún parámetro de fecha/hora; reloj del cliente +1 h sin efecto; `LEASE_LOST_BY_LATENCY` con hueco de servidor de 5516–5527 ms > lease de 5000 ms | PASS |
| B14 | `test_b14_deadlock_retried_without_duplicate_effects` | 1205 provocado; 1 reintento; efectos `['ab','ba']` sin duplicados | PASS |
| B15 | `test_b15_r_a` (BACKUP/RESTORE real), `r_b`, `r_c` (SIGKILL entre archivo y fila) | R-A: archivos sin fila informados y hashes idénticos. R-B: `MISSING`/`CORRUPT`, generación `INELIGIBLE`, 2 eventos de auditoría, idempotente. R-C: huérfanos informados, intactos y recuperados | PASS |
| B16 | Job SQL bloqueante (4 celdas); mutantes en esquemas aislados | Detectados: M1 (reclamación en dos pasos sin bloqueo de actualización), M2 × 7 (sin fencing en cada procedimiento con token), M3 (reconciliación que borra). M1b (UPDATE sin `UPDLOCK`) registrado como equivalente y sin violación de C1. Repeticiones de los escenarios concurrentes = 20 | PASS |

## 3. B12-a: conformidad de las pistas de bloqueo (decisión con T-SQL real)

Candidatos (nunca se combinan `ROWLOCK` y `READCOMMITTEDLOCK`; prueba estática `test_rowlock_never_combined_with_readcommittedlock`):

- **A** = `UPDLOCK, READPAST, ROWLOCK` (instalado en producción).
- **B** = `UPDLOCK, READPAST, READCOMMITTEDLOCK`.
- **C** = `UPDLOCK, READPAST`.

Para cada candidato y celda se midió:
1. Compilación del procedimiento en un esquema aislado.
2. Salto sin espera de una fila reclamada por un competidor real (el mismo procedimiento candidato en una transacción abierta) con `LOCK_TIMEOUT 3000`.
3. Exclusividad 8 × 100 con 3 repeticiones, con tablas vacías en cada una.

| Candidato | RCSI OFF · RC | RCSI OFF · SNAPSHOT | RCSI ON · RC | RCSI ON · SNAPSHOT |
|---|---|---|---|---|
| **A** `UPDLOCK, READPAST, ROWLOCK` (producción) | conforme (8 ms) | conforme (11 ms) | conforme (1 ms) | conforme (2 ms) |
| B `UPDLOCK, READPAST, READCOMMITTEDLOCK` | conforme (2 ms) | conforme (9 ms) | conforme (9 ms) | conforme (2 ms) |
| C `UPDLOCK, READPAST` | conforme (16 ms) | conforme (1 ms) | conforme (2 ms) | conforme (2 ms) |

"Conforme" significa que el procedimiento compila, que la fila del competidor se salta sin espera (tiempo entre paréntesis, límite 2500 ms) y que hay 0 violaciones de exclusividad en 3 × (8 workers × 100 solicitudes).

**Decisión:** se mantiene **A** (`UPDLOCK, READPAST, ROWLOCK`) en `usp_claim_request`.

- Los tres candidatos son sintácticamente válidos y conformes en RCSI ON/OFF × sesión RC/SNAPSHOT. El procedimiento fija `READ COMMITTED`, así que el nivel de la sesión no altera la semántica.
- Se prefiere A porque `ROWLOCK` mantiene la granularidad de fila que `READPAST` necesita para saltar exactamente la fila bloqueada.
- `READCOMMITTEDLOCK` (B) solo afecta a lecturas y no aporta nada en una sentencia `UPDATE`.

## 4. Defectos encontrados por las pruebas reales y corregidos

Todos se detectaron en CI contra SQL Server real. Se corrigieron sin relajar criterios ni umbrales y cada uno tiene una prueba de regresión (estática o SQL).

| # | Defecto | Evidencia | Corrección | Regresión |
|---|---|---|---|---|
| D1 | `DENY … CONTROL ON SCHEMA::nexus` implicaba `DENY EXECUTE`: el productor no podía ejecutar ningún procedimiento | error 229 sobre `usp_get_environment` | Sin `DENY CONTROL`; se añade `DENY SELECT` | `test_b03_producer_can_execute_procedures`, `test_permissions_never_deny_control` |
| D2 | Pooling del Driver Manager ODBC: una conexión descartada tras error, timeout o `KILL` podía reutilizarse | revisión de E0-R2 §2.5.1 en la implementación | `pyodbc.pooling = False` | B10 (KILL y SIGKILL) |
| D3 | `localhost` resolvía también a `::1`; el contenedor solo publica `127.0.0.1` (timeouts de login) | sondas TLS | `IpAddressPreference=IPv4First`; host `nexus-sql` (en el SAN) asignado a `127.0.0.1` | sondas TLS en CI |
| D4 | Lecturas `UPDLOCK, HOLDLOCK` y `UPDATE` sin índice recorrían tablas completas | B5 con 16 workers: leases vencidos | Índices `ix_scope_lease_attempt`, `ix_scope_lease_expiry`, `ix_attempt_request`, `ix_generation_request` | `test_concurrency_indexes_present` |
| D5 | Fencing con `JOIN scope_lease/attempt` bajo `UPDLOCK, HOLDLOCK`: admitía un plan de recorrido que bloqueaba los leases ajenos | B5 | Fencing en dos pasos por clave primaria (sin JOIN) | `test_fence_blocks_lock_only_by_primary_key` |
| D6 | `usp_acquire_lease` calculaba `expires_at` con `@now` tomado al inicio. La creación de la fila de lease con `IF NOT EXISTS … HOLDLOCK` sobre claves nuevas contiguas producía bloqueos de rango e interbloqueos, y el lease podía emitirse ya vencido | B5: `FENCED` también con heartbeat | Expiración con `SYSUTCDATETIME()` en la escritura; fila de lease creada fuera de la transacción con inserción directa; sin `HOLDLOCK` donde la fila de lease ya serializa | `test_lease_and_claim_expiry_computed_at_write_time`, `test_no_range_locks_where_lease_row_already_serializes` |
| D7 | `UPDATE scope_lease … WHERE attempt_id = …` (complete, fail, heartbeat) recorría `pk_scope_lease` tomando U sobre filas ajenas | grafos `xml_deadlock_report` de `system_health`: interbloqueos `usp_complete_run`/`usp_complete_run` en `pk_scope_lease` | Escrituras en `scope_lease` por clave primaria (`scope`) | `test_scope_lease_written_only_by_primary_key` |

Defectos de las propias pruebas (corregidos; no afectan al producto):

- La sonda B5 mantenía el lease sin heartbeat; ahora renueva cada 1 s como el worker real.
- B4c leía una DMV de servidor con el login de mínimo privilegio; ahora el cifrado se verifica desde la conexión de administración, por el `session_id` del productor.
- B12 usaba un bloqueador no representativo y acumulaba estados entre repeticiones.
- Los bucles de las sondas no estaban acotados (`faulthandler_timeout` y límite de 70 min añadidos).

## 5. Riesgos residuales (no resueltos en E1-B)

1. **Confianza TLS corporativa (P17).** La CA es efímera por ejecución y solo de confianza en el runner. La PKI corporativa, el anclaje de certificados y la rotación quedan pendientes.
2. **P12.** Versión, edición, collation y RCSI reales de `PULSO_MAGNUM_STAGE` siguen sin conocerse. E1-B solo prueba SQL Server 2022 CU14 Developer en contenedor, collation `SQL_Latin1_General_CP1_CI_AS`.
3. **Carga y hardware.** La concurrencia se probó con 16 workers en un runner de 2 vCPU. La latencia de una instancia compartida con producción (P15) no está medida. La pérdida de lease por latencia se clasifica como comportamiento correcto (B13), pero su frecuencia en el laboratorio es desconocida.
4. **Lease de 5 s y heartbeat de 1 s son solo de prueba.** Los valores de producción están pendientes de dimensionar.
5. **`usp_recover_expired`** recorre `scope_lease` y `run_request` con `UPDLOCK` (sin rango). Con muchos ámbitos puede tener coste; está pendiente medirlo en el laboratorio.
6. **Separación de identidades y certificación** son de E1-C. Las pruebas de E1-B usan `sa` del contenedor efímero para preparar escenarios y verificar.
7. **Administrador privilegiado:** sin cambio respecto de E0-R2 §4.5 (P5/P14/P16).
8. **Mutante equivalente M1b:** quitar `UPDLOCK` de la reclamación de una sola sentencia no es detectable porque es semánticamente equivalente; se registra y se exige que no viole C1. La variante peligrosa "sin UPDLOCK" (dos pasos, M1) sí se detecta.
9. **Windows** ejecuta E1-A y las pruebas unitarias con dobles; la integración SQL es solo Linux (decisión aceptada).

## 6. Procedimiento de reproducción

Toda la integración SQL corre en CI (`.github/workflows/ci.yml`, job `sql-ephemeral`).

1. Partir de un clon limpio de la rama en el commit del candidato.
2. **Sin SQL** (Linux o Windows), lanzar estos tres comandos:

```bash
uv sync --frozen
```

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && uv run lint-imports
```

```bash
uv run pytest -m "not sql" -ra
```

3. **Con SQL**, solo en un entorno aislado autorizado y nunca contra servidores corporativos, el job `sql-ephemeral` hace lo siguiente:
   1. Instala ODBC Driver 18 si falta (`ACCEPT_EULA=Y`).
   2. Asigna `nexus-sql` a `127.0.0.1` en `/etc/hosts`.
   3. Genera credenciales y *nonce* efímeros, enmascarados.
   4. Genera una CA y un certificado de servidor efímeros (SAN `nexus-sql`, `localhost`).
   5. Arranca el contenedor oficial con `ACCEPT_EULA=Y`, `MSSQL_PID=Developer`, publicado solo en `127.0.0.1`.
   6. Comprueba que la conexión TLS falla antes de confiar en la CA.
   7. Instala la CA en el almacén del runner y comprueba que la conexión TLS funciona y que un host fuera del SAN falla.
   8. Ejecuta `python -m nexus.metadata.bootstrap --rcsi on|off` con `NEXUS_BOOTSTRAP_PROFILE=e1b-ephemeral`.
   9. Ejecuta `pytest` (suite completa, cobertura ≥ 90 %).
   10. Recoge las evidencias y destruye el contenedor.

## 7. Licencias

- **SQL Server 2022 Developer Edition:** se usa la imagen oficial de Microsoft, aceptando su EULA (`ACCEPT_EULA=Y`), solo en contenedores efímeros de desarrollo y prueba, conforme a la autorización recibida.
- **Microsoft ODBC Driver 18:** se instala desde `packages.microsoft.com` con `ACCEPT_EULA=Y`.

## 8. Integridad

- `MANIFEST.sha256` (esta carpeta) contiene el SHA-256 de todos los archivos versionados en el commit de evidencia, excluido el propio manifiesto. Lo genera `scripts/make_manifest_e1b.sh`.
- Los SHA-256 de los scripts SQL del candidato figuran en la §9.

## 9. SHA-256 de los artefactos de E1-B del candidato

Calculados sobre el árbol del commit `531107c` (sin cambios de código posteriores):

```
36853aa7a0d241c3203916395a8e55a351867860e7069f37c180e9903bd0bc9a  .github/workflows/ci.yml
f15b11d23345d6a04b008a7f4e1d290140361aa7bd185303e2be65ded3ec2fa7  scripts/e1b_deadlocks.py
23b767bb08022ac213e53d4a6b9dbb67569adead213780237e32f22a963181cd  scripts/e1b_tls_probe.py
c60564c4616757c713c8ccf16b45f402d3fea9294f092c123dc3366fd73a528d  src/nexus/metadata/__init__.py
fc67fdf7386338f8f747a8a8193795a38d2562c9f3ac72cf055aecaedf15ddb5  src/nexus/metadata/bootstrap.py
5643090e932fd5b45d52ee29df92af8798fcaf9c1a1743f534d5806f98627cbd  src/nexus/metadata/connection.py
9e666e59e1f250234c56657de484b0700d949b5c37cddd3af4310a526746b9ba  src/nexus/metadata/errors.py
bf3a8768dfb1247b6ea7d17bfb8772c75ca35cd6c35d8dd3a516599d6385a27a  src/nexus/metadata/guards.py
69a8821eb6a7e213abcb4b3e2cfa581dfdfff700812833bfb9a5f8d3b1d7083e  src/nexus/metadata/memory.py
c5437a186922711fc769e5bc81851aad4029a2a7c5b42d1f5e3f78b18bdc7b25  src/nexus/metadata/migrations.py
64255e508440ece2629e3d139981025378ab9055f216d128839161f981c706fd  src/nexus/metadata/profile.py
859245931ce3962a7d4c3a2fd09b40d36a421030eb98254f5de88763490f86f2  src/nexus/metadata/profiles/e1b-ephemeral.toml
12956a8b172286be4ec80f59a9a1eca3e1d11b3f728acc6a149e9acee099dba6  src/nexus/metadata/repository.py
b1f3c0df28a53b5eb5e7349aa02eea2fb5f40d41f93a769a14b88199da6cefa0  src/nexus/metadata/sql/0001_schema.sql
aae97c35d37bcde6139cba932c2baba6fbc62bd889d36b41d04a97a2f87fc711  src/nexus/metadata/sql/0002_procedures.sql
d444809477447bbb98f7e96c19577dbe51d3328e1c1a8bf68ff5514ebe970516  src/nexus/metadata/sql/0003_permissions.sql
835857c5baa252c081acd79ae5d3cefde55bcf554eca01a3176a0c8a9a0ce1aa  src/nexus/metadata/sqlserver.py
00f0c3b47f87fe8a4829930f0da10891a3e8cbba478f685838ab85c8a736243d  src/nexus/pipeline/reconcile.py
b3863ff883c9afb0601c1348fa4fc037ad5f8e1f6cd3bb85896a933d4db9956f  src/nexus/pipeline/worker.py
```
