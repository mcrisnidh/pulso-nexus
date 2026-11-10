# Embarques (ASBG / CFDI) — regla comercial, controles y acreditación /2 (FIX5)

**Estado:** candidato de ingeniería offline. **IMPLEMENTACIÓN OFFLINE COMPLETA, VALIDACIÓN REAL PENDIENTE, PUBLICACIÓN CORPORATIVA NO AUTORIZADA.** Nada se activa solo.

## 1. Regla comercial (resuelta el 9-nov-2026; no se reabre)

En Associated Spring México, **la factura CFDI emitida ES el embarque**: cuando se emite la factura, el material está listo para cargarse.

- **Fuente autorizada:** `MF_CFD` / `MF_DETCFD` (ASBG), consulta del conector FIX4 (`conectores/embarques.py`, sin cambios en FIX5).
- **Fecha del evento:** `MF_CFD.FECEXP`.
- **No se exige** evidencia de salida física, transporte ni recepción.
- La acreditación `/1` exigía `fecha_es_embarque_fisico`, un evento logístico que la empresa no necesita demostrar. Se **sustituye** por `evento_comercial_cfdi_validado` en la acreditación `/2`. La `/1` se sigue verificando con su lógica original, sin relajarla, para no romper instalaciones existentes.

## 2. Qué controla la consulta y qué NO (alcance exacto)

| Control | Implementación | Estado |
|---|---|---|
| Periodo | `FECEXP >= día 1 del mes` y `< fecha de corte + 1`. Al generar se exige además que la captura empiece el día 1 del mes de la Junta | **Controlado** |
| ITEM exacto | `LTRIM(RTRIM(CODUPC))`, comparado **exacto** con el ITEM ERP, sin mayúsculas forzadas, sin quitar símbolos ni agrupar. Un ITEM con espacios exteriores o vacío invalida la captura | **Controlado** |
| Exclusiones | Se excluyen los `CODUPC` vacíos y los prefijos `RM`, `SCRAP` y `SERV16`, sin distinguir mayúsculas (FIX4) | **Controlado** |
| Nulos o desconocidos | La cantidad nula **no** se convierte en 0: se cuenta en `nulos` y, si un ITEM tiene **algún** renglón nulo, su cantidad queda vacía (desconocida), sin suma parcial. Desconocido + `ausencia_es_cero` sigue siendo **desconocido** | **Controlado** |
| ITEM sin factura en el periodo | Decisión comercial **explícita, aprobada y referenciada** en la acreditación (`ausencia_es_cero` + `decision_ausencia.referencia`), sin valor por omisión. Sin ella no se emite la `/2` | **Controlado** |
| Procedencia de la captura (FIX5.1) | La captura de referencia debe tener la procedencia que el preflight o el Refresh registran **al consultar** (§3.1). Un CSV puesto a mano se rechaza | **Controlado** (integridad local, no firma) |
| ITEM duplicado en la captura | Error `EMBARQUES_ITEM_DUPLICADO`, sin captura | **Controlado** |
| `NUMFOL` duplicado, vacío o universos discordantes | Errores `EMBARQUES_FACTURAS_DUPLICADAS`, `EMBARQUES_NUMFOL_INVALIDO` y `EMBARQUES_UNIVERSOS_INCONSISTENTES` | **Controlado** |
| Relaciones del comprobante | Cada `NUMFOL` debe existir en `MF_CUENTAS` y `MF_RECCFD` (lotes de 300). Si no, `EMBARQUES_RELACIONES_FALTANTES` | **Controlado** |
| Cancelaciones | La consulta **no lee** el estado de cancelación del CFDI | **NO resuelto**: una factura cancelada sigue sumando |
| Notas de crédito y devoluciones | La consulta **no lee** documentos de egreso ni tipo de comprobante | **NO resuelto**: no se descuentan |
| Refacturación (cancelar y volver a emitir) | Sin estado de cancelación no se puede distinguir | **NO resuelto**: puede contar dos veces |

**Lo que NEXUS declara:** Embarques = facturación CFDI emitida en el periodo. No se afirma que estén resueltas las cancelaciones, las notas de crédito, las devoluciones ni la refacturación. El texto de alcance (`alcance_ajustes`) queda en la acreditación y se muestra como aviso en **cada** generación acreditada. Resolverlo requiere una consulta que lea el estado de cancelación y los comprobantes de egreso. Eso sería un cambio del conector con una nueva huella y una nueva acreditación, fuera de FIX5.

### 2.1 Clasificación de cada ITEM del universo (FIX5.1)

En cada generación y escenario, `embarques.clasificacion` cuenta los ITEM de cada clase. Ningún desconocido se convierte en 0.

