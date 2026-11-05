# ADR-0003 — Niveles de reproducibilidad L1/L2/L3

- **Estado:** Aceptado para E1-A (P-ADR-05, E0-R2 §5).
- **Decisión:**
  - **L3 (identidad byte a byte):** `content_sha256` idéntico. Exige el mismo entorno y las mismas opciones del escritor Parquet (que forman parte del `input_manifest`).
  - **L2 (reproducibilidad numérica):** `logical_hash` idéntico. Usa una serialización canónica propia (`nexus.logical/1`) que no depende del orden de filas, del orden físico de columnas ni del escritor, y aplica Unicode NFC y un NaN canónico.
  - **L1 (equivalencia lógica):** mismas claves; igualdad exacta para enteros, decimales, cadenas y fechas; coma flotante dentro de la tolerancia absoluta declarada en el contrato.
- **Separación de datos y metadatos:**
  - `input_manifest` (con su `input_digest`) es la identidad de reproducibilidad.
  - `run_record` contiene los metadatos de la corrida (`run_id`, marcas de tiempo, host, hilos, rutas) y queda **excluido** de la equivalencia.
  - `result_digest` cubre solo datos de negocio.
- **Determinismo del motor sintético:**
  - Suma decimal exacta en Arrow.
  - `math.fsum` para coma flotante, con redondeo exacto e independiente del orden.
  - Salida canonicalizada por clave.
  - `as_of` inyectado: los motores no leen el reloj.
- **Condiciones que impiden reproducir:**
  - Un snapshot purgado impide la reproducción.
  - Un cambio de librería, de tzdata o de plataforma pierde L3 y posiblemente L2.
  - E1-A solo puede ejecutar la versión vigente de normalizador y motor: un manifiesto con otras versiones da error de configuración.
