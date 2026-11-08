"""Puerta de calidad Ruff para el componente aislado ``apps/nexus_junta_preview``.

Política: ``docs/quality/POLITICA_RUFF_NEXUS_JUNTA_PREVIEW.md`` (v2).

El componente queda fuera de ``ruff check .`` (``extend-exclude`` explícito en ``pyproject.toml``)
para que ESTA puerta lo evalúe con las MISMAS reglas estrictas del CORE contra una línea base
versionada (``quality/ruff_baseline_nexus_junta_preview.json``). La línea base registra IDENTIDADES:

- lint: huella de cada hallazgo = SHA-256(archivo, regla, texto de la línea), con multiplicidad.
  Corregir un hallazgo y añadir otro de la misma regla en el mismo archivo es una SUSTITUCIÓN:
  la huella nueva no existe en la línea base y la puerta falla aunque el conteo no cambie;
- formato: SHA-256 del contenido de cada archivo sin formatear. Un archivo con deuda de formato
  que se modifica debe formatearse (o pasar por una revisión explícita);
- supresiones (inventario de COMENTARIOS reales, vía ``tokenize``), comprobadas contra Ruff 0.17.3:
  * PROHIBIDAS siempre, en cualquier transición: de archivo o rango (``# ruff: noqa``,
    ``# ruff:noqa``, ``# flake8: noqa[: X]``, ``# ruff: file-ignore[X]``, ``# ruff: disable[X]`` /
    ``enable[X]``), de isort (``skip_file``, ``skip``, ``off``/``on``, ``# ruff: isort: ...``), de
    formato (``# fmt: off``/``on``/``skip``, ``# yapf: disable``) y ``noqa`` sin código o vacío
    (``# noqa``, ``#noqa``, ``# NOQA``, ``# noqa:``);
  * CON CÓDIGO (``# noqa: X``, ``# noqa : X``, ``# ruff: ignore[X]``): huella por comentario; una
    nueva SOLO entra con ``revision`` declarada, nunca como ``reduccion`` (si no, suprimir un
    hallazgo se registraría como reducción de deuda);
  * configuración Ruff ANIDADA en el componente (``ruff.toml``, ``.ruff.toml``, ``pyproject.toml``):
    prohibida (sustituiría las reglas del CORE sin cambiar el ``[tool.ruff]`` raíz);
- versión de Ruff, SHA-256 de ``[tool.ruff]`` y SHA-256 del árbol del componente.

Transiciones permitidas entre una línea base y la siguiente (las aplican ``update`` y, contra la
línea base de un commit base, ``check --base-ref``):

- ``reduccion``: solo desaparecen huellas; misma versión y configuración;
- ``revision``: aparecen huellas nuevas (hallazgos o supresiones CON código) SIN aumentar ningún
  conteo (archivo, regla), el total ni los archivos sin formato; exige ``--justificacion`` y
  ``--revisor``, y lista en el historial cada huella añadida;
- ``herramienta``: cambia la versión de Ruff o ``[tool.ruff]``; puede aumentar la deuda SOLO si el
  árbol del componente es idéntico (el aumento no puede venir del código); exige justificación y
  revisor.

El historial solo crece (no se reescribe). Ninguna transición acepta supresiones prohibidas.

  python scripts/junta_ruff_baseline.py check [--base-ref REF]
  python scripts/junta_ruff_baseline.py update [--revision --justificacion TXT --revisor NOMBRE]

``--base-ref`` (o la variable ``NEXUS_BASELINE_BASE_REF``): commit contra cuya línea base se valida
la transición (en la CI, la base del PR o el commit anterior al push). ``0000…`` = commit padre.
Salida: 0 conforme · 1 no conforme · 2 error de uso o de entorno.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tokenize
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COMPONENTE = "apps/nexus_junta_preview"
BASELINE_REL = "quality/ruff_baseline_nexus_junta_preview.json"
SCHEMA = "nexus.quality.ruff-baseline/3"
_I = re.IGNORECASE
# Directivas comprobadas contra ruff 0.17.3 (ver pruebas de gobernanza). Se buscan en COMENTARIOS.
SUPRESIONES_PROHIBIDAS = (
    ("archivo", re.compile(r"#\s*(?:ruff|flake8)\s*:\s*noqa\b", _I)),
    ("archivo-rango", re.compile(r"#\s*ruff\s*:\s*(?:file-ignore|disable|enable)\b", _I)),
    ("isort", re.compile(r"\bisort\s*:\s*(?:skip_file|skip|off|on)\b", _I)),
    ("formato", re.compile(r"\b(?:fmt\s*:\s*(?:off|on|skip)|yapf\s*:\s*(?:disable|enable))\b", _I)),
    ("noqa-sin-codigo", re.compile(r"#\s*noqa\b(?!\s*:\s*[A-Z]+[0-9]+)", _I)),
)
SUPRESIONES_CON_CODIGO = (
    ("noqa-codigo", re.compile(r"#\s*noqa\s*:\s*[A-Z]+[0-9]+", _I)),
    ("ruff-ignore", re.compile(r"#\s*ruff\s*:\s*ignore\s*\[", _I)),
)
CONFIG_ANIDADA = {"ruff.toml", ".ruff.toml", "pyproject.toml"}
IGNORADOS = {"__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", ".mypy_cache"}
CERO = re.compile(r"^0+$")


# ---------------------------------------------------------------- medición
def _ruff(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ruff", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rel(root: Path, path: str | Path) -> str:
    return Path(path).resolve().relative_to(root.resolve()).as_posix()


def _huella(*partes: str) -> str:
    return _sha("\0".join(partes).encode("utf-8"))[:24]


def _config_sha(root: Path) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))
    ruff_cfg = data.get("tool", {}).get("ruff", {})
    return _sha(json.dumps(ruff_cfg, sort_keys=True).encode("utf-8"))


def _archivos(root: Path) -> list[Path]:
    base = root / COMPONENTE
    return sorted(
        (
            p
            for p in base.rglob("*")
            if p.is_file() and not (IGNORADOS & set(p.relative_to(base).parts))
        ),
        key=lambda p: _rel(root, p),
    )


def _bytes_arbol(root: Path, p: Path) -> bytes:
    """Bytes exactos, salvo CRLF del manifiesto si Git confirma equivalencia con LF.

    El integrador Windows genera SHA256SUMS con WriteAllLines (CRLF). El índice Git guarda
    LF. No se normalizan otros archivos y NO se acepta un manifiesto con cambios semánticos:
    únicamente se admite la conversión que reproduce exactamente su blob en el índice.
    """
    contenido = p.read_bytes()
    rel = _rel(root, p)
    if rel != f"{COMPONENTE}/SHA256SUMS" or b"\r\n" not in contenido:
        return contenido
    indice = subprocess.run(
        ["git", "show", f":{rel}"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if indice.returncode == 0 and contenido.replace(b"\r\n", b"\n") == indice.stdout:
        return indice.stdout
    return contenido


def _arbol_sha(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(_archivos(root), key=lambda path: _rel(root, path)):
        h.update(f"{_rel(root, p)}\0{_sha(_bytes_arbol(root, p))}\n".encode())
    return h.hexdigest()


def _linea(root: Path, rel: str, fila: int, cache: dict[str, list[str]]) -> str:
    if rel not in cache:
        cache[rel] = (root / rel).read_text("utf-8").splitlines()
    lineas = cache[rel]
    return lineas[fila - 1].strip() if 0 < fila <= len(lineas) else ""


def _comentarios(texto: str) -> list[tuple[int, str]]:
    """Comentarios reales (no texto dentro de cadenas). Sin tokenizar: todo lo que siga a «#»."""
    try:
        return [
            (t.start[0], t.string)
            for t in tokenize.generate_tokens(iter(texto.splitlines(keepends=True)).__next__)
            if t.type == tokenize.COMMENT
        ]
    except (tokenize.TokenError, SyntaxError):
        return [(n, ln[ln.index("#") :]) for n, ln in enumerate(texto.splitlines(), 1) if "#" in ln]


