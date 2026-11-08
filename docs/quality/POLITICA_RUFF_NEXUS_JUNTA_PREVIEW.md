# Política Ruff del componente aislado `apps/nexus_junta_preview` (v2)

**Ámbito:** el paquete Junta Operativa Preview v0.3.2 (FG) integrado en `apps/nexus_junta_preview` por `50af193` y `adca87f` (rama `feature/fg-v032-core`, base `b4ec5be`).
**No se aplica al CORE:** `src/`, `tests/` y `scripts/` siguen con la configuración estricta de `pyproject.toml`, sin cambios de reglas.

## 1. Regla

1. **CORE estricto.** `ruff check .` y `ruff format --check .` evalúan el CORE con las reglas de siempre.
   - El componente queda fuera de esas dos invocaciones mediante `extend-exclude = ["apps/nexus_junta_preview"]` en `pyproject.toml`.
   - No es una exclusión silenciosa: la misma puerta obliga a pasar el punto 2.
2. **Componente contra línea base.** `scripts/junta_ruff_baseline.py check` evalúa el componente con **las mismas reglas del CORE**: mismo `pyproject.toml` y ningún `per-file-ignores` propio. Compara contra `quality/ruff_baseline_nexus_junta_preview.json` (esquema `/3`) por **identidad**, no solo por conteo:
   - **Lint:** cada hallazgo tiene una huella SHA-256 de (archivo, regla, texto de la línea), independiente del número de línea, con su multiplicidad. La medición debe coincidir **exactamente** con la línea base.
     - Una huella nueva falla aunque el conteo (archivo, regla) no cambie. Así se detecta la **sustitución**: corregir un hallazgo y añadir otro de la misma regla en el mismo archivo.
     - Una huella desaparecida también falla hasta registrar la reducción (trinquete).
   - **Formato:** se registra el SHA-256 del contenido de cada archivo sin formatear. Un archivo nuevo sin formato falla. Un archivo con deuda de formato que se **modifica** sin formatearlo también falla.
   - **Supresiones** (esquema `/3`). Se inventarían los **comentarios reales**, leídos con `tokenize`, de modo que el texto dentro de cadenas no cuenta. Las directivas se comprobaron una a una contra Ruff 0.17.3:
     - **Prohibidas siempre, en cualquier transición:**
       - de archivo o rango: `# ruff: noqa`, `# ruff:noqa`, `# ruff: noqa: X`, `# flake8: noqa[: X]`, `# ruff: file-ignore[X]` y `# ruff: disable[X]` … `# ruff: enable[X]`;
       - de isort: `skip_file`, `skip`, `off`/`on`, `# ruff: isort: …`;
       - de formato: `# fmt: off`/`on`/`skip` y `# yapf: disable`/`enable`;
       - `noqa` sin código o vacío: `# noqa`, `#noqa`, `# NOQA`, `# noqa:`;
       - **configuración Ruff anidada** en el componente (`ruff.toml`, `.ruff.toml`, `pyproject.toml`), que sustituiría las reglas del CORE sin cambiar el `[tool.ruff]` raíz.

       Se rechaza incluso la forma que Ruff no aplica (`# RUFF: NOQA`, `# noqa:`), por si cambia su comportamiento.
     - **Con código** (`# noqa: X`, `# noqa : X`, `# ruff: ignore[X]`): cada una tiene huella. Una nueva solo entra con una `revision` declarada, nunca como `reduccion`. Así, suprimir un hallazgo no puede registrarse como reducción de deuda.
   - **Herramienta y código:** la línea base fija la versión de Ruff, el SHA-256 de `[tool.ruff]` y el SHA-256 del árbol del componente.
