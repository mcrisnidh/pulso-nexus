# PULSO NEXUS — Propuesta de alcance y criterios de aceptación: E1-B (revisión R1)

**Estado:** PROPUESTA revisada con NX-E1B-01 a NX-E1B-06. **No implementada.** E1-B no está autorizada.

**Base:**
- E1-A cerrada y congelada (código `62c5600`, evidencia `1721bdc`; `docs/evidence/E1-A/CLOSURE.md`).
- Plan aprobado E0-R2 (`docs/architecture/E0.md` §2, §2.5.1, §4.2 y §9.5). Esta revisión no reabre E0.

## 0. Matriz de resolución de la revisión

| Hallazgo | Resolución | Sección |
|---|---|---|
| **NX-E1B-01** READPAST bajo RCSI | Contrato de reclamación con conjunto de hints **candidato** `UPDLOCK, READPAST, ROWLOCK, READCOMMITTEDLOCK` dentro de `READ COMMITTED` explícito. La combinación definitiva se fija **solo** por una prueba de conformidad que ejecuta T-SQL real en cada configuración (RCSI ON/OFF, sesión RC/SNAPSHOT) y demuestra el salto de filas bloqueadas sin espera | §4.3, B12 |
| **NX-E1B-02** Dirección de la reconciliación | Dos escenarios con nombre, detección y pruebas independientes. **R-A:** SQL restaurado a un punto anterior, archivos actuales → archivos sin fila. **R-B:** archivos restaurados o eliminados, SQL actual → filas sin archivo. **Nunca** se borran huérfanos automáticamente | §4.6, B15 |
| **NX-E1B-03** Cola, lease y fencing | Se separan tres contratos: exclusividad de la reclamación (claim con token y caducidad), reintentos de ejecución (*at-least-once* por intentos) e idempotencia de los efectos persistidos (una sola generación sellada por solicitud). Hay recuperación del claim sin lease, una tabla de procedimientos con o sin token y controles propios para cada excepción. Se retira la promesa de "exactamente una vez" | §4.2, §4.4, B5–B9 |
| **NX-E1B-04** Guards | Guard de **bootstrap** (sin sentinel, con comprobaciones restrictivas propias) separado del guard de **runtime** (base, entorno y sentinel antes de cualquier escritura operativa). La exclusión de `PULSO_MAGNUM_STAGE` es una regla del **perfil `e1b-ephemeral`**, no una prohibición codificada | §3.2, B4 |
| **NX-E1B-05** TLS/ODBC | TLS verificado con ODBC Driver 18: `Encrypt=yes`, `TrustServerCertificate=no`, CA efímera por ejecución, certificado con SAN del host de servicio y prueba negativa. `TrustServerCertificate=yes` se **rechaza** en todos los perfiles de E1-B. La confianza TLS corporativa queda **pendiente** (P17) | §3.3, B4 |
| **NX-E1B-06** Tiempos de lease | Lease de 5 s y heartbeat de 1 s **solo en pruebas** y configurables. Marcas de tiempo del **servidor** registradas. Las aserciones se basan en invariantes, no en tiempos absolutos, y se diferencia latencia de CI de defecto. Job SQL bloqueante y pruebas de mutación | §4.7, B13, B16 |

## 1. Objetivo

Añadir la persistencia de **metadatos** en **Microsoft SQL Server** y demostrar, en un entorno **aislado, efímero y con datos sintéticos**:
- exclusividad de reclamación;
- leases con fencing;
- reintentos controlados con efectos idempotentes;
- comportamiento correcto ante transacciones interrumpidas.

Todo ello sin degradar ninguna garantía de E1-A.

## 2. Restricciones vinculantes

1. Sin servidores, bases de datos, datos ni credenciales corporativas.
2. **No se acepta la EULA en representación del usuario ni se inicia ningún contenedor** hasta recibir autorización expresa.
3. Solo datos sintéticos. Sin conector de datos manuales (E2, depende de P2).
4. Sin certificación, publicación, políticas, firma ni segregación completa de identidades (E1-C). Sin web ni reportes (E5).
5. SQL Server es el único motor transaccional objetivo. Sin PostgreSQL. SQLite solo para pruebas de forma, nunca para certificar garantías.
6. Los datos tabulares permanecen en el ObjectStore. SQL solo guarda metadatos.
7. El núcleo de E1-A no se modifica salvo en la integración del pipeline (§4.5). Cualquier cambio en `storage/` abre la corrección de DD-1 y DD-2.