def supresiones(root: Path) -> tuple[Counter[str], Counter[str], dict[str, str], list[str]]:
    """Inventario: (prohibidas, con código, detalle huella→comentario, configuración anidada)."""
    prohibidas: Counter[str] = Counter()
    con_codigo: Counter[str] = Counter()
    detalle: dict[str, str] = {}
    config = [_rel(root, p) for p in _archivos(root) if p.name in CONFIG_ANIDADA]
    for p in _archivos(root):
        if p.suffix not in (".py", ".pyi"):
            continue
        rel = _rel(root, p)
        texto = p.read_text("utf-8")
        lineas = texto.splitlines()
        for fila, comentario in _comentarios(texto):
            norma = " ".join(comentario.lower().split())
            linea = lineas[fila - 1].strip() if 0 < fila <= len(lineas) else ""
            for grupo, patrones in (
                (prohibidas, SUPRESIONES_PROHIBIDAS),
                (con_codigo, SUPRESIONES_CON_CODIGO),
            ):
                for tipo, patron in patrones:
                    if patron.search(comentario):
                        fp = f"{rel}::{tipo}::{_huella(rel, tipo, norma, linea)}"
                        grupo[fp] += 1
                        detalle[fp] = comentario.strip()[:120]
    return prohibidas, con_codigo, detalle, config


