# R2: primer probe real en ASMXC1200 (noviembre 2026) y ajuste de lectores

**Entrada:** solo los tres informes saneados `probe_*.json` del commit `dabd81d`.
- Los SHA-256 de los originales fueron idénticos antes y después.
- No se recibió ni se versionó ninguna fila ni ningún archivo real.
- Las formas enmascaradas citadas abajo son las de esos informes: letras → `A`, dígitos → `9`.

## R2-A: OE965 (`R_20261101.TXT`)

| Observado (probe 1) | Interpretación por posiciones | Cambio |
|---|---|---|
| 238 `UNCLASSIFIED_LINE`, todas con forma `blancos … 9999 (fin 82) … 9999 (fin 94) … 9 (fin 106) … 9999 (fin 164)` | Las 4 columnas coinciden con ordenada, abierta, asignada y la columna final de los pedidos. Son **líneas de total**: 236 de ITEM (una por cada una de las 236 cabeceras) y 2 generales, tras los rótulos `*** … ***` | Clases `total_underline`, `item_total`, `grand_total_label` y `grand_total`. Solo se acepta un total con cifras **exactamente** en esas columnas e **inmediatamente después** de su subrayado (si no: `TOTAL_WITHOUT_UNDERLINE`, bloqueante) |
| 3,623 líneas de 164 caracteres | 3,147 pedidos + 236 subrayados + 236 totales + 2 rótulos con subrayado + 2 totales generales = 3,623 | Balance de líneas en el informe (`line_balance`) |
| Pedidos con cola `… AAAA (nombre) … 9999 (fin 164)` | Las 3,796 líneas del Pilot-20 muestran que el nombre ocupa `[124:149]`, hay una marca opcional `*` en la columna 150 y una cifra que termina en 164 | `customer_name` pasa de `[124:164]` a `[124:149]`. La cola se valida (`ORDER_TAIL_UNREADABLE`, bloqueante) y la cifra no se usa |
| Fecha `Date: 11/01/26`, CP850 | Sin cambio | — |

**Conciliación nueva:** cada total de ITEM y cada total general se compara con la suma de los pedidos **leídos**.
- Si no cuadran ordenada o abierta: `TOTAL_MISMATCH`, **bloqueante**. Una línea de pedido perdida o mal leída no puede pasar en silencio.
- Asignada y la columna final no se usan en el contrato: una discrepancia en ellas es advertencia.
- Una cantidad ilegible produce «no verificable», nunca 0.
- Otros casos bloqueantes: ITEM sin total (`ITEM_TOTAL_MISSING`) y pedido después del total (`ORDER_AFTER_ITEM_TOTAL`).

**Equivalencia:** el informe y el sidecar declaran `contract_equivalence: NOT_ESTABLISHED`. La web muestra «OE965: sin equivalencia certificada con 29 OERAS». El identificador `oeras_29` es el contrato lógico, no una prueba de equivalencia.

## R2-B: Portal CAT

**Observado en el probe 1:**
- UTF-16LE con BOM.
- Encabezado en la línea 1 (sin preámbulo).
- `HEADER_INCOMPLETE`: entre 5 y 6 de los 7 campos obligatorios coincidieron.

El informe no indicaba **cuáles** faltaban: el lector terminaba antes de registrar el diagnóstico. Con la evidencia disponible **no se pueden ajustar los alias**: asignar una columna a `ship_date` o `delivery_date` por suposición cambiaría el horizonte de la demanda.

**Cambio en el diagnóstico.** El informe saneado ahora incluye `flags.header_diagnosis`:
- el estado de cada campo obligatorio (`MATCHED`, `MISSING` o `AMBIGUOUS`), su columna y el alias de configuración que coincidió;
- las columnas no reconocidas, solo como forma enmascarada;
- `rows_by_column_count`.

Los **nombres** de columna reales solo aparecen con `--show-headers`. Con un encabezado incompleto **no se interpreta ninguna fila**.

**Sin cambios:**
- identidad exacta de ITEM y homologación exacta;
- `Shipped` vacío se mantiene como desconocido;
- una fecha con paréntesis (`revisada(original)`, con o sin espacio) es `DATE_AMBIGUOUS` y queda vacía (desconocida).

**Pendiente:** el segundo probe con `--show-headers`. Con los nombres reales se agregan los alias exactos en `physical_v1.json`, con su prueba sintética.

## R2-C: IN092U (`INV_STA289.TXT`)

| Observado (probe 1) | Interpretación | Cambio |
|---|---|---|
| `CUT_MISSING`; línea `AAAA:  9:99:99` sin clasificar (13 páginas) y `Date: 99/99/99` en el título | Fecha y hora **separadas** | Corte = `Date: MM/DD/YY` + línea sola `<rótulo>: H:MM:SS`. Ambas deben ser únicas en todas las páginas (`CUT_INCONSISTENT`) |
| 553 líneas de 129 caracteres: `ITEM … descripción … A-99 A 999 999 … 9,999.999 (fin 91) … .99999 (fin 110) … 9,999.99 (fin 129)` | Filas de datos **sin stockroom** | Fila = ITEM `[0:15]` y cifras que terminan exactamente en 91 (cantidad física) y 129 (valor). La última columna del campo queda para el signo negativo final. Una cifra corrida no se acepta |
| 5 líneas `AAAAAAAAA: AA  AAAAA:    9,999,999.999 … 99,999,999.99` en las mismas columnas | Total por stockroom (código de 2 letras) | Las filas anteriores pertenecen a ese stockroom solo si **la suma de sus cantidades es igual al total impreso**. Si no: `GROUP_QTY_TOTAL_MISMATCH`, bloqueante |

Otros casos bloqueantes: filas sin total de grupo, stockroom repetido e `(ITEM, stockroom)` duplicado.

**Sin cambios:**
- un ITEM ausente sigue siendo desconocido («Excludes Zero»);
- los negativos se conservan;
- la regla CORE vigente (`usable_fg_stockrooms = ["FG"]`) deja QH, RM, QF y SA fuera del FG asignable.

## Lo que se mantiene

- C1–C4, el Frozen, el aislamiento del replay y la puerta operativa: sin cambios de código.
- Huella dorada `400e36ce…`.
- Fallo cerrado: una línea con dígitos fuera de las estructuras reconocidas sigue siendo `UNCLASSIFIED_LINE`, bloqueante.

**Pruebas nuevas:**
- `test_core_r2_shapes.py`: instancia **literalmente** cada forma enmascarada de los tres probes y comprueba su clase, y que la misma forma corrida una columna se rechaza.
- `test_core_physical.py`: conciliación OE965 (oe20–oe25), IN092U (in07–in11) y diagnóstico Portal (po20–po22).

## Resultado esperado del segundo probe

| Archivo | Esperado si la interpretación es correcta |
|---|---|
| OE965 | `unclassified` 0. `item_total` 236, `grand_total` 2, `total_underline` 236. `flags.reconciliation.totals_matched` = `totals_checked` en ordenada y abierta. `valid: true` |
| IN092U | `cut_time` 13, `data_row` 553, `group_total` 5. Los 5 grupos con `qty_total_match: true`. `valid: true` |
| Portal | `valid: false` hasta agregar los alias. `flags.header_diagnosis.missing` dice exactamente qué falta |

Si algún total no cuadra, el probe lo informa por columna y por línea **sin valores**. Ese resultado es evidencia de que la semántica del total es distinta y se ajusta con un tercer probe. **No se relaja la regla.**
