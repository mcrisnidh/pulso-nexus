# E1-A-R1: resultados de CI separados por plataforma

Workflow: `.github/workflows/ci.yml`. Desde `268d138` el job de Windows **no** usa
`continue-on-error`: si falla, el workflow falla.

Los logs se obtuvieron con la API de GitHub (`get_job_logs`), que devuelve como máximo las
últimas 5000 líneas. La descarga del ZIP completo de logs está bloqueada por el proxy del
entorno (HTTP 403). Las líneas citadas abajo son literales de esos logs.

## Resumen por ejecución

| Run | Commit | Linux (`ubuntu-24.04`) | Windows (`windows-2022`) | Notas |
|---|---|---|---|---|
| [42061556595](https://github.com/mcrisnidh/pulso-nexus/actions/runs/42061556595) | `236187e` (R0) | ✅ 212 passed | ❌ `7 failed, 199 passed, 6 skipped` (oculto por `continue-on-error`) | Hallazgo NX-E1A-01. Log: `ci/r0-windows-job-127364324413-tail5000.log` |
| [42066295334](https://github.com/mcrisnidh/pulso-nexus/actions/runs/42066295334) | `268d138` (reproducción) | ❌ falla en el paso `sha256sum` (error de ruta del propio CI, ver `17b9d72`); las pruebas no llegan a ejecutarse | ❌ **Reproducción en Windows con el código original:** `8 failed, 111 passed, 1 skipped` hasta la cancelación (35 min, por `difflib` sobre el fixture CRLF). Incluye #6, #7, #8 y NX-E1A-02 (9 escapes) | Extracto: `ci/r1-repro-268d138-windows-job-127381167198-extract.log` |
| [42068088254](https://github.com/mcrisnidh/pulso-nexus/actions/runs/42068088254) | `f0d03cf` | ❌ mismo error de ruta de `sha256sum` | ❌ mismo error de ruta de `sha256sum` | Error del CI, no del producto |
| [42068614298](https://github.com/mcrisnidh/pulso-nexus/actions/runs/42068614298) | `17b9d72` | ✅ | ❌ `1 failed, 220 passed, 5 skipped` (`WinError 32` en la sonda concurrente) | Bytes de fixtures: `input_v1.csv: OK` |
| [42069556667](https://github.com/mcrisnidh/pulso-nexus/actions/runs/42069556667) | `f01e8ac` | ✅ | ✅ `221 passed, 5 skipped, 1 warning` | **No se acepta como evidencia:** el aviso era el hilo atacante muerto (`PytestUnhandledThreadExceptionWarning`), así que el ataque se detuvo |
| [42070656858](https://github.com/mcrisnidh/pulso-nexus/actions/runs/42070656858) | `aa7962c` | ✅ | ❌ `1 failed, 220 passed, 5 skipped` (`WinError 5`, delete-pending, con el ataque ya sostenido) | Fallo cerrado no clasificado |
| [42071400684](https://github.com/mcrisnidh/pulso-nexus/actions/runs/42071400684) | `cada514` | ✅ | ✅ `221 passed, 5 skipped in 74.46s`, 0 avisos | Verde, pero sin defensa explícita para #8 (detectado después en el log de reproducción) |
| [**42072985539**](https://github.com/mcrisnidh/pulso-nexus/actions/runs/42072985539) | **`e401cf6`** (código final) | ✅ **todos los pasos** | ✅ **`222 passed, 5 skipped in 76.39s`**, 0 avisos | **Ejecución de referencia de R1** |

## Ejecución de referencia (`e401cf6`)

### Linux, job `127405590590`

Todos los pasos `success`:

- bytes de fixtures;
- ruff, mypy `--strict` e import-linter;
- `pytest --cov-fail-under=90`;
- sondas TOCTOU;
- reproducibilidad con 1 y 8 hilos y distintas TZ y locales;
- `scripts/e1a_demo.sh`.

### Windows, job `127405591448`

```
file:C:/Program Files/Git/etc/gitconfig	true          <- core.autocrlf=true en el runner
input_v1.csv: OK
input_v1_invalid.csv: OK                               <- bytes preservados por .gitattributes
================== 222 passed, 5 skipped in 76.39s (0:01:16) ==================
test_publish_never_creates_file_outside_store[a/x.bin-a]   swap={'swapped': False, 'swap_error': "PermissionError(13, 'The process cannot access the file because it is being used by another process')"} PASSED
test_publish_never_creates_file_outside_store[a/b/x.bin-a] swap={'swapped': False, ...} PASSED
test_publish_never_creates_file_outside_store[a/b/x.bin-a/b] swap={'swapped': False, ...} PASSED
test_read_never_opens_file_outside_store                   swap={'swapped': False, ...} PASSED
test_concurrent_reads_during_parent_swaps swaps={'ok': 2210, 'blocked': 323, 'contention': 38} outcomes={'real': 36, 'rejected': 38, 'missing': 223, 'win_denied_5': 0, 'win_denied_32': 3} escapes=0 PASSED
test_final_component_swap_between_check_and_open PASSED
============================== 6 passed in 6.91s ==============================
```

Lectura del log de Windows:

- En las 4 sondas deterministas, el bloqueo de directorios **impide** el renombrado del atacante.
- En la variante concurrente, el atacante consiguió 2210 sustituciones **entre** operaciones del almacén, sin ningún escape (3965 en el run de `cada514`).
- La nueva prueba `test_read_tolerates_transient_exclusive_handle` (#8) se ejecutó en Windows con un handle exclusivo real (`CreateFileW`, `share=0`) y pasó.
- Las 5 omitidas son las pruebas que crean symlinks POSIX. El equivalente en Windows (junctions) lo cubren las sondas TOCTOU.

### Cobertura en Windows (informativa)

- Total del paquete: 96 %.
- `storage/_fs.py`: 60 %, porque en Windows no se ejecutan las ramas POSIX (`openat`/`linkat`).
- En Linux, el código exclusivo de Windows (`if sys.platform == "win32":`) está excluido del umbral A13 de forma declarada en `pyproject.toml`.

## Reproducción de los fallos de R0 en Windows

| # | Prueba | Confirmación |
|---|---|---|
| 1 | `test_fixtures_regenerate_byte_identical` | Log de R0 (diff `\r\n` frente a `\n`). Reproducido en Linux con `autocrlf=true`. En `268d138` la prueba quedó 35 min en `difflib` (de ahí la lentitud de R0) |
| 2 | `test_committed_fixtures_match_sha256sums` | Línea `FAILED` en el log de R0 (`95b63a63…`). Reproducido en Linux |
| 3 | `test_unsupported_filesystem_fails_closed` | Línea `FAILED` en el log de R0 (`WinError 5`) |
| 4 | `test_other_link_errors_propagate` | Línea `FAILED` en el log de R0 (`13 == 28`) |
| 5 | `test_missing_snapshot_not_reproducible` | Línea `FAILED` en el log de R0 (`WinError 5`, `rmtree`) |
| 6 | `test_missing_snapshot_is_not_reproducible_without_partial_result` | **Observado** en `268d138` (`WinError 5` en `content.bin`) |
| 7 | `test_object_put_crash_leaves_absent_or_valid[before_verify]` | **Observado** en `268d138` (`assert (0 == 1)`) |
| 8 | `test_same_content_concurrent_writers_all_succeed[2]` y `[4]` | **Observado** en `268d138`, **no** en R0, porque es intermitente: `WinError 183` y después `PermissionError [Errno 13]` al verificar el objeto recién publicado. **Defecto de producto en Windows**, corregido en `e401cf6` |

En R0 los 7 fallos fueron #1 a #7; #8 pasó por azar en esa ejecución. Las tres líneas `FAILED` que quedaron fuera de la ventana de 5000 líneas del log de R0 corresponden a #1, #6 y #7. Las tres fallan de forma determinista en Windows, y #6 y #7 se observaron en `268d138`.