def medir(root: Path = ROOT) -> dict[str, Any]:
    version = _ruff(root, "--version")
    if version.returncode != 0:
        raise SystemExit(f"ruff no disponible: {version.stderr.strip()}")
    lint = _ruff(root, "check", "--no-cache", "--output-format", "json", "--exit-zero", COMPONENTE)
    if lint.returncode != 0:
        raise SystemExit(f"ruff check falló: {lint.stderr.strip()[:500]}")
    cache: dict[str, list[str]] = {}
    huellas: Counter[str] = Counter()
    detalle: dict[str, str] = {}
    for h in json.loads(lint.stdout or "[]"):
        rel, regla = _rel(root, h["filename"]), h["code"] or "SYNTAX"
        texto = _linea(root, rel, int(h["location"]["row"]), cache)
        fp = f"{rel}::{regla}::{_huella(rel, regla, texto)}"
        huellas[fp] += 1
        detalle[fp] = texto[:120]
    fmt = _ruff(root, "format", "--no-cache", "--check", "--output-format", "json", COMPONENTE)
    if fmt.returncode not in (0, 1):
        raise SystemExit(f"ruff format falló: {fmt.stderr.strip()[:500]}")
    sin_formato = {
        rel: _sha((root / rel).read_bytes())
        for rel in sorted({_rel(root, h["filename"]) for h in json.loads(fmt.stdout or "[]")})
    }
    prohibidas, con_codigo, detalle_sup, config_anidada = supresiones(root)
    return {
        "schema": SCHEMA,
        "componente": COMPONENTE,
        "ruff_version": version.stdout.strip(),
        "config_ruff_sha256": _config_sha(root),
        "arbol_componente_sha256": _arbol_sha(root),
        "total_hallazgos": sum(huellas.values()),
        "por_regla": dict(sorted(Counter(k.split("::")[1] for k in huellas.elements()).items())),
        "por_archivo_regla": dict(
            sorted(Counter("::".join(k.split("::")[:2]) for k in huellas.elements()).items())
        ),
        "hallazgos": dict(sorted(huellas.items())),
        "detalle": dict(sorted(detalle.items())),
        "sin_formato": sin_formato,
        "supresiones_prohibidas": dict(sorted(prohibidas.items())),
        "supresiones_con_codigo": dict(sorted(con_codigo.items())),
        "detalle_supresiones": dict(sorted(detalle_sup.items())),
        "config_anidada": config_anidada,
    }


