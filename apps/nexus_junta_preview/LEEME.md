# PULSO NEXUS v0.3.2 — Live Data Connectors (PREVIEW / NO-GO COMERCIAL)

**Novedad de v0.3.2:** cierre del conector FG. Cada Refresh puede consultar el AS400 (STKBAL) directamente con una acreditación por alcance **verificable**. Si el conector falla, la contingencia queda marcada como «FG ANTERIOR». El lanzador funciona en UTF-8 y propaga los códigos de salida. **No activa nada.** Ver `CAMBIOS_v0.3.2.md` y `VALIDACION_ASMXC1200.md`.

**Novedad de v0.3:** conectores de solo lectura para Backlog (SSRS), FG (ODBC) y Embarques (CFDI, no habilitado en KPI). Ver `CAMBIOS_v0.3.md`, `DOCS/CONECTORES.md`, `DOCS/EMBARQUES_CFDI.md` y **`VALIDACION_ASMXC1200.md`** (procedimiento real paso a paso).

**Novedad de v0.2.1:** hay una sola Junta Operativa, sin selector de escenarios. Ver `CAMBIOS_v0.2.1.md`, que incluye la actualización desde v0.2 y el rollback.

Es la tabla de trabajo de la junta diaria con la interfaz de PULSO y los cálculos del motor NEXUS (`b4ec5be`).

El botón **Actualizar datos** procesa cortes nuevos de verdad:
- valida los archivos;
- recalcula con el motor;
- guarda una generación inmutable;
- conmuta la vista de forma atómica;
- conserva la anterior si algo falla.

Corrige la desconexión de PULSO Web, donde `STATUS_ROWS` leía un CSV fijo de octubre. Corre solo en `127.0.0.1:8877`.

## Qué muestra

| Columna | Cálculo | Estado hoy |
|---|---|---|
| Frozen | (primer R válido − líneas CAT por clave exacta, conservando TUPY y demás clientes) + primer Portal CAT depurado. Motor `frozen_universe` + `compute` | Provisional: R1/R3 sin aprobar |
| Pendiente Current | Último R + último Portal, con el **universo del Frozen** | Provisional |
| Var. F−C | Frozen − Current | Informativa: no es embarque ni faltante |
| FG | Último IN092U, stockroom FG | Ausente o negativo: N/D |
| Programa, Sobreembarque, Faltante | Contrato v3.10: `max(0, F+Adelanto−Emb)`, `max(0, Emb−F−Adelanto)`, `max(0, Programa−FG)` | **N/D**: no hay Embarques autorizados (CFDI no acredita salida física) |
| Brecha diag. | `max(Frozen − FG, 0)` | Diagnóstica: **no** es el faltante oficial |
| Cobertura 6 sem. | Current por fecha de línea ERP contra FG (diagnóstica). «FG hasta» v3.10: N/D | Las tarjetas WK muestran Frozen por semana (pzas congeladas) |
| WO, Forecast, Recup., Estado PULSO | — | N/D con causa (sin fuente autorizada) |
| Nota, Adelanto, Estatus CS, Entrega | Almacén privado local, append-only | Autor `EDITOR_LOCAL_SIN_AUTENTICAR` |

**Vista operativa:** es única y la fija la regla de aprobaciones, no el usuario.
- Hoy es **PROVISIONAL — R1/R3 PENDIENTES — NO PUBLICABLE**, construida con el escenario conservador A, que no está aprobado.
- Pasa a **VERIFICADA — NO PUBLICADA** solo con reglas R3 y registro R1 aprobados y completos.
- A, B y DIAG solo existen en «Escenarios R1/R3», que es diagnóstico no oficial y solo localhost.

## Instalación y arranque (un solo comando)

```powershell
cd <carpeta del ZIP descomprimido>
Set-ExecutionPolicy -Scope Process Bypass
$LAB = Join-Path $env:LOCALAPPDATA 'PULSO_NEXUS_LAB'
.\NexusJunta.ps1 -R3 (Join-Path $LAB 'private\<matriz_R3_provisional>.csv') -R1 (Join-Path $LAB 'private\<correspondencias_R1_provisionales>.csv')
```

Las siguientes veces basta con `.\NexusJunta.ps1`.

**Requisitos:** repositorio en `b4ec5be` y limpio, y `uv`. Flask está fijado en `uv.lock`, así que no se instala nada nuevo en el Python compartido: `uv run` usa el entorno del proyecto.