3. **Transiciones de la línea base.** Las aplica `update` antes de escribir (si no se cumplen, el archivo no cambia) y las vuelve a comprobar `check --base-ref` contra la línea base del commit base. Así, una edición manual del JSON no las esquiva.
   - **`inicial`:** solo cuando el commit base no tiene línea base. Exige `--justificacion` y `--revisor`.
   - **`reduccion`:** solo desaparecen huellas, con la misma herramienta. No requiere revisión.
   - **`revision`** (`--revision --justificacion … --revisor …`): pueden aparecer huellas nuevas de hallazgos o de supresiones **con código**, **sin** aumentar ningún conteo (archivo, regla), el total ni los archivos sin formato. Las supresiones prohibidas nunca entran. Cada huella añadida (`anadidas`, `supresiones_anadidas`) y cada archivo sin formato modificado queda listado en el historial, y `check --base-ref` exige que lo listado coincida exactamente con el cambio real.
   - **`herramienta`:** cambio de versión de Ruff o de `[tool.ruff]`. Exige justificación y revisor. Solo puede aumentar la deuda si el árbol del componente es **idéntico** al del commit base, porque el aumento no puede venir del código. Un cambio de herramienta mezclado con cambios de código se rechaza.
   - **Historial:** solo crece. Reescribir entradas anteriores, o cambiar la línea base sin una entrada nueva, falla.
   - **Referencia base:** si `--base-ref` no resuelve a un commit, la puerta falla cerrada.
4. **Misma política en local y en CI.** Ambas ejecutan `scripts/quality_gates.py` (`estatico`, `gobernanza`, `tipos`, `junta`, `core`, `todo`).
   - La CI lo invoca en Linux (con `fetch-depth: 0` y `NEXUS_BASELINE_BASE_REF` = base del PR o commit anterior al push) y en Windows (`junta` y `gobernanza`).
   - `verificar_ci()` analiza los **pasos** de cada job, no el texto. Cada orden exigida debe ser una línea ejecutable completa de un `run`, en el job correcto, sin `if: false` ni `continue-on-error: true`. No cuentan los comentarios YAML, los de shell ni la orden incluida dentro de otra (p. ej., `echo …`).
   - Localmente, la referencia base por omisión es `HEAD`.
   - **Dependencias de ejecución en Windows:** las puertas `junta` y `core` se lanzan con `uv run --frozen --with tzdata==2025.2`.
     - En Windows no hay base IANA del sistema y `uv.lock` no incluye `tzdata`, necesaria para `zoneinfo("America/Mexico_City")`.
     - `core` además carga explícitamente `pytest-cov` y el plugin de Hypothesis (`pytest -p pytest_cov -p hypothesis.extra.pytestplugin`), incluso si el proceso tiene `PYTEST_DISABLE_PLUGIN_AUTOLOAD` definido.
     - No se modifica `uv.lock` ni se desactiva esa variable de manera permanente.
     - Las invocaciones manuales de pytest CORE/Junta en Windows deben suministrar `tzdata==2025.2`; CORE también debe cargar los plugins de cobertura e Hypothesis si la carga automática está deshabilitada.
     - La CI Linux mantiene su comando CORE directo, sobre Ubuntu con base IANA y carga normal de plugins; la CI Windows existente ejecuta Junta y gobernanza, pero no CORE completo.
5. **Huellas.** Corregir la deuda de estilo del paquete altera las huellas de `apps/nexus_junta_preview/SHA256SUMS` (48 en `adca87f`). Por eso se aplaza: se corrige por lotes, cada lote con su actualización del manifiesto y la batería completa de pruebas. Esta política no modifica ningún archivo del paquete.

## 2. Línea base inicial (`adca87f`)

Medida con ruff 0.17.3 sobre `adca87f20f3541321e2b9cfec7c1686814cc87c4`: **938 hallazgos y 23 archivos sin formato**, todos en `apps/nexus_junta_preview`. Fuera del componente: 0.

