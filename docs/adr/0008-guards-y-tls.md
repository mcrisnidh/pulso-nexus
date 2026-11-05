# ADR-0008 — Guards de entorno y TLS verificado

- **Estado:** Propuesto (candidato E1-B). Pendiente de revisión independiente.
- **Decisión:**
  - **Guard de bootstrap** (antes de crear nada). Exige:
    - Perfil activado explícitamente y nonce válido.
    - Host permitido y no denegado.
    - Edición `Developer Edition (64-bit)`.
    - `@@SERVERNAME` igual a `^nexus-sql$`.
    - Servidor sin bases de usuario.
  - **Guard de runtime** (en cada conexión, antes de escribir). Exige:
    - Host permitido.
    - Nombre de base `^nexus_e1b_[a-z0-9]{8,32}$` y fuera de la lista de denegación del perfil (incluye `PULSO_MAGNUM_STAGE`).
    - Sentinel `nexus.usp_get_environment` con perfil, tipo, nonce y `schema_version`.
  - **TLS:** siempre `Encrypt=yes` y `TrustServerCertificate=no`. El validador rechaza cualquier degradación y no existe ningún interruptor que la permita. En CI se usa una CA efímera por ejecución, de confianza solo en el almacén del runner y fuera del código. La confianza TLS corporativa queda pendiente (P17).
- **Verificación:** B4a, B4b, B4c, `scripts/e1b_tls_probe.py` (sin CA → falla; con CA → cifrado; 127.0.0.1 fuera del SAN → falla) y las pruebas unitarias de `connection` y `guards`.