**Lo que hace el comando:**
1. Copia la aplicación a `%LOCALAPPDATA%\PULSO_NEXUS_LAB\nexus_junta_v02`.
2. Copia los tres archivos Frozen y FG, verificados por SHA-256, a `junta-inputs\2026-11`. Los originales no se tocan.
3. Crea los datos privados en `junta-data-2026-11`.
4. Calcula la primera generación y abre `http://127.0.0.1:8877/junta`.

**Asignaciones (opcional):** `-Asignaciones <csv>` acepta dos formatos, con claves exactas:
- `clave_cliente,cs,grupo`;
- las reglas de PULSO `Match Type,Match Value,CSR,Active`.

Sin ese archivo, CS y Grupo se muestran «Sin asignar».

## Validación con datos reales (solo en ASMXC1200)

1. Arranque con el comando anterior.
2. Compruebe que la Junta abre sin botones de escenario y con el encabezado `PROVISIONAL — R1/R3 PENDIENTES — NO PUBLICABLE`. En **Escenarios R1/R3** (diagnóstico), compruebe:

| Indicador | A | B |
|---|---:|---:|
| Universo ITEM | 238 | 210 |
| Líneas CAT ERP retiradas | 1,810 | 1,861 |
| Portal pendiente R1 | 4 | 0 |

3. En **Fuentes y Refresh**, anote el SHA-256 del Frozen bloqueado: `2b59eb…70d0d6` y `0592178d…60efd9`.
4. Busque un artículo, escriba una nota y un adelanto y pulse **Guardar**. Cierre el navegador y vuelva a abrir `http://127.0.0.1:8877/junta`: deben seguir ahí.
5. Copie el corte posterior real a la carpeta de la Junta (sin moverlo):
   ```powershell
   Copy-Item "$LAB\inputs\2026-11\R_20261106.TXT" "$LAB\junta-inputs\2026-11\"
   ```
6. Pulse **Actualizar datos**. Esperado:
   - «Actualización correcta» con `current_r: R_20261106.TXT`;
   - el encabezado «Pendiente Current» pasa a 06-nov;
   - Frozen por artículo y SHA-256 sin cambios;
   - universo sin artículos nuevos;
   - la nota y el adelanto se mantienen.
7. **Archivo inválido:**
   ```powershell
   Set-Content "$LAB\junta-inputs\2026-11\R_20261108.TXT" 'invalido'
   ```
   Pulse **Actualizar datos**: debe salir «Error» y la vista anterior quedar intacta. Después borre ese archivo.
8. **Doble clic** rápido en Actualizar: solo se ejecuta un trabajo; el segundo informa «Otro Refresh en curso».
9. Verifique que nada más cambió: `Get-FileHash "$LAB\inputs\2026-11\*"` sin cambios y `git status --short` vacío.

Esto es **PASS real** solo cuando se ejecuta en ASMXC1200. El PASS de este paquete es sintético (`resultados/`).

## Rollback

- **Vista:**
  ```powershell
  uv run --frozen --extra web python <app>\preview.py generaciones --data <datos>
  ```
  y después
  ```powershell
  uv run --frozen --extra web python <app>\preview.py activar --data <datos> --gen <id>
  ```
  No toca el Frozen ni las notas.
- **Aplicación:** `.\NexusJunta.ps1 -Desinstalar` restaura la instalación anterior si existe y conserva datos y notas. Con `-BorrarDatos` borra también datos, notas y la copia `junta-inputs`.

## Pruebas

```powershell
cd C:\Users\ucbaa\pulso-nexus
uv run --frozen --extra web --with tzdata==2025.2 pytest <ZIP>\app\tests -p no:cacheprovider -q
```

Resultado en el entorno de desarrollo, con datos sintéticos: **41 passed**, incluido el flujo en Chromium real (`resultados/pruebas_junta.txt`, capturas en `resultados/evidencia_navegador/`, incluidos los encabezados provisional y verificado sintético).

La prueba de navegador se omite si no hay Playwright para Node. El lanzador `.ps1` no pudo ejecutarse aquí porque no hay PowerShell; los comandos que invoca sí están probados.

El código de salida 40 del ejecutor de conciliación anterior sigue sin certificarse con el parche: es independiente de esta aplicación y no se oculta.