**Causa de la diferencia 937 → 938**, revisada y no aceptada de forma automática:
- `50af193` (integración) da 937, idéntico por (archivo, regla) al paquete v0.3.2 entregado.
- `adca87f` cambia `app/tests/test_v032.py::test_a10[enlace]`: si Windows rechaza `symlink_to` con `winerror 1314`, crea una **junction real** con `mklink /J`.
  - Añade 2 `assert` (**S101 +2**): `mklink` termina con 0 y el enlace es una junction (`Path.is_junction()`).
  - Divide la línea `symlink_to` original (**E501 −1**).
  - Neto: **+1**.
- Los 2 `assert` son aserciones de prueba, de la misma clase que los demás S101 de `app/tests/`. **Aceptados.**
- El cambio de `adca87f` en `test_preview.py` (resolver `npm.cmd`/`npm` y `node` con `shutil.which` y dejar SKIPPED la prueba de navegador sin Node) no altera ningún conteo.
- La puerta, ejecutada con la línea base de 937 sobre `adca87f`, informó exactamente `S101 93 → 95` y `E501 42 → 41` en `test_v032.py`. Así se comprobó que detecta el cambio antes de regenerar la línea base.

| Regla | N | Clasificación | Revisión |
|---|---:|---|---|
| E501 | 498 | Estilo | Líneas de más de 100 caracteres. Sin efecto funcional. |
| S101 | 388 | Estilo de pruebas | **Todos** en `app/tests/`; 2 son de la prueba de junction de `adca87f`. El CORE ignora S101 en `tests/**`; aquí se conserva como deuda visible en vez de añadir un `per-file-ignores` nuevo. |
| RUF100 | 12 | Estilo | `noqa` que nombran reglas no habilitadas (`E402`, `BLE001`, `S314`, `ANN201`). Anotaciones inertes. |
| PLR0915 | 9 | Mantenibilidad | Funciones largas. El caso mayor es `webapp.create_app` (189 sentencias), que registra las rutas Flask. Sin defecto asociado. |
| PLC0415 | 9 | Estilo | Imports diferidos: dependencias opcionales y pruebas. El CORE los permite en pruebas. |
| I001 | 8 | Estilo | Orden de imports. Corrección automática pendiente. |
| PLW1510 | 3 | Estilo de pruebas | `subprocess.run` sin `check` en pruebas que **examinan** `returncode` a propósito. |
| PLC0207 | 2 | Estilo | `split` sin `maxsplit` en el simulador de pruebas. |
| RUF059 | 2 | Estilo de pruebas | Variable desempaquetada sin uso. |
| **S104** | 1 | **Falso positivo, revisado** | `tests/test_preview.py::test_a12c_serve_solo_loopback` pasa `--host 0.0.0.0` para comprobar que el servidor lo **rechaza** (código 2, «solo 127.0.0.1»). Es una prueba del control de seguridad, no un bind. |
| **S608** | 1 | **Falso positivo, revisado** | `conectores/fg_odbc.py::sql`:<br>• biblioteca, tabla y columnas pasan por `odbc.identificador` (lista blanca por expresión regular);<br>• el operador solo admite `<>` y `>`, y el sufijo solo `FOR READ ONLY` y `FOR FETCH ONLY`;<br>• la compañía va como parámetro `?`.<br>La prueba `k07` rechaza `STKBAL; DROP TABLE X`. |
| **S311** | 1 | **Falso positivo, revisado** | `tests/dataset_demo.py`: `random.Random(7)` genera datos sintéticos reproducibles. No es criptografía. |
| **F401** | 1 | **Revisado, sin efecto** | `import json` sin uso en `tests/test_repro_defectos_v031.py`. Import muerto en una prueba. |
| **PLR1704** | 1 | **Revisado, sin efecto actual** | En `webapp.escenarios()`, el bucle usa `code`, el mismo nombre que el parámetro `code` de `create_app`. Es una variable local de la función anidada y `escenarios()` no usa el `code` externo, así que no hay defecto. Es un riesgo latente: renombrar a `item` en el próximo lote. |
| N802 | 1 | Estilo | Nombre de prueba con «R» mayúscula (backlog R). |
| UP031 | 1 | Estilo | Formato `%` en un mensaje de `preflight.py`. |

