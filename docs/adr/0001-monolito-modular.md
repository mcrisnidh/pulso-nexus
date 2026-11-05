# ADR-0001 — Monolito modular en Python con fronteras verificadas

- **Estado:** Aceptado para E1-A (P-ADR-01).
- **Contexto:** El proyecto es nuevo. La web no puede contener motores, los conectores no pueden contener reglas y los motores operan sobre contratos normalizados.
- **Decisión:** Un único paquete `nexus` (Python 3.12). Las fronteras se verifican en CI con:
  - `import-linter`: capas internas (`pyproject.toml`, `[tool.importlinter]`);
  - un análisis AST (`tests/architecture/test_boundaries.py`) que impide que `engines/` y `normalize/` importen E/S, red, procesos, reloj o aleatoriedad;
  - una prueba que exige que solo `pipeline/` integre motores y normalizadores.
- **Consecuencias:** Hay un solo artefacto desplegable y pocas dependencias. Separar en servicios requeriría un ADR nuevo.
- **Descartado:** microservicios (X7) y orquestador externo (X5).
