# E1-A-R1: análisis de causa raíz (NX-E1A-01 y NX-E1A-02)

Alcance: solo E1-A. Sin SQL, sin infraestructura corporativa, sin E1-B ni E1-C.

## Commits de la entrega R1

| Commit | Propósito | Estado esperado |
|---|---|---|
| `268d138` | **Reproducción.** Sondas TOCTOU y un único punto de instrumentación (`before_open_read`). La protección **no** se modifica. CI de Windows sin `continue-on-error` y con resumen de fallos sin truncar | **ROJO** a propósito |
| `f0d03cf` | Corrección TOCTOU y causas raíz de Windows | Linux verde |
| `17b9d72` | Corrige la ruta de `sha256sum -c` en el CI (error introducido en `268d138`/`f0d03cf`) | Linux verde, Windows 1 fallo (ver §1.6) |
| `f01e8ac` | Reintento acotado del bloqueo de directorios en Windows (`WinError 32`) | Verde, pero **no aceptado**: el hilo atacante había muerto (§1.6) |
| `aa7962c` | La sonda concurrente no puede debilitarse en silencio (atacante persistente, ≥ 300 intentos; los avisos de hilos pasan a ser error) | Windows: 1 fallo cerrado no clasificado (`WinError 5`) |
| `cada514` | Clasificación restringida de los rechazos cerrados de Windows (`winerror` 5 y 32) | Verde en Linux y Windows |
| `e401cf6` | #8: la lectura en Windows tolera handles exclusivos transitorios (reintento acotado) y se añade una prueba con un handle exclusivo real | **Verde en Linux y Windows (código final)** |
| commit de evidencia | Este documento, logs, manifiesto e informe | — |

## 1. NX-E1A-01: fallos en Windows (run `42061556595`, job `127364324413`)

### 1.1 Obtención de los logs originales

- La descarga del ZIP de logs (`results-receiver.actions.githubusercontent.com`) está **bloqueada por el proxy** del entorno (HTTP 403).
- Los logs se obtuvieron con la API de GitHub (`get_job_logs`), que devuelve como máximo **las últimas 5000 líneas**. Copia literal: `ci/r0-windows-job-127364324413-tail5000.log`.
- En esa ventana aparecen el resumen `7 failed, 199 passed, 6 skipped` y las **4 últimas** líneas `FAILED`. Las otras 3 líneas `FAILED` quedan antes, ocultas por un diff de varios miles de líneas generado por la prueba de regeneración de fixtures (5000 filas comparadas byte a byte).
- Para obtener la lista completa sin truncar, el commit de reproducción `268d138` ejecutó en Windows el **mismo código de producción** con `--tb=line` (ver §1.5).

### 1.2 Fallos y causas raíz

| # | Prueba | Síntoma en Windows | Causa raíz | Corrección (sin debilitar criterios) |
|---|---|---|---|---|
| 1 | `test_fixtures_regenerate_byte_identical` | Diff `\r\n` frente a `\n` | Git en el runner tiene `core.autocrlf=true` (registrado: `C:/Program Files/Git/etc/gitconfig true`). El checkout convertía LF→CRLF en los fixtures | `.gitattributes`: `* text=auto eol=lf` y `tests/fixtures/** -text`. Paso de CI `sha256sum -c` tras el checkout de Windows |
| 2 | `test_committed_fixtures_match_sha256sums` | SHA-256 `95b63a63…` en lugar de `0b7e48ec…` | Misma causa. **Reproducido en Linux** con `git -c core.autocrlf=true clone`, que da exactamente `95b63a63…` | Igual que #1. Verificado con una clonación `autocrlf=true` (`local/crlf/01_fixture_bytes.txt`) |
| 3 | `test_unsupported_filesystem_fails_closed` | `PermissionError [WinError 5]` al borrar `staging\*.tmp` | **(a) Producción:** en Windows un archivo con atributo de solo lectura no se puede borrar, y el temporal se marca como solo lectura antes de publicarse. **(b) Prueba:** hacía `monkeypatch IS_WINDOWS=False` (simulaba POSIX) y la limpieza decidía por esa bandera, no por el SO real | (a) `remove_own_temp` (Windows) quita el atributo solo si `st_nlink == 1`, así que nunca toca un objeto publicado. (b) Ninguna prueba simula otra plataforma: se rompe la primitiva **real** (`os.link` en POSIX, `os.rename` en Windows) |
| 4 | `test_other_link_errors_propagate` | `assert 13 == 28` | El `WinError 5` (errno 13) de la limpieza **ocultaba** el error original `ENOSPC` (28) | Igual que #3. La prueba verifica que el error original se propaga |
| 5 | `test_missing_snapshot_not_reproducible` | `WinError 5` en `shutil.rmtree` sobre `manifest.json` | La prueba simula una purga de operador con `shutil.rmtree`, que en Windows no puede borrar archivos de solo lectura | Helper `force_rmtree` (`conftest.py`) que quita el atributo, como haría un operador. El código de producción no borra objetos |
| 6 | `test_missing_snapshot_is_not_reproducible_without_partial_result` (**observado** en `268d138`) | `WinError 5` en `content.bin` | Mismo `shutil.rmtree` sobre objetos de solo lectura que #5 | `force_rmtree` |
| 7 | `test_object_put_crash_leaves_absent_or_valid[before_verify]` (**observado** en `268d138`) | `assert (0 == 1)` | La prueba esperaba 1 temporal tras una caída posterior a la publicación, pero en Windows `os.rename` **mueve** el temporal | La expectativa depende de la primitiva real: 0 temporales en Windows y 1 en POSIX (`os.link`). Se mantiene "nunca referenciado" |