**Por zona:** 649 hallazgos en `app/tests/` y 289 en el código de la aplicación. En la aplicación, los mayores son `servicio.py` (69), `conciliar.py` (40) y `orquestador.py` (39), casi todos E501.

**Conclusión:** ningún hallazgo de Ruff es un error funcional ni de seguridad. Los cinco potencialmente sustantivos (S104, S608, S311, F401, PLR1704) se revisaron uno a uno.

## 3. Defecto fuera de Ruff: ya corregido en `adca87f`

En `50af193`, `tests/test_preview.py::test_a13_navegador_flujo_completo` ejecutaba `subprocess.run(["npm", ...])` sin shell. En Windows sin Node (o con `npm.cmd`), eso terminaba en **ERROR** en lugar de **SKIPPED**.

`adca87f` lo corrige con `shutil.which("npm.cmd") or shutil.which("npm")` y `shutil.which("node")`, y salta con «prueba de navegador NO EJECUTADA» si falta alguno. Esta política no añade otra corrección: se conserva la de `adca87f`.

## 4. Pendiente

- **Lotes de deuda**, en este orden, cada uno con manifiesto actualizado y batería completa:
  1. correcciones automáticas seguras (`I001`, `RUF100`, `PLC0207`, `F401`);
  2. `PLR1704` y `UP031`;
  3. formato de los 23 archivos;
  4. E501;
  5. decidir si S101 en las pruebas del componente se trata como en el CORE (`tests/**`), lo que exigiría una regla explícita en esta política.
- **Prueba de junction:** solo se ejercita en Windows sin privilegio de enlaces simbólicos. En Linux/CI Linux el caso usa enlaces simbólicos. Su validación real corresponde al job Windows y a ASMXC1200.

## 5. Pruebas de gobernanza

`scripts/tests/test_puertas_calidad.py`, ejecutado con `quality_gates.py gobernanza` en Linux y Windows: 43 pruebas sobre repositorios temporales sintéticos. Demuestran que se rechaza:
- la sustitución de hallazgos con el mismo conteo, salvo mediante una revisión explícita y trazada;
- un aumento de deuda vía `update`, incluso con `--revision`;
- un archivo nuevo sin formato;
- un cambio de herramienta sin revisión, o mezclado con código;
- la edición manual del JSON, también disfrazada de `reduccion`;
- la reescritura del historial;
- una referencia base inexistente;
- **17 formas de supresión prohibida.** Cada prueba verifica primero si Ruff 0.17.3 suprime con esa forma, porque entonces el efecto parecería una reducción de deuda. Después demuestra el rechazo en `check`, en `update` y en `update --revision`;
- la **configuración Ruff anidada** (×3);
- las **supresiones con código** (×3) registradas como reducción. Solo se admiten con una `revision` declarada;
- en la CI: órdenes exigidas como comentario, dentro de otra orden, con `if: false` o `continue-on-error`, en otro job, o sin `NEXUS_BASELINE_BASE_REF` ni `fetch-depth: 0`.

Una cadena que contiene texto de directiva **no** es una supresión, y la puerta tampoco la cuenta.


**Portabilidad Windows/Linux del árbol:** el recorrido se ordena por ruta relativa POSIX, no por comparación de objetos `Path`. El manifiesto `apps/nexus_junta_preview/SHA256SUMS` puede existir físicamente en CRLF después de la integración con PowerShell 5.1: para la **huella del árbol solamente**, se admite la representación LF si y solo si coincide byte por byte con el blob del índice Git. No se normaliza ningún otro archivo; una modificación semántica del manifiesto sigue invalidando el árbol. Se prohíben supresiones globales incluso al **inicializar** una línea base nueva. Dos pruebas negativas acreditan estas condiciones.
