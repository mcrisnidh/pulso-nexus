"""PULSO NEXUS — Junta Operativa v0.2 (laboratorio local, PREVIEW / NO-GO COMERCIAL).

Corrección del defecto de PULSO Web: allí ``STATUS_ROWS = rows("status_ventas_oct2026.csv")`` se
leía UNA vez al importar ``app.py`` y el Refresh no la regeneraba. Aquí cada petición lee la
GENERACIÓN VIGENTE (puntero atómico + SHA-256) que produce el Refresh con el motor NEXUS; un
recargo del navegador solo vuelve a leer, nunca recalcula.

Seguridad: solo 127.0.0.1; validación de Host (anti DNS-rebinding); CSRF + mismo origen en todo
POST; CSP sin scripts en línea; la web escribe únicamente el almacén CS privado (append-only).
Sin autenticación corporativa: el autor se registra como ``EDITOR_LOCAL_SIN_AUTENTICAR``.
"""

from __future__ import annotations

import datetime as dt
import secrets
import zoneinfo
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.wrappers import Response

from nexus.core import demo
from nexus.core.cs import Association, CsConflictError, CsStore, CsValidationError, board_state
from nexus.storage.object_store import LocalObjectStore

from . import status_v310 as v310
from .generaciones import PreviewRefresh
from .r3_admin import R3Admin, R3AdminError, next_month_utc
from .servicio import evaluar_certificacion, vista_de

EDITOR = "EDITOR_LOCAL_SIN_AUTENTICAR"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)
FLAGS = {
    "brecha": "Con brecha diagnóstica (Frozen − FG)",
    "sin_cobertura": "Sin cobertura / parcial (diagnóstica)",
    "desconocido": "Datos desconocidos",
    "pendiente": "Pendiente R1/R3",
}
ESTADOS = {
    "NOT_RUN": "Sin Refresh",
    "RUNNING": "Actualizando…",
    "PASS": "Actualización correcta",
    "NO_CHANGE": "Sin cambios",
    "ERROR": "Error: se conserva la vista vigente",
    "BUSY": "Otro Refresh en curso",
}
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def fmt_num(value: Any) -> str:
    d = dec(value)
    if d is None:
        return "N/D"
    if d == d.to_integral():
        return f"{int(d):,}"
    return f"{d:,.3f}".rstrip("0").rstrip(".")


def fmt_date(value: Any) -> str:
    if not value:
        return "—"
    try:
        d = dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value)
    return f"{d.day:02d}-{MESES[d.month - 1]}"


def fmt_dt(value: Any) -> str:
    if not value:
        return "—"
    try:
        moment = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    return f"{moment.day:02d}-{MESES[moment.month - 1]}-{moment.year} {moment:%H:%M} UTC"


