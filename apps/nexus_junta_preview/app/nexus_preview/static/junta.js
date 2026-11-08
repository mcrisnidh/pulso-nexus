// PULSO NEXUS Junta Operativa: Refresh de Preview y seguimiento CS local.
(function () {
  "use strict";
  var csrf = (document.querySelector('meta[name="csrf"]') || {}).content || "";
  var banner = document.getElementById("refreshBanner");
  var stateEl = document.getElementById("refreshState");
  var timeEl = document.getElementById("refreshTime");
  var btn = document.getElementById("refreshNow");
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return "&#" + c.charCodeAt(0) + ";"; }); }
  function show(cls, html) { if (!banner) return; banner.className = "refresh-banner " + cls; banner.innerHTML = html; }
  function render(e) {
    if (stateEl) { stateEl.className = "refresh-state " + String(e.estado || "").toLowerCase(); stateEl.innerHTML = "<i></i>" + esc(e.etiqueta || e.estado); }
    if (timeEl) timeEl.textContent = e.fin || e.inicio || "";
  }
  function describe(e) {
    var f = e.fuentes ? Object.keys(e.fuentes).map(function (k) { return esc(k) + ": " + esc(e.fuentes[k]); }).join(" · ") : "";
    return "<b>" + esc(e.etiqueta || e.estado) + "</b> · " + esc(e.fin || e.inicio || "") +
      (e.articulos != null ? " · " + esc(e.articulos) + " artículos procesados" : "") +
      (f ? "<div class='small-muted'>Fuentes: " + f + "</div>" : "") +
      (e.motivos || []).map(function (x) { return "<div>✖ " + esc(x) + "</div>"; }).join("") +
      (e.advertencias || []).map(function (x) { return "<div>⚠ " + esc(x) + "</div>"; }).join("");
  }
  function poll() {
    fetch("/refresh/estado", { credentials: "same-origin", cache: "no-store" }).then(function (r) { return r.json(); }).then(function (e) {
      render(e);
      if (e.estado === "RUNNING") { setTimeout(poll, 800); return; }
      if (btn) btn.disabled = false;
      var extra = e.parcial ? "<div><b>Actualización parcial:</b> alguna fuente directa falló; se conserva su último corte válido (ver Fuentes y Refresh)</div>" : "";
      show(e.estado === "ERROR" ? "err" : (e.parcial ? "" : "ok"), describe(e) + extra + (e.estado === "PASS" ? "<div class='small-muted'>Recargando la vista con la nueva generación…</div>" : ""));
      if (e.estado === "PASS") setTimeout(function () { window.location.reload(); }, 1500);
    }).catch(function () { if (btn) btn.disabled = false; show("err", "<b>Error</b>: no se pudo consultar el estado"); });
  }
  if (btn) btn.addEventListener("click", function () {
    btn.disabled = true;
    show("", "<b>Actualizando…</b> leyendo y validando cortes de la carpeta privada");
    fetch("/refresh", { method: "POST", credentials: "same-origin", headers: { "X-CSRF": csrf } })
      .then(function (r) { return r.json(); })
      .then(function (e) {
        if (!e.iniciado && e.estado !== "RUNNING") { btn.disabled = false; render({ estado: "BUSY", etiqueta: "Otro Refresh en curso" }); show("err", "<b>Otro Refresh en curso</b>: no se inició un segundo trabajo"); return; }
        poll();
      }).catch(function () { btn.disabled = false; show("err", "<b>Error</b>: solicitud rechazada"); });
  });

  // ---- Seguimiento CS: borrador local (navegador) vs. guardado (almacén privado de Preview)
  function draftKey(row) { return "nexus_borrador:" + row.dataset.month + ":" + row.dataset.item + ":" + row.dataset.hoy; }
  function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function put(k, v) { try { if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) {} }
  document.querySelectorAll("tr[data-item]").forEach(function (row) {
    var note = row.querySelector("[data-note]"), status = row.querySelector("[data-status]"), save = row.querySelector("[data-save]");
    if (!note || !save) return;
    var saved = note.value, draft = get(draftKey(row));
    if (draft !== null && draft !== saved) { note.value = draft; status.textContent = "Borrador local sin guardar"; status.classList.add("draft"); }
    note.addEventListener("input", function () {
      if (note.value === saved) { put(draftKey(row), null); status.classList.remove("draft"); return; }
      put(draftKey(row), note.value); status.textContent = "Borrador local sin guardar"; status.classList.add("draft");
    });
    save.addEventListener("click", function () {
      save.disabled = true; status.textContent = "Guardando…";
      var body = {
        item: row.dataset.item, version: Number(row.dataset.version), nota: note.value,
        adelanto: (row.querySelector("[data-adelanto]") || {}).value || "",
        estatus: (row.querySelector("[data-estatus]") || {}).value || "",
        entrega: (row.querySelector("[data-entrega]") || {}).value || ""
      };
      fetch("/api/cs", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRF": csrf }, body: JSON.stringify(body) })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (x) {
          if (!x.ok || !x.d.ok) throw new Error(x.d.error || "No se pudo guardar");
          row.dataset.version = x.d.version; saved = note.value; put(draftKey(row), null);
          status.classList.remove("draft");
          status.textContent = x.d.guardados.length ? "Guardada " + (x.d.nota_ts || "") : "Sin cambios";
          save.classList.add("saved"); setTimeout(function () { save.classList.remove("saved"); }, 2200);
        })
        .catch(function (err) { status.textContent = "Error: " + err.message; status.classList.add("draft"); })
        .finally(function () { save.disabled = false; });
    });
  });
})();
