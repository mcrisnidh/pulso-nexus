"""C6 — Web mínima de CORE v1.0 (Flask, server-rendered, sin JavaScript).

Controles (ADR-0010): solo loopback (lo impone ``nexus web``), sin debug, autenticación
enchufable (``DevAuth`` sintético en el Hito A; rechazada en el canal operativo), roles con
denegación por defecto, CSRF en todo POST, cookies HttpOnly/SameSite=Strict, CSP sin
``unsafe-inline``, autoescape de Jinja2 y lectura SOLO de lo publicado con verificación de hash.
La web no importa motores ni adaptadores (contrato import-linter).
"""

from __future__ import annotations

import datetime as dt
import secrets
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from flask import Flask, abort, g, redirect, render_template, request, session, url_for
from werkzeug.wrappers import Response

from nexus.core.cs import (
    KINDS,
    Association,
    CsConflictError,
    CsStore,
    CsValidationError,
    board_state,
)
from nexus.core.read import PublishedReader
from nexus.core.refresh import RefreshService, iso
from nexus.core.state import PublicationConflictError, PublicationRefusedError
from nexus.errors import IntegrityError
from nexus.storage.object_store import LocalObjectStore
from nexus.web import present
from nexus.web.auth import DevAuth

Clock = Callable[[], dt.datetime]
ROLES = ("reader", "cs_editor", "refresher", "admin")
CSP = (
    "default-src 'self'; style-src 'self'; img-src 'self'; form-action 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)


def create_app(  # noqa: PLR0915 (rutas declaradas en la factory)
    data_dir: Path,
    channel: str,
    secret_key: str,
    clock: Clock | None = None,
    auth: DevAuth | None = None,
    refresh_hook: Callable[[str], None] | None = None,
    background: bool = True,
) -> Flask:
    if not secret_key or len(secret_key) < 32:
        raise ValueError("NEXUS_WEB_SECRET obligatorio (≥ 32 caracteres)")
    auth = auth or DevAuth.from_file(data_dir / "users.json")
    if channel == "operativo" and auth.kind == "DEV":
        raise ValueError("DevAuth (usuarios sintéticos) no se admite en el canal operativo")
    service = RefreshService(
        data_dir,
        channel,
        data_dir / "rules.json",
        **({"clock": clock} if clock else {}),
        hook=refresh_hook,
    )
    cs = CsStore(LocalObjectStore(data_dir / "cs"), service.rules.cs_status_vocabulary)
    reader = PublishedReader(service.state, channel)
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=False,
        MAX_CONTENT_LENGTH=64 * 1024,
        DEBUG=False,
        TESTING=False,
        PROPAGATE_EXCEPTIONS=False,
    )
    app.extensions["nexus"] = {"service": service, "cs": cs, "reader": reader, "auth": auth}
    worker_lock = threading.Lock()

    # ------------------------------------------------------------------ utilidades
    def user() -> dict[str, Any] | None:
        name = session.get("user")
        return auth.user(name) if isinstance(name, str) else None

    def require(role: str) -> dict[str, Any]:
        u = user()
        if u is None:
            abort(401)
        if role not in u["roles"]:
            abort(403)
        return u

    def check_csrf() -> None:
        sent = request.form.get("csrf", "")
        token = session.get("csrf", "")
        if not token or not secrets.compare_digest(str(sent), str(token)):
            abort(403)

    def now() -> str:
        return iso(service.clock())

    @app.before_request
    def _load() -> None:
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        g.user = user()

    @app.after_request
    def _headers(resp: Response) -> Response:
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(401)
    def _unauth(_e: Any) -> Any:
        return redirect(url_for("login"))

    @app.errorhandler(403)
    def _forbidden(_e: Any) -> Any:
        return render_template(
            "error.html",
            code=403,
            message="Permiso denegado: su rol no permite esta acción o el formulario expiró.",
        ), 403

    @app.errorhandler(404)
    def _missing(_e: Any) -> Any:
        return render_template("error.html", code=404, message="No encontrado"), 404

    @app.errorhandler(500)
    def _boom(_e: Any) -> Any:
        return render_template("error.html", code=500, message="Error interno"), 500

    @app.context_processor
    def _ctx() -> dict[str, Any]:
        return {
            "user": g.get("user"),
            "csrf": session.get("csrf", ""),
            "channel": channel,
            "month": service.current_month(),
            "fmt": fmt_value,
            "nav": request.endpoint,
        }

    for name in (
        "num",
        "date_es",
        "datetime_es",
        "month_es",
        "status_label",
        "kind_label",
        "event_label",
    ):
        app.add_template_filter(getattr(present, name), name)
    app.add_template_global(present.cs_value, "cs_value")

    # ------------------------------------------------------------------ autenticación
    @app.get("/login")
    def login() -> Any:
        return render_template("login.html", error=None)

    @app.post("/login")
    def login_post() -> Any:
        check_csrf()
        name = request.form.get("username", "")
        if auth.verify(name, request.form.get("password", "")):
            session.clear()
            session["user"] = name
            session["csrf"] = secrets.token_urlsafe(32)
            return redirect(url_for("board"))
        return render_template("login.html", error="Credenciales inválidas"), 401

    @app.post("/logout")
    def logout() -> Any:
        check_csrf()
        session.clear()
        return redirect(url_for("login"))

    @app.get("/healthz")
    def healthz() -> Any:
        return {"status": "ok"}

    # ------------------------------------------------------------------ junta
    def cs_board(month: str, items: list[str]) -> dict[str, Any]:
        out = {}
        for item in items:
            threads = cs.threads_for_item(item)
            states = [{**t, "state": board_state(t["versions"], month)} for t in threads]
            current: dict[str, Any] = {}
            last_note = None
            for st in states:
                for kind, v in st["state"]["current"].items():
                    if kind not in current or v["server_ts"] > current[kind]["server_ts"]:
                        current[kind] = v
                note = st["state"]["last_note"]
                if note and (last_note is None or note["server_ts"] > last_note["server_ts"]):
                    last_note = note
            out[item] = {"current": current, "last_note": last_note, "threads": len(threads)}
        return out

    @app.get("/")
    def board() -> Any:
        require("reader")
        pub = reader.current()
        items = sorted(pub.summary["rows"]) if pub.summary else []
        month = service.current_month()
        rows = present.board_rows(pub.summary, cs_board(month, items)) if pub.summary else []
        args = {
            k: request.args.get(k, "")
            for k in ("q", "cobertura", "estatus", "excepciones", "orden")
        }
        shown = present.apply_filters(rows, args)
        events = service.state.events(channel)
        return render_template(
            "board.html",
            pub=pub,
            rows=shown,
            total_rows=len(rows),
            args=args,
            totals=present.totals_view(pub.summary["totals"], rows) if pub.summary else None,
            failure=present.last_refresh_failure(events),
            failure_reasons=present.failure_reasons,
            vocabulary=service.rules.cs_status_vocabulary,
            alerts=service.state.alerts(channel, month),
        )

    @app.get("/item")
    def item_view() -> Any:
        require("reader")
        item = request.args.get("i", "")
        pub = reader.current()
        detail, error = None, None
        if pub.manifest is not None:
            try:
                detail = reader.item(pub.manifest, item)
            except KeyError:
                abort(404)
            except IntegrityError as exc:
                error = str(exc)
        month = service.current_month()
        threads = (
            [{**t, "state": board_state(t["versions"], month)} for t in cs.threads_for_item(item)]
            if item
            else []
        )
        if item and not any(
            not (t["customer_key"] or t["order"] or t["line"]) for t in threads
        ):  # seguimiento general del ITEM, aún sin versiones (solo presentación)
            general: dict[str, Any] = {
                "thread_id": None,
                "item": item,
                "customer_key": "",
                "order": "",
                "line": "",
                "versions": [],
            }
            threads.insert(0, {**general, "state": board_state([], month)})
        future = [f for f in (pub.summary or {}).get("future_lines", []) if f["item"] == item]
        values = (
            {k: present.to_dec(detail["coverage"]["facts"][k]["value"]) for k in present.METRICS}
            if detail
            else {}
        )
        return render_template(
            "item.html",
            item=item,
            pub=pub,
            detail=detail,
            error=error,
            threads=threads,
            kinds=KINDS,
            vocabulary=service.rules.cs_status_vocabulary,
            future=future,
            item_bar=present.coverage_bar(values) if detail else None,
            saved=request.args.get("ok", ""),
            saved_version=request.args.get("v", ""),
        )

    @app.get("/cs/history")
    def cs_history() -> Any:
        require("reader")
        thread = request.args.get("t", "")
        if not thread.isalnum() or len(thread) != 32:
            abort(404)
        versions = cs.history(thread)
        if not versions:
            abort(404)
        retracted = {v["value"]: v["version"] for v in versions if v["kind"] == "RETRACT"}
        return render_template("cs_history.html", versions=versions, retracted=retracted)

    @app.post("/cs")
    def cs_append() -> Any:
        u = require("cs_editor")
        check_csrf()
        f = request.form
        kind = f.get("kind", "")
        value: Any = f.get("value", "")
        if kind == "ADVANCE":
            value = {"qty": f.get("value", ""), "date": f.get("date", "")}
        elif kind == "RETRACT":
            value = int(f.get("value", "0")) if f.get("value", "").isdigit() else 0
        try:
            assoc = Association(
                f.get("item", ""), f.get("customer_key", ""), f.get("order", ""), f.get("line", "")
            )
            expected = int(f.get("expected_version", "-1"))
            head = service.state.publication_head(channel)
            saved = cs.append(
                assoc,
                kind,
                value,
                author=u["name"],
                expected_version=expected,
                server_ts=now(),
                month=service.current_month(),
                reason=f.get("reason", ""),
                published_gen=head["gen_id"] if head else None,
            )
        except CsConflictError as exc:
            return render_template(
                "error.html",
                code=409,
                title="Otro usuario guardó un cambio antes",
                message=f"No se guardó su versión ({exc}). Nada se perdió: abra de nuevo el "
                "ITEM para ver la versión vigente y vuelva a capturar su cambio.",
                back=url_for("item_view", i=f.get("item", "")),
            ), 409
        except (CsValidationError, ValueError) as exc:
            return render_template(
                "error.html",
                code=400,
                title="Dato no válido",
                message=f"No se guardó: {exc}.",
                back=url_for("item_view", i=f.get("item", "")) if f.get("item") else None,
            ), 400
        return redirect(
            url_for("item_view", i=assoc.item, ok=kind, v=saved["version"], _anchor="cs")
        )

    # ------------------------------------------------------------------ Refresh y publicación
    def _work() -> None:
        with worker_lock:
            service.run_pending(worker="web-worker")

    @app.post("/refresh")
    def refresh() -> Any:
        u = require("refresher")
        check_csrf()
        try:
            req = service.request(actor=u["name"])
        except PublicationRefusedError as exc:
            return render_template("error.html", code=409, message=str(exc)), 409
        if background:
            threading.Thread(target=_work, daemon=True, name="nexus-refresh").start()
        else:
            _work()
        return redirect(url_for("refresh_status", k=req["key"]))

    def refresh_view(key: str) -> dict[str, Any]:
        """Estado legible de una solicitud. Solo dice "publicado" si la generación de la solicitud
        figura en el historial de publicación; nunca lo supone."""
        status = service.status(key)
        events = service.state.events(channel)
        if status is None:
            return {"phase": "unknown", "events": events}
        rid = int(status["request_id"])
        view: dict[str, Any] = {"status": status, "events": events, "request_id": rid}
        state = status["state"]
        if state == "PENDING":
            return {**view, "phase": "pending"}
        if state in ("CLAIMED", "RUNNING"):
            return {**view, "phase": "running"}
        if state == "FAILED_PERMANENT":
            fails = [
                e
                for e in events
                if e["kind"] == "REFRESH_ATTEMPT_FAILED" and e["detail"].get("request_id") == rid
            ]
            return {**view, "phase": "failed", "reasons": [str(e["detail"]) for e in fails]}
        gen = next(
            (
                str(g["store_run_id"])
                for g in service.repo.list_generations()
                if g["request_id"] == rid and g["sealed_at"]
            ),
            None,
        )
        if gen is None:
            return {**view, "phase": "running"}
        manifest = service.state.get_manifest(channel, gen)
        if manifest["validation"]["status"] != "PASS":
            reasons = [
                *manifest["validation"]["failures"],
                *manifest["validation"]["invariant_violations"],
            ]
            return {**view, "phase": "not_published", "gen": gen, "reasons": reasons}
        pubs = [p for p in service.state.publication_history(channel) if p["gen_id"] == gen]
        refused = [
            e for e in events if e["kind"] == "PUBLISH_REFUSED" and e["detail"].get("gen_id") == gen
        ]
        requested = [
            e["server_ts"]
            for e in events
            if e["kind"] == "REFRESH_REQUESTED" and e["detail"].get("idempotency_key") == key
        ]
        if pubs and requested and pubs[-1]["server_ts"] < requested[-1]:
            head = service.state.publication_head(channel)
            return {
                **view,
                "phase": "unchanged",
                "gen": gen,
                "publication": pubs[-1],
                "is_current": bool(head and head["gen_id"] == gen),
            }
        if pubs:
            head = service.state.publication_head(channel)
            return {
                **view,
                "phase": "published",
                "gen": gen,
                "publication": pubs[-1],
                "is_current": bool(head and head["gen_id"] == gen),
            }
        if refused:
            return {
                **view,
                "phase": "not_published",
                "gen": gen,
                "reasons": [str(e["detail"].get("reason")) for e in refused],
            }
        return {**view, "phase": "publishing", "gen": gen}

    @app.get("/refresh/status")
    def refresh_status() -> Any:
        require("reader")
        key = request.args.get("k", "")
        view = refresh_view(key)
        return render_template(
            "refresh_status.html",
            key=key,
            view=view,
            head=service.state.publication_head(channel),
            auto_reload=view["phase"] in ("pending", "running", "publishing"),
        )

    @app.get("/history")
    def history() -> Any:
        require("reader")
        publications = reader.history()
        return render_template(
            "history.html",
            publications=publications,
            head=publications[-1] if publications else None,
            events=reader.events(),
            chain=service.state.verify_publication_chain(channel),
        )

    @app.get("/admin/rollback")
    def rollback_confirm() -> Any:
        require("admin")
        gen = request.args.get("gen", "")
        publications = reader.history()
        target = next((p for p in reversed(publications) if p["gen_id"] == gen), None)
        if target is None:
            abort(404)
        return render_template("rollback_confirm.html", target=target, head=publications[-1])

    @app.post("/admin/rollback")
    def rollback() -> Any:
        u = require("admin")
        check_csrf()
        try:
            service.rollback(request.form.get("gen_id", ""), actor=u["name"])
        except (PublicationRefusedError, PublicationConflictError) as exc:
            return render_template("error.html", code=409, message=str(exc)), 409
        return redirect(url_for("history"))

    return app


def fmt_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, dict):
        return "; ".join(f"{k}={v}" for k, v in sorted(value.items()))
    return str(value)
