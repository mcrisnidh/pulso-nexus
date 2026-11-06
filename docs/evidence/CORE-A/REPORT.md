# CORE v1.0 — Hito A (CORE técnico): informe y estado GO/NO-GO

- **Rama:** `feature/core-v1`, desde `151154b`. Sin PR y sin GitHub Actions: el CI solo se dispara en `claude/**` y `main`, y los commits llevan `[skip ci]`.
- **Commits:**
  - código: `75a0827`;
  - ajustes, documentación y evidencia: el commit que contiene este informe.
- **Datos:** **solo sintéticos** (`source_system = SYNTHETIC`, ITEM `SYN-*`, usuarios de demostración). Sin fuentes corporativas, sin PULSO/MAGNUM, sin `PULSO_MAGNUM_STAGE` y sin despliegues.
- **Fecha de la evidencia:** 2026-11-06 (entorno Linux de la sesión).

## Estado: **GO CONDICIONADO**

El CORE técnico funciona localmente de punta a punta y cumple todas las puertas G1–G7-CS y los criterios A1–A12. La condición es que las **garantías transaccionales de SQL Server no están certificadas** (§4): no había SQL Server disponible y no se instaló infraestructura (condición 10). Su certificación pasa a ser la primera condición de entrada del Hito B.

## 1. Qué funciona (resumen verificable)

| Componente | Entregado | Evidencia |
|---|---|---|
| C1 Frozen mensual | Primer extracto válido por instante (29 OERAS y Portal). Inmutable aun con escritores concurrentes. El Current nunca lo cambia. Un extracto tardío anterior genera `FROZEN_PRECEDENCE_CONFLICT` sin reemplazo. Frozen alterado → fallo cerrado | `test_g2_*` (5) |
| C2 Current y FG | Current informativo (+/−/Δ frente al Frozen). FG usable solo en stockroom FG; QF/QH/RM/SA aparte. Desconocido ≠ 0; negativo `NOT_CERTIFIABLE` | `test_g2_first…`, `test_g5_fg…`, `test_g5_unknown…` |
| C3 Embarques físicos | Contrato propio: `movement_id` único, reversas, UOM, ventana [inicio de mes, corte]. La facturación nunca entra (AST + AT-16) | `test_g4_*`, `test_at16_*` |
| C4 Cobertura | Demanda (TUPY y revisión conservados; CAT sustituido por el Portal; horizonte vencida + mes; futuras consultables). Atendida por evidencia (línea → pedido → agregado). Pendiente, FG, cubierta, sin cubrir. 8 invariantes antes de publicar. KPI OFICIAL solo con reglas aprobadas y fuentes certificadas | `test_g5_*`, Hypothesis 150 casos, mutaciones |
| C5 Refresh y publicación | Worker E1-B (cola idempotente, lease, fencing) con ejecutor enchufable. Generación determinista. Publicación CAS con historial encadenado. Rollback. Canales `operativo`/`replay`/`sintetico` (el operativo rechaza datos sintéticos y otros meses) | `test_g6_*` (19) |
| Recuperación | Fallo inyectado en 8 etapas, sin publicación parcial; el reintento publica. Proceso **muerto con SIGKILL** a mitad de Refresh (2 puntos) y reinicio sin corrupción | `test_g6_failure_at_every_stage…` (8), `test_g6_process_killed…` |
| C6 Web | Flask solo en loopback (socket verificado en `/proc/net/tcp`; 0.0.0.0 rechazado). Sin debug, CSRF, roles, CSP estricta, sin JS. Lectura verificada con fallo cerrado por ITEM | `tests/web` (9) + navegador headless (8/8) |
| C7 Customer Service | Notas, Estatus CS, Fecha CS y Adelanto en almacén **propio**, solo anexar, con historial, autor e instante del servidor. Concurrencia optimista (hilos y procesos). Vigencia mensual con referencia "requiere reconfirmar". Sobrevive a Refresh, fallos, rollback y reinicio. No altera la cobertura | `test_cs*` (18) + web |

## 2. Resultados de pruebas (`test-results/`)

