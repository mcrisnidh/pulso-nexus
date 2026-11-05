# ADR-0007 — Cola, lease y fencing (contratos C1–C3)

- **Estado:** Propuesto (candidato E1-B). Pendiente de revisión independiente.
- **Decisión:**
  - **C1 (claim exclusivo):** la reclamación es una única sentencia `UPDATE` sobre un CTE `TOP(1)` con las pistas indicadas bajo el marcador `-- CLAIM_HINTS`, más `OUTPUT`. Nunca se usa `SELECT … READPAST` como cola. Las pistas se eligen por conformidad medida en B12 (RCSI ON/OFF × sesión READ COMMITTED/SNAPSHOT). `ROWLOCK` y `READCOMMITTEDLOCK` nunca se combinan; lo verifica una prueba estática.
  - **Lease por ámbito con `fencing_token` creciente:**
    - Los siete procedimientos de escritura del intento validan el token y la vigencia del lease, leyendo con `UPDLOCK, HOLDLOCK` dentro de la transacción (bloques `FENCE:BEGIN/END`).
    - Excepciones y sus controles propios:
      - `enqueue`: clave de idempotencia.
      - `claim`: `UPDATE` atómico.
      - `release_claim` y `acquire_lease`: token de claim.
      - `recover_expired`: actúa solo sobre lo vencido e incrementa el token.
      - `mark_artifact_state`: solo transiciones `PRESENT→MISSING/CORRUPT`, con auditoría.
  - **C2:** al menos una vez por intento, con intentos acotados (`max_attempts`) y backoff con `not_before` según la hora del servidor.
  - **C3:** como máximo una generación sellada por solicitud, garantizada por el índice único filtrado `WHERE sealed_at IS NOT NULL`. Los efectos son idempotentes y se adopta una generación ya sellada.
  - **Tiempos solo de prueba (perfil `e1b-ephemeral`):** lease de 5 s y heartbeat de 1 s.
- **Verificación:** B5–B9, B12 y B14. Los mutantes de B16 (reclamación en dos pasos y ausencia de fencing en cada procedimiento) deben ser detectados.
