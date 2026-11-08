# Embarques — qué hace la consulta heredada (MF_CFD / MF_DETCFD)

Fuente analizada:
- `asbg_item_adapter.py` (`ASBG_SHIPMENTS_ITEM_SQL`) y `live_sources.py` (`capture_asbg`) de PULSO Web;
- auditoría Power Query de Status Ventas v3.10 (`Sales_Current_Month_By_Item` → `Frozen_With_Embarques`).

| Pregunta | Lo que hace la consulta heredada | Consecuencia en NEXUS v0.3 |
|---|---|---|
| ¿Qué evento representa cada registro? | Una partida (`MF_DETCFD`) de un CFDI emitido (`MF_CFD`), unidos por `NUMFOL` | **Facturación**, no salida física |
| ¿Qué fecha usa? | `MF_CFD.FECEXP` (expedición del comprobante) | No es la fecha de embarque; puede diferir de la salida física |
| ¿Qué cantidad? | `SUM(COALESCE(CANTID, 0))` | NEXUS no convierte nulos en 0: cuenta los nulos y deja la cantidad desconocida |
| ¿Cómo se relaciona con el ITEM ERP? | `UPPER(LTRIM(RTRIM(CODUPC)))`, y en Python una clave que quita símbolos (`item_key`) | Normalizar está descartado (X15). NEXUS usa `CODUPC` recortado y lo compara **exacto** con el ITEM ERP. La equivalencia `CODUPC` ↔ ITEM no está acreditada |
| ¿Cancelaciones? | **No se filtran** (no hay estatus de CFDI en la consulta) | Bloquea la acreditación |
| ¿Notas de crédito y devoluciones? | **No se distinguen** (no hay serie ni tipo de comprobante); suman con su signo | Bloquea la acreditación |
| ¿Duplicados? | Solo la agregación por ITEM; sin control de refacturación | NEXUS informa renglones y `COUNT(DISTINCT NUMFOL)` para auditar |
| ¿Periodo mensual? | `FECEXP >= día 1` y `< fecha de corte + 1` | Igual, con los dos límites como parámetros |
| ¿Movimientos anteriores o posteriores al Frozen? | No los distingue: acumula desde el día 1 hasta la fecha de corte | La relación con el Frozen del 1-nov requiere la definición comercial del libro |
| ¿Atribución a pedido o línea? | No existe: agregado por ITEM | Solo admite la fórmula v3.10 por ITEM (Programa/Faltante), no la atención por línea del motor |

## Requisitos para acreditar la fuente (v0.3.1: acreditación VERIFICABLE)

Archivo `junta-data-2026-11\acreditacion_embarques.json`, preparado y firmado por el negocio, **no por NEXUS**:

```json
{"schema": "nexus.preview.acreditacion-embarques/1", "acreditado": true,
 "evidencia_ruta": "acreditacion\\embarques_reconocidos.csv",
 "evidencia_sha256": "<SHA-256 de ese archivo>",
 "conciliacion_ruta": "conciliaciones\\embarques_<instante>_<id>\\informe_privado.json",
 "conciliacion_sha256": "<SHA-256 de ese informe>",
 "aprobado_por": "<aprobador listado en aprobadores.json>",
 "trata_cancelaciones": true, "trata_notas_credito": true,
 "fecha_es_embarque_fisico": true, "identidad_item_exacta": true,
 "ausencia_es_cero": false}
```

**Lo que NEXUS verifica en cada Refresh**, y sin lo cual no acredita:
1. **Evidencia:** el archivo existe **dentro** de la carpeta de datos (sin rutas externas ni enlaces) y su SHA-256 coincide.
2. **Conciliación:** el informe existe dentro de la carpeta de datos y su SHA-256 coincide. Además, es de `embarques` con resultado `PASS` y sin diferencias.
3. **Correspondencia:** el informe se hizo contra **esa** evidencia (`evidencia_sha256` del informe) y contra una captura **producida por el conector** (`capturas\embarques\*.raw` o `preflight\embarques\*.raw`).
4. **Declaraciones y aprobación:** las declaraciones del negocio son verdaderas, incluidas la naturaleza física y la identidad exacta del ITEM, y el aprobador está autorizado.

**Procedimiento:**
```powershell
.\NexusJunta.ps1 -Conciliar embarques -Directa "$env:LOCALAPPDATA\PULSO_NEXUS_LAB\junta-data-2026-11\preflight\embarques\<instante>.raw" -Evidencia "...\acreditacion\embarques_reconocidos.csv"
```
El negocio redacta el JSON con las rutas y huellas que imprime el comando.

**Comportamiento:**
- Si se alteran la evidencia o el informe, o se usan hashes inventados, rutas externas o una conciliación con diferencias, la acreditación se rechaza. Embarques, Programa y Faltante siguen **N/D**, con el motivo visible en «Fuentes y Refresh».
- La huella del archivo de acreditación forma parte de la identidad de la generación, de modo que cualquier cambio queda trazado.
- **Si se acredita:** Programa, Sobreembarque y Faltante usan la fórmula v3.10 con la cantidad por ITEM exacto. Un ITEM sin facturas sigue desconocido, salvo `ausencia_es_cero: true`.
