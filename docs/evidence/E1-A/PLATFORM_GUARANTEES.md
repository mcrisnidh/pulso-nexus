# Garantías por plataforma: almacén de objetos (E1-A)

Fuente: `src/nexus/storage/_fs.py`, el único módulo de E1-A con llamadas específicas
del sistema operativo. Leyenda:
- **Demostrada**: hay una prueba automatizada que la ejecuta en este entorno y pasa.
- **Diseñada**: implementada según la semántica documentada de la API, pero no ejecutada
  en esa plataforma.
- **Pendiente**: requiere certificación en el almacenamiento real.

Entorno de demostración: Linux (kernel 6.18), contenedor del sandbox con sistema de
archivos de tipo overlay/ext4, Python 3.12.3 y pyarrow 26.0.1.

| Garantía | Linux / POSIX | Windows / NTFS |
|---|---|---|
| **Publicación sin reemplazo** | **Demostrada.** `os.link` falla con `EEXIST` si el destino existe (`test_concurrency.py`: 8 procesos × 5 rondas con el mismo contenido y con contenidos distintos; exactamente un ganador, el contenido final es el del ganador) | **Diseñada.** `os.rename` → `MoveFileExW` sin `MOVEFILE_REPLACE_EXISTING`, que falla si el destino existe. Solo se ha simulado la **ruta de código** en Linux (`test_windows_code_path_uses_non_replacing_rename`). **Pendiente de certificar en NTFS real.** |
| No usar `os.replace` ni `shutil.move` | **Demostrada** (prueba AST `test_no_replace.py`, independiente de la plataforma) | Igual |
| FS sin enlaces duros | **Demostrada:** falla cerrado (`UnsupportedFilesystemError`), sin degradar a rename | No aplica: en Windows `os.rename` ya es no-reemplazante |
| Exclusividad del temporal | **Demostrada:** `O_CREAT\|O_EXCL\|O_NOFOLLOW` | Diseñada: `O_CREAT\|O_EXCL\|O_BINARY` (Windows no tiene `O_NOFOLLOW`) |
| `fsync` del archivo | Se ejecuta (`os.fsync`). La durabilidad ante corte eléctrico **no** se demuestra (requeriría hardware o una VM con corte real) | Diseñada: `os.fsync` → `FlushFileBuffers`. **Pendiente** |
| `fsync` del directorio | Se ejecuta (`test_fsync_dir_posix`) | **No disponible** en Windows. La durabilidad del rename en NTFS queda **pendiente de certificar** (posible `MOVEFILE_WRITE_THROUGH` vía API nativa en una etapa futura) |
| Caídas en mitad de la escritura | **Demostrada:** `os._exit` en `before_fsync`, `before_publish` y `before_verify`. El resultado es siempre o ningún objeto final o un objeto válido (`test_crash_injection.py`) | Diseñada. No ejecutada en Windows |
| Enlaces simbólicos o junctions en destino o ruta | **Demostrada:** se detectan con `lstat` y se rechazan; el destino del enlace no se toca | Diseñada: se detectan por `FILE_ATTRIBUTE_REPARSE_POINT`. Solo la detección por atributo está simulada en Linux |
| Solo lectura | `chmod 0444` antes de publicar (**disuade, no impide** a root) | Atributo read-only (disuade). Las ACL reales están pendientes (P13) |
| Claves portables | **Demostrada:** minúsculas `[a-z0-9._-]`, sin nombres reservados (`CON`, `NUL`, `COM1`…), sin `:` (ADS), sin punto final, longitud ≤ 180 | Las mismas reglas evitan colisiones por mayúsculas y minúsculas y rutas inválidas en NTFS. **Pendiente** de probar en NTFS real |
| Recursos de red SMB/NAS | **No probado** | **No probado** (P5) |
| Escritor externo malicioso o administrador | **Detectado al leer** (hash), no prevenido | Igual. La prevención y la evidencia externa corresponden a E1-C (P16) |

CI incluye un job `windows-2022` **informativo** (`continue-on-error: true`). Su resultado,
si llega a observarse, se trata como evidencia auxiliar y no como certificación.
