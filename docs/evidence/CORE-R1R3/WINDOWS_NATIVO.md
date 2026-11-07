# Cierre nativo en Windows (ASMXC1200): R1/R3, Frozen definitivo, `stage()` y `RefreshService`

**Referencia técnica:** `d98e4d3`. Este documento y `scripts/core_win_native_check.py` se añaden en un commit posterior que **no modifica** `src/`, `tests/` ni `pyproject.toml`. El verificador lo comprueba: `code_identical_to_reference`.

**Alcance:**
- Solo datos sintéticos y directorios temporales bajo el laboratorio.
- Sin SQL corporativo, sin importaciones productivas, sin despliegues y sin publicación real.
- Los orígenes reales de noviembre **no** se tocan.

## 1. Qué verifica (un solo comando)

| Bloque | Contenido | Criterio PASS |
|---|---|---|
| **A. Entorno** | Commit; `src/`, `tests/` y `pyproject.toml` idénticos a `d98e4d3`; árbol limpio; `uv.lock` sha256 `7d6889a8…`; Python 3.12; `tzdata` presente; API `Path.is_junction` | Todo verdadero |
| **B. Suites** | `pytest -m "not sql"` completa: 455 originales, batería R1/R3 (74), controles puros (34), T1–T5, huella dorada, portabilidad. Extrae los 11 casos clave (ver detalle debajo de esta tabla) | 0 fallos y 0 errores; los 11 casos clave en PASS; el repositorio no cambia por las pruebas |
| **C. Aislamiento nativo** | **Junctions de Windows** (sin privilegios) en `inbox`, `originals`, `provenance`, `inbox/<fuente>`, el directorio de datos, un componente padre y dentro del origen. Los mismos 7 casos con **enlaces simbólicos** si el sistema los permite. También `store/` previo, salida dentro del repositorio y el alcance de escritura en un proceso con `HOME`, `USERPROFILE`, `TMP`, `TEMP`, `LOCALAPPDATA` y `APPDATA` dirigidos al laboratorio | Cada caso aplicable rechazado **antes de escribir**, con el mensaje esperado, el exterior intacto y el directorio de datos sin cambios; el alcance de escritura limitado a `originals/`, `inbox/` y `provenance/`; repositorio sin cambios |

Los 11 casos clave del bloque B:
- UNKNOWN y pendiente R1 bloquean la publicación;
- v1 rechazado con fuentes reales y confinado al histórico sintético;
- los externos no cambian los resultados;
- huella dorada histórica y escritura estilo Windows;
- `stage` solo escribe en datos;
- Frozen definitivo;
- R1 por línea.

**Dictamen:** **PASS** solo si A, B y C son PASS. En Windows, además, los 7 casos de junction deben haberse ejecutado. Un caso `NOT_AVAILABLE` (enlace simbólico sin privilegio) se informa, pero no sustituye a los junctions.

## 2. Ejecución en ASMXC1200 (PowerShell)

```powershell
cd C:\Users\ucbaa\pulso-nexus
git fetch origin feature/core-v1
git checkout feature/core-v1
git pull origin feature/core-v1
git status --short
uv sync --frozen
$VER = Join-Path $env:LOCALAPPDATA 'PULSO_NEXUS_LAB\verificacion-win'
uv run --frozen --with tzdata==2025.2 python scripts\core_win_native_check.py --lab $VER
```

- `git status --short` debe salir vacío antes de ejecutar.
- Código de salida: 0 = PASS, 1 = FAIL.
- El informe queda en `$VER\win_native_<fecha>_*\informe.json`, junto con `junit-not-sql.xml`, `pytest-not-sql.txt` y la carpeta `aislamiento\`.
- **Para compartir:** solo `informe.json` y, si hay fallos, `pytest-not-sql.txt`. Son saneados: sintéticos y sin rutas absolutas.
- **Limpieza:** `Remove-Item -Recurse $VER`. No toca el repositorio ni el laboratorio de noviembre.

## 3. Diferencias Linux / Windows conocidas antes de ejecutar

| Tema | Linux (sandbox) | Windows (esperado) |
|---|---|---|
| Junctions | No existen: los 7 casos dan `NOT_APPLICABLE` | **Se ejecutan** con `_winapi.CreateJunction` (sin privilegios). Es la cobertura que faltaba: en `pytest`, las pruebas de enlaces se omiten en Windows |
| Enlaces simbólicos | 7/7 PASS | Requieren privilegio o Modo de desarrollador; si no se pueden crear, `NOT_AVAILABLE` |
| `O_NOFOLLOW` | Activo | No existe: la protección es `_reject_links()` en toda la ruta, antes de abrir |
| Pruebas `posix_only` y `needs_symlinks` | Se ejecutan | Se omiten por diseño: se cuentan en `skipped_cases` |
| `tzdata` | Del sistema | Capa efímera `--with tzdata==2025.2` |
| Riesgo de falso rechazo | — | Si un componente **padre** del laboratorio es un junction (redirección de carpetas, OneDrive), `stage()` se niega por diseño. Se informará como hallazgo, no como defecto |

## 4. Resultado de referencia en Linux (este entorno, sobre `d98e4d3`)

- **Dictamen PASS.**
- **A:** OK.
- **B:** 736 passed, 0 failed, 12 skipped; 11/11 casos clave en PASS; repositorio sin cambios.
- **C:** 10 PASS (7 de enlaces simbólicos, más `store/` previo, salida en el repositorio y alcance de escritura) y 7 `NOT_APPLICABLE` (junctions).

**Corrección del propio verificador durante su validación (transparencia):** el caso del enlace anidado `inbox/oeras_29` daba FAIL porque el criterio «`inbox/oeras_29` no existe» seguía el propio enlace creado por la prueba. `stage()` había rechazado correctamente y con el exterior intacto. El criterio se sustituyó por una comparación de la huella del directorio de datos sin seguir enlaces.

## 5. Pendiente

- Ejecución nativa en ASMXC1200 y su `informe.json`. Con él se emite el **dictamen único de Windows**.
- Cualquier diferencia demostrada que requiera cambiar código se reportará con evidencia y **requerirá autorización adicional**.

**Dependencias comerciales independientes (sin cambio):**
- aprobaciones R1 y R3;
- equivalencia 29 OERAS ↔ OE965;
- embarques físicos.

**NO-GO operativo.**
