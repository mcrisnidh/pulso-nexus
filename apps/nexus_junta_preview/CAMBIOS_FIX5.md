# FIX5 — Embarques (ASBG/CFDI) en «Actualizar datos» junto con Backlog SSRS y FG

**Base:** FIX4 `6a088bd`.
**Estado:** IMPLEMENTACIÓN OFFLINE COMPLETA, VALIDACIÓN REAL PENDIENTE, PUBLICACIÓN CORPORATIVA NO AUTORIZADA.

Un clic en «Actualizar datos» hace todo esto:
1. Consulta Backlog Current (SSRS), FG (ODBC) y Embarques (CFDI), cada uno con un corte nuevo.
2. Construye **una** generación atómica con las tres procedencias.
3. Calcula Programa, Sobreembarque y Faltante con el módulo v3.10 existente (`status_v310.py`).

Lo que no toca:
- Frozen, notas y adelantos;
- `embarques.py` FIX4, que conserva su SHA-256 `244ee198…59e7dd9`;
- el CORE, que no cambia.

## Diagnóstico (contra el código FIX4)

| # | Hallazgo en FIX4 | Corrección FIX5 |
|---|---|---|
| H1 | Embarques ya se capturaba en `capturar_todo` cuando estaba activo | Se conserva |
| H2 | El bloqueo del KPI era la acreditación `/1`, que exige evidencia física y `fecha_es_embarque_fisico`. Los textos N/D decían «CFDI no acredita salida física» | Nueva acreditación `/2` (`acreditacion_embarques.py`) con la regla resuelta. Textos corregidos. La `/1` queda intacta por compatibilidad |
| H3 | Las capturas de Embarques se **reutilizaban** (`reutilizar=True`): una consulta nueva con los mismos datos conservaba el instante viejo | `reutilizar=False`: cada consulta es un corte nuevo |
| H4 | `embarques_generacion` usaba la **última captura del mes**, aunque el conector hubiera fallado en este Refresh | Con CAPTURADO usa la captura de **este** Refresh (`CONSULTA_DIRECTA`). Con FALLO usa la última válida marcada `CONTINGENCIA`, con un aviso «EMBARQUES ANTERIOR» y su antigüedad |
| H5 | El CORE solo admite embarques por movimiento. La captura CFDI va agregada por ITEM | La atribución por línea del CORE sigue sin aplicar, con una razón técnica exacta. Los indicadores usan `status_v310` (ya en `webapp.enrich`). No hay motor paralelo |
| H6 | La Junta mostraba «Faltante N/D» fijo | Tarjetas Embarques (CFDI), Programa y Faltante con suma de conocidos y conteo de desconocidos (desconocido ≠ 0) |

## Registro de decisiones

| ID | Decisión | Motivo |
|---|---|---|
| F5-D1 | Regla `CFDI_EMITIDA_ES_EMBARQUE` con fecha `FECEXP`. `evento_comercial_cfdi_validado` sustituye a `fecha_es_embarque_fisico` | Decisión comercial del 9-nov-2026. No se reabre |
| F5-D2 | Migración compatible: la `/2` es un esquema nuevo y la `/1` se verifica con su lógica original, sin relajarla | No romper instalaciones existentes ni rebajar garantías de la `/1` |
| F5-D3 | `embarques.py` no cambia | Conserva el SHA-256 certificado en FIX4. La regla vive fuera del conector |
| F5-D4 | Alcance declarado: cancelaciones, notas de crédito, devoluciones y refacturación **no se descuentan** | La consulta no lee estado de cancelación ni egresos. No se afirma lo que no se verifica |
| F5-D5 | `ausencia_es_cero` es obligatorio y explícito en la emisión | Un ITEM sin factura no se convierte en 0 por omisión |
| F5-D6 | La huella de la `/2` cubre la conexión, la cuenta y el **texto SQL**. El timeout queda fuera | Un cambio de fuente o de consulta invalida. Ajustar el timeout no |
| F5-D7 | Captura de referencia solo de `preflight\embarques` o `capturas\embarques` | Evita acreditaciones sobre archivos fabricados o externos |
| F5-D8 | `intento_utc` no se guarda en la generación | La misma entrada debe dar la misma identidad (`gen_id`). La antigüedad se calcula al construir |
| F5-D9 | Nuevo `-AcreditarEmbarques` en `NexusJunta.ps1` | Emite la `/2` con el mismo entorno instalado que `-Conciliar`. No consulta ni activa nada |

### Conflictos heredados de FIX4

| ID | Conflicto | Resolución |
|---|---|---|
| F5-C1 | FIX4 no supera la puerta Ruff: deja +29 hallazgos en `test_conectores.py` y `simuladores.py` (S101×22, PLC0415×3, E501×4) y modifica 3 archivos con deuda de formato sin revisión | Corrección **mecánica**: `assert X` → `_exige(X)` con la misma condición; `import datetime` al nivel del módulo; líneas partidas. Ninguna aserción eliminada ni debilitada. Revisión declarada en la línea base: deuda 924 → 914 |
| F5-C2 | FIX4 modificó `embarques.py`, `simuladores.py` y `test_conectores.py` **sin** actualizar `SHA256SUMS` | Manifiesto regenerado. Consecuencia: el lanzador decide reinstalar según la huella de `SHA256SUMS`, así que FIX4 **no** se reinstalaba solo con `NexusJunta.ps1`. Debe comprobarse en el equipo qué `embarques.py` está instalado (sección de instalación) |
| F5-C3 | `embarques.NATURALEZA` dice «no acredita embarque físico» | Se mantiene en el conector (F5-D3). El estado del Refresh y la generación usan la naturaleza de la `/2` cuando está vigente |

