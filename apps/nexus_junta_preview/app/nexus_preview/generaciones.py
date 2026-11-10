"""Generaciones privadas e inmutables de Preview + Refresh aislado.

- Cada generación vive en ``generaciones/<gen_id>/generacion.json`` (solo lectura).
- ``vigente.json`` (puntero con SHA-256) se reemplaza de forma ATÓMICA (``os.replace``): la web
  lee siempre una generación completa, la anterior o la nueva, nunca una mezcla.
- ``frozen_<mes>.json`` se crea UNA vez (``O_EXCL``) con la primera generación del mes: un
  Refresh posterior jamás sustituye el Frozen; si el archivo bloqueado falta o cambia, el
  Refresh falla y la generación vigente se conserva.
- ``refresh.lock`` (``O_EXCL``) impide dos Refresh simultáneos (también entre procesos).
- No invoca ``stage()`` ni ``RefreshService``, no escribe SQL y no publica nada oficial.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
import shutil
import stat
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nexus.core.stage import check_private_output

from .conectores import ConectorError
from .conectores import orquestador as orq
from .servicio import Config, RefreshError, build_generation, vista_de

LOCK_STALE_SECONDS = 20 * 60
Clock = Callable[[], dt.datetime]


def _utc(clock: Clock) -> str:
    return clock().astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dumps(obj: Any) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")


def _write_atomic(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)  # atómico en el mismo volumen (POSIX y NTFS)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def code_version(*roots: Path) -> str:
    h = hashlib.sha256()
    for root in roots:
        for p in sorted(root.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


class Almacen:
    def __init__(self, data_dir: Path) -> None:
        self.data = data_dir
        self.gens = data_dir / "generaciones"
        self.pointer = data_dir / "vigente.json"
        self.gens.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- lectura
    def vigente(self) -> dict[str, Any] | None:
        if not self.pointer.exists():
            return None
        ptr = json.loads(self.pointer.read_text("utf-8"))
        path = self.gens / ptr["gen_id"] / "generacion.json"
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != ptr["sha256"]:
            raise RuntimeError("generación vigente alterada: la huella no coincide")
        doc = json.loads(data)
        doc["_puntero"] = ptr
        return doc

    def frozen_lock(self, month: str) -> dict[str, Any] | None:
        p = self.data / f"frozen_{month}.json"
        return json.loads(p.read_text("utf-8")) if p.exists() else None

    def historial(self) -> list[dict[str, Any]]:
        p = self.data / "refresh_log.jsonl"
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text("utf-8").splitlines() if x.strip()][::-1]

    # ---------------------------------------------------------------- escritura
    def commit(self, doc: dict[str, Any], committed_at: str) -> None:
        gen_id = doc["gen_id"]
        data = _dumps(doc)
        digest = hashlib.sha256(data).hexdigest()
        final = self.gens / gen_id
        if final.exists():
            existing = (final / "generacion.json").read_bytes()
            if hashlib.sha256(existing).hexdigest() != digest:
                raise RefreshError([f"generación {gen_id} existente con otro contenido"])
        else:
            tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=self.gens))
            target = tmp / "generacion.json"
            with target.open("xb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            target.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
            os.rename(tmp, final)
        month = doc["month"]
        lock = self.data / f"frozen_{month}.json"
        if not lock.exists():
            body = {
                "schema": "nexus.preview.frozen-lock/1",
                "month": month,
                "oe965": doc["sources"]["frozen_r"],
                "portal": doc["sources"]["frozen_p"],
                "bloqueado_en": committed_at,
                "gen_id": gen_id,
            }
            with lock.open("xb") as fh:  # una sola vez: nunca se reemplaza
                fh.write(_dumps(body))
            lock.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
        _write_atomic(
            self.pointer, _dumps({"gen_id": gen_id, "sha256": digest, "vigente_desde": committed_at})
        )


def fg_contexto(fgc: dict[str, Any], data: Path, ahora: str) -> dict[str, Any]:
    """v0.3.2 — de dónde sale el FG de esta generación (trazabilidad del corte)."""
    estado = fgc.get("estado")
    if estado == "CAPTURADO":
        return {"estado": "CONSULTA_DIRECTA", "archivo": f"fg_odbc:{fgc['archivo']}",
                "instante_consulta_utc": fgc.get("instante_consulta_utc"), "alcance": fgc.get("alcance"),
                "acreditacion_sha256": fgc.get("acreditacion_sha256")}
    if estado == "FALLO" and fgc.get("politica") == "CONSERVAR_ULTIMO_CORTE":
        try:
            cfg_fg = orq.cargar_config(data).get("fg_odbc") or {}
        except ConectorError:
            cfg_fg = {}
        return {"estado": "CONTINGENCIA", "codigo": fgc.get("codigo"), "motivo": fgc.get("motivo", ""), "intento_utc": ahora,
                "politica": "CONSERVAR_ULTIMO_CORTE", "max_horas": cfg_fg.get("contingencia_max_horas")}
    if estado == "NO_ACREDITADO":
        return {"estado": "NO_ACREDITADO", "motivo": fgc.get("motivo", "")}
    return {"estado": "MANUAL"}


def backlog_contexto(bc: dict[str, Any], data: Path, ahora: str) -> dict[str, Any]:
    """v0.3.3 — de dónde sale el Backlog Current de esta generación (trazabilidad del corte)."""
    estado = bc.get("estado")
    if estado == "CAPTURADO":
        interactivo = bc.get("modo") == "importacion_interactiva"
        return {
            "estado": "IMPORTACION_INTERACTIVA" if interactivo else "CONSULTA_DIRECTA",
            "archivo": f"backlog_ssrs:{bc['archivo']}",
            "instante_consulta_utc": bc.get("instante_consulta_utc"),
            "instante_corte_utc": bc.get("instante_utc"),
            "alcance": bc.get("alcance"),
            "acreditacion_sha256": bc.get("acreditacion_sha256"),
        }
    if estado == "FALLO" and bc.get("politica") == "CONSERVAR_ULTIMO_CORTE":
        try:
            cfg_b = orq.cargar_config(data).get("backlog_ssrs") or {}
        except ConectorError:
            cfg_b = {}
        return {
            "estado": "CONTINGENCIA",
            "codigo": bc.get("codigo"),
            "motivo": bc.get("motivo", ""),
            "intento_utc": ahora,
            "politica": "CONSERVAR_ULTIMO_CORTE",
            "max_horas": cfg_b.get("contingencia_max_horas"),
        }
    if estado == "NO_ACREDITADO":
        return {"estado": "NO_ACREDITADO", "motivo": bc.get("motivo", "")}
    return {"estado": "MANUAL"}


def activar(store: Almacen, gen_id: str, when: str) -> dict[str, Any]:
    """Rollback de la VISTA: vuelve a apuntar a una generación existente (verificada). No toca el
    Frozen bloqueado, las fuentes ni las notas CS."""
    path = store.gens / gen_id / "generacion.json"
    if not path.is_file():
        raise RefreshError([f"generación {gen_id} inexistente"])
    data = path.read_bytes()
    doc = json.loads(data)
    if doc.get("gen_id") != gen_id:
        raise RefreshError([f"generación {gen_id} inconsistente"])
    digest = hashlib.sha256(data).hexdigest()
    _write_atomic(store.pointer, _dumps({"gen_id": gen_id, "sha256": digest, "vigente_desde": when, "rollback": True}))
    return {"gen_id": gen_id, "sha256": digest}


class PreviewRefresh:
    """Refresh de laboratorio. Estados: RUNNING, PASS, NO_CHANGE, ERROR (BUSY si ya hay uno en curso)."""

    def __init__(
        self,
        data_dir: Path,
        code: str,
        clock: Clock | None = None,
        hook: Callable[[str], None] | None = None,
        deps: dict[str, Any] | None = None,
    ) -> None:
        self.deps = deps or {}
        self.data = data_dir
        self.store = Almacen(data_dir)
        self.code = code
        self.clock = clock or (lambda: dt.datetime.now(dt.UTC))
        self.hook = hook
        self.status_path = data_dir / "estado_refresh.json"
        self.lock_path = data_dir / "refresh.lock"
        self._thread_lock = threading.Lock()

    def config(self) -> Config:
        return Config.load(self.data / "preview.json")

    def estado(self) -> dict[str, Any]:
        if not self.status_path.exists():
            return {"estado": "NOT_RUN"}
        return json.loads(self.status_path.read_text("utf-8"))

    def _acquire(self) -> bool:
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - self.lock_path.stat().st_mtime
            except FileNotFoundError:
                return self._acquire()
            if age < LOCK_STALE_SECONDS:
                return False
            self.lock_path.unlink(missing_ok=True)  # candado abandonado (proceso caído)
            return self._acquire()
        with os.fdopen(fd, "w") as fh:
            fh.write(f"{os.getpid()} {_utc(self.clock)}\n")
        return True

    def iniciar(self, actor: str) -> bool:
        """Para la web: reserva el candado y ejecuta en segundo plano. False = ocupado."""
        if not self._thread_lock.acquire(blocking=False):
            return False
        if not self._acquire():
            self._thread_lock.release()
            return False
        self._write_status({"estado": "RUNNING", "inicio": _utc(self.clock), "actor": actor})
        threading.Thread(target=self._run_locked, args=(actor,), daemon=True).start()
        return True

    def ejecutar(self, actor: str) -> dict[str, Any]:
        """Síncrono (CLI y pruebas)."""
        with self._thread_lock:
            if not self._acquire():
                return {"estado": "BUSY", "motivos": ["otro Refresh está en curso"]}
            self._write_status({"estado": "RUNNING", "inicio": _utc(self.clock), "actor": actor})
            return self._run(actor)

    def _run_locked(self, actor: str) -> None:
        try:
            self._run(actor)
        finally:
            self._thread_lock.release()

    def _run(self, actor: str) -> dict[str, Any]:
        start = _utc(self.clock)
        previous = None
        status: dict[str, Any]
        work = Path(tempfile.mkdtemp(prefix="_work_", dir=self.data))
        try:
            cfg = self.config()
            check_private_output(self.data, cfg.src)
            try:
                previous = self.store.vigente()
            except Exception as exc:  # noqa: BLE001 (se informa)
                raise RefreshError([f"generación vigente ilegible: {exc}"]) from exc
            # v0.3: conectores corporativos de solo lectura (cada uno con su identidad y política)
            try:
                conectores = orq.capturar_todo(self.data, cfg.month, cfg.tz, self.clock, self.deps)
            except ConectorError as exc:
                raise RefreshError([f"configuración de conectores: {exc}"]) from exc
            fallos = {k: v for k, v in conectores.items() if v["estado"] == "FALLO"}
            detener = [f"{k}: {v['codigo']} {v.get('motivo', '')}".strip() for k, v in fallos.items() if v.get("politica") == "DETENER"]
            fgc = conectores.get("fg_odbc") or {}
            bc = conectores.get("backlog_ssrs") or {}
            if bc.get("estado") == "NO_ACREDITADO" and bc.get("politica") == "DETENER":
                # v0.3.3: un Backlog SSRS ACTIVO sin acreditación válida sigue su política
                detener.append(f"backlog_ssrs: NO_ACREDITADO {bc.get('motivo', '')}".strip())
            if fgc.get("estado") == "NO_ACREDITADO" and fgc.get("politica") == "DETENER":
                # v0.3.2: un conector FG ACTIVO sin acreditación válida sigue su política explícita
                detener.append(f"fg_odbc: NO_ACREDITADO {fgc.get('motivo', '')}".strip())
            if detener:
                raise RefreshError([f"conector con política DETENER falló — {m}" for m in detener])
            try:
                capturas = orq.cargar_capturas(
                    self.data, cfg.month, orq.habilitadas(conectores), self.store.frozen_lock(cfg.month),
                    fg_archivo=fgc.get("archivo") if fgc.get("estado") == "CAPTURADO" else None,
                    backlog_archivo=bc.get("archivo") if bc.get("estado") == "CAPTURADO" else None,
                )
            except ConectorError as exc:
                raise RefreshError([f"captura inválida: {exc}"]) from exc
            aprobadores: frozenset[str] = frozenset()
            if cfg.approvers and cfg.approvers.is_file():
                aprobadores = frozenset(json.loads(cfg.approvers.read_text("utf-8")).get("approvers", []))
            emb = orq.embarques_generacion(
                self.data, cfg.month, aprobadores, "embarques" in orq.habilitadas(conectores),
                conectores.get("embarques"),
            )
            emb["intento_utc"] = start  # FIX5: antigüedad del corte en contingencia
            contexto = {
                "fg_conector": fg_contexto(fgc, self.data, start),
                "backlog_conector": backlog_contexto(bc, self.data, start),
            }
            lock = self.store.frozen_lock(cfg.month)
            doc = build_generation(cfg, lock, work, self.code, capturas, emb, **contexto)
            if self.hook:
                self.hook("antes_de_conmutar")
            vista = doc["vista_operativa"]
            esc = doc["escenarios"][vista["escenario"]]
            avisos = list(doc["warnings"])
            for k, v in fallos.items():
                avisos.append(f"ACTUALIZACIÓN PARCIAL — {k}: {v['codigo']} ({v.get('motivo', '')}); se conserva su último corte válido")
            cambio_vista = cambio_aprob = False
            if previous is not None:
                prev_vista = vista_de(previous)
                cambio_vista = (prev_vista["modo"], prev_vista["escenario"]) != (vista["modo"], vista["escenario"])
                cambio_aprob = previous.get("aprobaciones") is not None and previous["aprobaciones"] != doc["aprobaciones"]
                if cambio_aprob:
                    avisos.append("CAMBIO EN APROBACIONES R1/R3 respecto de la generación anterior: requiere validación")
                if cambio_vista:
                    avisos.append(
                        f"CAMBIO DE VISTA OPERATIVA: {prev_vista['modo']}/{prev_vista['escenario']} → "
                        f"{vista['modo']}/{vista['escenario']} (por cambio de aprobaciones; no es publicación)"
                    )
            base = {
                "inicio": start,
                "fin": _utc(self.clock),
                "actor": actor,
                "gen_id": doc["gen_id"],
                "fuentes": {k: v["file"] for k, v in doc["sources"].items()},
                "articulos": esc["kpis"]["items"],
                "escenario": vista["escenario"],
                "vista": {"modo": vista["modo"], "escenario": vista["escenario"]},
                "cambio_vista": cambio_vista,
                "cambio_aprobaciones": cambio_aprob,
                "conectores": conectores,
                "parcial": bool(fallos),
                "advertencias": avisos,
                "motivos": [],
            }
            if previous and previous["gen_id"] == doc["gen_id"]:
                status = {**base, "estado": "NO_CHANGE"}
            else:
                self.store.commit(doc, base["fin"])
                status = {**base, "estado": "PASS", "gen_anterior": previous["gen_id"] if previous else None}
        except RefreshError as exc:
            status = {
                "estado": "ERROR",
                "inicio": start,
                "fin": _utc(self.clock),
                "actor": actor,
                "motivos": exc.reasons,
                "gen_vigente_conservada": previous["gen_id"] if previous else None,
            }
        except Exception as exc:  # noqa: BLE001 (nunca destruye la vista vigente)
            status = {
                "estado": "ERROR",
                "inicio": start,
                "fin": _utc(self.clock),
                "actor": actor,
                "motivos": [f"error interno: {type(exc).__name__}: {str(exc)[:300]}"],
                "gen_vigente_conservada": previous["gen_id"] if previous else None,
            }
        finally:
            shutil.rmtree(work, ignore_errors=True)
            self.lock_path.unlink(missing_ok=True)
        self._write_status(status)
        with (self.data / "refresh_log.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(status, ensure_ascii=False, sort_keys=True) + "\n")
        return status

    def _write_status(self, status: dict[str, Any]) -> None:
        _write_atomic(self.status_path, _dumps(status))