## 3. Entorno aislado

### 3.1 Plataforma

| Aspecto | Propuesta | Estado |
|---|---|---|
| Motor | SQL Server 2022 Developer Edition, contenedor Linux con CU fijada | **Opción técnica condicionada** a la aceptación de la licencia y a la política aplicable. No autorizada |
| Primitivas | Solo las disponibles desde SQL Server 2016 SP1 | P12 pendiente de verificación exacta |
| Integración SQL real | **Solo Linux** en E1-B (*service container* efímero en CI) | Aceptado por la revisión |
| Windows | Mantiene la suite de E1-A y pruebas unitarias con dobles del repositorio de metadatos. La validación con SQL Server real se hará más adelante | Aceptado por la revisión |
| Local | `compose.yaml` solo para desarrollo, donde haya Docker. El sandbox actual no tiene daemon | Hecho observado |
| Driver | `pyodbc` + Microsoft ODBC Driver 18, versión fijada y verificada al inicio del job | Propuesto |

### 3.2 Guards (NX-E1B-04)

La configuración se agrupa en **perfiles** (`config/profiles/*.toml`) versionados. E1-B solo define el perfil `e1b-ephemeral`. El código aplica las reglas del perfil y **no** contiene nombres corporativos prohibidos de forma permanente.

**Guard de bootstrap** (`nexus-bootstrap`, con el login administrativo del contenedor y solo en el job de CI). No puede usar el sentinel porque aún no existe. Todas sus comprobaciones se hacen antes de cualquier `CREATE`:

1. Activación explícita: `NEXUS_BOOTSTRAP_PROFILE=e1b-ephemeral` y un *nonce* de ejecución generado por el job. Sin ambos, aborta.
2. Host en la allowlist del perfil (`localhost`, `127.0.0.1` o el nombre del servicio del job). Cualquier otro host aborta.
3. Identidad del servidor: `SERVERPROPERTY('EngineEdition')` y `SERVERPROPERTY('Edition')` deben indicar Developer (o lo que fije el perfil). `@@SERVERNAME` debe coincidir con el patrón del contenedor.
4. **Servidor vacío:** no puede existir ninguna base de usuario (`sys.databases` con `database_id > 4`), salvo las del patrón efímero creadas por el mismo *nonce*. Una instancia con bases ajenas hace abortar el bootstrap.
5. La base se crea con el nombre `nexus_e1b_<nonce>`. `CREATE DATABASE` sobre un nombre existente se trata como error y no se reutiliza.
6. Al final crea el sentinel `nexus.environment(profile, kind='EPHEMERAL', nonce, created_at_server)`, los logins `nexus_migrator` y `nexus_producer`, y aplica las migraciones.
7. Denylist del perfil `e1b-ephemeral`: incluye `PULSO_MAGNUM_STAGE`. Un futuro perfil de certificación expresamente autorizado (E4) tendría su **propio** bootstrap o validación, su sentinel y su aprobación. No hereda nada de `e1b-ephemeral`.

**Guard de runtime** (worker y CLI, con `nexus_producer`). Se ejecuta antes de **cualquier** escritura operativa, en cada conexión nueva:

1. Host en la allowlist del perfil.
2. `DB_NAME()` en la allowlist del perfil (patrón `nexus_e1b_*`) y fuera de su denylist.
3. Sentinel presente y coincidente con el perfil, con `kind='EPHEMERAL'` y el *nonce* configurado para la ejecución.
4. `nexus.schema_version` en la versión que espera el código.
5. Si algo falla, se aborta sin escribir. La conexión se descarta y el error queda auditado en el log del cliente.

### 3.3 TLS y ODBC (NX-E1B-05)

1. Por cada ejecución de CI se genera una **CA efímera** y un certificado de servidor con SAN igual al nombre del host de servicio y `localhost`. La clave privada no sale del job.
2. El contenedor se configura con `network.tlscert`, `network.tlskey` y `network.forceencryption = 1`.
3. El cliente usa `Encrypt=yes;TrustServerCertificate=no` (el modo por defecto de ODBC Driver 18) y confía en la CA efímera **solo** dentro del job. La CA se añade al almacén de confianza del runner o se indica con el atributo de certificado que admita la versión fijada del driver; la elección se fija en la implementación y se documenta.
4. **Validador de configuración:** rechaza `TrustServerCertificate=yes`, `Encrypt=no` y `Encrypt=optional` en todos los perfiles de E1-B. No existe interruptor de desarrollo que lo permita por defecto.
5. **Pruebas negativas:** un certificado de otra CA o con un nombre de host que no coincide debe hacer **fallar** la conexión.
6. **P17 (nuevo, pendiente):** confianza TLS con las instancias corporativas (CA, nombres y política). Es independiente y **no** se resuelve en E1-B.

