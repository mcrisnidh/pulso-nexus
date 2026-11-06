# CORE v1.0 — Adenda UI/UX: informe final

- **Rama:** `feature/core-v1`. Sin PR, sin GitHub Actions (el CI no se dispara en esta rama; los commits llevan `[skip ci]`) y sin cambios en `main`.
- **Base:** `b2d6a76`, el Hito A, con estado GO condicionado.
- **Commit de esta intervención:** el que contiene este informe.
- **Datos:** solo sintéticos. Sin Hito B.

## Resultado

La interfaz se rediseñó **sin alterar el comportamiento funcional del CORE**. Quedó lista para una demostración ante la junta y validada en 1920, 1366, tablet (820 px) y móvil (390 px).

## 1. Qué se cambió (solo presentación)

| Archivo | Cambio |
|---|---|
| `src/nexus/web/present.py` (nuevo) | Formato de números, fechas y meses; etiquetas de estados y eventos; excepciones por ITEM; filtros y orden; barra de cobertura (solo con cifras conocidas que cuadran); detección del último Refresh no publicado. Solo lee lo publicado y no importa el motor (contrato import-linter C6) |
| `src/nexus/web/__init__.py` | Datos de presentación para las plantillas: filtros, estado legible del Refresh verificado contra el historial, pantalla de confirmación de rollback (GET, solo administrador) y mensajes de guardado y error. **Sin cambios** en autenticación, roles, CSRF, CSP, cabeceras, almacenamiento, publicación ni en las llamadas al servicio |
| `templates/*.html`, `static/style.css` | Nueva identidad visual sobria, jerarquía de indicadores, tabla compacta y responsiva, formularios CS separados, accesibilidad. Sin estilos inline ni `<script>`: la CSP no cambió |
| `tests/web/test_core_web.py` | Comprobaciones de texto actualizadas a la nueva presentación (mismos hechos verificados) y 2 pruebas nuevas: token CSRF en todo formulario POST; fases del Refresh verificadas y no supuestas |
| `scripts/core_a_browser.js`, `scripts/core_a_demo.py`, `scripts/core_ui_screens.js` | Verificación de navegador ampliada (33 comprobaciones), un texto de referencia mensual actualizado, capturas comparables antes/después |

**No se tocó** (`no-calc-change/git_diff_scope.txt`: 0 líneas de diff):

| Área | Ubicación |
|---|---|
| Cálculo, estado, publicación y CS | `src/nexus/core` |
| Infraestructura E1-B | `metadata`, `pipeline` |
| Pilot-20 | `planning` |
| E1-A | `storage`, `snapshots`, `hashing`, `sealed` |
| Dependencias | `pyproject.toml`, `uv.lock` |
| Autenticación | `src/nexus/web/auth.py` |

## 2. Evidencia de que no se alteraron cálculos ni reglas comerciales (`no-calc-change/`)

1. `git_diff_scope.txt`: los únicos archivos modificados están en `src/nexus/web`, `tests/web` y `scripts`.
2. `core_sha256_compare.txt`: los SHA-256 de los archivos de `nexus.core` son **idénticos** a `b2d6a76`.
3. `generations_compare.txt`: la demostración de dos meses ejecutada con el código `b2d6a76` y con el nuevo produce las **mismas 5 generaciones**, con los **27 archivos idénticos byte a byte** (gen_id determinista por entradas).
4. Las pruebas de cálculo y CORE (`tests/core`, 61) pasan sin modificación.

## 3. Pruebas (`test-results/`)

| Comprobación | Resultado |
|---|---|
| `ruff`, `ruff format`, `mypy --strict`, `lint-imports` | OK; **11 contratos** |
| `pytest -m "not sql"` | **417 passed, 12 skipped, 0 failed**. Los 12 skipped son las pruebas Pilot-20 de datos reales, solo locales |
| Web (`tests/web`) | 11 passed: 9 de antes con textos actualizados + 2 nuevas |
| Demostración de dos meses | 11/11 pasos (`demo.txt`) |
| Loopback | Servidor solo en `127.0.0.1` (`listen.txt`); `0.0.0.0` rechazado (`reject.txt`) |
| **Navegador headless** (`browser.txt`) | **33/33** comprobaciones |

