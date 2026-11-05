# ADR-0009 — Reconciliación SQL ↔ ObjectStore de solo informe

- **Estado:** Propuesto (candidato E1-B). Pendiente de revisión independiente.
- **Decisión:** `nexus reconcile` (`src/nexus/pipeline/reconcile.py`) nunca borra, mueve, altera ni registra archivos.
  - **R-A (SQL restaurado a un punto anterior):** los archivos sin fila se informan como `UNREGISTERED_FILE`, con la etiqueta `COMPLETE_STORE_RUN` o `INCOMPLETE_RUN`.
  - **R-B (archivos ausentes o alterados):** el procedimiento auditado `usp_mark_artifact_state` aplica la transición a `MISSING` o `CORRUPT`, y la generación pasa a `INELIGIBLE`.
  - **R-C (caída entre el archivo durable y la fila):** igual que R-A. Los huérfanos se conservan para análisis humano.
- **Verificación:** B15, con BACKUP/RESTORE real, borrado o alteración de archivos y SIGKILL real. Los hashes del almacén permanecen idénticos. Un mutante que borra huérfanos (B16-M3) debe ser detectado.
