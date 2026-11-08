# PULSO NEXUS v0.3.2 — cierre del conector FG (AS400 / STKBAL)

**Objetivo:** que cada Refresh consulte el FG directamente en el AS400, sin importar a mano `INV_STA*.TXT`.

Parte de v0.3.1 y **no activa nada**: `fg_odbc.activo` sigue en `false` hasta que se active a mano en ASMXC1200 con autorización expresa. No cambia reglas comerciales, el motor, el Frozen, el universo del mes, las notas, los adelantos, los escenarios ni los conectores Backlog y Embarques.

## Punto de partida (evidencia real de ASMXC1200, aportada por el usuario)

| Paso | Resultado |
|---|---|
| Entorno, conexión y consulta de solo lectura a `AULT2F2PRD.STKBAL` (DSN `AuroraODBC`, `SORTTYPE=0` solo en `conectores.json`, cuenta `remcb`, credencial `PULSO_NEXUS/fg`) | PASS |
| Extracción | 560 registros, SHA-256 `3c8709d0…162db63d` |
| Conciliación contra `INV_STA291.TXT` del 8-nov | 559 pares manuales / 560 directos. FG: **343 de 343 ITEM idénticos**, sin diferencias de cantidad ni de identidad. Global: **1 registro adicional en TH** (`SERV19`, cantidad 1). Informe SHA-256 `d91e94f2…a84efde` |
| Desfase entre cortes | ≈ 70 minutos: **no son simultáneos** |

Con v0.3.1 esa conciliación no podía acreditarse: `--acreditar` exigía PASS global, y la acreditación de FG solo comprobaba la *forma* del documento.

## Cambios

| # | Requisito | Cambio | Pruebas |
|---|---|---|---|
| 1 | Acreditación explícita con alcance FG, conservando el informe global y la discrepancia TH | `conciliar.py fg --alcance FG --acreditar` (lanzador: `-Alcance FG`). El informe guarda **siempre** el resultado global (`DIFERENCIAS` por TH) y, aparte, el del alcance (`PASS`). Se acredita solo si el alcance no tiene diferencias. La acreditación (esquema `/2`) registra el resultado global, el número de diferencias fuera de alcance y los stockrooms afectados. Sin `--alcance`, la regla sigue siendo PASS global | `a01`, `a03`, `a04` |
| 2 | Verificación criptográfica del informe, las capturas de origen, la identidad exacta de ITEM y el alcance; rechazo de acreditaciones falsificadas, alteradas o ajenas | Módulo nuevo `conectores/acreditacion_fg.py`. **En cada Refresh**:<br>• comprueba el SHA-256 del informe;<br>• comprueba las copias de evidencia (IN092U y consulta directa) guardadas en la carpeta de conciliación;<br>• comprueba que la captura **original** existe en `preflight/fg_odbc` o `capturas/fg_odbc` con el mismo SHA-256;<br>• **recalcula** la conciliación y la compara campo a campo;<br>• exige cero diferencias y los mismos ITEM exactos dentro del alcance;<br>• comprueba la **huella de la consulta** (DSN, biblioteca, tabla, columnas, compañía, filtro, cuenta, `atributos_conexion` como `SORTTYPE`).<br>Las rutas no pueden ser absolutas, escapar de los datos ni seguir enlaces | `a08` ×5 falsificadas, `a09` ×9 alteradas, `a10` ×5 ajenas, `a05` identidad, `a11` ×7 huella, `a12` `SORTTYPE` |
| 3 | El Refresh usa una consulta ODBC nueva, no el último TXT manual, cuando la captura directa tiene éxito; trazabilidad del corte | • Con `CAPTURADO`, el FG de la generación es **la consulta de ese Refresh**. Un IN092U manual con instante igual o posterior no la sustituye y queda anotado como aviso.<br>• Cada consulta es un **corte nuevo**, aunque los bytes coincidan con el anterior. En v0.3.1 la captura idéntica se reutilizaba con su instante viejo y un TXT intermedio la desplazaba.<br>• Solo ese corte entra a la generación; los anteriores quedan en disco para auditoría.<br>• La generación registra origen, instante, alcance y huella de la acreditación (`sources.fg.conector`, `inputs.fg_conector`).<br>• La Junta muestra «FG: consulta directa AS400 de este Refresh · corte …» | `r01`, `r02` ×2, `r03` |
| 4 | Ante un fallo de conexión, aplicar solo la política autorizada e indicar que el dato es más antiguo | • `CONSERVAR_ULTIMO_CORTE`: la generación queda marcada `CONTINGENCIA` con el código del fallo y la **antigüedad** del corte usado. La Junta muestra «**FG ANTERIOR** … antigüedad N h MM min» y el aviso aparece en la lista.<br>• Nuevo límite opcional `contingencia_max_horas`: si se supera, el Refresh se detiene y conserva la vista vigente.<br>• `DETENER` sin cambios.<br>• Un conector FG **activo** con acreditación inválida sigue su política: `DETENER` detiene; `CONSERVAR` usa el IN092U manual con aviso.<br>• Si el alcance acreditado no cubre los stockrooms usables, no se usa la consulta y el Refresh se detiene | `r04`, `r05` ×2, `r06` ×2, `r07`, `r09` |
| 5 | UTF-8 en Windows y propagación de errores del lanzador | • `NexusJunta.ps1` se guarda en **UTF-8 con BOM**. Sin BOM, Windows PowerShell 5.1 lo leía como ANSI y corrompía los textos con acentos.<br>• El lanzador fija la consola, `PYTHONUTF8` y `PYTHONIOENCODING` en UTF-8.<br>• `preflight.py`, `conciliar.py` y `preview.py` reconfiguran stdout y stderr a UTF-8. Antes, `preflight` terminaba con `UnicodeEncodeError` en consolas cp437/cp850 al imprimir «→».<br>• Cada llamada a `uv` revisa `$LASTEXITCODE`, y el script termina con `exit $rc`. Antes, `& uv @a; return` devolvía 0 aunque fallara. Un Refresh inicial fallido ya no arranca la Junta | `w01`, `w02`, `w03`, `w04` |
| 6 | Pruebas negativas, de Refresh y de rollback; sin fijar 343 como invariante | 50 pruebas nuevas en `test_v032.py` y 6 de reproducción en `test_repro_defectos_v031.py`. Ninguna usa 343: las cantidades salen del dato (`a06` acredita 7 ITEM, `a01` 30). Rollback en `tests/verificar_rollback.py` | ver abajo |
| 7 | Preservar Frozen, las identidades del universo mensual, notas, adelantos, escenarios y demás conectores | • FG no participa en el Frozen.<br>• Backlog conserva su acreditación `/1` y Embarques su cadena v0.3.1.<br>• Las 91 pruebas previas pasan **sin cambiar ninguna aserción** | `r08`, suite completa |

