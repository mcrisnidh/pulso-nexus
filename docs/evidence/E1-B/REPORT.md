# Informe de evidencia E1-B: persistencia de metadatos en SQL Server efímero

> **Estado: CANDIDATO ÚNICO para revisión independiente.** No se declara GO.
> No se crea la etiqueta `e1a-baseline`. No se inicia E1-C, E2 ni etapas posteriores.

| Dato | Valor |
|---|---|
| Rama | `claude/blissful-feynman-obiif0` |
| Commit del candidato (código y pruebas) | `{{CANDIDATE_SHA}}` |
| Ejecución CI del candidato | `{{RUN_URL}}` (run `{{RUN_ID}}`) |
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
| E1-A Linux (A1–A14, `-m "not sql"`, A13 por paquete) | {{E1A_LINUX}} |
| E1-A Windows (`-m "not sql"`) | {{E1A_WINDOWS}} |
| E1-B SQL · RCSI OFF · sesión READ COMMITTED | {{CELL_OFF_RC}} |
| E1-B SQL · RCSI OFF · sesión SNAPSHOT | {{CELL_OFF_SN}} |
| E1-B SQL · RCSI ON · sesión READ COMMITTED | {{CELL_ON_RC}} |
| E1-B SQL · RCSI ON · sesión SNAPSHOT | {{CELL_ON_SN}} |

Cada celda SQL ejecuta la suite completa (unitaria, contrato, reproducibilidad, adversarial, arquitectura y `tests/sql`) con cobertura de ramas total ≥ 90 %.

## 2. Criterios B1–B16

Las evidencias por criterio están en `ci-run-{{RUN_ID}}/<celda>/evidence.json`, extraídas de los registros completos `ci-run-{{RUN_ID}}/<celda>/job.log`. Los secretos aparecen enmascarados (`***`) por GitHub Actions.

| ID | Prueba(s) | Evidencia (las 4 celdas) | Estado |
|---|---|---|---|
{{CRITERIA_ROWS}}

## 3. B12-a: conformidad de las pistas de bloqueo (decisión con T-SQL real)

Candidatos (nunca se combinan `ROWLOCK` y `READCOMMITTEDLOCK`; prueba estática `test_rowlock_never_combined_with_readcommittedlock`):

- **A** = `UPDLOCK, READPAST, ROWLOCK` (instalado en producción).
- **B** = `UPDLOCK, READPAST, READCOMMITTEDLOCK`.
- **C** = `UPDLOCK, READPAST`.

Para cada candidato y celda se midió:
1. Compilación del procedimiento en un esquema aislado.
2. Salto sin espera de una fila reclamada por un competidor real (el mismo procedimiento candidato en una transacción abierta) con `LOCK_TIMEOUT 3000`.
3. Exclusividad 8 × 100 con 3 repeticiones, con tablas vacías en cada una.

{{HINT_MATRIX}}

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

## 9. SHA-256 de los artefactos SQL del candidato

{{SQL_HASHES}}
