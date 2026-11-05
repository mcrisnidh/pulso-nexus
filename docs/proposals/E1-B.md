# PULSO NEXUS — Propuesta de alcance y criterios de aceptación: E1-B

**Estado:** PROPUESTA para revisión independiente. **No implementada.** E1-B no está autorizada.

**Base:** E1-A congelada (`62c5600` código, `1721bdc` evidencia; ver `docs/evidence/E1-A/CLOSURE.md`) y el plan aprobado E0-R2 (`docs/architecture/E0.md`: §2, §2.5.1, §4.2 y §9.5).

## 1. Objetivo

Añadir a NEXUS la persistencia de **metadatos** en **Microsoft SQL Server** y demostrar en un entorno **aislado, efímero y con datos sintéticos** cuatro garantías: cola de ejecuciones, leases, fencing de **todas** las escrituras y comportamiento ante transacciones interrumpidas. Debe hacerse sin degradar ninguna garantía de E1-A.

## 2. Restricciones vinculantes

1. Sin servidores, bases de datos ni credenciales corporativas. **Prohibido** conectar a `PULSO_MAGNUM_STAGE` o a cualquier instancia que no sea la temporal aislada.
2. Solo datos sintéticos. Sin conector de datos manuales (E2, depende de P2).
3. Sin certificación, publicación, políticas, firma ni segregación completa de identidades (E1-C).
4. Sin web ni reportes (E5).
5. SQL Server es el único motor objetivo. Sin PostgreSQL. SQLite solo para pruebas de forma, nunca para certificar garantías transaccionales.
6. Los datos tabulares permanecen en el ObjectStore. SQL solo guarda metadatos (E0-R2 §2.3).

## 3. Entorno aislado propuesto

| Aspecto | Propuesta | Estado |
|---|---|---|
| Motor | **SQL Server 2022 Developer Edition** en contenedor Linux (`mcr.microsoft.com/mssql/server:2022-<CU fijada>`) | **[?] Pendiente de autorización:** aceptación de la EULA de Developer Edition (uso exclusivo de desarrollo y prueba) y uso del registro `mcr.microsoft.com` |
| Versión objetivo | La versión corporativa es desconocida (P12). Las primitivas se limitan a las disponibles desde **SQL Server 2016 SP1** (E0-R2 §2.1). Matriz opcional con 2019 si P12 lo exige | Pendiente P12 |
| Ejecución de referencia | **Job Linux de GitHub Actions** con *service container* efímero: se crea y destruye en cada ejecución, sin red corporativa | Propuesto |
| Ejecución local | `compose.yaml` solo para desarrollo, donde haya Docker. **Observado:** el sandbox actual tiene el binario `docker` pero **no** su daemon, así que aquí no hay SQL local | Hecho observado |
| Windows | Los runners de Windows no ejecutan contenedores Linux de SQL Server. El job de Windows ejecuta **pruebas unitarias con dobles** del repositorio de metadatos más la suite de E1-A. La integración SQL solo se certifica en Linux | Limitación declarada |
| Credenciales | Generadas en cada ejecución (aleatorias, efímeras), nunca versionadas ni registradas en logs. Contraseña `sa` solo para el *bootstrap*; la aplicación usa logins de mínimo privilegio | Propuesto |
| Driver | `pyodbc` + **Microsoft ODBC Driver 18** con `Encrypt=yes` y certificado del contenedor de prueba confiado **solo** en el entorno efímero | Propuesto |
| Guard de entorno | Se exige `DB_NAME()` en una lista blanca de bases efímeras (`nexus_e1b_<run>`), fila sentinel `nexus.environment = 'E1B-ISOLATED'` y host *loopback*/servicio de CI. **Lista negra explícita:** `PULSO_MAGNUM_STAGE` y cualquier host no local: el proceso aborta antes de escribir | Propuesto |

## 4. Alcance funcional

### 4.1 Incluido

1. **Módulo `nexus.metadata`.** Es el único que puede importar `pyodbc` (import-linter + AST).
   - Expone una interfaz de repositorio.
   - Tiene un doble en memoria para pruebas unitarias y para Windows.
   - Usa T-SQL explícito, sin ORM.
2. **Migraciones** como scripts `.sql` numerados e idempotentes, con `nexus.schema_version`.
   - Se aplican con un login `nexus_migrator` (DDL).
   - La aplicación (`nexus_producer`) **no** tiene permisos DDL.
3. **Tablas mínimas de E1-B:**
   - `run_request`, con `idempotency_key` UNIQUE.
   - `scope_lease`: `holder`, `fencing_token BIGINT`, `expires_at datetime2`.
   - `run` y `run_event` (append-only).
   - `snapshot_ref`, `artifact`, `generation`, `generation_artifact`.
   - `environment` (sentinel) y `schema_version`.
