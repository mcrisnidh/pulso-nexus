# PULSO NEXUS Junta Preview v0.3.3 — Backlog SSRS (29 OERAS) en «Actualizar datos»

**Estado: candidato de ingeniería OFFLINE. NO-GO para activación operativa** hasta acreditar la fuente y completar la validación real en ASMXC1200 (`DOCS/BACKLOG_SSRS.md`).

- **Alcance:** solo Backlog SSRS.
- **FG v0.3.2:** sin cambios de comportamiento.
- **Embarques:** fuera de alcance; sigue N/D.
- **No activa nada:** `backlog_ssrs.activo` sigue en `false`.
- **Sin cambios en** reglas comerciales, Frozen, universo mensual, R1/R3, CAT/TUPY, notas, adelantos ni escenarios.

## Diagnóstico de lo existente (sobre `004bc79`)

| Componente | Estado previo | Acción |
|---|---|---|
| Botón `/refresh` → `PreviewRefresh.iniciar()` → `orquestador.capturar_todo()` → `_backlog()` | Correcto: consulta SSRS con sesión Windows y modo de importación interactiva | **Se conserva** |
| `ssrs.parse` (reporte, periodo, entidad, moneda, ecuación ordenado − embarcado = abierto, duplicados, XML inseguro) | Falla cerrado ante XML inválido, periodo incorrecto o filas incompletas | **Se conserva** |
| `ssrs_fetch.ps1` / `fetch_windows` (401/403 → `AUTENTICACION_REQUERIDA`; HTTP no válido → `SSRS_DESCARGA_FALLIDA`) | Correcto | **Se conserva** |
| Acreditación de Backlog (`acreditacion_backlog_ssrs.json`, esquema `/1`) | **DEFECTO:** solo comprobaba la *forma* del documento (`informe_sha256` con 64 hex), igual que FG en v0.3.1. Una acreditación inventada activaba el conector | **Corregido:** módulo nuevo `acreditacion_backlog.py`, esquema `/2` verificable (ver abajo) |
| `conciliar.py backlog --acreditar` | **DEFECTO:** acreditaba cualquier CSV, sin procedencia ni copia de evidencia | **Corregido:** exige el XML ORIGINAL de `preflight/backlog_ssrs` o `capturas/backlog_ssrs`, guarda evidencias inmutables y la huella del reporte |
| Captura directa (`guardar` con reutilización por SHA-256) | **DEFECTO:** una respuesta con bytes idénticos reutilizaba la captura vieja con su instante viejo. Parecía que no se había consultado, y un R manual intermedio podía desplazarla | **Corregido:** cada consulta es un corte nuevo (`reutilizar=False`), con `instante_consulta_utc` |
| Precedencia del Current ERP | **DEFECTO:** el último por fecha; un R manual con fecha igual o posterior desplazaba a la consulta del Refresh | **Corregido:** con una consulta exitosa, Current = la consulta de este Refresh, con aviso por cada R manual ignorado. El Frozen se elige antes y no cambia |
| Contingencia de Backlog | **DEFECTO:** el Refresh quedaba «parcial», pero la generación no marcaba el corte anterior ni su antigüedad | **Corregido:** «BACKLOG ANTERIOR» con código y antigüedad; límite opcional `contingencia_max_horas` |
| Backlog activo con acreditación inválida y política `DETENER` | **DEFECTO:** no se detenía (solo FG tenía esta regla) | **Corregido:** sigue su política |
| Carga de capturas de Backlog | Cargaba **todas** las del mes en cada Refresh, con un coste creciente | Solo el primer corte del mes, el Frozen bloqueado y el Current. Los intermedios quedan para auditoría, sin cambiar la precedencia |

## Acreditación verificable (R1)

- **Esquema:** `nexus.preview.acreditacion-conector/2`.
- **Alcance:** `FUENTE_Y_CONTRATO`.
- **Equivalencia comercial:** `equivalencia_comercial: NOT_ESTABLISHED`. El registro de decisiones V26 no se certifica.
- **Qué se verifica en cada Refresh:**
  - el informe y su SHA-256;
  - las copias de evidencia (R manual y XML);
  - la captura ORIGINAL con el mismo SHA-256;
  - el **recálculo** de la conciliación por identidad EXACTA (ITEM, cliente, pedido, línea), sin normalizar;
  - las líneas declaradas;
  - la **huella del reporte**: servidor, ruta, parámetros, nombre, atributo de periodo, entidad, moneda, cuenta Windows, atributos y formato de clave de cliente.
- **Se rechaza:** cualquier acreditación fabricada, alterada, ajena, con rutas inseguras o enlaces, o de otra configuración.
- **Corte inicial:** solo se acredita una conciliación **sin ninguna diferencia**. Los cortes futuros pueden diferir por cambios comerciales legítimos: se trazan y no se comparan contra el corte inicial.

## Diagnóstico de discrepancias (R2)

El informe privado de `conciliar.py backlog` incluye `diagnostico`, que clasifica cada línea presente en un solo lado con evidencia estructural:
- `IDENTIDAD_DISTINTA`
- `CLIENTE_AUSENTE`
- `PEDIDO_AUSENTE`
- `LINEA_AUSENTE`

Cada línea lleva además vencimiento al corte y si tuvo embarque parcial. El resumen saneado lleva solo conteos.

Las 9 líneas del corte del 8-nov **no se clasifican offline**: no hay datos reales en el desarrollo. El procedimiento reproducible está en `DOCS/BACKLOG_SSRS.md`. Nada se convierte en cero ni en excepción permanente.

## Pruebas

- `tests/test_v033_backlog.py`: 56 pruebas nuevas, con simuladores y datos sintéticos.
- La suite de la Junta da **202 passed**: 146 anteriores y 56 nuevas. `test_a12` exige un árbol confirmado y se valida tras el commit.

**Ajustes a ayudas o pruebas existentes** (codificaban el defecto corregido, igual que `k12` en v0.3.1):
- `simuladores.acreditar(data, "backlog_ssrs")` construye ahora una cadena verificable real. La falsa sigue como `acreditar_falsa`.
- `test_k21`: su preparación usa el XML original en `preflight/backlog_ssrs` y la configuración del reporte. Sus aserciones no cambian.

## Calidad

Línea base Ruff del componente: **924 hallazgos** (antes 938; −14 por líneas reescritas), en transición `revision` declarada. Sin aumentos por (archivo, regla) y sin supresiones prohibidas. Las supresiones con código nuevas son 9 `# noqa: E402` en imports tras `sys.path` y 1 `# noqa: PLC0415`, todas declaradas una a una.