## 4. Alcance funcional

### 4.1 Módulos y migraciones

- **`nexus.metadata`:** es el único que importa `pyodbc`. Contiene la interfaz de repositorio, un doble en memoria (solo para pruebas unitarias y para Windows), la implementación SQL Server, los guards y el validador de conexión. Usa T-SQL explícito, sin ORM.
- **Migraciones:** scripts `.sql` numerados e idempotentes con `nexus.schema_version`, aplicados por `nexus_migrator`. `nexus_producer` solo tiene `EXECUTE` sobre los procedimientos; `INSERT`/`UPDATE`/`DELETE` directo le está **denegado** y las tablas append-only no se modifican.
- **Tablas:**
  - `run_request`: `idempotency_key` UNIQUE, `state`, `attempts`, `not_before`, `claim_token`, `claimed_by`, `claim_expires_at`.
  - `scope_lease`: `holder`, `fencing_token BIGINT`, `expires_at`.
  - `run` y `run_event` (append-only, con `server_ts`).
  - `snapshot_ref`, `artifact`, `generation`, `generation_artifact`.
  - `environment` y `schema_version`.

### 4.2 Semántica (NX-E1B-03)

Tres contratos distintos:

| Contrato | Garantía | Lo que **no** se promete |
|---|---|---|
| **C1. Exclusividad de reclamación** | En cada instante, una solicitud tiene **como máximo un claim vigente**: token generado por el servidor y caducidad `claim_expires_at` en hora del servidor. El claim se toma de forma atómica (`UPDATE … OUTPUT`) | Que el titular del claim llegue a ejecutar |
| **C2. Reintentos de ejecución** | **Al menos una vez por intento.** Cada intento es un `run` nuevo (`run_id` y `gen_id` distintos). Los intentos fallidos o abandonados quedan `FAILED`/`ABANDONED`. Hay un máximo de intentos configurable y, al superarlo, `FAILED_PERMANENT`. Entre intentos se aplica un *backoff* con `not_before` | **No hay "exactamente una ejecución física"**: bajo fallos, un trabajo puede ejecutarse físicamente más de una vez |
| **C3. Idempotencia de efectos persistidos** | Por solicitud se sella **como máximo una generación** (`UNIQUE(request_id) WHERE state='SEALED'`) y la solicitud pasa a `COMPLETED` una sola vez. El registro de un artefacto es idempotente por `(gen_id, contract)`. Los efectos de intentos fenced o abandonados nunca se convierten en el resultado | Que no queden artefactos huérfanos de intentos abandonados (se informan, §4.6) |

**Claim reclamado sin lease:** si el worker reclama pero no consigue el lease del ámbito (ocupado o error):

1. Llama a `usp_release_claim(@request_id, @claim_token)`: la solicitud vuelve a `PENDING` con `not_before = now + backoff` y **sin** consumir un intento de ejecución.
2. Si el worker muere sin liberar, `usp_recover_expired` encuentra `claim_expires_at < SYSUTCDATETIME()` y devuelve la solicitud a `PENDING`.
3. Un claim liberado o caducado invalida su `claim_token`, así que el worker anterior ya no puede adquirir el lease con él.

### 4.3 Reclamación y READPAST bajo RCSI (NX-E1B-01)

- **Contrato:** la reclamación es **una única sentencia de modificación** dentro de un procedimiento que fija `SET TRANSACTION ISOLATION LEVEL READ COMMITTED` y `SET XACT_ABORT ON`:

  `UPDATE TOP (1) … WITH (UPDLOCK, READPAST, ROWLOCK, READCOMMITTEDLOCK) … OUTPUT inserted.*`, sobre un CTE ordenado por `not_before, created_at`.

  `READCOMMITTEDLOCK` es el **candidato** para que la lectura bajo RCSI ON use bloqueos y no versiones, compatible con `READPAST`. **No** se usa `SELECT … READPAST` como mecanismo de cola.
