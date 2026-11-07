# R2 final: segundo probe real (ASMXC1200) y cierre de formatos Portal CAT e IN092U

**Entrada:**
- Resultado del segundo probe sobre `b813c8b`.
- Diagnóstico y conciliación independiente del equipo: líneas físicas, grupos y conteos, **sin valores**.
- Los SHA-256 de los tres originales permanecen intactos.

| Fuente | Probe 2 | Corrección |
|---|---|---|
| **OE965** | PASS: 3,147 pedidos, 236 ITEM, 238/238 totales conciliados, 0 excepciones | **Sin cambios.** Equivalencia con 29 OERAS: `NOT_ESTABLISHED` |
| **Portal CAT** | `HEADER_INCOMPLETE`: falta solo `item_portal`; el encabezado real es `Item ID` (columna 2); 257 filas de 12 columnas | Se agregó **solo** el alias `Item ID` en `physical_v1.json`. Ninguna otra correspondencia cambió. `Item ID` es la identidad Portal: **no** autoriza la homologación con el ERP, que sigue exigiendo la tabla exacta `APPROVED` (R1) |
| **IN092U** | 546/553 filas; 7 sin clasificar; 2 grupos sin conciliar | Ver detalle debajo de la tabla |

Detalle de IN092U:
- **ITEM con espacios internos** (FG, líneas 32, 33, 81 y 82): campo fijo `[0:15]`, columna 0 no vacía, columna 15 separadora, sin tabuladores ni caracteres de control.
  - Los espacios internos se conservan **literalmente**; solo se quita el relleno derecho.
  - `"A 1"` y `"A  1"` son identidades distintas. No hay fusión ni normalización.
  - Cada caso queda marcado con `ITEM_INTERNAL_SPACES_PRESERVED` (advertencia).
- **Decimal sin entero `.999`** (RM, líneas 488, 551 y 563): gramática estricta. Se admiten entero simple o miles agrupados de 3 en 3, decimal opcional o solo decimal, y signo al inicio o al final, nunca ambos.
  - Se rechazan `..5`, `1.2.3`, `.`, `1,23.000`, `-.5-`, `1.`, `1,2345.0`, `,123.0`, `1 .5` y `.5.5`.
  - Cada campo de cifra debe contener **una sola** cifra que termine exactamente en su columna.
- **Conciliación exigida en el lector**: cantidad **y valor** de cada grupo. Una discrepancia de valor pasa de advertencia a **bloqueante**, porque el probe mostró que los 5 grupos concilian exactamente.

## Indicador de balance

El campo `line_balance.balanced` se **retiró**: podía leerse como PASS aunque hubiera líneas sin clasificar. Ahora hay dos bloques separados en el informe del probe.

**`line_accounting`**: conteo contable de líneas.
- Campos: `all_lines_counted`, `unclassified_lines` y `meaning` («conteo contable de líneas; NO indica que el archivo sea válido»).

**`acceptance`**: veredicto de aceptación.
- `verdict` es `PASS` o `FAIL`.
- `records_accepted` vale 0 si el archivo no es válido.
- También incluye `records_read`, `unclassified_lines`, `blocking_exceptions` y `rule`.

Prueba: `test_st21_line_accounting_is_never_a_pass`.

## Pruebas sintéticas (`tests/core/test_core_r2_final.py`)

| Prueba | Qué cubre |
|---|---|
| rf01 | 553 filas en 13 páginas; grupos **FG 341, QF 1, QH 6, RM 188, SA 17**; los 5 grupos conciliados exactamente en cantidad y valor; 0 líneas sin clasificar |
| rf02 | Los 4 patrones FG con espacios internos, literales y sin fusión |
| rf03 | Los 3 patrones RM con decimal sin entero (cantidad y valor) |
| rf04 | Negativos conservados; QH y RM presentes y no asignables como FG |
| rf05 | 10 decimales mal formados, rechazados |
| rf06 | Cero real impreso, distinto de cantidad ausente (nunca 0) y de campo inválido |
| rf07 | ITEM ambiguos: invade el separador, contiene tabulador o no empieza en la columna 0 |
| rf08 | Duplicado exacto: bloqueante |
| rf09 | Total alterado en cantidad o en valor: bloqueante |
| rf10 | Desplazamiento de columna de un decimal con punto inicial: rechazado |
| rf20–rf24 | Portal: 12 columnas con `Item ID` en la columna 2; **257/257 filas** interpretadas; sin homologación ERP implícita; Shipped vacío y fechas ambiguas como desconocidos; estados conservados; fila corta como excepción explícita (`ROW_SHAPE`); veredicto `acceptance` del probe |

## Resultados en Linux

| Verificación | Resultado |
|---|---|
| `pytest -m "not sql"` | 607 passed, 12 skipped |
| `ruff` / `ruff format` | sin errores |
| `mypy --strict src` | sin errores |
| import-linter | 14 contratos KEPT, 0 BROKEN |

**Sin cambios:**
- C1–C4, el Frozen y el aislamiento del replay;
- la puerta operativa;
- la huella dorada `400e36ce…`;
- el lector OE965 y su conciliación.

## Esperado en el tercer probe

| Archivo | Esperado |
|---|---|
| OE965 | `acceptance.verdict` PASS, igual que en el probe 2 |
| Portal CAT | `acceptance.verdict` PASS; `records_read` 257; `header_diagnosis.missing` vacío |
| IN092U | `acceptance.verdict` PASS; `records_accepted` 553; 5 grupos con `qty_total_match` y `value_total_match` en `true`; `line_accounting.unclassified_lines` 0 |

En el Portal, las advertencias `UNMAPPED_ITEM` (una por fila) son **esperadas** sin tabla de homologación: el Portal se lee, pero `stage` lo pondrá en cuarentena hasta resolver R1.
