# Ensayo nativo en Windows (ASMXC1200): correcciones de portabilidad de pruebas

## Primera corrida nativa

- Commit: `c7c7cfc`.
- Entorno: Python 3.12, `tzdata` 2025.2.
- Resultado: **547 PASS, 17 SKIPPED, 2 FAIL**.
- La corrección CRLF de TZ-18 pasó y se conserva intacta.

## Fallas y corrección

El alcance es exclusivamente de pruebas. No se modificaron:
- lectores;
- reglas;
- Frozen;
- generación;
- almacenamiento;
- publicación.

| Falla | Causa | Corrección |
|---|---|---|
| WIN-01 `test_boundaries.py::test_import_linter_contracts_pass` | Los contratos se cumplen, pero Rich imprime `Δ` con la codificación de consola CP1252 y lanza `UnicodeEncodeError` | El subproceso recibe `PYTHONIOENCODING=utf-8` y `PYTHONUTF8=1`, y su salida se lee con `encoding="utf-8"` y `errors="strict"`. Se conservan los contratos, sus nombres y la verificación `returncode == 0` / sin `BROKEN`. Reproducido en Linux con `PYTHONIOENCODING=cp1252`: `lint-imports` devuelve 1; la prueba corregida pasa incluso con esa variable presente |
| WIN-02 `test_core_refresh.py::test_g6_process_killed_mid_refresh_then_restart_recovers` | `signal.SIGKILL` no existe en Windows | Ver detalle debajo de la tabla |

Detalle de WIN-02:
- **POSIX:** sin cambio. `SIGKILL` y código esperado `-9`.
- **Windows:** `os.kill(pid, SIGTERM)` ejecuta `TerminateProcess`. Es una terminación abrupta real, sin limpieza, con código de salida esperado `15`.
- Se mantienen la muerte del proceso en `after_compute` y en `after_generation`, la verificación de que no hay publicación parcial (`seq == 1`) y la recuperación tras reiniciar (`seq == 2`, embarcado 490).
- No hay `skip` ni excepción controlada.

## Regresión en Linux

| Verificación | Resultado |
|---|---|
| `pytest -m "not sql"` | 554 passed, 12 skipped |
| `ruff` / `ruff format` | sin errores |
| `mypy --strict src` | sin errores |
| import-linter | 14 contratos KEPT, 0 BROKEN |

La rama Windows de WIN-02 solo puede observarse en ASMXC1200: está **pendiente de la nueva corrida nativa**.
