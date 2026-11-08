"""Pruebas adversariales de las puertas de calidad.

Cubren ``junta_ruff_baseline.py`` y ``quality_gates.py``.

Cada prueba reproduce un modo de burlar la política y demuestra que se rechaza:
- línea base: aumento por ``update``, sustitución de hallazgos con el mismo conteo, archivo nuevo
  sin formato, ``noqa`` global o sin código, cambio de herramienta sin revisión o mezclado con
  código, edición manual del JSON y reescritura del historial;
- CI: órdenes exigidas insertadas como comentario (YAML o shell), dentro de otra orden, en otro
  job, desactivadas con ``if: false`` o ``continue-on-error``, o sin la referencia base.

Se ejecutan sobre repositorios temporales sintéticos (sin red, sin datos corporativos). Las
comprobaciones usan ``_exige`` (no ``assert``): ``scripts/`` sigue las reglas estrictas del CORE.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import junta_ruff_baseline as jrb  # noqa: E402
import quality_gates as qg  # noqa: E402

LARGA_A = 'VALOR_A = "' + "a" * 100 + '"\n'
LARGA_B = 'VALOR_B = "' + "b" * 100 + '"\n'
CORTA = 'VALOR_A = "a"\n'


def _exige(condicion: object, mensaje: str = "") -> None:
    if not condicion:
        pytest.fail(mensaje or "condición no cumplida")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Repositorio sintético con la configuración Ruff real y un componente con 1 hallazgo E501."""
    shutil.copy(SCRIPTS.parent / "pyproject.toml", tmp_path / "pyproject.toml")
    app = tmp_path / jrb.COMPONENTE / "app"
    app.mkdir(parents=True)
    (app / "modulo.py").write_text(LARGA_A, "utf-8", newline="\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "pruebas-sinteticas")
    _git(tmp_path, "config", "user.email", "pruebas@example.invalid")
    _git(tmp_path, "config", "core.autocrlf", "false")
    rc = jrb.main(
        ["update", "--justificacion", "línea base sintética", "--revisor", "pruebas"], tmp_path
    )
    _exige(rc == 0, "no se creó la línea base inicial")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def _modulo(repo: Path, texto: str) -> None:
    (repo / jrb.COMPONENTE / "app" / "modulo.py").write_text(texto, "utf-8", newline="\n")


def _base(repo: Path) -> dict[str, Any]:
    return json.loads((repo / jrb.BASELINE_REL).read_text("utf-8"))


def _check(repo: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[int, str]:
    rc = jrb.main(["check", "--base-ref", "HEAD", *extra], repo)
    return rc, capsys.readouterr().out


def _update(repo: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[int, str]:
    antes = (repo / jrb.BASELINE_REL).read_bytes()
    rc = jrb.main(["update", *extra], repo)
    if rc != 0:
        _exige(
            (repo / jrb.BASELINE_REL).read_bytes() == antes,
            "un update rechazado modificó la línea base",
        )
    return rc, capsys.readouterr().out


# ---------------------------------------------------------------- defecto 2: sustitución
def test_sustitucion_mismo_archivo_y_regla_se_detecta(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base(repo)
    _modulo(repo, LARGA_B)  # se corrige el E501 de VALOR_A y aparece otro E501 en el mismo archivo
    actual = jrb.medir(repo)
    # reproducción del defecto: el control por conteo (archivo, regla) NO ve la sustitución
    _exige(
        actual["por_archivo_regla"] == base["por_archivo_regla"],
        "el escenario debe conservar el conteo",
    )
    rc, out = _check(repo, capsys)
    _exige(rc == 1 and "NUEVO hallazgo" in out and "VALOR_B" in out, out)
    rc, out = _update(repo, capsys)
    _exige(rc == 1 and "SUSTITUCIÓN" in out, out)


def test_sustitucion_solo_con_revision_explicita_y_trazada(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _modulo(repo, LARGA_B)
    rc, out = _update(repo, capsys, "--revision")
    _exige(rc == 1 and "sin justificación o revisor" in out, out)
    rc, out = _update(
        repo, capsys, "--revision", "--justificacion", "renombre revisado", "--revisor", "qa"
    )
    _exige(rc == 0, out)
    entrada = _base(repo)["historial"][-1]
    _exige(entrada["tipo"] == "revision" and len(entrada["anadidas"]) == 1, str(entrada))
    _exige(_check(repo, capsys)[0] == 0)


# ---------------------------------------------------------------- defecto 1: update sin límites
def test_update_no_acepta_aumento_ni_con_revision(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _modulo(repo, LARGA_A + LARGA_B)
    for extra in ((), ("--revision", "--justificacion", "x", "--revisor", "qa")):
        rc, out = _update(repo, capsys, *extra)
        _exige(rc == 1 and "AUMENTO" in out, out)


def test_update_no_acepta_archivo_nuevo_sin_formato(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / jrb.COMPONENTE / "app" / "nuevo.py").write_text("x=[1,\n 2]\n", "utf-8", newline="\n")
    rc, out = _update(repo, capsys, "--revision", "--justificacion", "x", "--revisor", "qa")
    _exige(rc == 1 and "AUMENTO archivo sin formato" in out, out)


@pytest.mark.parametrize("texto", ["# ruff: noqa\nX = 1\n", "X = 1  # noqa\n"])
def test_update_no_acepta_noqa_global_ni_sin_codigo(
    repo: Path, capsys: pytest.CaptureFixture[str], texto: str
) -> None:
    _modulo(repo, LARGA_A + texto)
    rc, out = _update(repo, capsys, "--revision", "--justificacion", "x", "--revisor", "qa")
    _exige(rc == 1 and "noqa" in out, out)


def test_reduccion_se_acepta_sin_revision(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _modulo(repo, CORTA)
    _exige(_check(repo, capsys)[0] == 1)  # la deuda reducida exige registrar la línea base
    rc, out = _update(repo, capsys)
    _exige(rc == 0 and _base(repo)["historial"][-1]["tipo"] == "reduccion", out)
    _exige(_check(repo, capsys)[0] == 0)


def test_cambio_de_herramienta_exige_revision_y_codigo_intacto(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    py = repo / "pyproject.toml"
    py.write_text(
        py.read_text("utf-8").replace("line-length = 100", "line-length = 20", 1), "utf-8"
    )
    rc, out = _check(repo, capsys)
    _exige(rc == 1 and "config_ruff_sha256" in out, out)
    rc, out = _update(repo, capsys)
    _exige(rc == 1 and "sin justificación o revisor" in out, out)
    _modulo(repo, LARGA_A + LARGA_B)  # cambio de herramienta MEZCLADO con código: rechazado
    rc, out = _update(repo, capsys, "--revision", "--justificacion", "ruff", "--revisor", "qa")
    _exige(rc == 1 and "SIN cambios de código" in out, out)
    _modulo(repo, LARGA_A)
    rc, out = _update(
        repo, capsys, "--revision", "--justificacion", "nueva config revisada", "--revisor", "qa"
    )
    _exige(rc == 0 and _base(repo)["historial"][-1]["tipo"] == "herramienta", out)
    _exige(_check(repo, capsys)[0] == 0)


# ---------------------------------------------------------------- supresiones (Ruff 0.17.3)
LINEA_A = LARGA_A.rstrip("\n")
# (id, texto del módulo, ¿Ruff 0.17.3 suprime así el E501 de VALOR_A?)
PROHIBIDAS = [
    ("ruff-noqa", "# ruff: noqa\n" + LARGA_A, True),
    ("ruff-noqa-sin-espacio", "# ruff:noqa\n" + LARGA_A, True),
    ("ruff-noqa-codigo", "# ruff: noqa: E501\n" + LARGA_A, True),
    ("RUFF-NOQA-mayusculas", "# RUFF: NOQA\n" + LARGA_A, False),
    ("flake8-noqa", "# flake8: noqa\n" + LARGA_A, True),
    ("flake8-noqa-codigo", "# flake8: noqa: E501\n" + LARGA_A, True),
    ("ruff-file-ignore", "# ruff: file-ignore[E501]\n" + LARGA_A, True),
    ("ruff-disable-enable", "# ruff: disable[E501]\n" + LARGA_A + "# ruff: enable[E501]\n", True),
    ("noqa-en-linea", LINEA_A + "  # noqa\n", True),
    ("noqa-sin-espacio", LINEA_A + "  #noqa\n", True),
    ("NOQA-mayusculas", LINEA_A + "  # NOQA\n", True),
    ("noqa-vacio", LINEA_A + "  # noqa:\n", False),
    ("isort-skip-file", "# isort: skip_file\n" + LARGA_A, False),
    ("ruff-isort-skip-file", "# ruff: isort: skip_file\n" + LARGA_A, False),
    ("fmt-off", "# fmt: off\n" + LARGA_A + "# fmt: on\n", False),
    ("fmt-skip", LINEA_A + "  # fmt: skip\n", False),
    ("yapf-disable", "# yapf: disable\n" + LARGA_A, False),
]


@pytest.mark.parametrize(
    ("texto", "suprime"), [c[1:] for c in PROHIBIDAS], ids=[c[0] for c in PROHIBIDAS]
)
def test_supresion_prohibida_no_entra_ni_como_reduccion_ni_con_revision(
    repo: Path, capsys: pytest.CaptureFixture[str], texto: str, suprime: bool
) -> None:
    total = _base(repo)["total_hallazgos"]
    _modulo(repo, texto)
    medido = jrb.medir(repo)["total_hallazgos"]
    # la directiva haría pasar la supresión por una REDUCCIÓN de deuda (comportamiento de Ruff)
    _exige((medido < total) == suprime, f"Ruff 0.17.3: total {total} → {medido}")
    rc, out = _check(repo, capsys)
    _exige(rc == 1 and "PROHIBIDA" in out, out)
    rc, out = _update(repo, capsys)  # intento de registrarla como reducción
    _exige(rc == 1 and "SUPRESIÓN PROHIBIDA nueva" in out, out)
    rc, out = _update(repo, capsys, "--revision", "--justificacion", "x", "--revisor", "qa")
    _exige(rc == 1 and "SUPRESIÓN PROHIBIDA nueva" in out, out)


@pytest.mark.parametrize("nombre", ["ruff.toml", ".ruff.toml", "pyproject.toml"])
def test_configuracion_ruff_anidada_prohibida(
    repo: Path, capsys: pytest.CaptureFixture[str], nombre: str
) -> None:
    cuerpo = '[lint]\nignore = ["E501"]\n'
    if nombre == "pyproject.toml":
        cuerpo = '[tool.ruff.lint]\nignore = ["E501"]\n'
    (repo / jrb.COMPONENTE / "app" / nombre).write_text(cuerpo, "utf-8")
    _exige(
        jrb.medir(repo)["total_hallazgos"] < _base(repo)["total_hallazgos"],
        "Ruff aplica la config anidada",
    )
    rc, out = _check(repo, capsys)
    _exige(rc == 1 and "ANIDADA" in out, out)
    for extra in ((), ("--revision", "--justificacion", "x", "--revisor", "qa")):
        rc, out = _update(repo, capsys, *extra)
        _exige(rc == 1 and "ANIDADA" in out, out)


@pytest.mark.parametrize(
    "texto",
    [
        LINEA_A + "  # noqa: E501\n",
        LINEA_A + "  # noqa : E501\n",
        LINEA_A + "  # ruff: ignore[E501]\n",
    ],
    ids=["noqa-codigo", "noqa-espacio-codigo", "ruff-ignore"],
)
def test_supresion_con_codigo_no_se_registra_como_reduccion(
    repo: Path, capsys: pytest.CaptureFixture[str], texto: str
) -> None:
    _modulo(repo, texto)
    _exige(
        jrb.medir(repo)["total_hallazgos"] < _base(repo)["total_hallazgos"],
        "la supresión debe actuar",
    )
    rc, out = _check(repo, capsys)
    _exige(rc == 1 and "NUEVA supresión con código" in out, out)
    rc, out = _update(repo, capsys)
    _exige(rc == 1 and "no puede registrarse como reducción" in out, out)
    rc, out = _update(
        repo, capsys, "--revision", "--justificacion", "supresión revisada", "--revisor", "qa"
    )
    _exige(rc == 0, out)
    entrada = _base(repo)["historial"][-1]
    _exige(
        entrada["tipo"] == "revision" and len(entrada["supresiones_anadidas"]) == 1, str(entrada)
    )
    _exige(_check(repo, capsys)[0] == 0)


def test_directiva_dentro_de_una_cadena_no_es_supresion(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _modulo(repo, LARGA_A + 'TEXTO = "# ruff: noqa # noqa # fmt: off"\n')
    medido = jrb.medir(repo)
    _exige(
        medido["supresiones_prohibidas"] == {}
        and medido["total_hallazgos"] == _base(repo)["total_hallazgos"]
    )
    rc, out = _update(repo, capsys)
    _exige(rc == 0 and _check(repo, capsys)[0] == 0, out)


# ---------------------------------------------------------------- ediciones manuales del JSON
def test_edicion_manual_para_aceptar_un_aumento_se_detecta_contra_la_base(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _modulo(repo, LARGA_A + LARGA_B)
    falsa = jrb.medir(
        repo
    )  # el atacante escribe la medición actual a mano, con el historial intacto
    falsa["historial"] = _base(repo)["historial"]
    (repo / jrb.BASELINE_REL).write_text(json.dumps(falsa), "utf-8")
    rc, out = jrb.main(["check", "--base-ref", ""], repo), capsys.readouterr().out
    _exige(rc == 0, "sin referencia base, la línea base editada coincide con la medición: " + out)
    rc, out = _check(repo, capsys)
    _exige(rc == 1 and "sin entrada en el historial" in out, out)
    falsa["historial"] = [*falsa["historial"], {"tipo": "reduccion"}]  # disfraz de reducción
    (repo / jrb.BASELINE_REL).write_text(json.dumps(falsa), "utf-8")
    rc, out = _check(repo, capsys)
    _exige(rc == 1 and "AUMENTO" in out, out)


def test_historial_reescrito_se_detecta(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    doc = _base(repo)
    doc["historial"][0]["justificacion"] = "otra"
    (repo / jrb.BASELINE_REL).write_text(json.dumps(doc), "utf-8")
    rc, out = _check(repo, capsys)
    _exige(rc == 1 and "REESCRITO" in out, out)


def test_referencia_base_inexistente_falla_cerrado(repo: Path) -> None:
    with pytest.raises(SystemExit, match="no resuelve"):
        jrb.main(["check", "--base-ref", "f" * 40], repo)


# ---------------------------------------------------------------- CI
CI_REAL = (SCRIPTS.parent / ".github" / "workflows" / "ci.yml").read_text("utf-8")
JUNTA = "        run: uv run python scripts/quality_gates.py junta\n"


def _sin_paso_junta_linux(ci: str) -> str:
    linux, resto = ci.split("\n  windows:", 1)
    _exige(JUNTA in linux)
    return linux.replace(JUNTA, "        run: echo omitido\n", 1) + "\n  windows:" + resto


def test_ci_real_cumple() -> None:
    _exige(qg.verificar_ci(CI_REAL) == [], str(qg.verificar_ci(CI_REAL)))


@pytest.mark.parametrize(
    "insercion",
    [
        "      # uv run python scripts/quality_gates.py junta\n",
        "      - run: echo listo  # uv run python scripts/quality_gates.py junta\n",
        "      - run: |\n"
        "          # uv run python scripts/quality_gates.py junta\n"
        "          echo listo\n",
        "      - run: echo uv run python scripts/quality_gates.py junta\n",
        "      - if: false\n        run: uv run python scripts/quality_gates.py junta\n",
        "      - continue-on-error: true\n"
        "        run: uv run python scripts/quality_gates.py junta\n",
    ],
    ids=[
        "comentario-yaml",
        "comentario-en-linea",
        "comentario-shell",
        "dentro-de-otra-orden",
        "if-false",
        "continue-on-error",
    ],
)
def test_ci_no_se_satisface_con_comentarios_ni_pasos_inertes(insercion: str) -> None:
    ci = _sin_paso_junta_linux(CI_REAL).replace(
        "\n  windows:", "\n" + insercion + "\n  windows:", 1
    )
    _exige("[linux] uv run python scripts/quality_gates.py junta" in qg.verificar_ci(ci), insercion)


def test_ci_exige_el_paso_en_el_job_correcto_y_la_referencia_base() -> None:
    _exige(
        "[linux] uv run python scripts/quality_gates.py junta"
        in qg.verificar_ci(_sin_paso_junta_linux(CI_REAL))
    )
    sin_env = CI_REAL.replace("NEXUS_BASELINE_BASE_REF:", "OTRA_VARIABLE:", 1)
    _exige(any("sin env NEXUS_BASELINE_BASE_REF" in f for f in qg.verificar_ci(sin_env)))
    sin_historia = CI_REAL.replace("fetch-depth: 0", "fetch-depth: 1", 1)
    _exige(any("fetch-depth" in f for f in qg.verificar_ci(sin_historia)))


# ---------------------------------------------------------------- tzdata para la Junta (Windows)
ZONA = (
    "import importlib.util, zoneinfo; "
    "print('tzdata_paquete=' + str(importlib.util.find_spec('tzdata') is not None)); "
    "zoneinfo.ZoneInfo('America/Mexico_City'); print('ZONA_OK')"
)


def test_lanzador_junta_y_core_incluyen_tzdata_sin_afectar_otras_puertas() -> None:
    junta = qg.comando_uv("junta", qg.JUNTA[0])
    core = qg.comando_uv("core", qg.CORE[0])
    prefijo = ["uv", "run", "--frozen", "--with", "tzdata==2025.2"]
    _exige(junta[:5] == prefijo and junta[5:] == qg.JUNTA[0], str(junta))
    _exige(core[:5] == prefijo and core[5:] == qg.CORE[0], str(core))
    plugins = ["pytest", "-p", "pytest_cov", "-p", "hypothesis.extra.pytestplugin"]
    _exige(core[5:10] == plugins, "CORE sin carga explícita de plugins")
    # La CI de Windows debe lanzar Junta a través de su puerta (no con pytest directo).
    _exige((f"{qg.GATES} junta", False) in qg.REQUISITOS["windows"], str(qg.REQUISITOS["windows"]))
    sin_windows = CI_REAL.split("\n  windows:", 1)
    _exige(
        len(sin_windows) == 2 and f"{qg.GATES} junta" in sin_windows[1],
        "ci.yml: Windows sin la puerta junta",
    )
    for grupo, cmds in (
        ("estatico", qg.ESTATICO),
        ("gobernanza", qg.GOBERNANZA),
        ("tipos", qg.TIPOS),
    ):
        for cmd in cmds:
            _exige(
                "--with" not in qg.comando_uv(grupo, cmd), f"{grupo}: dependencia extra inesperada"
            )


def test_lanzador_junta_resuelve_la_zona_sin_tzdata_del_sistema(tmp_path: Path) -> None:
    """Simula un equipo sin base de zonas (como Windows).

    PYTHONTZPATH apunta a un directorio vacío: solo el paquete tzdata puede resolver la zona."""
    if shutil.which("uv") is None:
        pytest.fail("uv no está en PATH: la puerta junta no puede lanzarse")
    vacio = tmp_path / "sin_zonas"
    vacio.mkdir()
    env = {**os.environ, "PYTHONTZPATH": str(vacio)}

    def lanzar(grupo: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            qg.comando_uv(grupo, ["python", "-c", ZONA]),
            cwd=qg.ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    # Control negativo: uv.lock SIN tzdata y sin --with. No usar la puerta CORE
    # porque CORE ahora también exige tzdata en Windows.
    control = subprocess.run(
        [*qg.UV_BASE, "python", "-c", ZONA],
        cwd=qg.ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if "tzdata_paquete=False" in control.stdout:
        # uv.lock no trae tzdata: sin la dependencia explícita la zona NO se resuelve
        # (el fallo observado en ASMXC1200)
        _exige(
            control.returncode != 0 and "ZoneInfoNotFoundError" in control.stderr,
            control.stderr[-400:],
        )
    for grupo in ("junta", "core"):
        prueba = lanzar(grupo)
        _exige(
            prueba.returncode == 0 and "ZONA_OK" in prueba.stdout,
            prueba.stdout + prueba.stderr[-400:],
        )
        _exige("tzdata_paquete=True" in prueba.stdout, prueba.stdout)


def test_lanzador_core_carga_cobertura_sin_autoload() -> None:
    if shutil.which("uv") is None:
        pytest.fail("uv no está en PATH: CORE no puede lanzarse")
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    proceso = subprocess.run(
        qg.comando_uv("core", [*qg.CORE[0][:5], "--help"]),
        cwd=qg.ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    _exige(
        proceso.returncode == 0
        and "--cov=" in proceso.stdout
        and "--hypothesis-profile" in proceso.stdout,
        proceso.stdout[-300:] + proceso.stderr[-500:],
    )


# -------------------------------------------------------- portabilidad e inicio protegido


def test_arbol_portable_en_orden_y_manifiesto_crlf(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = repo / jrb.COMPONENTE
    manifiesto = app / "SHA256SUMS"
    manifiesto.write_bytes(b"abc\ndef\n")
    (app / "LEEME.md").write_bytes(b"documento\n")
    _git(repo, "add", "--", jrb.COMPONENTE)
    esperado = jrb._arbol_sha(repo)

    # Solo se acepta CRLF si vuelve exactamente a los bytes LF del índice Git.
    manifiesto.write_bytes(b"abc\r\ndef\r\n")
    _exige(jrb._arbol_sha(repo) == esperado, "CRLF equivalente debe conservar la huella")

    # Simular la iteración en otro orden: Windows y Linux deben dar la misma huella.
    archivos_original = jrb._archivos
    monkeypatch.setattr(jrb, "_archivos", lambda r: list(reversed(archivos_original(r))))
    _exige(jrb._arbol_sha(repo) == esperado, "orden de archivos no es portable")

    # Un cambio real del manifiesto sigue invalidando la huella, aunque use CRLF.
    manifiesto.write_bytes(b"abc\r\ndef\r\nCAMBIO\r\n")
    _exige(jrb._arbol_sha(repo) != esperado, "cambio real del manifiesto fue ocultado")


def test_linea_base_inicial_rechaza_supresion_prohibida(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ruta = repo / jrb.BASELINE_REL
    ruta.unlink()
    _modulo(repo, "# ruff: noqa\n" + LARGA_A)
    rc = jrb.main(["update", "--justificacion", "inicio", "--revisor", "pruebas"], repo)
    salida = capsys.readouterr().out
    _exige(rc == 1 and "SUPRESIÓN PROHIBIDA" in salida, salida)
    _exige(not ruta.exists(), "línea base inicial prohibida fue escrita")
