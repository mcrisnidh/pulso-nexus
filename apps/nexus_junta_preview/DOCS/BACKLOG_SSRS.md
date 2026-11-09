# Backlog SSRS (29 OERAS) — acreditación, diagnóstico y validación en ASMXC1200 (v0.3.3)

**Estado:** candidato de ingeniería offline. **NO-GO para activación operativa** hasta que:
1. la conciliación contra el R manual sea PASS sin diferencias;
2. exista `acreditacion_backlog_ssrs.json` verificable;
3. se complete esta validación real con autorización.

**Reglas que no cambian:**
- **Sin secretos:** ni contraseñas, ni cookies, ni tokens. SSRS usa la sesión Windows `ucbaa`.
- **Sin escrituras en sistemas corporativos.**
- **Frozen inmutable:** el SSRS directo alimenta **Current**.
- **V26:** la equivalencia comercial OE965 ↔ 29 OERAS sigue `NOT_ESTABLISHED`. La acreditación de la fuente no la certifica.

**Comprobación tras cada comando:** en Windows PowerShell 5.1, revise `$LASTEXITCODE`. 0 es PASS; cualquier otro valor es fallo o diferencias.

## 0. Actualización y rollback (preserva Frozen, notas, FG y generaciones)

Rama `feature/fg-v032-core` con el parche v0.3.3 aplicado y confirmado (procedimiento de aplicación en la entrega). Desde el repositorio:
```powershell
.\apps\nexus_junta_preview\NexusJunta.ps1 -Preflight backlog_ssrs
```

- **Actualización.** El lanzador reinstala la aplicación porque cambió el manifiesto. Guarda la anterior como `nexus_junta_v02.anterior_*` y conserva `junta-data-2026-11` intacto:
  - generaciones, Frozen bloqueado, notas y adelantos;
  - capturas y `conectores.json`, con FG activo;
  - la acreditación FG.
- **Rollback a v0.3.2:**
  ```powershell
  .\apps\nexus_junta_preview\NexusJunta.ps1 -Desinstalar
  ```
  Restaura la instalación anterior. v0.3.2 no acepta la acreditación de Backlog `/2` (fallo cerrado): si el conector estuviera activo, quedaría `NO_ACREDITADO` y la Junta usaría el R manual. FG sigue igual.

## 1. Preflight sin red

```powershell
.\apps\nexus_junta_preview\NexusJunta.ps1 -Preflight backlog_ssrs
```

Esperado: el informe trae `acreditacion_backlog.valida = false`, con el motivo «esquema /2» o «sin acreditación», y `activo = false`.

## 2. Consulta real de solo lectura (requiere autorización)

```powershell
.\apps\nexus_junta_preview\NexusJunta.ps1 -Preflight backlog_ssrs -Nivel consulta -Autorizo
```