### 1.3 Pruebas que simulaban POSIX en Windows

`test_windows_code_path_uses_non_replacing_rename` sustituía `os.rename` por una imitación con semántica Windows *en Linux*, y dos pruebas forzaban `IS_WINDOWS=False` *en Windows*. Las dos prácticas se eliminaron:

- La constante `IS_WINDOWS` ya no se modifica en ninguna prueba.
- Las ramas de Windows se ejecutan únicamente en el job de Windows, sobre NTFS real.

### 1.4 Omisiones (skips)

- **R0:** 6 omisiones en Windows.
- **R1:** 5. `test_fsync_dir_posix` pasó a ser `test_fsync_dir_by_platform`, que se ejecuta en ambas plataformas con la expectativa propia de cada una.
- No se añadió **ningún** skip ni xfail nuevo. Las 5 restantes necesitan crear symlinks POSIX, y su equivalente en Windows (junctions) lo cubren las sondas TOCTOU, que sí se ejecutan en Windows.

### 1.5 Confirmación en Windows con el código original y defecto adicional (#8)

El run de reproducción `268d138` (código de producción de R0) se canceló a los 35 minutos. pytest seguía calculando con `difflib` el diff del fixture CRLF de 5000 filas, que es también la causa de los 14,5 minutos de R0. Tras la cancelación, su log quedó disponible: `ci/r1-repro-268d138-windows-job-127381167198-extract.log`.

- **#6 y #7 se observan** en Windows, con los mismos síntomas analizados arriba.
- **NX-E1A-02 se reproduce también en Windows:** archivos creados fuera en 2 variantes, lectura del archivo externo y 9 lecturas concurrentes escapadas.
- **#8 (nuevo, defecto de producto en Windows):** `test_same_content_concurrent_writers_all_succeed[2]` y `[4]`.
  - **Síntoma:** el proceso perdedor recibe `FileExistsError` (correcto) y, al verificar el objeto recién publicado por el ganador, `PermissionError [Errno 13]`, de modo que `put` falla en falso en lugar de ser idempotente.
  - **Causa:** violación de compartición transitoria sobre un archivo recién creado (antivirus o indexador, o el cierre del `MoveFileEx` del otro proceso). La CRT de Windows la traduce a `EACCES`. Es intermitente: en R0 pasó.
  - **Corrección (`e401cf6`):** la apertura de lectura en Windows reintenta de forma acotada (5 × 50 ms) ante `PermissionError`. Si persiste, el error se propaga (fallo cerrado).
  - **Prueba:** `test_read_tolerates_transient_exclusive_handle` sujeta el objeto con `CreateFileW(GENERIC_READ, share=0)`, un handle exclusivo **real**, durante 120 ms. Pasa en Windows (CI de `e401cf6`).

### 1.6 Fallos observados durante la corrección (`17b9d72`, `f01e8ac`, `aa7962c`)

- **Resultado:** 220 pasadas, 5 omitidas y 1 fallo en `test_concurrent_reads_during_parent_swaps` por `PermissionError [WinError 32]`.
- **Causa:** el renombrado del atacante y el `CreateFileW` sin `FILE_SHARE_DELETE` competían por el directorio. Es un **fallo cerrado, sin escape**, pero la sonda no lo clasificaba.
- **Corrección (`f01e8ac`):**
  - Reintento acotado (3 × 50 ms) ante violaciones de compartición transitorias. Si persisten, `PermissionError`, y nunca se continúa sin bloqueo.
  - La sonda cuenta `WinError 32` como `lock_contention`. Cualquier otro error sigue haciendo fallar la prueba.
