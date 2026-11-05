# PULSO NEXUS — Informe de entrega E1-A-R1

| Campo | Valor |
|---|---|
| Alcance | **Solo E1-A** (corrección de NX-E1A-01 y NX-E1A-02). Sin SQL, sin E1-B ni E1-C, sin infraestructura corporativa, solo datos sintéticos |
| Rama | `claude/blissful-feynman-obiif0` (repositorio privado) |
| Commit de código auditado | **`e401cf650488cbd3ea784dca603f31db42fa383e`** |
| Commits de R1 | `268d138` (reproducción, rojo a propósito) → `f0d03cf` → `17b9d72` → `f01e8ac` → `aa7962c` → `cada514` → `e401cf6` → commit de evidencia (contiene este archivo) |
| CI de referencia | [run 42072985539](https://github.com/mcrisnidh/pulso-nexus/actions/runs/42072985539). **Linux ✅ y Windows ✅** (Windows ya es bloqueante) |
| Estado propuesto | **GO de E1-A-R1 sujeto a revisión independiente.** Los 14 criterios A1–A14 se cumplen en Linux y la suite completa pasa en Windows. Esto **no** certifica Windows/NTFS en el almacenamiento corporativo ni la resistencia a cortes eléctricos (§5) |

## 1. Corrección del informe anterior

En la entrega E1-A dije que el job de Windows "todavía no ha producido ninguna ejecución visible". Era **incorrecto**: el run `42061556595` ya se había ejecutado y terminó con `7 failed`, oculto por `continue-on-error`. En R1 ese job es bloqueante.

## 2. NX-E1A-01: Windows

Análisis completo en `ROOT_CAUSE.md` y ejecuciones en `CI_RESULTS.md`.

| Causa raíz | Corrección |
|---|---|
| `core.autocrlf=true` en el runner cambiaba los bytes de los fixtures (SHA-256 `95b63a63…`). Reproducido en Linux | `.gitattributes`: `* text=auto eol=lf` y `tests/fixtures/** -text`. Paso `sha256sum -c` en CI tras el checkout de Windows. Verificado también en una clonación local con `autocrlf=true` |
| `WinError 5`: el temporal de staging marcado como solo lectura no se podía borrar en Windows. El error ocultaba además el error original (ENOSPC) | `remove_own_temp` (Windows): quita el atributo solo si `st_nlink == 1`. Nunca desprotege un objeto publicado (prueba dedicada) |
| Pruebas que simulaban POSIX en Windows (`IS_WINDOWS=False`) y Windows en Linux | Eliminadas. Cada plataforma rompe y prueba su primitiva **real** |
| Expectativa de temporales tras `os.rename` | La expectativa depende de la primitiva real |
| `shutil.rmtree` de objetos de solo lectura en pruebas | `force_rmtree`: purga de operador que quita el atributo |

| **#8 (nuevo):** en Windows, el escritor concurrente perdedor recibía `PermissionError` al verificar un objeto recién publicado (violación de compartición transitoria) | Lectura con reintento acotado en Windows. Prueba con un handle exclusivo real (`CreateFileW`, `share=0`) |

- **Omisiones en Windows:** 6 → **5**. No se añadió ningún skip ni xfail.
- **Fallos de R0:** los 7 están **observados**, 5 en el log de R0 y #6 y #7 en el run de reproducción `268d138`. Ese run, con el código original en Windows, reveló además #8, que en R0 pasó por azar.

## 3. NX-E1A-02: TOCTOU

| Fase | Resultado |
|---|---|
| Reproducción con la protección original (`268d138`, `local/toctou_before_fix.txt`) | 4 sondas en rojo. `put` **devolvía éxito con el archivo creado FUERA** del almacén. `get` abría el archivo externo. Entre 30 y 100 lecturas concurrentes escapaban |
| Corrección (`f0d03cf`) | POSIX: `openat`/`linkat` relativos a descriptores con `O_NOFOLLOW`. Windows: directorios bloqueados con `CreateFileW` sin `FILE_SHARE_DELETE` y rechazo de puntos de reanálisis. Ambas plataformas: identidad `(st_dev, st_ino)` antes y después de abrir |
| Linux tras la corrección | 6/6 sondas. **0 escapes en 20 × 300** sustituciones concurrentes, en ambas clonaciones (`local/lf|crlf/13_toctou_x20.txt`) |
| Windows con la protección original (`268d138`) | Reproducido: archivos fuera en 2 variantes, lectura externa y **9 escapes concurrentes** |
| Windows tras la corrección (CI) | 6/6 sondas. Renombrado del atacante bloqueado en las sondas deterministas. Variante concurrente: **2210 sustituciones reales, 0 escapes** (`e401cf6`; 3965 en `cada514`) |
| Control de la sonda | La sonda endurecida falla 5/6 contra la protección original (74 escapes). No es una prueba que pase siempre |

## 4. Criterios A1–A14

Resultados de `e401cf6` en dos clonaciones limpias: `local/lf` con `core.autocrlf=false` y `local/crlf` con `core.autocrlf=true`. En ambas, los 15 pasos de `scripts/r1_evidence.sh` terminaron con `exit 0`.

| ID | Resultado | Evidencia |
|---|---|---|
| A1 | **PASS** | `02_uv_sync`, `03_ruff`, `04_mypy`, `05_lint_imports` (6 contratos *kept*) |
| A2 | **PASS** | `test_boundaries.py`. Nueva prueba AST: el ObjectStore no hace E/S por ruta completa |
| A3 | **PASS** | `test_forbidden_deps.py` |
| A4 | **PASS** | `test_object_store.py`, `test_concurrency.py`, `test_tampering.py`, `test_toctou.py`, `test_no_replace.py` |
| A5 | **PASS** | `test_crash_injection.py` (expectativa por primitiva real) |
| A6 | **PASS** | `test_snapshots.py`, `test_manifest_hygiene.py` |
| A7 | **PASS** | `tests/contract/*` |
| A8 | **PASS** | `test_hashing.py` |
| A9 | **PASS** | `test_run_twice.py`, demo |
| A10 | **PASS** (misma observación de locale que en E1-A) | `09_*`, `10_*`, `test_threads_tz.py` |
| A11 | **PASS** | `test_replay_levels.py` |
| A12 | **PASS** | `test_comparator_mutations` |
| A13 | **PASS** | `07_coverage_by_package`: hashing 100 %, **storage 100 %**, snapshots 100 %, pipeline 99 %, engines 100 %. Total 99 %. El código exclusivo de Windows está excluido del umbral Linux de forma declarada y se ejecuta en el job de Windows |
| A14 | **PASS** | `test_fixtures_and_network.py`, `01_fixture_bytes` |

- **Linux:** 227 pruebas, 227 superadas, 0 omitidas (`local/*/junit.xml`).
- **Windows (CI):** 222 superadas y 5 omitidas, todas `posix_only` con symlinks.

## 5. Limitaciones y riesgos residuales

1. **Ejecución no es certificación.** Windows pasa en el runner de GitHub sobre NTFS local. No se ha probado en el almacenamiento corporativo ni sobre SMB/NAS (P5).
2. **Durabilidad física:**
   - Ni en Linux ni en Windows se ha demostrado resistencia a cortes eléctricos.
   - En Windows no existe `fsync` de directorio, así que la durabilidad del rename en NTFS sigue **pendiente de certificar**.
3. **Defensa TOCTOU en Windows:**
   - El bloqueo dura lo que dura cada operación. Una sustitución entre operaciones se detecta en la siguiente y falla cerrado (`IntegrityError`, `PermissionError` 5/32), sin escapes.
   - Bajo ataque activo, las operaciones pueden fallar cerrado de forma repetida: es disponibilidad, no integridad.
4. Los **ancestros de la raíz** del almacén son de confianza.
5. **Administrador privilegiado:** sin cambios respecto a E1-A. Se detecta, no se previene. La firma con custodia externa corresponde a E1-C (P16).
6. **Logs de CI:** la descarga completa está bloqueada por el proxy. Solo hay acceso a las últimas 5000 líneas por job.
7. **Contención en Windows:** el reintento de lectura ante `PermissionError` es acotado (≈ 250 ms). Bajo contención sostenida (antivirus agresivo), la operación falla cerrado; es disponibilidad, no integridad.
8. **Locale `es_MX.UTF-8`:** sigue sin estar disponible en el sandbox.

## 6. Reproducción desde una clonación limpia

```bash
git clone --branch claude/blissful-feynman-obiif0 https://github.com/mcrisnidh/pulso-nexus.git && cd pulso-nexus
```
```bash
git checkout e401cf650488cbd3ea784dca603f31db42fa383e
```
```bash
uv sync --frozen && uv run pytest --cov=nexus --cov-branch --cov-fail-under=90
```
```bash
uv run pytest tests/adversarial/test_toctou.py -v -s
```
```bash
bash scripts/r1_evidence.sh "$PWD" HEAD /tmp/ev /tmp/clone true
```
```bash
sha256sum -c docs/evidence/E1-A/MANIFEST.sha256
```

El último comando se ejecuta sobre el commit de evidencia (la punta de la rama), no sobre `e401cf6`.
