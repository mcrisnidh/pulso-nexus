# ADR-0011 — Contratos de fuentes y estado durable de CORE v1.0 (Hito A)

## Contratos

Archivo: `src/nexus/core/layouts/core_v1.json`. Las cuatro fuentes son:

| Fuente | Instante | Notas |
|---|---|---|
| 29 OERAS | `extraction_ts` | Frozen = primer extracto válido del mes; los siguientes son Current |
| Portal CAT | `capture_ts` | Primera captura cronológica válida del mes |
| Inventario FG | `cut_ts` | Stockroom, física y reservada opcional |
| Embarques físicos | `cut_ts` | `movement_id` único, tipos SHIP/REVERSAL, UOM EA |

- El formato físico es CSV **sintético** (`PROVISIONAL_PENDING_REAL_SAMPLE`). Se contrastará con ejemplares reales autorizados (p. ej., `R_YYYYMMDD.TXT` de PULSO Mark III) en el Hito B.
- OE965 y la facturación **no** son fuentes CORE.

## Estado durable

Mientras no exista una base aislada de NEXUS, el estado se guarda en el ObjectStore de E1-A: staging, fsync, enlace sin reemplazo y relectura verificada.

| Estado | Ubicación | Regla |
|---|---|---|
| Frozen | `frozen/<canal>/<mes>/<fuente>.json` | La primera escritura gana |
| Generaciones | `gen/<canal>/<id>/…` | `id` determinista por entradas |
| Publicación | `pub/<canal>/<seq>.json` | CAS sobre `seq`, cadena de hash |
| Eventos | `events/…` | Historial de Refresh, fallos y rollback |

**Notas CS:** almacén **separado** (`cs/`), solo anexar, con versión N+1 por enlace sin reemplazo (concurrencia optimista).

**Refresh:** usa el worker de E1-B (cola idempotente, lease y fencing en memoria por proceso) con un ejecutor enchufable.

**Riesgos declarados:**
- Entre procesos web distintos no hay lease compartido. La publicación CAS impide la doble publicación, pero dos procesos pueden calcular a la vez (efecto idempotente).
- La misma semántica sobre SQL Server aislado está **pendiente** y no se certifica con esta implementación.