| Comprobación | Resultado |
|---|---|
| `ruff check` y `ruff format --check` | OK (`ruff.txt`) |
| `mypy --strict src` | OK, 60 archivos (`mypy.txt`) |
| `lint-imports` | **11 contratos KEPT**: 8 existentes + 3 nuevos de CORE (`lint-imports.txt`) |
| `pytest -m "not sql"` completo | **415 passed, 12 skipped, 0 failed** (`pytest_full.txt`, `junit.xml`). Los 12 skipped son las pruebas Pilot-20 de datos reales, que solo corren en local con las variables de entorno: no aplican al Hito A |
| CORE y web (`tests/core`, `tests/web`) | **70 passed** (`pytest_core_web.txt`) |
| Regresión E1-A/E1-B/Pilot-20 | Las 345 pruebas de la línea base siguen en verde |
| A13 de E1-A (ramas ≥ 90 % en sus paquetes) | **98 %** (`coverage_a13_e1a.txt`) |
| Cobertura de ramas CORE/web | 85–100 % por módulo, 89 % total del repositorio (`coverage_core_web.txt`) |

### Pruebas de aceptación AT-01…AT-20 (sintéticas)

| AT | Prueba |
|---|---|
| 01 Primer Refresh registra el Frozen | `test_g2_first_valid_extract_is_frozen_later_is_current` |
| 02 Refresh posterior no cambia el Frozen | idem; `test_g6_two_months_frozen_m1_intact` |
| 03 Recaptura del Frozen rechazada | `test_g2_frozen_cannot_be_overwritten_even_concurrently` |
| 04 Doble clic produce una ejecución | `test_g6_idempotent_double_click_single_run` |
| 05 Mismas fuentes producen la misma generación | `test_g6_same_sources_same_generation_after_restart` |
| 06 Fuente ausente: no se publica | `test_g6_missing_or_misaligned_sources_do_not_publish`, `test_g2_invalid_first_never_frozen` |
| 07 Columna, duplicado o corte desalineado | `test_g1_*`, `test_g6_missing_or_misaligned…` |
| 08 Worker o proceso caído | `test_g6_failure_at_every_stage…`, `test_g6_process_killed…` |
| 09 Artefacto alterado | `test_g6_tampered_published_artifact_detected`, `test_integrity_error_fails_closed_per_item` |
| 10 Rollback | `test_g6_rollback_and_chain`, `test_rollback_via_web_keeps_cs` |
| 11 Conservación | `test_g5_properties_…` (Hypothesis) |
| 12 Sin doble conteo | `test_g5_attribution_levels_…`, propiedades, mutaciones |
| 13 Embarque sin pedido o línea → agregado provisional | `test_g5_attribution_levels_…` |
| 14 TUPY y CAT en el mismo ITEM | `test_g5_tupy_preserved_cat_substituted_review_kept` |
| 15 Portal desconocido / `8000(0)` | `test_g5_unknown_is_never_zero` |
| 16 Facturación no sustituye embarques | `test_at16_…`, `test_g4_shipments_adapter_never_reads_billing` |
| 17 Permisos | `test_roles_default_deny`, `test_headers_auth_and_csrf` |
| 18 Sintético o replay en el canal operativo | `test_g6_operativo_channel_rejects_synthetic_and_other_month` |
| 19 Determinismo de bytes | `test_g6_determinism_same_bytes` |
| 20 Cabecera con instantes y fuentes | `test_refresh_board_item_and_history` |

## 3. Demostración de dos meses (`demo/`, `screenshots/`)

`bash scripts/core_a_demo.sh <dir>` ejecutado **tres veces seguidas sin fallos**. En cada ejecución, los 11 pasos verificados del driver (`demo.log`, `demo_log.json`) son:

1. Frozen del mes 1 y primer corte publicado.
2. El lector no ve el botón Refresh.
3. Dos editores CS sobre la misma versión: uno gana y el otro recibe 409.
4. Current, extracto inválido y extracto tardío anterior: alerta, Frozen intacto.
5. Nuevo corte: entregado 490, sin cubrir 70.
6. Refresh fallido: no publica.
7. Rollback atómico con notas intactas.
8. Reinicio de la aplicación: todo persiste.
9. Mes 2: Frozen nuevo; Frozen del mes 1 intacto.
10. Mes 2: Estatus CS reiniciado, con el valor de enero como referencia.
11. Cadena de publicaciones íntegra (6 publicaciones).

Después:
- servidor en `127.0.0.1` (`listen.txt`);
- `--host 0.0.0.0` rechazado (`reject.log`);
- **8/8 comprobaciones en Chromium headless** (`browser.log`): login, Refresh, nota CS, permisos, CSP sin violaciones, vista móvil a 390 px sin scroll horizontal.