# ---------------------------------------------------------------- comparación exacta
def comparar(base: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Medición actual frente a la línea base versionada: deben coincidir exactamente."""
    errores: list[str] = []
    if base.get("schema") != SCHEMA:
        return [f"schema de la línea base {base.get('schema')!r}: se exige {SCHEMA}"]
    for campo in ("componente", "ruff_version", "config_ruff_sha256"):
        if base.get(campo) != actual[campo]:
            errores.append(f"{campo}: línea base {base.get(campo)!r} ≠ actual {actual[campo]!r}")
    b, a = Counter(base.get("hallazgos", {})), Counter(actual["hallazgos"])
    for k in sorted(set(b) | set(a)):
        if a[k] > b[k]:
            texto = actual["detalle"].get(k, "")
            errores.append(f"NUEVO hallazgo {k} ({b[k]} → {a[k]}): «{texto}»")
        elif a[k] < b[k]:
            errores.append(f"deuda reducida {k}: {b[k]} → {a[k]} (ejecute update)")
    bf, af = base.get("sin_formato", {}), actual["sin_formato"]
    for f in sorted(set(af) - set(bf)):
        errores.append(f"NUEVO archivo sin formato: {f}")
    for f in sorted(set(af) & set(bf)):
        if af[f] != bf[f]:
            errores.append(f"archivo con deuda de formato MODIFICADO sin formatear: {f}")
    for f in sorted(set(bf) - set(af)):
        errores.append(f"formato resuelto: {f} (ejecute update)")
    errores += [f"configuración Ruff ANIDADA prohibida: {f}" for f in actual["config_anidada"]]
    for campo, nombre in (
        ("supresiones_prohibidas", "PROHIBIDA"),
        ("supresiones_con_codigo", "con código"),
    ):
        bs, as_ = Counter(base.get(campo, {})), Counter(actual[campo])
        for k in sorted(as_ - bs):
            texto = actual["detalle_supresiones"].get(k, "")
            errores.append(f"NUEVA supresión {nombre} {k}: «{texto}»")
        errores += [f"supresión eliminada {k} (ejecute update)" for k in sorted(bs - as_)]
    if not errores and base.get("arbol_componente_sha256") != actual["arbol_componente_sha256"]:
        # mismos hallazgos con otro código: la línea base debe registrar el árbol vigente
        errores.append("árbol del componente distinto al registrado (ejecute update)")
    return errores


# ---------------------------------------------------------------- transiciones
def _sin_aumentos(base: dict[str, Any], nuevo: dict[str, Any]) -> list[str]:
    errores = []
    bc, nc = Counter(base.get("por_archivo_regla", {})), Counter(nuevo.get("por_archivo_regla", {}))
    errores += [f"AUMENTO {k}: {bc[k]} → {nc[k]}" for k in sorted(nc) if nc[k] > bc[k]]
    if nuevo.get("total_hallazgos", 0) > base.get("total_hallazgos", 0):
        errores.append(f"AUMENTO total: {base.get('total_hallazgos')} → {nuevo['total_hallazgos']}")
    nuevos_fmt = sorted(set(nuevo.get("sin_formato", {})) - set(base.get("sin_formato", {})))
    errores += [f"AUMENTO archivo sin formato: {f}" for f in nuevos_fmt]
    return errores


def _supresiones_prohibidas(base: dict[str, Any] | None, nuevo: dict[str, Any]) -> list[str]:
    """En CUALQUIER transición: ni configuración anidada ni supresiones prohibidas nuevas."""
    errores = [
        f"configuración Ruff ANIDADA prohibida: {f}" for f in nuevo.get("config_anidada", [])
    ]
    nuevas = Counter(nuevo.get("supresiones_prohibidas", {})) - Counter(
        base.get("supresiones_prohibidas", {}) if base is not None else {}
    )
    detalle = nuevo.get("detalle_supresiones", {})
    errores += [f"SUPRESIÓN PROHIBIDA nueva {k}: «{detalle.get(k, '')}»" for k in sorted(nuevas)]
    return errores


def transicion(base: dict[str, Any] | None, nuevo: dict[str, Any]) -> list[str]:
    """Errores de la transición base → nuevo (vacío = permitida)."""
    errores = _supresiones_prohibidas(base, nuevo)
    hist = nuevo.get("historial", [])
    if base is None:
        if not hist or hist[0].get("tipo") != "inicial" or len(hist) != 1:
            errores.append("la primera línea base debe tener exactamente una entrada 'inicial'")
        elif not hist[0].get("justificacion") or not hist[0].get("revisor"):
            errores.append("la entrada 'inicial' exige justificación y revisor")
        return errores
    bh = base.get("historial", [])
    if hist[: len(bh)] != bh:
        return [*errores, "el historial de la línea base fue REESCRITO (solo puede crecer)"]
    entradas = hist[len(bh) :]
    if not entradas:
        if {k: v for k, v in nuevo.items() if k != "historial"} != {
            k: v for k, v in base.items() if k != "historial"
        }:
            errores.append("la línea base cambió sin entrada en el historial")
        return errores
    tipos = {e.get("tipo") for e in entradas}
    if extra := tipos - {"reduccion", "revision", "herramienta"}:
        errores.append(f"tipo de entrada no admitido: {sorted(map(str, extra))}")
    for e in entradas:
        if e.get("tipo") in ("revision", "herramienta") and not (
            e.get("justificacion") and e.get("revisor")
        ):
            errores.append(f"entrada '{e.get('tipo')}' sin justificación o revisor")
    herramienta = (base.get("ruff_version"), base.get("config_ruff_sha256")) != (
        nuevo.get("ruff_version"),
        nuevo.get("config_ruff_sha256"),
    )
    if herramienta:
        if "herramienta" not in tipos:
            errores.append("cambio de versión de Ruff o de [tool.ruff] sin entrada 'herramienta'")
        if base.get("arbol_componente_sha256") != nuevo.get("arbol_componente_sha256"):
            errores.append(
                "un cambio de herramienta debe ir SIN cambios de código en el componente "
                "(árbol distinto): sepárelo en otra revisión"
            )
        return errores
    if "herramienta" in tipos:
        errores.append("entrada 'herramienta' sin cambio de versión ni de configuración")
    errores += _sin_aumentos(base, nuevo)
    sup_nuevas = Counter(nuevo.get("supresiones_con_codigo", {})) - Counter(
        base.get("supresiones_con_codigo", {})
    )
    sup_decl = Counter(
        fp
        for e in entradas
        if e.get("tipo") == "revision"
        for fp in e.get("supresiones_anadidas", [])
    )
    if sup_nuevas != sup_decl:
        errores.append(
            "SUPRESIÓN con código nueva no declarada "
            "(no puede registrarse como reducción de deuda): "
            f"{sorted(sup_nuevas)} ≠ declaradas en 'revision' {sorted(sup_decl)}"
        )
    anadidas = Counter(nuevo.get("hallazgos", {})) - Counter(base.get("hallazgos", {}))
    fmt_cambiados = sorted(
        f
        for f, h in nuevo.get("sin_formato", {}).items()
        if base.get("sin_formato", {}).get(f, h) != h
    )
    if anadidas or fmt_cambiados:
        declaradas = Counter(
            fp for e in entradas if e.get("tipo") == "revision" for fp in e.get("anadidas", [])
        )
        if Counter(anadidas) != declaradas:
            errores.append(
                "SUSTITUCIÓN de hallazgos no declarada: huellas nuevas "
                f"{sorted(anadidas)} ≠ declaradas en 'revision' {sorted(declaradas)}"
            )
        fmt_decl = sorted(
            {
                f
                for e in entradas
                if e.get("tipo") == "revision"
                for f in e.get("formato_modificado", [])
            }
        )
        if fmt_cambiados != fmt_decl:
            errores.append(
                f"archivos sin formato modificados {fmt_cambiados} ≠ declarados {fmt_decl}"
            )
    return errores


def nueva_linea_base(
    base: dict[str, Any] | None,
    actual: dict[str, Any],
    justificacion: str,
    revisor: str,
    revision: bool,
) -> dict[str, Any]:
    hist = list((base or {}).get("historial", []))
    entrada: dict[str, Any] = {
        "total_anterior": (base or {}).get("total_hallazgos"),
        "total": actual["total_hallazgos"],
    }
    if base is None:
        entrada["tipo"] = "inicial"
    elif (base.get("ruff_version"), base.get("config_ruff_sha256")) != (
        actual["ruff_version"],
        actual["config_ruff_sha256"],
    ):
        entrada.update(
            tipo="herramienta",
            ruff_version=actual["ruff_version"],
            config=actual["config_ruff_sha256"],
        )
    elif revision:
        anadidas = Counter(actual["hallazgos"]) - Counter(base.get("hallazgos", {}))
        sup = Counter(actual["supresiones_con_codigo"]) - Counter(
            base.get("supresiones_con_codigo", {})
        )
        entrada.update(
            tipo="revision",
            anadidas=sorted(anadidas.elements()),
            supresiones_anadidas=sorted(sup.elements()),
            formato_modificado=sorted(
                f
                for f, h in actual["sin_formato"].items()
                if base.get("sin_formato", {}).get(f, h) != h
            ),
        )
    else:
        entrada["tipo"] = "reduccion"
    if justificacion or revisor:
        entrada.update(justificacion=justificacion, revisor=revisor)
    return {**actual, "historial": [*hist, entrada]}


# ---------------------------------------------------------------- git
def linea_base_en(root: Path, ref: str) -> dict[str, Any] | None:
    """Línea base versionada en ``ref`` (None si no existía). Falla cerrado si ``ref`` no existe."""
    if CERO.match(ref):
        ref = "HEAD~1"
    resolver = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if resolver.returncode != 0:
        raise SystemExit(f"--base-ref {ref!r} no resuelve a un commit (¿checkout sin historia?)")
    show = subprocess.run(
        ["git", "show", f"{resolver.stdout.strip()}:{BASELINE_REL}"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return json.loads(show.stdout) if show.returncode == 0 else None


# ---------------------------------------------------------------- CLI
def main(argv: list[str], root: Path = ROOT) -> int:
    ap = argparse.ArgumentParser(prog="junta_ruff_baseline.py")
    ap.add_argument("modo", choices=("check", "update"))
    ap.add_argument("--base-ref", default=os.environ.get("NEXUS_BASELINE_BASE_REF", ""))
    ap.add_argument("--revision", action="store_true")
    ap.add_argument("--justificacion", default="")
    ap.add_argument("--revisor", default="")
    a = ap.parse_args(argv)
    ruta = root / BASELINE_REL
    actual = medir(root)
    base = json.loads(ruta.read_text("utf-8")) if ruta.is_file() else None
    if a.modo == "update":
        nuevo = nueva_linea_base(
            base, actual, a.justificacion.strip(), a.revisor.strip(), a.revision
        )
        errores = transicion(base, nuevo)
        if errores:
            print("UPDATE RECHAZADO — la línea base no se modifica:")
            for e in errores:
                print("  " + e)
            return 1
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(
            json.dumps(nuevo, ensure_ascii=False, indent=1) + "\n", "utf-8", newline="\n"
        )
        tipo = nuevo["historial"][-1]["tipo"]
        print(f"línea base actualizada ({tipo}): {nuevo['total_hallazgos']} hallazgos")
        return 0
    if base is None:
        print(f"falta la línea base {BASELINE_REL}", file=sys.stderr)
        return 2
    errores = comparar(base, actual)
    if a.base_ref:
        errores += [
            f"[transición desde {a.base_ref}] {e}"
            for e in transicion(linea_base_en(root, a.base_ref), base)
        ]
    n_fmt, n_fmt_base = len(actual["sin_formato"]), len(base.get("sin_formato", {}))
    total_base = base.get("total_hallazgos")
    resumen = (
        f"{COMPONENTE}: {actual['total_hallazgos']} hallazgos (línea base {total_base}), "
        f"{n_fmt} archivos sin formato (línea base {n_fmt_base})"
    )
    if errores:
        print("NO CONFORME — " + resumen)
        for e in errores:
            print("  " + e)
        return 1
    print(
        "CONFORME — "
        + resumen
        + (f" · transición desde {a.base_ref}: válida" if a.base_ref else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
