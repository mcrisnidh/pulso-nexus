"""PULSO NEXUS — planificación funcional por ITEM (Pilot-20 y escalado).

Capas (sin reglas por número de pieza):

- ``model``: hechos con valor, estado de certeza, procedencia y limitaciones.
- ``rules``: reglas comerciales parametrizadas, cargadas de un archivo versionado.
- ``adapters``: lectores de fuentes con identidad exacta, línea, SHA-256, corte y granularidad.
- ``engine``: etapas puras Demanda → FG → Cobertura → WO → Operaciones → Avance → Capacidad →
  Embarques → Faltante final.
- ``runner``: ejecución offline determinista sobre un directorio de datos local.

Los datos reales NUNCA se versionan: el repositorio solo contiene código, esquemas y fixtures
sintéticos.
"""
