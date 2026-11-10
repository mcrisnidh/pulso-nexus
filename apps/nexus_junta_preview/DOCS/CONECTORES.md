# Conectores corporativos de solo lectura — PULSO NEXUS v0.3 (FG cerrado en v0.3.2)

| Conector | Identidad | Autenticación | Qué produce | Origen en PULSO Web |
|---|---|---|---|---|
| `backlog_ssrs` | `ucbaa` | Sesión Windows del usuario (`Invoke-WebRequest -UseDefaultCredentials`, Negotiate/NTLM). Sin contraseñas, cookies ni tokens | Backlog = 29 OERAS = `R_YYYYMMDD.TXT`, normalizado al contrato `oeras_29` | `ssrs_preview_core.py`, `stage50a_probe.py` («Month End - Backlog Detail», XML, campos, controles de calidad) |
| `fg_odbc` | `remcb` | Credencial genérica `PULSO_NEXUS/fg` del Administrador de credenciales | FG por ITEM y stockroom con la semántica de IN092U, contrato `fg_inventory` | Consulta ODBC del libro «CO 36 inventory 32INAS.xlsx»: `DB2LOCAL` → `AULT2F2PRD.STKBAL` (`PNUM60`, `STRC60`, `SPHY60`, `CONO60='36'`) |
| `embarques` | `sm0003` | Credencial genérica `PULSO_NEXUS/embarques`, Azure SQL con `Encrypt=yes` y `ApplicationIntent=ReadOnly` | Facturación CFDI por ITEM. Regla comercial (FIX5): la factura emitida **es** el embarque; requiere acreditación `/2` (`DOCS/EMBARQUES_CFDI.md`) | `asbg_item_adapter.py` (`MF_CFD` + `MF_DETCFD`), `live_sources.py` |

## Seguridad

- **Sin secretos guardados:** no hay secretos en código, configuración, logs, capturas, estados ni paquete.
  - `conectores.json` rechaza claves de contraseña, token o cookie, y también `UID`, `PWD` o `Trusted_Connection` en los atributos.
  - FG y Embarques no pueden compartir credencial.
  - La cuenta de cada credencial debe coincidir con la esperada. SSRS solo corre en la sesión `ucbaa`.
- **Solo lectura:** se ejecutan únicamente SELECT constantes y parametrizadas.
  - La conexión se abre con `readonly=True`, `autocommit=False` y `rollback` al cerrar.
  - En DB2 se añade `FOR READ ONLY`. Los identificadores configurables se validan.
- **Errores:** los mensajes del controlador se sanean (sin `PWD`, sin secreto).
- **Entorno:** usa el `.venv` propio del repositorio NEXUS, nunca el de AMFT, Invoice Monitor ni PULSO Web. `pyodbc` ya es dependencia de `uv.lock`.
- **Sin efectos fuera de Preview:** no hay tareas programadas ni servicios, y nada escribe en ERP, SSRS, SQL ni PULSO Web.

## Ciclo de activación (por conector, independiente)

1. **Credencial**, solo FG y Embarques. Se escribe en una consola, no en NEXUS:
   ```
   cmdkey /generic:PULSO_NEXUS/fg /user:remcb /pass
   ```
   Pide la contraseña sin mostrarla.