- **`f01e8ac` pasó, pero no se aceptó.** El aviso `PytestUnhandledThreadExceptionWarning` reveló que el hilo atacante había muerto (`WinError 32` al borrar la junction que el lector mantenía abierta). Una prueba verde con el ataque detenido no es evidencia.
- **`aa7962c`:**
  - El atacante persiste ante contención y se exigen al menos 300 intentos.
  - Cualquier excepción del atacante hace fallar la prueba.
  - En toda la suite, los avisos de hilos no capturados pasan a ser **error**.
  - Resultado en Windows: 1 fallo cerrado no clasificado. Era `WinError 5` (`ERROR_ACCESS_DENIED`) al abrir la junction en estado *delete-pending*.
- **`cada514`:** la sonda acepta como rechazo cerrado **solo** `winerror` 5 y 32. CI verde en ambas plataformas, con 3965 sustituciones reales y 0 escapes.
- Se verificó además que la sonda endurecida **sigue fallando** contra la protección original (5 de 6 en rojo, 74 escapes).

## 2. NX-E1A-02: carrera TOCTOU en el ObjectStore

### 2.1 Defecto

La protección de R0 validaba la ruta con `_path()` (`lstat` de cada componente) y después operaba por **nombre completo** (`os.link(src, dst)`, `os.open(path)`). `follow_symlinks=False` y `O_NOFOLLOW` solo afectan al **último** componente: los directorios padre se resuelven siguiendo enlaces. Entre la validación y la operación, un actor local podía sustituir un directorio intermedio por un enlace hacia un directorio externo.

### 2.2 Reproducción con la protección original (`268d138`; log `local/toctou_before_fix.txt`)

| Sonda | Resultado con R0 |
|---|---|
| `put("a/x.bin")` con sustitución de `a` en `before_publish` | **put devuelve éxito** y el archivo se crea en `outside/x.bin` (FUERA) |
| `put("a/b/x.bin")` con sustitución de `a/b` | **Éxito con el archivo fuera** |
| `put("a/b/x.bin")` con sustitución de `a` | `FileNotFoundError` (no escapa porque `b` no existe en el destino externo) |
| `get` con sustitución en `before_open_read` | Abre el archivo externo: `hash no coincide (actual=e37b89…)`, que es el hash del contenido del atacante |
| Lecturas concurrentes con 300 sustituciones | **Entre 30 y 100 lecturas abrieron el archivo externo** según la ejecución |

### 2.3 Corrección (`f0d03cf`)

- **POSIX:**
  - La ruta se recorre con descriptores (`openat(O_DIRECTORY|O_NOFOLLOW)`).
  - Se publica con `linkat(src_dir_fd, dst_dir_fd)` sin seguir enlaces.
  - Se lee con `openat(O_NOFOLLOW)` relativo al padre.
  - Un renombrado posterior no cambia el inodo al que apunta el descriptor, que está dentro del almacén.
- **Windows:**
  - Cada directorio de la ruta se mantiene abierto con `CreateFileW(FILE_LIST_DIRECTORY, FILE_SHARE_READ|FILE_SHARE_WRITE, FILE_FLAG_OPEN_REPARSE_POINT)`.
  - Sin `FILE_SHARE_DELETE`: ningún proceso puede renombrar ni borrar el directorio durante la operación.
  - Las junctions y los symlinks se rechazan.
- **Ambas plataformas:** la identidad `(st_dev, st_ino)` se compara antes y después de abrir el objeto.
- **Arquitectura:** una prueba AST impide que el ObjectStore use E/S por ruta completa.

### 2.4 Resultado tras la corrección

| Sonda | Linux (local y CI) | Windows (CI) |
|---|---|---|
| Escritura con sustitución (3 variantes) | Nunca hay archivo fuera. El objeto queda dentro, en el directorio original renombrado. La clave deja de ser legible a través del enlace (`IntegrityError`) | El renombrado del atacante se bloquea; nunca hay archivo fuera |
| Lectura con sustitución | Devuelve el contenido real o rechaza el enlace; nunca abre el archivo externo | Igual |
| Concurrente (20 × 300 sustituciones) | **0 escapes** en todas las repeticiones (`local/*/13_toctou_x20.txt`) | 0 escapes (ver `ci/`) |
| Objeto sustituido entre comprobación y apertura | `IntegrityError` ("cambió") | Igual |

### 2.5 Límites declarados

1. Los ancestros de la raíz del almacén son de confianza.
2. En Windows el bloqueo dura lo que dura la operación. Entre operaciones, una sustitución se detecta en la siguiente operación (punto de reanálisis) y falla cerrado.
3. Ninguna de estas pruebas certifica durabilidad física ni resistencia a cortes eléctricos.