- **Validación obligatoria con T-SQL real (B12-a).** El conjunto definitivo de hints se fija **solo** tras ejecutar la prueba de conformidad en cada configuración:
  - `READ_COMMITTED_SNAPSHOT` ON y OFF;
  - sesión llamante en `READ COMMITTED` y en `SNAPSHOT` (con `ALLOW_SNAPSHOT_ISOLATION ON`).

  En cada configuración debe:
  1. compilar y ejecutarse sin error;
  2. **saltar sin esperar** una fila bloqueada por otra sesión (la sesión A bloquea la fila 1 y la sesión B reclama la fila 2 en menos de 1 s con `LOCK_TIMEOUT` corto);
  3. no reclamar dos veces la misma fila con N sesiones concurrentes.

  Si un candidato falla en alguna configuración, se evalúa el siguiente (sin `READCOMMITTEDLOCK`, o con `READCOMMITTED` explícito). El conjunto aceptado y el resultado de cada configuración quedan en la evidencia. La inspección estática (AST de `.sql`) solo complementa: impide `SELECT … READPAST` y `NOLOCK`.

### 4.4 Fencing: procedimientos con y sin token (NX-E1B-03)

| Procedimiento | ¿Requiere fencing token vigente? | Control transaccional propio |
|---|---|---|
| `usp_heartbeat` | **Sí** | Token = actual y `expires_at > SYSUTCDATETIME()`, con `UPDLOCK, HOLDLOCK` sobre `scope_lease` |
| `usp_register_snapshot_ref`, `usp_register_artifact`, `usp_append_run_event` | **Sí** | Ídem. Además, la idempotencia por clave natural |
| `usp_seal_generation`, `usp_complete_run`, `usp_fail_run` | **Sí** | Ídem. Además, la unicidad de la generación sellada por solicitud (C3) y las transiciones de estado válidas |
| `usp_enqueue_request` | **No** (excepción: no hay lease) | `idempotency_key` UNIQUE; un duplicado devuelve la solicitud existente; validación de parámetros; solo `EXECUTE` |
| `usp_claim_request` | **No** (excepción: precede al lease) | `UPDATE … OUTPUT` atómico con el predicado `state='PENDING' AND not_before <= now`; `claim_token` generado por el servidor; caducidad en hora del servidor |
| `usp_release_claim` | **No** (excepción) | Exige el `claim_token` vigente; sin él, no hace nada (0 filas) |
| `usp_acquire_lease` | **No** (excepción: lo emite) | Exige el `claim_token` vigente de la solicitud. `UPDATE` condicional (`holder IS NULL OR expires_at < now`) que **incrementa** `fencing_token` y lo devuelve con `OUTPUT` |
| `usp_recover_expired` | **No** (excepción: actúa sobre leases y claims vencidos) | Solo filas con `expires_at`/`claim_expires_at < SYSUTCDATETIME()` bajo `UPDLOCK, HOLDLOCK`. **Incrementa** el token (invalida al zombi), marca run y generación, libera el claim y es idempotente |

Una prueba de arquitectura recorre todos los procedimientos y falla si alguno que escribe estado operativo no está en la tabla o no valida lo que la tabla exige.

### 4.5 Integración con el pipeline

- Se mantiene el orden "archivo durable y verificado antes que fila" (E0-R2 §4.2, pasos 1–5).
- `nexus run --trigger manual|scheduled` sigue siendo el mismo camino.
- El ObjectStore no cambia.
- El `result_digest` no depende de SQL.

### 4.6 Reconciliación (NX-E1B-02)

`nexus reconcile` es de **solo informe** salvo la transición de estado indicada. **Nunca borra ni mueve archivos.**

| Escenario | Causa típica | Síntoma | Acción |
|---|---|---|---|
| **R-A** | **SQL restaurado** a un punto anterior; archivos actuales | **Archivos sin fila SQL**: artefactos, manifiestos o `run_record` del ObjectStore no registrados | Se informan como `UNREGISTERED_FILE` con clave, hash e identificador de corrida del `run_record` si es legible. **No** se borran, no se mueven ni se registran automáticamente. Su re-registro requiere una acción explícita fuera de E1-B |
| **R-B** | **Archivos restaurados** a un punto anterior o **eliminados**; SQL actual | **Filas sin archivo**, o archivo con hash distinto | `usp_mark_artifact_missing` pasa las filas a `MISSING` (o `CORRUPT` si el hash no coincide), con evento de auditoría. La generación afectada deja de ser elegible. No se toca ningún archivo |
| R-C | Caída entre "archivo durable" y "fila" | Archivo sin fila de una corrida no completada | Igual que R-A, etiquetado `INCOMPLETE_RUN` |

