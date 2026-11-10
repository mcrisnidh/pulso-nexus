"""Activación controlada de FIX6 en un laboratorio con noviembre 2026 ya sellado.

No consulta sistemas corporativos, no toca el Frozen previo ni sus generaciones y no
habilita publicación. La única mutación de configuración es ssrs_automatico=true.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .conectores import orquestador as orq
from .servicio import Config, RefreshError


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic(target: Path, body: bytes) -> None:
    fd, temp = tempfile.mkstemp(prefix=f".{target.name}.fix6.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(body)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def activar(data: Path) -> dict[str, Any]:
    config = data / "preview.json"
    if not config.is_file():
        raise RefreshError(["FIX6: preview.json no localizado"])
    original = config.read_bytes()
    body = json.loads(original)
    cfg = Config.load(config)
    if body.get("ssrs_automatico") is True:
        return {"estado": "YA_ACTIVO", "config_sha256": _sha(original)}
    if cfg.month != "2026-11":
        raise RefreshError(["FIX6: migración desde noviembre 2026 requerida; no inventar Frozen"])
    lock_file = data / "frozen_2026-11.json"
    if not lock_file.is_file():
        raise RefreshError(["FIX6: Frozen de noviembre ausente; abortar sin cambios"])
    lock_raw = lock_file.read_bytes()
    lock = json.loads(lock_raw)
    if lock.get("schema") != "nexus.preview.frozen-lock/1" or lock.get("month") != "2026-11":
        raise RefreshError(["FIX6: lock de noviembre inválido"])
    for key in ("oe965", "portal"):
        v = lock.get(key, {})
        if not isinstance(v.get("sha256"), str) or len(v["sha256"]) != 64:
            raise RefreshError(["FIX6: Frozen de noviembre incompleto"])
    pointer = json.loads((data / "vigente.json").read_text("utf-8"))
    gen = (data / "generaciones" / pointer["gen_id"] / "generacion.json").read_bytes()
    gen_doc = json.loads(gen)
    if _sha(gen) != pointer.get("sha256") or gen_doc.get("month") != "2026-11":
        raise RefreshError(["FIX6: puntero/generación previa inválidos"])
    for lock_key, source in (("oe965", "frozen_r"), ("portal", "frozen_p")):
        prior = gen_doc.get("sources", {}).get(source, {})
        if prior.get("file") != lock[lock_key].get("file") or prior.get("sha256") != lock[lock_key].get("sha256"):
            raise RefreshError(["FIX6: Frozen de noviembre no concuerda con generación vigente"])
    if any(p.is_file() and p.name != "frozen_2026-11.json"
           for p in data.glob("frozen_20*.json")):
        raise RefreshError(["FIX6: hay otro Frozen mensual existente; requiere migración específica"])
    settings = orq.cargar_config(data)
    if not all((settings.get(k) or {}).get("activo") is True for k in
               ("backlog_ssrs", "fg_odbc", "embarques")):
        raise RefreshError(["FIX6: los tres conectores deben estar activos"])
    # Las acreditaciones de FG y Embarques siguen siendo independientes e inalteradas.
    if not (data / "acreditacion_fg_odbc.json").is_file():
        raise RefreshError(["FIX6: falta acreditación FG existente"])
    emb = data / "acreditacion_embarques.json"
    if not emb.is_file():
        raise RefreshError(["FIX6: falta acreditación CFDI existente"])
    emb_doc = json.loads(emb.read_text("utf-8"))
    if emb_doc.get("schema") != "nexus.preview.acreditacion-embarques/2" or not emb_doc.get("acreditado"):
        raise RefreshError(["FIX6: CFDI /2 no acreditado"])
    if not cfg.approvers or not cfg.approvers.is_file():
        raise RefreshError(["FIX6: falta lista privada de aprobadores"])
    backup_dir = data.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    token = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"preview_antes_fix6_{token}_{_sha(original)[:12]}.bak"
    with backup.open("xb") as out:
        out.write(original)
        out.flush()
        os.fsync(out.fileno())
    new = dict(body)
    new["ssrs_automatico"] = True
    _atomic(config, (json.dumps(new, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    if _sha(lock_file.read_bytes()) != _sha(lock_raw):
        raise RefreshError(["FIX6: Frozen de noviembre alterado inesperadamente"])
    return {"estado": "FIX6_ACTIVO", "frozen_noviembre": "INMUTABLE",
            "backup": str(backup), "frozen_lock_sha256": _sha(lock_raw),
            "ssrs": "CURRENT_AUTOMATICO", "proximo_mes": "PRIMER_SSRS_CONFIRMADO"}