## Archivos

- **Nuevos:**
  - `app/nexus_preview/conectores/acreditacion_embarques.py`
  - `app/acreditar_embarques.py`
  - `app/tests/test_fix5_embarques.py` (54 pruebas tras FIX5.1)
  - `CAMBIOS_FIX5.md`
- **Modificados:**
  - `conectores/orquestador.py`, `generaciones.py`, `servicio.py`, `webapp.py`, `templates/junta.html`
  - `app/preflight.py` (FIX5.1: procedencia al consultar)
  - `NexusJunta.ps1`, `LEEME.md`, `DOCS/EMBARQUES_CFDI.md`, `DOCS/CONECTORES.md`, `SHA256SUMS`
  - Heredados (F5-C1): `tests/test_conectores.py` y `tests/simuladores.py`
- **Raíz:** `quality/ruff_baseline_nexus_junta_preview.json`, con la revisión declarada.

## FIX5.1 — corrección acotada a cuatro hallazgos de la revisión independiente

| # | Defecto | Corrección | Criterio de cierre | Evidencia |
|---|---|---|---|---|
| D1 | Procedimiento no compatible con la política efectiva de PowerShell 5.1: se ejecutaba `.ps1` directamente | Toda invocación pasa por un proceso hijo explícito: `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File`. El ámbito es solo ese proceso y nunca se usa `Set-ExecutionPolicy`. Antes se comprueba `MachinePolicy`/`UserPolicy` con `Get-ExecutionPolicy -Scope`: si alguna está definida por GPO, **se detiene** sin eludirla. `$LASTEXITCODE` se comprueba tras cada proceso nativo, `throw` detiene la ejecución y cada mensaje OK depende de las comprobaciones previas | El analizador de PowerShell no encuentra errores en ningún bloque. No quedan patrones prohibidos (`.\*.ps1` directo, `Set-ExecutionPolicy`, `StartsWith`, `-AusenciaEsCero si/no` literal). La prueba aislada del mecanismo da: código 0 → sigue; código ≠ 0 → `throw`; `throw` interno → código 1 → `throw`; argumentos con espacios, intactos | `evidencia/sintaxis_procedimiento.txt`, `evidencia/prueba_invocacion.txt` |
| D2 | El SHA-256 de la captura del 9-nov se comparaba por prefijo | Comparación **exacta** con `43AA7BBA614A7C4F118F316D61EA145AA64A33F7C51B2256CB0575A8C63FCCDD`, solo lectura y sin consultar | El procedimiento no contiene comparaciones por prefijo | procedimiento §6a |
| D3 | El ejemplo presuponía `-AusenciaEsCero si` | `-AusenciaEsCero` y `-ReferenciaDecision` son obligatorios, sin valores por omisión, y quedan registrados en `decision_ausencia`. El procedimiento se detiene si las variables de la decisión están vacías. Una función pura clasifica los 5 casos (`asignar`), y la identidad no exacta ya no puede volverse 0 | `f02`, `a03`, `a02` (`sin_referencia_decision`, `decision_incoherente`, `ausencia_no_booleana`), `c01`–`c03` |  `evidencia/pruebas_fix5_1.txt` |
| D4 | Ubicación + columnas + SHA-256 no demostraban la procedencia | Procedencia registrada **al consultar** (preflight y Refresh, sin tocar `embarques.py`) y verificada al emitir, en cada Refresh y en la captura usada por la generación (§3.1 de `DOCS/EMBARQUES_CFDI.md`) | Rechazo de la captura fabricada, sin procedencia, en formato legado FIX4, con hash alterado, con periodo inconsistente, sustituida, con `.raw` distinto, de otra configuración o de otro conector, o con conteos incoherentes. También se rechazan la procedencia sustituida tras acreditar y la generación con una captura sin procedencia | `f01` (10 casos), `f02`–`f04`, `a02` |

**Mutaciones:** cada control nuevo, desactivado por separado, hace fallar al menos una prueba (`evidencia/mutaciones_fix5_1.txt`). Cuatro controles de FIX5 quedaban cubiertos solo por casos que rompían varias condiciones a la vez; se añadieron casos aislados (`aprobador_retirado`, `huella_del_documento`, `ausencia_no_booleana`, `raw_alterado_tras_acreditar`).

**Limitación declarada (D4):** la captura del 9-nov conserva su valor como evidencia técnica, pero **no** puede acreditarse con el nuevo contrato, porque es anterior a la procedencia FIX5.1. No se le fabrican metadatos. La `/2` requiere **una** captura nueva durante la validación real autorizada.
