# ADR-0002 — Protocolo del almacén de objetos inmutables (sin reemplazo)

- **Estado:** Aceptado para E1-A (P-ADR-04, E0-R2 §4.2 pasos 1–3 y §10.1–10.2).
- **Decisión:**
  1. Las claves son POSIX relativas y portables a NTFS: minúsculas `[a-z0-9._-]`, sin `..`, sin nombres reservados de Windows, sin `:`, sin punto final y con longitud ≤ 180.
  2. Si el destino existe: con el mismo hash, la operación es idempotente y no escribe; con otro hash, `ImmutableConflictError` y no se toca nada.
  3. Se escribe en `staging/<uuid>.tmp` con `O_CREAT|O_EXCL|O_NOFOLLOW` y se hace `fsync`.
  4. El temporal se marca como solo lectura **antes** de publicarse.
  5. Publicación sin reemplazo:
     - POSIX: `os.link`, que falla si el destino existe.
     - Windows: `os.rename`, que se traduce en `MoveFileExW` sin `REPLACE_EXISTING` y también falla si el destino existe.
     - Si un FS POSIX no admite enlaces duros, se lanza `UnsupportedFilesystemError`: falla cerrado, sin degradar a un rename que pueda reemplazar.
  6. Se hace `fsync` del directorio (solo POSIX), se relee y se verifica el hash, y se elimina el temporal.
  7. Toda lectura exige el hash esperado, no sigue enlaces y falla cerrado (`IntegrityError`).
- **Prohibido:** `os.replace`, `shutil.move` y `Path.replace` o `Path.rename` en `src/`. Lo verifica `tests/architecture/test_no_replace.py`.
- **Garantías por plataforma:** ver `docs/evidence/E1-A/PLATFORM_GUARANTEES.md`.
- **Riesgo residual:** sin almacenamiento WORM, un administrador del FS puede alterar archivos. La alteración se **detecta** al leer, pero no se previene. La protección frente a administradores privilegiados es de E1-C (firma y anclaje externo, P16).
