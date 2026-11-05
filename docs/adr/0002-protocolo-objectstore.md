# ADR-0002 — Protocolo del almacén de objetos inmutables (sin reemplazo, resistente a TOCTOU)

- **Estado:** Aceptado para E1-A (P-ADR-04, E0-R2 §4.2 pasos 1–3 y §10.1–10.2). Revisado en E1-A-R1 (NX-E1A-01 y NX-E1A-02).

## Decisión

1. **Claves:** POSIX relativas y portables a NTFS. Minúsculas `[a-z0-9._-]`, sin `..`, sin nombres reservados de Windows, sin `:`, sin punto final, longitud ≤ 180.
2. **Sin resolución de rutas por nombre (NX-E1A-02).** Toda operación trabaja sobre directorios ya abiertos:
   - **POSIX:** la ruta se recorre componente a componente con `openat(O_DIRECTORY|O_NOFOLLOW)` desde `objects/` o `staging/`. Se publica con `linkat(src_dir_fd, dst_dir_fd)` sin seguir enlaces y se lee con `openat(O_NOFOLLOW)` relativo al padre. Renombrar un directorio una vez abierto no redirige la operación.
   - **Windows:** cada directorio de la ruta se abre con `CreateFileW(FILE_LIST_DIRECTORY, FILE_SHARE_READ|FILE_SHARE_WRITE, FILE_FLAG_OPEN_REPARSE_POINT)`, es decir, **sin** `FILE_SHARE_DELETE`. Durante la operación nadie puede renombrar ni borrar esos directorios y se rechazan los puntos de reanálisis (junctions o symlinks).
3. **Destino existente:** con el mismo hash, la operación es idempotente y no escribe; con otro hash, `ImmutableConflictError` y no se toca nada.
4. **Staging:** el temporal se crea en `staging/<uuid>.tmp` con `O_CREAT|O_EXCL|O_NOFOLLOW`, se hace `fsync` y se marca como solo lectura **antes** de publicarlo.
5. **Publicación sin reemplazo:**
   - POSIX: `linkat`, que falla si el destino existe.
   - Windows: `os.rename`, que se traduce en `MoveFileExW` sin `REPLACE_EXISTING`.
   - Si un FS POSIX no admite enlaces duros, se lanza `UnsupportedFilesystemError` (falla cerrado).
6. **Tras publicar:** `fsync` del directorio (solo POSIX), relectura con verificación de hash y eliminación del temporal propio. En Windows se quita el atributo de solo lectura **solo** si `st_nlink == 1`.
7. **Lectura:** exige el hash esperado, compara la identidad `(st_dev, st_ino)` antes y después de abrir y falla cerrado (`IntegrityError`).

## Contención física (NX-E1A-03, E1-A-R1b)

- La defensa del punto 2 impide **seguir enlaces**, pero **no** garantiza contención física. En POSIX un descriptor de directorio apunta a un inodo, y si otro proceso mueve el directorio fuera del almacén, `linkat(dst_dir_fd)` escribe fuera. No existe mecanismo de espacio de usuario que lo impida y toda comprobación de ruta conserva una carrera.
- **Control efectivo:** aislamiento por permisos del SO (`ensure_private_dir`). El árbol se crea con modo `0700` y es propiedad del uid efectivo, y no se abre una raíz de otro uid ni una escribible por grupo u otros.
- **Complemento:** no declarar éxito si se detecta la pérdida de contención y retirar el enlace propio. Es detección con carrera residual declarada.
- **Windows:** el bloqueo de la cadena sin `FILE_SHARE_DELETE` impide la relocalización durante la operación.

## Prohibido

`os.replace`, `shutil.move`, `Path.replace` y `Path.rename` en `src/`. Además, el ObjectStore no puede llamar directamente a `os.open`, `os.link`, `os.rename`, `os.unlink`, `open`, `os.stat` ni `os.lstat`. Lo verifica `tests/architecture/test_no_replace.py`.

## Garantías y riesgos

- Ver `docs/evidence/E1-A/PLATFORM_GUARANTEES.md`.
- Los ancestros de la raíz del almacén son de confianza (administración).
- Sin almacenamiento WORM, un administrador puede alterar archivos: se **detecta** al leer, no se previene. La protección frente a administradores privilegiados corresponde a E1-C (P16).