Deja en `junta-data-2026-11\preflight\backlog_ssrs\` tres archivos:
- `<instante>.raw`: XML ORIGINAL del reporte; es el que se concilia y acredita;
- `<instante>.csv`: contrato `oeras_29`, solo informativo;
- `<instante>.json`: procedencia (instante y SHA-256).

## 3. Diagnóstico de las discrepancias (R2), SIN acreditar

Para separar cambios entre cortes de diferencias de fuente:
1. Obtenga el **R manual y la consulta SSRS lo más cerca posible en el tiempo** (misma mañana). El R manual solo tiene fecha.
2. Concilie sin `-Acreditar`:
   ```powershell
   .\apps\nexus_junta_preview\NexusJunta.ps1 -Conciliar backlog -Manual <ruta>\R_AAAAMMDD.TXT -Directa <junta-data-2026-11>\preflight\backlog_ssrs\<instante>.raw
   ```
3. Revise **en el equipo** `conciliaciones\backlog_*\informe_privado.json` → `diagnostico.lineas`, con la clase de cada línea:

| Clase | Evidencia | Qué comprobar (sin normalizar ni tolerar) |
|---|---|---|
| `CLIENTE_AUSENTE` | La clave de cliente no aparece en la otra fuente | Parámetros del reporte (¿qué filtra cada parámetro configurado?), maestro de clientes, `formato_clave_cliente` |
| `PEDIDO_AUSENTE` | El cliente existe, el pedido no | Cierre o cancelación entre cortes; filtro por tipo o estado de pedido |
| `LINEA_AUSENTE` | El pedido existe con otras líneas | Embarque o cierre de la línea entre cortes; filtro por estado de línea |
| `IDENTIDAD_DISTINTA` | El mismo pedido/línea aparece con otro ITEM o cliente | Contrato o identidad: **no se normaliza** |

Cada línea trae además `vencida_al_corte` y `sin_embarque_parcial`, que ayudan a separar un cierre normal de un filtro.

4. Comparta solo `resumen_saneado.json` (conteos por clase, sin identidades).

**Interpretación:**
- Las diferencias **desaparecen** con cortes cercanos: eran temporales. Continúe con el paso 4.
- **Persisten** (por ejemplo, todas `CLIENTE_AUSENTE` del mismo cliente): diferencia de **filtro o parámetro**. Revise la configuración del reporte con el dueño del proceso. Cambiar parámetros cambia la huella y exige una nueva conciliación.
- Si no se puede explicar con evidencia: **NO-GO**. Backlog sigue inactivo. No se declaran «embarcadas», «canceladas» ni «normales».

## 4. Acreditación (solo con PASS sin diferencias)

```powershell
.\apps\nexus_junta_preview\NexusJunta.ps1 -Conciliar backlog -Manual <R> -Directa <preflight\backlog_ssrs\<instante>.raw> -Acreditar
```

Se emite `acreditacion_backlog_ssrs.json`, con alcance `FUENTE_Y_CONTRATO` y `equivalencia_comercial NOT_ESTABLISHED`. El conector **no** se activa.

Con cualquier diferencia: código 1 y sin acreditación.

Después:
```powershell
.\apps\nexus_junta_preview\NexusJunta.ps1 -Preflight backlog_ssrs
```
Debe mostrar `acreditacion_backlog.valida = true`.

## 5. Activación y botón «Actualizar datos» (autorización separada)

1. En `conectores.json` cambie **solo** `backlog_ssrs.activo = true`.
   - Opcional: `contingencia_max_horas`.
   - No cambie servidor, ruta, parámetros, entidad, moneda, cuenta ni atributos: invalidarían la acreditación.
2. Ejecute `.\apps\nexus_junta_preview\NexusJunta.ps1` y pulse **Actualizar datos**.

**Esperado:**
- «Actualización correcta».
- En «Fuentes y Refresh»: `backlog_ssrs` CAPTURADO y `fg_odbc` CAPTURADO.
- En la Junta:
  - «Backlog Current: consulta directa SSRS de este Refresh · corte …»;
  - «FG: consulta directa AS400 de este Refresh · corte …».
- Frozen ERP y Portal con los **mismos** archivos y SHA-256; mismo universo mensual (238 ITEM); notas y adelantos intactos.
- Embarques, Programa y Faltante: **N/D** con causa.
- **Segunda pulsación:** un archivo nuevo en `capturas\backlog_ssrs` con el instante nuevo, aunque los datos no cambien.

## 6. Fallo controlado (autorización)

Para provocar un fallo **sin** tocar la huella, use una sesión sin acceso al servidor SSRS o desconecte su red.
- **`CONSERVAR_ULTIMO_CORTE`:** «Actualización parcial» y «**BACKLOG ANTERIOR** — el conector SSRS falló (…); Current del corte … · antigüedad …». El FG sigue directo.
- **`DETENER`:** error y se conserva la generación vigente.

Cambiar servidor, ruta o parámetros produce `NO_ACREDITADO`, no `FALLO`.