Las 33 comprobaciones en navegador cubren:
- **Ventanas:** 1920, 1366, 820 y 390, sin desbordamiento horizontal de página. A 1366 y 1920, la tabla de la junta completa sin desplazamiento horizontal.
- **Legibilidad:** texto de al menos 11 px.
- **Contraste:** WCAG AA medido en los colores renderizados de títulos, indicadores, avisos, distintivos, tablas, botones y navegación.
- **Teclado:** el primer Tab enfoca "Saltar al contenido", que lleva el foco al contenido; el botón Refresh tiene foco visible.
- **Permisos y filtros:** el lector no ve Refresh ni formularios CS; filtros por ITEM y por cobertura.
- **Refresh:** estado final verificado contra el historial.
- **Customer Service:**
  - formularios separados (textarea, selector, fecha, cantidad y fecha);
  - guardado con confirmación;
  - última nota y estatus visibles;
  - edición simultánea con aviso de conflicto sin pérdida;
  - dato inválido con mensaje claro (400);
  - historial con autor y fecha.
- **Rollback:** pantalla de confirmación y registro como vigente.
- **CSP y consola:** CSP sin `unsafe-inline`; sin errores de consola, JavaScript ni violaciones de CSP.

Defectos encontrados y corregidos durante la verificación:
1. **Botón Refresh con token CSRF vacío.** Las macros Jinja importadas no veían el contexto. La seguridad lo rechazaba (403), pero el botón quedaba inutilizable. Se corrigió y una prueba nueva exige el token de sesión en **todo** formulario POST de todas las pantallas.
2. **Claves de diccionario tapadas por métodos.** `values` e `items` resolvían a los métodos del diccionario en Jinja, y "Sin cubrir > 0" se mostraba en verde.
3. **Barra del ITEM vacía.** Una variable tenía el mismo nombre que una macro.
4. **Desbordamiento de 48 px** en el detalle del ITEM a 390 px.

## 4. Capturas

| Contenido | Ubicación |
|---|---|
| Antes y después | `before/`, `after/` y `COMPARISON.md`: junta en 4 tamaños, detalle de ITEM y CS, Refresh, Historial, historial CS |
| Recorrido de demostración (febrero, sintético) | `screens/`: resumen en 4 tamaños, Customer Service, conflicto CS, historial CS, estado del Refresh, historial y confirmación de rollback |

## 5. JavaScript

**Ninguno.**
- La recarga del estado del Refresh usa `<meta http-equiv="refresh">` mientras el proceso sigue en curso.
- Las secciones técnicas usan `<details>` nativo.
- Las barras son SVG con atributos de geometría, sin `style`.

## 6. Demostración navegable

```bash
uv sync --frozen
```

```bash
bash scripts/core_a_demo.sh /tmp/core_ui_demo
```

Para navegar a mano después de la demostración:

```bash
NEXUS_WEB_SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(32))') uv run nexus web --data /tmp/core_ui_demo/data --simulate-start 2031-02-10T21:00:00Z
```

Abrir http://127.0.0.1:8765. Usuarios sintéticos: `lector`, `editorcs`, `editorcs2`, `refresher`, `admin`, con contraseña `<usuario>-demo`.

## 7. Rollback

| Qué | Cómo |
|---|---|
| Solo la interfaz | `git revert <commit de esta intervención>`, o `git checkout b2d6a76 -- src/nexus/web tests/web scripts`. El CORE, sus datos, el Frozen, las publicaciones y las notas CS no dependen de la presentación y no cambian |
| Publicaciones | Sin cambios: `nexus core rollback` o pantalla de administrador |

## 8. Limitaciones

- Contraste y foco verificados de forma automática. No hubo revisión con lector de pantalla ni con usuarios reales; eso corresponde al piloto con la junta.
- Fechas en UTC. La zona horaria de la planta está pendiente de definir.
- Se mantienen las garantías no certificadas del Hito A: SQL Server, seguridad corporativa y fuentes reales.
