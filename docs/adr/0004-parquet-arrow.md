# ADR-0004 — Parquet/Arrow para datos tabulares

- **Estado:** Aceptado para E1-A (P-ADR-08).
- **Decisión:** Los artefactos tabulares se guardan en Parquet escrito con `pyarrow` y opciones fijas:
  - `zstd` nivel 3, `row_group_size` 65536, sin diccionario, estadísticas activadas, página v2, formato 2.6, `store_schema`.
  - Sin metadatos de ejecución en el archivo (verificado en A9).
  - Las opciones forman parte del `input_manifest`.
- **Justificación:** Formato columnar, con esquema explícito y determinista con parámetros fijos. Es la única dependencia de ejecución.
- **Riesgo:** los metadatos `created_by` incluyen la versión de la librería, de modo que una actualización rompe L3 pero no L2. Esto es lo esperado y se documenta en ADR-0003.
