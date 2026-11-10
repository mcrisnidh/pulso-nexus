# FIX6 — SSRS automático, Frozen por mes y excepción ERP

## Política de negocio (cerrada 10-nov-2026)

- `R_*.TXT` fue **solo una referencia de desarrollo**, no una fuente operativa ni un
  control recurrente de equivalencia. Su uso corriente está **prohibido** en modo FIX6.
- En **noviembre de 2026**, `frozen_2026-11.json` conserva los Frozen ERP+Portal ya
  sellados. Ningún Refresh cambia sus bytes, reglas, universo o huellas.
- A partir de **diciembre de 2026**, el primer **SSRS mensual confirmado** sustituye al
  TXT para `frozen_r`, combinado con el primer Portal CAT Frozen válido. Se conserva la
  regla comercial CAT/TUPY y el universo resultante; los cortes SSRS posteriores sirven
  solo para `current_r` y nunca sustituyen Frozen.
- FG ODBC y CFDI ASBG siguen independientes, con sus acreditaciones / alcances y una
  consulta nueva por clic (sus contingencias se presentan como parciales).
- SSRS es el corte fuente **overnight**, no el estado instantáneo del ERP. Compararlo
  con un TXT manual **NO** es condición de operación.

## Confirmación del primer SSRS de mes (limitación de evidencia)

El XML validado `Month End - Backlog Detail` contiene periodo `MM/AAAA` y detalles,
pero el contrato observado **no acredita la hora en que terminó el lote nocturno**.
Por ello FIX6 exige **una única confirmación** del primer SSRS del mes antes de
sellar Frozen. No basta con la hora del reloj ni con el primer reporte que responda.
Mientras falta esa confirmación, captura SSRS solo lectura, mantiene vigente el
mes anterior y marca `ERROR / Frozen PENDIENTE`; no crea `frozen_2026-12.json`.

El operador elige la **primera captura cuya actualización nocturna esté realmente
confirmada**; intentos anteriores del mismo mes permanecen como evidencia pero no
entran en Frozen. El SHA por sí solo no identifica un instante si hubo capturas
idénticas: se exige además el identificador de captura (`archivo`).

La confirmación verifica: XML original, nombre del reporte, mes, contrato de líneas,
identidad de aprobador autorizado, SHA256 del primer archivo y referencia declarada
por el operador para la ejecución ETL overnight. Se escribe con `O_EXCL`, nunca se sustituye. Para automatizar la
confirmación sin intervención en el futuro, SSRS debe aportar una marca de ejecución
del ETL verificable o un manifiesto independiente de publicación.

Para inspeccionar la primera captura local sin consultarla otra vez:

```powershell
$D=Join-Path $env:LOCALAPPDATA 'PULSO_NEXUS_LAB\junta-data-2026-11'
Get-ChildItem (Join-Path $D 'capturas\backlog_ssrs') -Filter '*.json' |
  Sort-Object Name | ForEach-Object {
    $j=Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json
    if ($j.fecha_documento -like '2026-12*') {
      [pscustomobject]@{Archivo=$j.archivo; Mes=$j.meta.periodo; SHA256=$j.sha256; UTC=$j.instante_utc}
    }
  }
```

Confirmar **únicamente** si puede evidenciar que el lote nocturno quedó publicado:

```powershell
.\apps\nexus_junta_preview\NexusJunta.ps1 -ConfirmarSSRS '2026-12' `
 -ArchivoSSRS '<ID_CAPTURA_CONFIRMADA>' -ShaSSRS '<SHA256_CAPTURA>' -Aprobador 'as0\ucbaa' `
 -ReferenciaLote '<REFERENCIA_REAL_ETL_OVERNIGHT>'
```

Luego pulsar **Actualizar datos** una vez: el Frozen se sellará si también existe
el primer Portal CAT válido de diciembre y las otras entradas válidas. Sin Portal
Frozen no hay sellado, no se inventa.

## Activación aislada desde la instalación FIX6

La herramienta `preview.py activar-fix6 --data <data>` verifica que el Frozen de
noviembre y la generación vigente son íntegros, que los tres conectores están activos,
que existen las acreditaciones FG/CFDI y la lista privada de aprobadores. Respalda
`preview.json` y agrega `"ssrs_automatico": true`. No toca claves, Frozen ni SQL.
El modo queda activado una sola vez: `month` se deriva de la fecha **local de negocio**
en cada Refresh posterior. Ninguna edición manual mensual del JSON es necesaria.

## ERP urgente — excepcional, nunca predeterminado

```powershell
.\apps\nexus_junta_preview\NexusJunta.ps1 -RefreshERP 'C:\...\R_20261203.TXT' `
 -Aprobador 'as0\ucbaa' -MotivoERP 'cambio crítico autorizado en la orden...'
```

Solo acepta un OE965 auténtico del **mes vigente**, después de verificar el Frozen
y al aprobador privado. En esa **única generación** toma `Current` del TXT, registra
huella/identidad/motivo, pero **nunca** modifica Frozen ni la configuración habitual.
La siguiente pulsación ordinaria vuelve a SSRS y consulta nuevamente FG y CFDI.
Es un override de CURRENT, **no** una actualización forzada del servidor SSRS.

## Límites del laboratorio

- No hay publicación oficial; la Junta sigue `PROVISIONAL / NO-GO`.
- No se cambian `src/` CORE, acreditación FG, acreditación CFDI ni consultas SQL.
- El histórico, las fuentes originales y las capturas SSRS permanecen preservados.
- Una consulta exitosa no acredita que las tres fuentes tengan el mismo origen temporal.
