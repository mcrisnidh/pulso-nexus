# CORE v1.0 — Noviembre 2026: implementación offline, excepciones y GO/NO-GO

- **Rama:** `feature/core-v1`. Base verificada: `119ffae`. El commit es el que contiene este informe.
- Sin PR, sin despliegue, sin GitHub Actions (`[skip ci]`).
- **Solo datos sintéticos.** Ningún archivo, fila, captura ni log real.
- Sin operaciones sobre SQL Server, ASMXC1012, PULSO Web, MAGNUM, AMFT ni el canal productivo.

## Estado

**GO para ejecutar `probe` en ASMXC1200.**
- Ejecutar `stage` y el replay de noviembre queda **condicionado** a validar el informe de `probe` (R2) y a tener las reglas privadas (R3).
- **NO-GO operativo** sin cambio.

## 1. Qué cambió

| # | Cambio | Archivos |
|---|---|---|
| Δ6 | CRLF/TZ-18: `rules.json`, `users.json` y el extracto inválido del escenario sintético se escriben en **bytes LF** | `core/demo.py` |
| Δ1 | Lectores físicos **puros**: OE965 (ancho fijo jerárquico, CP850, signo final), Portal (UTF-16 con BOM, TAB, preámbulo, homologación exacta) e IN092U (stockroom, corte local convertido a UTC con zona declarada) | `core/physical/` (nuevo), `core/layouts/physical_v1.json` (nuevo, PROVISIONAL_PENDING_PROBE) |
| Δ2 | `nexus core probe` (estructura saneada, solo lectura) y `nexus core stage` (directorio privado; originales por SHA; CSV del contrato; sidecar con tiempos separados) | `core/stage.py` (nuevo), `cli.py` |
| Δ3 | Instante con **precisión de fecha** para 29 OERAS y Portal: mes y orden por fecha local. Dos contenidos distintos con la misma fecha y sin Frozen: no se registra ni se publica. Un Frozen con precisión de fecha queda **PROVISIONAL**, con la evidencia del primer archivo y su procedencia | `core/sources.py`, `core/refresh.py`, `core/timebase.py` |
| Δ4 | Publicación **informativa parcial solo en `replay`**. Sin embarques del mismo corte: Embarcado, Pendiente, Cubierto y Sin cubrir en `BLOCKED_SOURCE`, sin valor. Sin Portal (R1): demanda CAT desconocida | `core/coverage.py` (rama `None`), `core/refresh.py` |
| C2 | Almacén **físico propio** para `replay` (`DATA/replay/store`, `DATA/replay/cs`). `CoreState` rechaza claves de otros canales. `publish` rechaza generaciones parciales o informativas fuera de `replay` | `core/state.py`, `core/refresh.py`, `web/__init__.py` |
| Δ5/C3 | Totales sin datos: `known_sum = null`, nunca 0. Una cifra total incompleta se muestra «—» con «No certificado», más la suma de los ITEM con dato rotulada como tal. Muestra «Embarques: no disponibles» y avisa de la vista informativa | `core/coverage.py`, `web/templates/*`, `web/present.py`, `web/static/style.css` |
| Higiene | `.gitignore` con los nombres de las fuentes reales y una prueba que impide versionarlas. Dos contratos import-linter nuevos (14 en total) | `.gitignore`, `pyproject.toml`, `tests/architecture/test_no_real_sources.py` |

**Sin cambios:**
- fórmulas de la rama con embarques;
- reglas comerciales, sustitución CAT y preservación TUPY;
- Customer Service (salvo su directorio en `replay`);
- calendario B0-7, SQL y autenticación;
- puertas del canal operativo;
- dependencias y `uv.lock`;
- **ninguna prueba existente** (`test-results/existing_tests_diff.txt` está vacío);
- **huella dorada `400e36ce…`** (constante no modificada).

## 2. Resultados

| Suite | Resultado |
|---|---|
| `pytest -m "not sql"` | **554 passed, 12 skipped, 0 failed**: las 455 previas sin modificar + 99 nuevas. Los 12 skipped son Pilot-20 con datos reales, solo locales |
| Zona horaria `test_core_timezone.py` | **38/38** |
| Portabilidad Δ6 | 4/4. Huella dorada con escritura CRLF emulada estilo Windows. **Sin la corrección fallan 3 de 4** (verificado) |
| Lectores físicos | 47/47 |
| `probe` y `stage` | 18/18 |
| Noviembre (Δ3, Δ4, C1–C4) | 24/24 |
| Web replay | 4/4 |
| Higiene | 2/2 |
| Demostración de dos meses | **11/11** (`test-results/demo_two_months.txt`) |
| Navegador | **33/33** (`test-results/browser_33.txt`) |
| `ruff`, `ruff format`, `mypy --strict`, import-linter | Sin errores; **14 contratos** respetados (`test-results/static_checks.txt`) |
| `tests/sql` (115) | Deseleccionadas: requieren el entorno SQL efímero de E1-B. Sin cambio respecto de la base |

Las pantallas `screenshots/replay_*_sintetico.png` usan fixtures **sintéticos** con el formato físico real.

### Dependencias de plataforma

