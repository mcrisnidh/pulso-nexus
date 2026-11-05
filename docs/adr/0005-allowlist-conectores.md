# ADR-0005 — Allowlist de conectores

- **Estado:** Aceptado para E1-A (P-ADR-07, R6).
- **Decisión:**
  - Todo módulo de `src/nexus/connectors/` (salvo `__init__` y `base`) debe figurar en `src/nexus/connectors/allowlist.toml`, junto con el ADR que lo aprueba.
  - En E1-A el único conector aprobado es `synthetic_file`, que lee fixtures sintéticos locales.
- **Descartado:**
  - SharePoint (X1).
  - Microsoft Graph para datos manuales (X2).
  - Conectores heredados no aprobados (X3).
  - Cualquier conector SQL en E1-A (el SQL corresponde a E1-B y no está autorizado).
- **Verificación:**
  - `tests/architecture/test_manifest_hygiene.py::test_connectors_package_matches_allowlist`.
  - `tests/architecture/test_forbidden_deps.py`: sin `msal`, `msgraph*`, `office365*`, `sharepoint*`, `psycopg*`, `pyodbc`, `sqlalchemy`.
- **Contrato de conectores:** solo transportan bytes. Sin filtrado, sin transformación y sin reglas de negocio. No conservan rutas absolutas ni secretos.