**Única ayuda de prueba modificada:** `simuladores.acreditar(data, "fg_odbc")`. Antes escribía una acreditación con `informe_sha256 = "a"*64`, que es justo lo que v0.3.2 debe rechazar. Ahora construye una cadena verificable real: captura de preflight simulada, IN092U sintético y `conciliar.py --alcance FG --acreditar`. La versión falsa sigue disponible como `acreditar_falsa` para las pruebas negativas.

## Evidencia

- `resultados/reproduccion_defectos_sobre_v031.txt`: las 6 pruebas de reproducción **fallan sobre v0.3.1** sin modificar:
  - acreditación inventada aceptada;
  - TXT posterior que desplaza a la consulta;
  - captura idéntica reutilizada con instante viejo;
  - contingencia sin marcar;
  - `UnicodeEncodeError`;
  - lanzador sin BOM y con `return`.
- `resultados/pruebas_v032.txt` y `junit_v032.xml`: **147 passed** (91 anteriores, 50 nuevas y 6 de reproducción).
- `resultados/rollback_v032_a_v031.txt`: 12 de 12 PASS. v0.3.1 muestra la generación de v0.3.2, rechaza la acreditación `/2`, **no consulta ODBC**, vuelve al IN092U manual y conserva notas y Frozen. Al regresar a v0.3.2, vuelve la consulta directa.
- `resultados/evidencia_navegador/13_junta_fg_consulta_directa.png` y `14_junta_fg_contingencia.png`, más `navegador_v032.json`.
- **Cambio mínimo:** `PARCHE_v0.3.1_a_v0.3.2.diff`.

## Limitaciones declaradas

- **Cortes no simultáneos.** El IN092U manual y la consulta directa no son simultáneos (≈ 70 min en la evidencia real). La conciliación los compara tal cual y registra el desfase (`cortes.desfase_minutos`, `simultaneos: false`). Una coincidencia exacta en FG con 70 minutos de desfase es evidencia fuerte, pero **no** prueba que ambos midan el mismo instante.
- **Alcance.** La acreditación cubre **solo** los stockrooms declarados (FG). Los demás stockrooms capturados por la consulta (TH, etc.) quedan sin acreditar. El motor solo usa los stockrooms usables (`usable_fg_stockrooms`), y el Refresh se detiene si estos no están dentro del alcance.
- **Resistencia a manipulación.** La verificación detecta manipulaciones inconsistentes. Quien controle la carpeta de datos podría fabricar una cadena completa y coherente (evidencia falsa, conciliación y acreditación). La defensa es el control de acceso a `%LOCALAPPDATA%\PULSO_NEXUS_LAB`. No hay firma con clave externa.
- **Validación.** Todo se probó con **simuladores y datos sintéticos**. No hubo conexiones corporativas en el desarrollo y no hay PASS corporativo de v0.3.2: la acreditación con alcance, la activación y el botón Refresh se validan en ASMXC1200 según `VALIDACION_ASMXC1200.md`.
- **Lanzador PowerShell.** Su comportamiento se verificó por análisis estático (no hay PowerShell en el entorno de desarrollo). Los códigos de salida y la consola UTF-8 deben comprobarse en ASMXC1200 (paso 0 de la validación).

## Actualización y rollback

- **Actualización:** `.\NexusJunta.ps1` desde este ZIP. Guarda la instalación v0.3.1 como `.anterior_*` y conserva datos, capturas, preflight, notas, `conectores.json` y Frozen.
- **Rollback:** `.\NexusJunta.ps1 -Desinstalar` restaura v0.3.1. La acreditación `/2` no es válida para v0.3.1, que vuelve al IN092U manual (fallo cerrado) aunque `fg_odbc.activo` siga en `true`.