4. **Procedimientos almacenados.** Todos con `SET XACT_ABORT ON` y `SET TRANSACTION ISOLATION LEVEL READ COMMITTED` explícitos, y el tiempo **siempre** de `SYSUTCDATETIME()`:
   - `usp_enqueue_request` (idempotente);
   - `usp_claim_request`: `UPDATE … WITH (UPDLOCK, READPAST, ROWLOCK) OUTPUT`; **nunca** `SELECT … READPAST`;
   - `usp_acquire_lease`, `usp_heartbeat`;
   - `usp_recover_expired`: incrementa el token, marca `FAILED(LEASE_EXPIRED)`, deja la generación como `ABANDONED` y vuelve a encolar con un *attempt* nuevo;
   - `usp_register_snapshot`, `usp_register_artifact`, `usp_seal_generation`;
   - `usp_complete_run`, `usp_fail_run`.
5. **Fencing de todas las escrituras de estado.** Cada procedimiento recibe `(@run_id, @fencing_token)` y lo valida en la misma transacción contra `scope_lease` con `UPDLOCK, HOLDLOCK`.
6. **Permisos:**
   - `nexus_producer` solo tiene `EXECUTE` sobre los procedimientos.
   - `DENY INSERT/UPDATE/DELETE` directo sobre las tablas.
   - Tablas append-only sin `UPDATE`/`DELETE` para ningún login de aplicación.
7. **Integración con el pipeline de E1-A.** Orden "archivo durable y verificado antes que fila" (E0-R2 §4.2, pasos 1–5):
   - `nexus run` reclama una solicitud, adquiere el lease, ejecuta, registra artefactos y sella.
   - `nexus run --trigger manual|scheduled` sigue siendo el mismo camino.
   - El ObjectStore no cambia.
8. **`nexus reconcile`:** compara filas y archivos. Las filas sin archivo pasan a `MISSING`; los archivos sin fila se informan como huérfanos.
9. **Cliente:**
   - Ante un error o timeout se **descarta la conexión**.
   - Ante un commit ambiguo se consulta el estado por clave de idempotencia antes de reintentar.
   - Reintento acotado ante deadlock (1205).

### 4.2 Excluido (y dónde va)

| Excluido | Etapa |
|---|---|
| Certificación, publicación CAS, políticas, firma, principales `validator`/`certifier`/`publisher` | E1-C |
| Conector de datos manuales y esquema P2 | E2 |
| Laboratorio `PULSO_MAGNUM_STAGE` | Certificación en E4, con autorización expresa |
| Web y reportes | E5 |
| Resolución de DD-1 y DD-2 de E1-A | Solo si E1-B toca `storage/`; si no, siguiente revisión de `storage/` |

## 5. Criterios de aceptación (B1–B16)

Todos se ejecutan contra la instancia efímera, salvo indicación contraria. Ninguno puede marcarse PASS con dobles en memoria si su objeto es una garantía transaccional.