- **Linux** (este entorno): todo lo anterior.
- **Windows (ASMXC1200)**:
  - `zoneinfo` necesita `tzdata`. Se carga como capa efímera `uv run --with tzdata==2025.2` (verificado: carga `America/Mexico_City` y no modifica `uv.lock`).
  - Las pruebas `posix_only` de E1-A se omiten por diseño.
  - La portabilidad CRLF se demostró por **emulación**; la ejecución nativa en ASMXC1200 queda **pendiente de observar**.

## 3. Informe de excepciones

| # | Excepción | Efecto | Estado |
|---|---|---|---|
| R1 | Sin tabla de homologación ITEM Portal → ERP con evidencia exacta | El Portal queda en cuarentena. La demanda CAT es **desconocida** y el resto se ve completo | **Abierto.** Comercial CAT + TI-ERP |
| R2 | Estructura física de noviembre no observada: encabezados y paginación de OE965, preámbulo y encabezados del Portal, forma de las filas IN092U | El layout `physical_v1.json` es **PROVISIONAL_PENDING_PROBE**. Si no coincide, el archivo queda en cuarentena (fallo cerrado); nunca se lee a medias | **Abierto.** `probe` en ASMXC1200 |
| R3 | Reglas de clientes de noviembre: claves exactas CAT, TUPY y revisión; APTIV ≠ DELPHI | Sin `rules.json` el Refresh no arranca | **Abierto.** Dueño del proceso comercial |
| E1 | Los encabezados del Portal se toman de alias genéricos (`Line ID`, `Part Number`, `Ordered`…) | Si no coinciden: `HEADER_NOT_FOUND`/`HEADER_INCOMPLETE` y cuarentena | Se resuelve con R2 (`--show-headers`) |
| E2 | La fecha del documento Portal proviene del **nombre** (`Portal_CAT_2026-11-03`), con la advertencia `DOCUMENT_DATE_FROM_FILE_NAME`. La marca `_0955` no se usa | Frozen Portal PROVISIONAL, con precisión de fecha | Declarado. Si el preámbulo trae una fecha confiable, se ajusta con R2 |
| E3 | OE965 con año de 2 dígitos (`MM/DD/YY`): se interpreta como 20YY. Un día de 1 dígito o un mes > 12 se rechazan por ambiguos | Archivo inválido si hay ambigüedad | Declarado |
| E4 | Clave de cliente OE965 `[109:122]`: se eliminan solo los espacios **finales** del ancho fijo; los internos son significativos | La clave debe coincidir exactamente con `rules.json` | Declarado. Verificar con `--keys-out` |
| E5 | Las líneas Portal sin homologar con estado excluido (`Cancelled`, …) se descartan; las activas sin homologar ponen el Portal en cuarentena | Ninguna línea activa se pierde en silencio | Declarado |
| E6 | Un Frozen con precisión de fecha es **PROVISIONAL**: la precedencia no es certificable sin hora | Un archivo distinto del mismo día genera la alerta `FROZEN_PRECEDENCE_AMBIGUOUS` y no reemplaza al Frozen | Cumple C1 |
| E7 | La zona `America/Mexico_City` es una **hipótesis de laboratorio** (B0-7) | El corte IN092U se convierte con esa zona y la zona queda ligada al canal replay | Declarado |
| E8 | No hay embarques físicos | Embarcado, Pendiente, Cubierto y Sin cubrir quedan BLOCKED_SOURCE y «No certificado» | Declarado (Δ4) |
| E9 | FG de OE965 (cabecera de ITEM) | Se cuenta como traza y no se usa como FG (el FG viene de IN092U) | Declarado |
| — | Fuera de alcance: STKBAL/xlsx, MexicoDC, WO, CFDI y facturación | `stage` los ignora sin leerlos | Sin cambio |

## 4. GO/NO-GO

**GO limitado (demostración informativa aislada en ASMXC1200).** Requiere:

| Requisito | Estado |
|---|---|
| Código, pruebas y evidencia offline | ✅ |
| Puerta operativa intacta (nv02) | ✅ |
| Rollback local (nv43 y §2.6 de `docs/core/NOV2026_REPLAY.md`) | ✅ |
| R2: `probe` válido en los tres archivos reales | ⏳ |
| R3: `rules.json` privado | ⏳ |
| Suites en verde en ASMXC1200 | ⏳ |
| R1 | Opcional para el GO limitado. Sin él, CAT se ve como desconocido |

**NO-GO operativo** sin cambio. Siguen faltando:
- embarque físico certificado;
- equivalencia 29 OERAS ↔ OE965;
- corte FG simultáneo;
- homologación sin excepciones críticas;
- persistencia y respaldo CS;
- autenticación corporativa;
- aislamiento del host.

## 5. Siguientes pasos

Todo se ejecuta en local, en ASMXC1200.

1. Ejecutar §2.1 y §2.2 de `docs/core/NOV2026_REPLAY.md`. Compartir solo los tres `probe_*.json` (saneados).
2. Si algún `probe` no es válido, ajustar el layout con el informe saneado: commit en `feature/core-v1` con la prueba sintética correspondiente.
3. Con R3 (y R1 si está disponible), ejecutar §2.3–§2.5: `stage` y luego `refresh --channel replay`.
4. Revisar en la web local la Junta y el Detalle de noviembre. Informar el resultado **sin capturas reales**.