| Clase | Situación | Valor |
|---|---|---|
| `FACTURADO` | ITEM **exacto** en una consulta completa y exitosa, con cantidad conocida | La cantidad |
| `CANTIDAD_NULA` | ITEM exacto con algún renglón de cantidad nula | **Desconocido** (aunque la ausencia valga 0) |
| `IDENTIDAD_NO_EXACTA` | El ITEM no aparece exacto, pero sí una variante (mayúsculas, espacios o símbolos) | **Desconocido**: no se le asigna la cantidad de la variante ni se presume 0 |
| `AUSENTE_CERO` / `AUSENTE_DESCONOCIDO` | Ausente de una consulta **completa y exitosa** | 0 solo con la decisión aprobada `ausencia_es_cero = true`. Si no, desconocido |
| `FUERA_DEL_UNIVERSO` | Facturado pero fuera del universo mensual (Frozen) | No entra al cálculo. Se conserva en la captura |
| `SIN_CONSULTA_ACREDITADA` | Consulta fallida o sin corte acreditado utilizable | **Desconocido** para todos |

El conector no produce capturas parciales: ante cualquier error no escribe nada (todo o nada). En contingencia se usa el último corte **completo** y se marca «EMBARQUES ANTERIOR».

## 3. Acreditación /2 (verificable; no se fabrica)

Archivo `junta-data-2026-11\acreditacion_embarques.json`. Lo emite **solo** `acreditar_embarques.py` (o el lanzador con `-AcreditarEmbarques`), a partir de una captura con **procedencia corroborable**:

```json
{"schema": "nexus.preview.acreditacion-embarques/2", "acreditado": true,
 "regla": "CFDI_EMITIDA_ES_EMBARQUE", "fecha_evento": "FECEXP",
 "evento_comercial_cfdi_validado": true, "identidad_item_exacta": true,
 "ausencia_es_cero": "<true|false según la decisión aprobada>",
 "decision_ausencia": {"ausencia_es_cero": "<igual>", "referencia": "<minuta/correo/folio>",
                       "aprobado_por": "<aprobador>"},
 "alcance_ajustes": "Cancelaciones, notas de crédito, devoluciones y refacturaciones NO se descuentan…",
 "aprobado_por": "<aprobador de la lista privada>",
 "captura_referencia": "preflight/embarques/<instante>.csv", "captura_sha256": "<SHA-256>",
 "procedencia": "preflight/embarques/<instante>.json", "procedencia_sha256": "<SHA-256>",
 "instante_referencia_utc": "…", "periodo_referencia": ["AAAA-MM-01", "AAAA-MM-DD"],
 "items_referencia": 0, "facturas_referencia": 0, "huella_config_embarques": "<SHA-256>",
 "acreditado_utc": "…", "activa_conector": false}
```

**Lo que NEXUS verifica al emitir y en cada Refresh.** Ante cualquier fallo, Embarques, Programa y Faltante quedan en **N/D** con el motivo visible.

1. **Esquema y regla:** `/2`, `CFDI_EMITIDA_ES_EMBARQUE` y `FECEXP`.
2. **Criterios verdaderos:** `evento_comercial_cfdi_validado` e `identidad_item_exacta`.
3. **Decisión sobre la ausencia:**
   - `ausencia_es_cero` es booleano;
   - `decision_ausencia` coincide con él, trae una referencia no vacía y el mismo aprobador.
4. **Aprobador:** figura en la lista privada (`preview.json` → `approvers`).
5. **Captura de referencia:** está dentro de `preflight\embarques` o `capturas\embarques`, sin rutas externas ni enlaces, y su SHA-256 coincide.
6. **Procedencia (§3.1):** la verificación se repite en cada Refresh. Además, el SHA-256 del archivo de procedencia no cambió desde la acreditación.
7. **Huella de configuración y consulta:** cubre controlador, servidor, base, credencial, cuenta, atributos ODBC y el **texto SQL** de FIX4. El timeout no forma parte de ella.
8. **Captura usada en cada generación:** su procedencia debe llevar la misma huella. Una captura sin ella, por ejemplo de FIX5 previo, deja Embarques en N/D.
9. **Identidad de la generación:** el SHA-256 de la acreditación entra en ella.

### 3.1 Procedencia de la captura (FIX5.1)

Se reutilizan los archivos que **ya** escriben el preflight (`preflight\embarques\<instante>.raw`, `.csv` y `.json`) y el almacén del Refresh (`capturas\embarques\<nombre>.raw` y `.json`). FIX5.1 solo añade a sus metadatos, **en el momento de la consulta y sin modificar `embarques.py`**, estos campos:
- `conector`;
- `tipo_conector` (`ODBC_SQLSERVER_ASBG_CFDI`);
- `resultado: OK` (solo se escribe si `embarques.capturar` terminó sin error);
- `huella_config_embarques`;
- `sha256_sql`, el contrato SQL;
- `contrato_columnas`.