2. **Configuración** en `junta-data-2026-11\conectores.json`: completar los marcadores `<...>`. La plantilla viene con todo inactivo.
3. **Preflight** `entorno` (sin red). Después, solo con autorización: `conexion` y `consulta` con `-Autorizo`. El resultado de `consulta` queda en `preflight\<conector>\` y **no** lo usa el Refresh.
4. **Conciliación** contra el extracto manual equivalente, con `-Acreditar`. Solo un PASS emite `acreditacion_<conector>.json`.
   - **FG (v0.3.2):** `-Alcance FG`. Se acredita si el alcance es PASS. El informe conserva el resultado global, incluidas las diferencias en otros stockrooms (p. ej. TH). Detalle en «FG en v0.3.2».
5. **Activación:** `"activo": true`, siempre a mano: la acreditación nunca activa. Sin acreditación válida el Refresh lo marca `NO_ACREDITADO` y usa los archivos manuales; un FG activo con `DETENER` se detiene.

## Refresh

**Orden de cada Refresh:**
1. Ejecuta los conectores activos y acreditados. Cada captura se guarda en `capturas\<fuente>\` con:
   - el original (`.raw`);
   - la normalización (`.csv`);
   - la procedencia (`.json`): cuenta, consulta o host, instante y SHA-256.
2. Integra las capturas con los archivos manuales de `junta-inputs\2026-11` (respaldo operativo).
3. Aplica la precedencia:
   - **Frozen:** primer corte del mes. Ese mismo día, el archivo manual tiene precedencia y, entre capturas, la primera. Una vez bloqueado **no cambia**.
   - **Current:** último corte. Ese mismo día, la consulta directa más reciente.
   - **FG:** con una consulta FG exitosa en ese Refresh, **esa consulta**, aunque haya un IN092U manual igual o posterior. Si no, el instante más reciente.
4. Calcula con el motor NEXUS, crea la generación de forma atómica y conserva la anterior ante un error.

**Integridad de las capturas:**
- Una captura idéntica se reutiliza y produce `NO_CHANGE`.
- Una captura alterada detiene el Refresh con `CAPTURA_ALTERADA`.

**Política de fallo por conector** (`politica_fallo`):
- **`CONSERVAR_ULTIMO_CORTE`** (por defecto): el Refresh continúa con el último corte válido de esa fuente y queda **PARCIAL**, con un aviso explícito en el resultado y en «Fuentes y Refresh».
- **`DETENER`**: el Refresh falla y se conserva la generación vigente.
- Nunca se usan datos vacíos ni ceros.

## FG en v0.3.2

**Acreditación verificable** (`acreditacion_fg_odbc.json`, esquema `/2`). Cada Refresh la verifica completa:
- SHA-256 del informe y de las copias de evidencia (IN092U y consulta) guardadas en `conciliaciones\fg_…\`;
- captura **original** en `preflight\fg_odbc` o `capturas\fg_odbc`;
- **recálculo** de la conciliación;
- cero diferencias y los mismos ITEM exactos dentro del alcance;
- resultado global y diferencias fuera de alcance tal como se declararon;
- huella de la consulta: DSN, biblioteca, tabla, columnas, compañía, filtro, cuenta y `atributos_conexion`.

Si algo no coincide, el conector queda `NO_ACREDITADO`.

**Cortes:**
- Cada consulta es un corte nuevo en `capturas\fg_odbc\`, aunque los bytes se repitan.
- Solo el corte del Refresh entra a la generación, con su instante (`sources.fg.conector`, `inputs.fg_conector`).

**Contingencia** (`CONSERVAR_ULTIMO_CORTE`):
- La generación queda `CONTINGENCIA`, con el código del fallo y la antigüedad del último corte válido. La Junta lo muestra como «FG ANTERIOR».
- `contingencia_max_horas` (opcional) limita esa antigüedad: si se supera, el Refresh se detiene.

**Limitación:** el IN092U manual y la consulta no son simultáneos. El desfase queda registrado en el informe y en la acreditación.

**SSRS interactivo:** si el servidor pide iniciar sesión (`AUTENTICACION_REQUERIDA`):
1. El usuario abre el reporte en su navegador.
2. Lo exporta a XML.
3. Lo guarda en `junta-data-2026-11\ssrs-import\Backlog_SSRS_AAAAMMDD_HHMM.xml`.

El conector lo valida con los mismos controles. Con `"modo": "importacion_interactiva"` solo se usa esta vía.

**Portal CAT:** no tiene conector. Sigue siendo manual y su primer corte válido del mes es Frozen inmutable.

## Valores que el código heredado NO contiene (configuración local obligatoria)

- **URL del servidor SSRS y ruta del reporte:** estaban en `ssrs_fetch_readonly.ps1`, que no viene en el paquete de referencia. También los parámetros, si el reporte los exige.
- **Relleno de la clave de cliente** (`formato_clave_cliente`, por defecto `{num:<10}{seq}`): la conciliación de Backlog lo verifica. Si hay claves «solo manual» o «solo directa», el informe lo sugiere.
- **Nombre de cliente en el XML** (`atributos.nombre_cliente`): solo se usa para mostrarlo.
- **Servidor ASBG de Embarques.**