| ID | Criterio verificable |
|---|---|
| **B1** | A1–A14 de E1-A siguen en PASS en Linux y Windows. El `result_digest` de la demostración es idéntico con y sin persistencia SQL |
| **B2** | `uv sync --frozen`, ruff, `mypy --strict` e import-linter en verde. Solo `nexus.metadata` importa `pyodbc`. Siguen prohibidos `sqlalchemy`, `psycopg*`, `msal`, `msgraph*`, `sharepoint*` y `office365*` |
| **B3** | Las migraciones se aplican sobre una base vacía y su segunda aplicación es nula (idempotente). `schema_version` es correcto. El login `nexus_producer` recibe *permission denied* al intentar DDL, `INSERT`/`UPDATE`/`DELETE` directo, o `UPDATE`/`DELETE` sobre tablas append-only |
| **B4** | **Guard:** conectar a una base cuyo `DB_NAME()`, sentinel o host no estén permitidos aborta **antes** de cualquier escritura. Una prueba con `PULSO_MAGNUM_STAGE` simulado (base local con ese nombre) **falla cerrado** |
| **B5** | **Cola:** N = 16 procesos reclaman M = 200 solicitudes simultáneamente. Cada solicitud se reclama **exactamente una vez** y no hay *starvation* |
| **B6** | **Idempotencia:** `usp_enqueue_request` repetido de forma concurrente con la misma clave genera **una** solicitud |
| **B7** | **Lease único:** 16 procesos compiten por el mismo ámbito y exactamente uno obtiene el lease. El token de fencing es estrictamente creciente |
| **B8** | **Fencing:** un worker zombi (lease expirado y recuperado por otro) intenta cada procedimiento de escritura. **Todas** las escrituras se rechazan y no se modifica ninguna fila. Lo verifica un recuento por procedimiento |
| **B9** | **Recuperación:** se mata el worker (`SIGKILL`) en cada etapa (claim, registro, sellado, completado). `usp_recover_expired` deja el run en `FAILED(LEASE_EXPIRED)` y la generación en `ABANDONED`, nunca publicable, y el reintento crea un *attempt* nuevo con `run_id`/`gen_id` distintos |
| **B10** | **Transacciones interrumpidas:** proceso terminado a mitad de transacción, timeout de cliente (*attention*) y conexión cortada. Rollback completo, sin bloqueos residuales (consulta a `sys.dm_tran_locks`) y conexión descartada |
| **B11** | **Commit ambiguo:** con inyección de fallo tras `COMMIT` y antes del acuse, el cliente consulta el estado y **no duplica** la operación |
| **B12** | **Matriz de aislamiento:** B5–B11 se ejecutan con `READ_COMMITTED_SNAPSHOT` ON y OFF y con la sesión en `SNAPSHOT`. Resultados idénticos. Una prueba demuestra que `SELECT … READPAST` bajo RCSI **no** sirve como cola y que el código no lo usa (AST sobre los scripts `.sql`) |
| **B13** | **Reloj:** con desfase artificial del reloj del cliente (±1 h), la expiración depende solo de `SYSUTCDATETIME()` |
| **B14** | **Deadlocks:** un escenario provoca un 1205 y el cliente lo reintenta sin efectos duplicados |
| **B15** | **Coherencia SQL ↔ archivos:** la caída entre "archivo durable" y "fila" nunca deja una fila sin archivo. `nexus reconcile` detecta los huérfanos y las filas `MISSING` tras borrar archivos o hacer un restore de SQL a un punto anterior |
| **B16** | **Evidencia:** CI con un job SQL Linux bloqueante (sin `continue-on-error`). Clonación limpia, logs por criterio, manifiesto SHA-256 e informe con límites. Las sondas adversariales deben **fallar** contra una versión deliberadamente defectuosa (control de mutación: sin `UPDLOCK`, sin validar el token) |

## 6. Entregables

- `src/nexus/metadata/` (interfaz, doble en memoria, implementación SQL Server y guard).
- `sql/migrations/*.sql` y `sql/procedures/*.sql`.
- `compose.yaml` (solo desarrollo) y ampliación de `.github/workflows/ci.yml` con el servicio SQL.
- `tests/sql/`, marcados como `sql`, más `tests/adversarial/sql/`.
- ADR-0006 (SQL Server + T-SQL explícito), ADR-0007 (leases y fencing) y ADR-0008 (guard de entorno).
- `docs/evidence/E1-B/` con informe, logs por criterio, CI por plataforma y manifiesto.

## 7. Decisiones pendientes antes de autorizar E1-B

1. **EULA de SQL Server Developer Edition** y uso de la imagen de `mcr.microsoft.com` en CI. Decisión del usuario o de su área legal.
2. **P12:** versión, edición, collation y estado de RCSI/SNAPSHOT del entorno objetivo. Si no se conoce, la matriz B12 cubre ambos estados.
3. ¿Es aceptable que la **certificación transaccional de E1-B** se haga **solo en Linux**, por la limitación de contenedores en los runners de Windows?
4. **Duración de leases y heartbeats** en las pruebas: se proponen 5 s / 1 s para que las pruebas sean rápidas. Los valores productivos quedan pendientes (P8).
5. Autorización para crear la etiqueta `e1a-baseline` (ver `CLOSURE.md`).

## 8. Riesgos

| Riesgo | Mitigación |
|---|---|
| Diferencias entre el contenedor Developer y la instancia corporativa (versión, configuración, carga compartida) | Solo primitivas de 2016 SP1 y matriz RCSI. La certificación final se hace en `PULSO_MAGNUM_STAGE` en E4, con autorización |
| Pruebas de concurrencia intermitentes | Repeticiones obligatorias (≥ 20 por escenario en CI), estadísticas en el log y control de mutación (B16) |
| Fuga de credenciales en logs | Generadas en cada ejecución, enmascaradas y con un escaneo de patrones sobre logs y manifiestos |
| Dependencia del driver ODBC (instalación de sistema) | Versión fijada, verificada al inicio del job y documentada |
| Imposibilidad de SQL local en el sandbox actual (sin daemon de Docker) | La referencia es el CI efímero. Se declara en la evidencia |