La verificación en navegador detectó y permitió corregir un defecto real: un estilo inline bloqueado por la propia CSP. Ahora hay una prueba que lo impide.

## 4. Garantías NO certificadas (pendientes para el Hito B)

| Garantía | Situación en el Hito A | Qué falta |
|---|---|---|
| Unicidad del Frozen, CAS de publicación y versión CS sobre **SQL Server** | Implementadas sobre el ObjectStore de E1-A (enlace sin reemplazo, demostrado en Linux con hilos y procesos). **No hay implementación SQL** de estas tablas | Esquema y procedimientos SQL de CORE + batería RCSI ON/OFF/SNAPSHOT en SQL Server aislado de NEXUS |
| DENY UPDATE/DELETE (Frozen, historial, notas CS) por permisos SQL | Inmutabilidad por la API del almacén y archivos en solo lectura. Un administrador del sistema de archivos puede alterarlos; se **detecta** por hash al leer, pero **no se impide** | Permisos SQL EXECUTE-only y ACL reales |
| Exclusión de Refresh **entre procesos** | Lease E1-B en memoria por proceso. Entre procesos web distintos solo la publicación CAS evita la doble publicación; el cálculo puede repetirse (idempotente) | Lease SQL compartido |
| Durabilidad ante corte eléctrico; Windows/NTFS/SMB | Igual que E1-A: fsync a nivel de API; no demostrado ante corte eléctrico; Windows no ejecutado para CORE | Certificación en el almacenamiento real (P5) |
| Respaldo y restauración de notas CS (dato no reproducible) | No aplica en el Hito A | Política y ensayo (B0.9, B8) |
| Seguridad web corporativa | `DevAuth` sintético; HTTP en loopback | SSO/AD, TLS, WSGI de producción, revisión de TI |
| Formato físico real de 29 OERAS, Portal, FG y embarques | Contratos lógicos con CSV sintético (`PROVISIONAL_PENDING_REAL_SAMPLE`) | Ejemplares reales autorizados; extractor de embarques certificado |

## 5. Cambios en código existente (declarados)

1. `pipeline/worker.py`: parámetro opcional `executor`. Por defecto, el pipeline E1-A idéntico; todas las pruebas E1-B/E1-A siguen en verde.
2. `tests/architecture/test_boundaries.py::test_no_web_layer_in_e1a`: E1-A prohibía toda capa web. Se acota a lo autorizado: solo `nexus.web` puede importar Flask y ningún otro paquete importa un framework web. **Es la única prueba existente modificada.**
3. `pyproject.toml` / `uv.lock`: Flask 3.1.3 como extra `web` y en el grupo dev. Las dependencias de ejecución del núcleo siguen siendo `pyarrow` y `pyodbc` (prueba E1-A intacta). Contratos import-linter nuevos y CORE/web añadidos a la prohibición de `pyodbc`.
4. `cli.py`: subcomandos `core` y `web`, aditivos.

Sin cambios: migraciones SQL E1-B, `nexus.planning`, E1-A, datos congelados y evidencia E1-A/E1-B.

## 6. Reglas comerciales en el Hito A

Las reglas sintéticas `core-synthetic-1` son **PROVISIONAL_NO_CRC**:
- fecha del Portal `delivery_date` **pendiente de contrato comercial**;
- asignación `due_date_fifo` **no aprobada**, así que la cobertura por línea queda `BLOCKED_CRC`.

Por construcción **ninguna cifra es KPI OFICIAL** (prueba dedicada), y la web lo muestra con un banner. Adelanto y Fecha CS son seguimiento: no alteran el cálculo (CS-14).

## 7. Ejecución y rollback

Ver `docs/core/CORE_v1_HITO_A.md`. Ruta rápida: `uv sync --frozen && bash scripts/core_a_demo.sh /tmp/core_a_demo`.

Rollback:
- publicación: `nexus core rollback` o botón de administrador;
- código: descartar o revertir `feature/core-v1`; `main` y `151154b` están intactos;
- las notas CS nunca se revierten.

## 8. Fuera de alcance (respetado)

No se tocaron E1-C, WO, routing, capacidad, forecast, máquinas, programación, OE965 ni facturación como fuente CORE. Tampoco hubo despliegues ni conexiones corporativas.
