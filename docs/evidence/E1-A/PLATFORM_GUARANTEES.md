# Garantías por plataforma: almacén de objetos (E1-A, revisado en E1-A-R1)

Fuente: `src/nexus/storage/_fs.py`, el único módulo con llamadas específicas del sistema
operativo. Leyenda:
- **Demostrada**: hay una prueba automatizada que la ejecuta en esa plataforma y pasa.
- **Ejecutada en CI**: las pruebas pasan en el runner de GitHub Actions de esa plataforma
  (sistema de archivos local del runner). Ejecución satisfactoria **no** es certificación.
- **Pendiente**: requiere certificación en el almacenamiento real.

Entornos:
- **Linux:** contenedor del sandbox (kernel 6.18) y runner `ubuntu-24.04`.
- **Windows:** runner `windows-2022` (NTFS local). Resultados por ejecución en `docs/evidence/E1-A-R1/ci/`.

| Garantía | Linux / POSIX | Windows / NTFS |
|---|---|---|
| **Publicación sin reemplazo** | **Demostrada.** `linkat` falla con `EEXIST` (`test_concurrency.py`: 8 procesos × 5 rondas; `test_publication_primitive_never_replaces`) | **Ejecutada en CI.** `MoveFileExW` sin `REPLACE_EXISTING`, con las mismas pruebas sobre la primitiva real (ya no simulada). Pendiente de certificar en el almacenamiento corporativo |
| **TOCTOU: directorio intermedio sustituido por un enlace (escritura)** | **Demostrada.** `openat`/`linkat` relativos a descriptores: nunca se crea un archivo fuera (`test_toctou.py`; antes de corregir, 2 de 3 variantes escribían fuera) | **Ejecutada en CI.** Directorios bloqueados sin `FILE_SHARE_DELETE`: el renombrado del atacante falla. Las junctions se rechazan |
| **G1. No seguir symlinks/junctions** (distinta de la contención física) | **Demostrada** (`openat`/`linkat` con `O_NOFOLLOW`) | **Ejecutada en CI** (rechazo de puntos de reanálisis) |
| **G2. Contención física frente a relocalización externa de un directorio abierto (NX-E1A-03)** | **Demostrada contra un principal distinto:** árbol `0700` del uid del almacén; `nobody` recibe `EACCES`. **No se previene** frente al mismo uid o root (se detecta, P16) | **Ejecutada en CI:** relocalización rechazada mientras la cadena está bloqueada. ACL pendientes (P13) |
| **G3. Ningún éxito sin contención** | Detección por identidad del directorio tras publicar y retirada del enlace propio. **Carrera residual** posterior a la comprobación y existencia transitoria fuera (documentadas y probadas) | No necesaria durante el bloqueo |
| **TOCTOU: lectura (determinista y concurrente)** | **Demostrada.** 0 escapes en 20 × 300 sustituciones concurrentes (antes de corregir, entre 30 y 100 lecturas abrían el archivo externo) | **Ejecutada en CI** (mismas sondas, con junction) |
| **Objeto sustituido entre comprobación y apertura** | **Demostrada:** la identidad `(st_dev, st_ino)` difiere y se lanza `IntegrityError` | **Ejecutada en CI** (identificador de archivo NTFS) |
| No usar `os.replace` ni `shutil.move`; ningún acceso por ruta completa en el ObjectStore | **Demostrada** (prueba AST) | Igual |
| FS sin enlaces duros | **Demostrada:** falla cerrado (`UnsupportedFilesystemError`) | No aplica |
| Temporales de solo lectura en staging | Se eliminan; el modo de un objeto publicado no se toca (`test_remove_own_temp_never_touches_published_object`) | **Ejecutada en CI.** Se quita el atributo solo si `st_nlink == 1`. Esta era la causa del `WinError 5` en R0 |
| Objeto recién publicado abierto por otro proceso sin compartir lectura (#8) | No aplica (POSIX no tiene modos de compartición) | **Ejecutada en CI.** Reintento acotado de lectura (5 × 50 ms) con un handle exclusivo real en la prueba. Bajo contención sostenida, falla cerrado |
| `fsync` del archivo | Se ejecuta (`os.fsync`) | Se ejecuta (`FlushFileBuffers`) |
| `fsync` del directorio | Se ejecuta (`test_fsync_dir_by_platform`) | **No disponible.** La durabilidad del rename en NTFS queda **pendiente de certificar** |
| **Resistencia a cortes eléctricos** | **No demostrada** (requiere pruebas de pérdida de energía real o VM con corte) | **No demostrada** |
| Caídas del proceso en mitad de la escritura | **Demostrada** (`os._exit` en 3 puntos y 15 combinaciones en el pipeline) | **Ejecutada en CI.** Tras un rename no queda temporal |
| Bytes de fixtures y manifiestos | LF (`.gitattributes`) | **Ejecutada en CI:** `sha256sum -c` tras el checkout de Windows. Antes de corregir, `autocrlf` cambiaba el SHA-256 |
| Solo lectura | `0444` (disuade, no impide a root) | Atributo read-only (disuade) |
| Recursos de red SMB/NAS | **No probado** (P5) | **No probado** (P5) |
| Escritor externo malicioso o administrador | **Detectado al leer** (hash e identidad), no prevenido | Igual. La prevención y la evidencia externa corresponden a E1-C (P16) |

## Límites declarados de la defensa TOCTOU

1. Los ancestros de la raíz del almacén (`<root>` y superiores) son de confianza.
2. En Windows, el bloqueo dura solo mientras la operación está en curso. Un atacante puede sustituir un directorio entre operaciones; la siguiente operación lo detecta (punto de reanálisis) y falla cerrado.
3. En Windows, `os.open` no tiene `O_NOFOLLOW`. Si el componente final se sustituye por un symlink de archivo (requiere privilegio), la comparación de identidad detecta el cambio y falla cerrado.