### 4.7 Tiempos de prueba (NX-E1B-06)

- `lease = 5 s` y `heartbeat = 1 s` **solo en pruebas** (perfil `e1b-ephemeral`). Los valores productivos quedan pendientes (P8).
- Cada evento registra `server_ts = SYSUTCDATETIME()`. El *harness* registra además el reloj monotónico del cliente y el `server_ts` devuelto por cada llamada.
- **Las aserciones son invariantes, no tiempos:**
  - nunca dos titulares a la vez;
  - toda escritura con token obsoleto se rechaza;
  - como máximo una generación sellada por solicitud.

  Cuando la latencia del CI provoca que un heartbeat llegue tarde (hueco > lease medido en hora del servidor), el resultado esperado es la **pérdida del lease y el rechazo por fencing**. La prueba lo **clasifica** como `LEASE_LOST_BY_LATENCY` con sus marcas de servidor y verifica que el sistema reaccionó correctamente. No se cuenta como defecto ni se oculta.
- Las aserciones temporales (por ejemplo, "la recuperación ocurre después de la caducidad") usan **solo `server_ts`** con margen explícito. No se usa el reloj del cliente.

## 5. Criterios de aceptación (B1–B16)

Las garantías transaccionales solo pueden marcarse PASS contra SQL Server real en Linux. Los dobles en memoria no cuentan.

| ID | Criterio verificable |
|---|---|
| **B1** | A1–A14 de E1-A siguen en PASS en Linux y Windows. El `result_digest` de la demostración es idéntico con y sin persistencia SQL |
| **B2** | `uv sync --frozen`, ruff, `mypy --strict` e import-linter en verde. Solo `nexus.metadata` importa `pyodbc`. Siguen prohibidos `sqlalchemy`, `psycopg*`, `msal`, `msgraph*`, `sharepoint*` y `office365*` |
| **B3** | Migraciones idempotentes. `nexus_producer` recibe *permission denied* ante DDL, DML directo y `UPDATE`/`DELETE` sobre tablas append-only |
| **B4** | **Guards y TLS:**<br>**(a)** el bootstrap aborta sin perfil o *nonce*, con un host fuera de la allowlist, con una edición no permitida o con bases de usuario ajenas;<br>**(b)** el runtime aborta **antes de escribir** si la base, el sentinel, el *nonce* o la versión de esquema no coinciden, y el perfil `e1b-ephemeral` rechaza una base llamada `PULSO_MAGNUM_STAGE` (simulada en local);<br>**(c)** TLS verificado con `TrustServerCertificate=no`, conexión rechazada con una CA ajena o con un host que no coincide, y el validador rechaza `TrustServerCertificate=yes`, `Encrypt=no` y `Encrypt=optional` |
| **B5** | **C1:** con N = 16 workers y M = 200 solicitudes, nunca hay dos claims vigentes sobre la misma solicitud (auditoría de `run_event`). Sin fallos inyectados, cada solicitud se reclama una vez. Con fallos, el número de intentos queda acotado por la configuración |
| **B6** | `usp_enqueue_request` repetido de forma concurrente con la misma clave genera **una** solicitud |
| **B7** | Lease único por ámbito con 16 competidores. `fencing_token` estrictamente creciente. El claim sin lease se libera (`usp_release_claim`) o se recupera por caducidad sin consumir un intento |
| **B8** | **Fencing:** un zombi con token obsoleto intenta **cada** procedimiento de la §4.4 que exige token y todos rechazan sin modificar filas (recuento por procedimiento). Las excepciones se prueban con sus controles propios (claim token inválido, `idempotency_key` duplicada, recuperación sin vencimiento) |
| **B9** | **C2/C3 bajo fallos:** `SIGKILL` en cada etapa. Hay reintento con un *attempt* nuevo, **como máximo una generación sellada** y la solicitud `COMPLETED` una sola vez. Los efectos de intentos abandonados quedan `ABANDONED`/huérfanos y se informan |
| **B10** | Transacciones interrumpidas (muerte del proceso, *attention* por timeout, conexión cortada): rollback completo, sin bloqueos residuales (`sys.dm_tran_locks`) y conexión descartada |
| **B11** | Commit ambiguo: con inyección tras `COMMIT` y antes del acuse, el cliente consulta el estado y **no duplica** efectos |
| **B12** | **(a)** Conformidad de la reclamación con T-SQL real en RCSI ON/OFF × sesión RC/SNAPSHOT: compila, salta filas bloqueadas sin esperar y nunca reclama dos veces. Se registran el conjunto de hints aceptado y el resultado por configuración.<br>**(b)** B5–B11 pasan en cada configuración.<br>**(c)** El AST de `.sql` prohíbe `SELECT … READPAST` como cola y `NOLOCK` |
| **B13** | Reloj del cliente desfasado ±1 h sin efecto: la expiración usa `SYSUTCDATETIME()`. Las pruebas registran `server_ts` y clasifican la pérdida de lease por latencia como comportamiento correcto (§4.7) |
| **B14** | Deadlock 1205 provocado y reintentado sin efectos duplicados |
| **B15** | **(a) R-A:** restore de SQL a un punto anterior con archivos actuales; `reconcile` informa `UNREGISTERED_FILE` y **no** borra ni mueve nada (hash de todos los archivos igual antes y después).<br>**(b) R-B:** archivos eliminados o restaurados a una versión anterior con SQL actual; filas `MISSING`/`CORRUPT` con auditoría; generación no elegible; ningún archivo tocado.<br>**(c) R-C:** caída entre archivo y fila; nunca hay una fila sin archivo |
| **B16** | Job SQL Linux **bloqueante**. Clonación limpia, logs por criterio con `server_ts`, manifiesto e informe. **Mutaciones:** las sondas deben **fallar** contra variantes defectuosas a propósito (sin `UPDLOCK`; sin validar el token en cada procedimiento de §4.4; reclamación con `SELECT … READPAST`; reconciliación que borra huérfanos). Repeticiones de cada escenario concurrente ≥ 20 |