**Comprobaciones:**
- conector, tipo, resultado, contrato SQL, contrato de columnas y huella iguales a los actuales;
- el SHA-256 registrado es igual al del archivo, y en el preflight el `.csv` es igual al `.raw` de la misma ejecución;
- periodo: empieza el día 1, está dentro de un mismo mes y no es posterior al instante de ejecución;
- el número de ITEM registrado es igual al del archivo, y las facturas verificadas son coherentes con los documentos por ITEM (`max ≤ facturas ≤ suma`);
- se rechaza la captura sin `.json` de procedencia, con procedencia de otro conector o de otra configuración, o con archivos sustituidos.

**Alcance honesto:** es un control de **integridad y coherencia local**, no una firma criptográfica.
- **Detecta:** CSV fabricados o sustituidos, procedencias ajenas o incoherentes, y alteraciones posteriores a la acreditación.
- **No protege** frente a quien pueda reescribir a la vez el CSV, su procedencia y la acreditación dentro de la carpeta de datos. Esa protección exigiría una firma con una clave fuera de su alcance, y no existe en este laboratorio.

**Captura del 9-nov** (`preflight\embarques\20261109T224456Z.csv`, SHA-256 `43AA7BBA614A7C4F118F316D61EA145AA64A33F7C51B2256CB0575A8C63FCCDD`: 55 ITEM, 93 facturas, periodo 1–9 nov 2026):
- se **conserva intacta** como evidencia histórica y su certificación técnica sigue válida;
- la produjo el preflight FIX4, **antes** de que existieran los campos de procedencia FIX5.1. Por eso **no puede acreditarse retrospectivamente** con el nuevo contrato, y no se fabrican metadatos para ella;
- la acreditación `/2` se emite sobre **una sola captura nueva**, hecha durante la validación real autorizada (`PROCEDIMIENTO_FIX5_PS51.md` §6).

## 4. Procedimiento en ASMXC1200 (Windows PowerShell 5.1)

El procedimiento completo, ejecutable y con control de errores, está en `PROCEDIMIENTO_FIX5_PS51.md` (paquete FIX5.1). En resumen:
1. Verificar la captura del 9-nov por SHA-256 completo, solo lectura y sin consultar.
2. Hacer **una** captura nueva autorizada con el preflight FIX5.1.
3. Emitir la `/2` sobre ella con la decisión comercial aprobada:
   ```
   -AcreditarEmbarques <captura nueva> -Aprobador <aprobador> -AusenciaEsCero <si|no aprobado> -ReferenciaDecision <folio>
   ```
   Sin decisión aprobada no se emite nada.
4. Activar **solo** `embarques.activo = true`, con autorización aparte.
5. Pulsar «Actualizar datos» una vez y comprobar en la generación vigente que SSRS, FG y Embarques aparecen juntos.

## 5. Fallos (nunca se presenta un corte viejo como nuevo)

| Situación | Resultado |
|---|---|
| El conector falla (red, credencial, ODBC, timeout, duplicados) con `CONSERVAR_ULTIMO_CORTE` | «Actualización parcial». La Junta muestra «**EMBARQUES ANTERIOR** — el conector de Embarques falló; se usa el último corte válido del … (antigüedad …)». SSRS y FG siguen directos |
| Falla con `DETENER` | Error. Se **conserva** la generación vigente |
| La captura del almacén está alterada | Error `CAPTURA_ALTERADA`. Se conserva la generación vigente |
| La captura no cubre el mes desde el día 1 | N/D con motivo |
| Cambia la huella (servidor, consulta, etc.) | `NO_ACREDITADO`: N/D, no `FALLO` |
| Desconocido | Sigue desconocido. Nunca 0 |

## 6. Indicadores

- Programa, Sobreembarque y Faltante se calculan con el módulo existente `status_v310.py`, contrato v3.10, por ITEM:
  - `max(0, F + Adelanto − Emb)`;
  - `max(0, Emb − F − Adelanto)`;
  - `max(0, Programa − FG)`.
- El CORE (`coverage.py`) atribuye embarques **por movimiento** (`movement_id`, SHIP/REVERSAL). La captura CFDI está **agregada por ITEM**, así que esa atribución por línea no aplica y se informa como tal. No se construye un motor paralelo.

## 7. Historial (FIX4 y anteriores)

La consulta heredada de PULSO Web (`asbg_item_adapter.py`) normalizaba `CODUPC` con `item_key` y convertía nulos en 0. NEXUS descartó ambas cosas (X15) y lo mantiene. La `/1` (v0.3.1) se conserva por compatibilidad: exigía evidencia y conciliación de embarques físicos.