def create_app(data_dir: Path, port: int = 8877, code: str = "dev", refresher: PreviewRefresh | None = None) -> Flask:
    refresher = refresher or PreviewRefresh(data_dir, code)
    vocabulary = tuple(demo.RULES["cs_status_vocabulary"])
    cs = CsStore(LocalObjectStore(data_dir / "cs"), vocabulary)
    r3 = R3Admin(Path(__file__).with_name("r3_catalogo_visual.json"), data_dir / "r3_admin")
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secrets.token_hex(32),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        MAX_CONTENT_LENGTH=64 * 1024,
        DEBUG=False,
        PROPAGATE_EXCEPTIONS=False,
    )
    for name, fn in (("num", fmt_num), ("fecha", fmt_date), ("fechahora", fmt_dt)):
        app.add_template_filter(fn, name)
    app.extensions["preview"] = {"refresher": refresher, "cs": cs, "allowed_hosts": allowed_hosts}

    def tz() -> zoneinfo.ZoneInfo:
        try:
            return zoneinfo.ZoneInfo(refresher.config().tz)
        except Exception:  # noqa: BLE001
            return zoneinfo.ZoneInfo("UTC")

    def now_utc() -> str:
        return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    @app.before_request
    def _guard() -> None:
        if request.host not in app.extensions["preview"]["allowed_hosts"]:
            abort(400)
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        if request.method == "POST":
            sent = request.headers.get("X-CSRF") or request.form.get("csrf", "")
            if not secrets.compare_digest(str(sent), str(session["csrf"])):
                abort(403)
            origin = request.headers.get("Origin") or request.headers.get("Referer")
            if origin and urlparse(origin).netloc not in app.extensions["preview"]["allowed_hosts"]:
                abort(403)

    @app.after_request
    def _headers(resp: Response) -> Response:
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def _error(code: int, message: str) -> Any:
        if request.path.startswith("/api/") or request.headers.get("X-CSRF") is not None:
            return jsonify({"ok": False, "error": message}), code
        return render_template("error.html", code=code, message=message), code

    app.register_error_handler(400, lambda _e: _error(400, "Solicitud rechazada (host no permitido)."))
    app.register_error_handler(403, lambda _e: _error(403, "Solicitud no autorizada: recargue la página."))
    app.register_error_handler(404, lambda _e: _error(404, "No encontrado."))
    app.register_error_handler(500, lambda _e: _error(500, "Error interno."))

    @app.context_processor
    def _ctx() -> dict[str, Any]:
        st = refresher.estado()
        try:
            g = refresher.store.vigente()
        except Exception:  # noqa: BLE001
            g = None
        return {
            "vista_global": vista_de(g) if g else None,
            "csrf": session.get("csrf", ""),
            "estado_refresh": st,
            "estado_label": "Actualización parcial" if st.get("parcial") else ESTADOS.get(st.get("estado", ""), st.get("estado", "")),
            "editor": EDITOR,
        }

    def current() -> dict[str, Any] | None:
        return refresher.store.vigente()

    def operational(gen: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Vista operativa ÚNICA: la fija la regla de aprobaciones de la generación. No admite
        parámetros de escenario (A/B/DIAG solo existen en el módulo de diagnóstico)."""
        name = vista_de(gen)["escenario"]
        return name, gen["escenarios"][name]

    def diagnostic(gen: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        name = request.args.get("esc") or "A"
        if name not in gen["escenarios"]:
            abort(404)
        return name, gen["escenarios"][name]

    def cs_state(item: str, month: str) -> dict[str, Any]:
        versions = cs.history(Association(item).thread_id)
        st = board_state(versions, month)
        today = dt.datetime.now(tz()).date().isoformat()
        notes = [v for v in versions if v["kind"] == "NOTE"]
        retracted = {v["value"] for v in versions if v["kind"] == "RETRACT"}
        notes = [v for v in notes if v["version"] not in retracted]

        def local_day(v: dict[str, Any]) -> str:
            moment = dt.datetime.fromisoformat(v["server_ts"].replace("Z", "+00:00"))
            return moment.astimezone(tz()).date().isoformat()

        today_notes = [v for v in notes if local_day(v) == today]
        cur = st["current"]
        return {
            "version": len(versions),
            "nota_hoy": today_notes[-1] if today_notes else None,
            "nota_anterior": None if today_notes or not notes else notes[-1],
            "adelanto": cur.get("ADVANCE"),
            "estatus": cur.get("CS_STATUS"),
            "entrega": cur.get("DELIVERY_DATE"),
            "referencia": st["reference"],
            "hoy": today,
        }

    def enrich(gen: dict[str, Any], esc: dict[str, Any], r: dict[str, Any]) -> dict[str, Any]:
        """Aplica v3.10: sin registro de adelanto mensual equivale a 0, no N/D."""
        c = cs_state(r["item"], gen["month"])
        adelanto = dec(c["adelanto"]["value"]["qty"]) if c["adelanto"] else Decimal(0)
        frozen, fg, emb = dec(r["frozen"]["value"]), dec(r["fg"]["value"]), dec(r.get("embarques"))
        prog = v310.programa(frozen, adelanto, emb)
        weeks = None if not r["semanas_frozen_completas"] else [
            (w["wk"], Decimal(q)) for w, q in zip(esc["semanas"], r["semanas_frozen"], strict=True)
        ]
        hasta, primera = v310.cobertura_fg(weeks, emb, fg)
        return {
            **r,
            "cs_state": c,
            "adelanto": adelanto,
            "programa_v310": prog,
            "sobreembarque_v310": v310.sobreembarque(frozen, adelanto, emb),
            "faltante_v310": v310.faltante(prog, fg),
            "cobertura_hasta": hasta,
            "faltante_semana": primera,
        }

    # ---------------------------------------------------------------- Junta Operativa
    @app.get("/")
    def index() -> Any:
        return redirect(url_for("junta"))

    @app.get("/daily")
    def daily() -> Any:  # ruta histórica de PULSO Web
        return redirect(url_for("junta", **request.args))

    @app.get("/junta")
    def junta() -> Any:
        gen = current()
        if gen is None:
            return render_template("vacio.html", active="junta")
        name, esc = operational(gen)
        q = (request.args.get("q") or "").strip().upper()
        f_cs, f_grupo = request.args.get("cs", ""), request.args.get("grupo", "")
        f_flag, f_week = request.args.get("flag", ""), (request.args.get("week") or "").upper()
        week_idx = next((i for i, w in enumerate(esc["semanas"]) if w["wk"] == f_week), None)
        all_rows = [enrich(gen, esc, r) for r in esc["rows"]]
        all_rows.sort(key=v310.orden)
        for n, r in enumerate(all_rows, start=1):
            r["prioridad"] = n
        view = []
        for r in all_rows:
            hay = " ".join([r["item"], r["cliente"], r.get("cliente_nombre") or "", r["grupo"] or "", r["cs"] or ""]).upper()
            if q and q not in hay:
                continue
            if f_cs and (r["cs"] or "Sin asignar") != f_cs:
                continue
            if f_grupo and (r["grupo"] or "Sin grupo") != f_grupo:
                continue
            if f_flag and not r["flags"].get(f_flag):
                continue
            if week_idx is not None and Decimal(r["semanas_frozen"][week_idx]) <= 0:
                continue
            view.append(r)

        def total(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
            vals = [dec(r[key]["value"]) for r in rows]
            return {"suma": sum((v for v in vals if v is not None), Decimal(0)), "nd": sum(1 for v in vals if v is None)}

        def total_v310(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
            """FIX5: suma por ITEM del contrato v3.10; un dato ausente NO se suma como 0."""
            vals = [dec(r.get(key)) for r in rows]
            conocidos = [v for v in vals if v is not None]
            return {
                "suma": sum(conocidos, Decimal(0)),
                "nd": len(vals) - len(conocidos),
                "conocidos": len(conocidos),
            }

        indicadores = {
            k: total_v310(all_rows, k)
            for k in ("embarques", "programa_v310", "sobreembarque_v310", "faltante_v310")
        }
        totales = {
            "universo": {k: total(all_rows, k) for k in ("frozen", "actual", "fg")} | {"n": len(all_rows)},
            "filtro": {k: total(view, k) for k in ("frozen", "actual", "fg")} | {"n": len(view)},
        }
        return render_template(
            "junta.html",
            active="junta",
            vista=vista_de(gen),
            gen=gen,
            esc_name=name,
            esc=esc,
            rows=view,
            totales=totales,
            indicadores=indicadores,
            filtros={"q": request.args.get("q", ""), "cs": f_cs, "grupo": f_grupo, "flag": f_flag, "week": f_week if week_idx is not None else ""},
            all_cs=sorted({r["cs"] or "Sin asignar" for r in all_rows}),
            all_grupos=sorted({r["grupo"] or "Sin grupo" for r in all_rows}),
            flags=FLAGS,
            vocab=vocabulary,
        )

    @app.get("/item")
    def item() -> Any:
        gen = current()
        if gen is None:
            abort(404)
        name, esc = operational(gen)
        row = next((r for r in esc["rows"] if r["item"] == request.args.get("id", "")), None)
        if row is None:
            abort(404)
        hist = cs.history(Association(row["item"]).thread_id)
        return render_template("item.html", active="junta", vista=vista_de(gen), diag=False, gen=gen, esc_name=name, esc=esc, row=enrich(gen, esc, row), hist=hist[::-1])

    @app.get("/escenarios/item")
    def escenarios_item() -> Any:
        gen = current()
        if gen is None:
            abort(404)
        name, esc = diagnostic(gen)
        row = next((r for r in esc["rows"] if r["item"] == request.args.get("id", "")), None)
        if row is None:
            abort(404)
        hist = cs.history(Association(row["item"]).thread_id)
        return render_template("item.html", active="escenarios", vista=vista_de(gen), diag=True, gen=gen, esc_name=name, esc=esc, row=enrich(gen, esc, row), hist=hist[::-1])

    @app.get("/api/certificacion")
    def certificacion() -> Any:
        gen = current()
        if gen is None:
            abort(404)
        return jsonify({"gen_id": gen["gen_id"], "vista": vista_de(gen), **evaluar_certificacion(gen)})

    @app.post("/api/certificar")
    def certificar() -> Any:
        """Preview NUNCA certifica: rechaza siempre e informa por qué (sin aprobaciones, o porque la
        certificación y la publicación requieren controles independientes fuera del laboratorio)."""
        gen = current()
        if gen is None:
            abort(404)
        ev = evaluar_certificacion(gen)
        motivo = "NO_CERTIFICABLE_SIN_APROBACIONES" if not ev["apta_para_revision"] else "CERTIFICACION_FUERA_DE_PREVIEW"
        return jsonify({"ok": False, "resultado": motivo, **ev}), 409

    @app.get("/escenarios")
    def escenarios() -> Any:
        gen = current()
        if gen is None:
            return render_template("vacio.html", active="escenarios")
        names = list(gen["escenarios"])
        diffs = []
        if "A" in names and "B" in names:
            a = {r["item"]: r for r in gen["escenarios"]["A"]["rows"]}
            b = {r["item"]: r for r in gen["escenarios"]["B"]["rows"]}
            for code in sorted(a.keys() | b.keys()):
                ra, rb = a.get(code), b.get(code)
                if ra is None or rb is None or ra["frozen"]["value"] != rb["frozen"]["value"] or ra["origen"] != rb["origen"]:
                    diffs.append({"item": code, "a": ra, "b": rb})
        return render_template("escenarios.html", active="escenarios", vista=vista_de(gen), gen=gen, names=names, diffs=diffs, cert=evaluar_certificacion(gen))

    @app.get("/fuentes")
    def fuentes() -> Any:
        gen = current()
        lock = refresher.store.frozen_lock(gen["month"]) if gen else None
        return render_template("fuentes.html", active="fuentes", gen=gen, lock=lock, log=refresher.store.historial()[:30])

    # ---------------------------------------------------------------- R3: propuestas privadas, NUNCA reglas operativas
    def _r3_page(error: str | None = None, status: int = 200) -> Any:
        try:
            all_customers = r3.catalog()
            history = r3.proposals()
        except R3AdminError as exc:
            return _error(409, str(exc))
        q = (request.args.get("q") or "").strip().upper()[:80]
        fuente = request.args.get("fuente") or ""
        if fuente not in ("", "PORTAL_CAT", "SSRS", "PENDIENTE"):
            fuente = ""
        shown = [c for c in all_customers if
                 (not q or q in (c["customer_key_exact"] + " " + c["customer_name"]).upper())
                 and (not fuente or fuente == c["commercial_criterion"])]
        registered = request.args.get("registrada", "")
        if registered and registered not in {x["id"] for x in history}:
            registered = ""
        return render_template(
            "clientes_portal_cat.html", active="clientes_cat", clientes=shown,
            total=len(all_customers),
            confirmadas_portal=sum(c["commercial_criterion"] == "PORTAL_CAT" for c in all_customers),
            confirmadas_ssrs=sum(c["commercial_criterion"] == "SSRS" for c in all_customers),
            pendientes=sum(c["commercial_criterion"] == "PENDIENTE" for c in all_customers),
            propuestas=history, registrada=registered, error=error,
            mes_propuesto=next_month_utc(), q=q, fuente=fuente,
        ), status

    @app.get("/clientes-portal-cat")
    def clientes_portal_cat() -> Any:
        return _r3_page()

    @app.post("/clientes-portal-cat/solicitudes")
    def solicitar_cambio_portal_cat() -> Any:
        try:
            record = r3.submit(
                number=request.form.get("numero_cliente", ""),
                sequence=request.form.get("secuencia", ""),
                action=request.form.get("accion", ""),
                effective_month=request.form.get("mes_efectivo", ""),
                reason=request.form.get("motivo", ""),
                evidence_ref=request.form.get("evidencia", ""), author=EDITOR,
            )
        except R3AdminError as exc:
            return _r3_page(str(exc), 400)
        return redirect(url_for("clientes_portal_cat", registrada=record["id"]), code=303)

    # ---------------------------------------------------------------- notas y seguimiento (privado)
    @app.post("/api/cs")
    def api_cs() -> Any:
        gen = current()
        if gen is None:
            abort(404)
        body = request.get_json(silent=True) or {}
        code = str(body.get("item", ""))
        if not any(r["item"] == code for e in gen["escenarios"].values() for r in e["rows"]):
            abort(404)
        assoc = Association(code)
        state = cs_state(code, gen["month"])
        try:
            expected = int(body.get("version", -1))
        except (TypeError, ValueError):
            expected = -1
        changes: list[tuple[str, Any]] = []
        note = str(body.get("nota") or "").strip()
        if note and (state["nota_hoy"] is None or state["nota_hoy"]["value"] != note):
            changes.append(("NOTE", note))
        # Adelanto es 0 por defecto para todo ITEM/mes. Campo vacío explícito =
        # volver a 0; campo ausente del JSON = no modificar el seguimiento CS.
        if "adelanto" in body:
            adv_texto = str(body["adelanto"] if body["adelanto"] is not None else "").strip()
            adv = Decimal(0) if adv_texto == "" else dec(adv_texto)
            if adv is None or not adv.is_finite() or adv < 0:
                return jsonify({"ok": False, "error": "Adelanto inválido: indique cero o una cantidad no negativa."}), 400
            anterior = dec(state["adelanto"]["value"]["qty"]) if state["adelanto"] else Decimal(0)
            if adv != anterior:
                changes.append(("ADVANCE", {"qty": str(adv)}))
        status = str(body.get("estatus") or "")
        if status and (state["estatus"] is None or state["estatus"]["value"] != status):
            changes.append(("CS_STATUS", status))
        delivery = str(body.get("entrega") or "")
        if delivery and (state["entrega"] is None or state["entrega"]["value"] != delivery):
            changes.append(("DELIVERY_DATE", delivery))
        try:
            version = expected
            for kind, value in changes:
                cs.append(assoc, kind, value, author=EDITOR, expected_version=version, server_ts=now_utc(),
                          month=gen["month"], reason="Junta Operativa (Preview local)", published_gen=gen["gen_id"])
                version += 1
        except CsConflictError:
            return jsonify({"ok": False, "error": "Otro guardado ocurrió antes: recargue la página y repita."}), 409
        except CsValidationError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        new = cs_state(code, gen["month"])
        return jsonify({
            "ok": True,
            "guardados": [k for k, _ in changes],
            "version": new["version"],
            "nota_ts": fmt_dt(new["nota_hoy"]["server_ts"]) if new["nota_hoy"] else None,
            "autor": EDITOR,
        })

    # ---------------------------------------------------------------- Refresh de Preview
    @app.post("/refresh")
    def refresh() -> Any:
        started = refresher.iniciar("web-local")
        st = refresher.estado()
        if not started and st.get("estado") != "RUNNING":
            st = {**st, "estado": "BUSY"}
        if request.headers.get("X-CSRF"):
            return jsonify({"iniciado": started, **st}), (202 if started else 409)

        return redirect(url_for("fuentes"))

    @app.get("/refresh/estado")
    def refresh_estado() -> Any:
        st = refresher.estado()
        etiqueta = "Actualización parcial" if st.get("parcial") else ESTADOS.get(st.get("estado", ""), st.get("estado"))
        return jsonify({**st, "etiqueta": etiqueta})

    @app.get("/api/refresh/status")
    def refresh_status_alias() -> Any:
        return refresh_estado()

    @app.template_global()
    def anchor(code: str) -> str:
        return Association(code).thread_id[:12]

    return app