## 6. Entregables

- `src/nexus/metadata/` (interfaz, doble en memoria, SQL Server, guards de bootstrap y runtime, validador TLS/ODBC).
- `config/profiles/e1b-ephemeral.toml`.
- `sql/migrations/`, `sql/procedures/`, `scripts/bootstrap_ephemeral.*` (incluye la generación de la CA efímera).
- `compose.yaml` (solo desarrollo) y el job SQL en `.github/workflows/ci.yml`.
- `tests/sql/`, `tests/adversarial/sql/` y `tests/mutation/sql/`.
- ADR-0006 (SQL Server + T-SQL), ADR-0007 (C1/C2/C3, leases y fencing), ADR-0008 (perfiles y guards) y ADR-0009 (TLS ODBC).
- `docs/evidence/E1-B/`.

## 7. Decisiones pendientes antes de autorizar código

1. **Licencia y política de SQL Server Developer Edition y de la imagen `mcr.microsoft.com`.** Opción técnica propuesta; **no se acepta la EULA ni se inicia ningún contenedor** sin autorización expresa.
2. **P12:** versión, edición, collation y RCSI/SNAPSHOT objetivo, pendientes de verificación exacta. La matriz B12 cubre ambos estados.
3. **P8:** tiempos productivos de lease y heartbeat (5 s y 1 s aceptados provisionalmente solo para pruebas).
4. **P17 (nuevo):** confianza TLS corporativa.
5. **Etiqueta `e1a-baseline`:** recomendada; requiere autorización expresa de creación.

Las decisiones ya tomadas por la revisión (integración SQL real solo en Linux durante E1-B y Windows con su suite) quedan incorporadas en §3.1.

## 8. Riesgos

| Riesgo | Mitigación |
|---|---|
| Diferencias entre el contenedor Developer y la instancia corporativa | Primitivas de 2016 SP1, matriz B12 y certificación en E4 con autorización y su propio perfil |
| Combinación de hints no conforme en alguna configuración | Se descubre en B12-a con T-SQL real antes de dar el contrato por bueno; alternativas evaluadas y documentadas |
| Intermitencia por latencia de CI | Invariantes en lugar de tiempos, `server_ts` y clasificación `LEASE_LOST_BY_LATENCY` |
| Fuga de credenciales o claves TLS | Generadas en cada ejecución, enmascaradas y con escaneo de patrones en logs y manifiestos |
| Bootstrap ejecutado contra un servidor equivocado | Guard de bootstrap restrictivo (activación, allowlist de host, edición, servidor vacío) |
| SQL local imposible en el sandbox actual | La referencia es el CI efímero, una vez autorizado |
