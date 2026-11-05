# ADR-0006 — Metadatos de ejecución en SQL Server (E1-B)

- **Estado:** Propuesto (candidato E1-B). Pendiente de revisión independiente.
- **Contexto:** E0-R2 §2 (R1) y la propuesta E1-B revisada (`4e46eaf`).
- **Decisión:**
  - Los metadatos de cola, intentos, generaciones, artefactos y eventos residen en el esquema `nexus` de SQL Server. Toda escritura pasa por procedimientos almacenados (`src/nexus/metadata/sql/0002_procedures.sql`).
  - Acceso con `pyodbc` y Microsoft ODBC Driver 18, mediante T-SQL explícito y sin ORM. `pyodbc` solo lo importa `nexus.metadata`, de forma diferida. Esto sustituye, a partir de E1-B, la prohibición de `pyodbc` de A3/ADR-0005, que sigue vigente para E1-A congelada (`tests/architecture/test_forbidden_deps.py::test_only_metadata_imports_pyodbc`).
  - Migraciones `.sql` numeradas y consecutivas, con suma de comprobación (`nexus.schema_version`). Las aplica un login migrador. El login productor solo tiene `EXECUTE` y tiene `DENY` sobre DML y DDL directos.
  - `run_event` es solo de inserción: un disparador `INSTEAD OF UPDATE, DELETE` lanza el error 50090, también contra el propietario.
  - Cada procedimiento fija `SET XACT_ABORT ON` y `READ COMMITTED` y usa `SYSUTCDATETIME()`. Ningún parámetro es de tipo fecha u hora.
  - El cliente no usa el pooling del Driver Manager. Ante un error o timeout descarta la conexión, y ante un commit ambiguo consulta el estado antes de reintentar.
- **Verificación:** B3, B10, B11, B13 y `tests/unit/test_sql_static.py`.
