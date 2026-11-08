// REUTILIZADO de PULSO Web (base.html y daily.html): barra lateral plegable y barra de
// desplazamiento superior sincronizada. Movido a archivo estático (CSP sin scripts en línea).
(function () {
  "use strict";
  var body = document.body;
  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }
  function apply(c) { body.classList.toggle("sidebar-collapsed", !!c); }
  apply(store("pulso_sidebar_collapsed") === "1");
  function toggle() { var n = !body.classList.contains("sidebar-collapsed"); apply(n); store("pulso_sidebar_collapsed", n ? "1" : "0"); }
  ["sidebarToggle", "sidebarToggleTop"].forEach(function (id) { var b = document.getElementById(id); if (b) b.addEventListener("click", toggle); });
  document.querySelectorAll(".panel").forEach(function (panel) {
    var proxy = panel.querySelector("[data-scroll-proxy]"), source = panel.querySelector("[data-scroll-source]");
    if (!proxy || !source) return;
    var inner = proxy.firstElementChild, lock = false;
    function sync() { var t = source.querySelector("table"); inner.style.width = (t ? t.scrollWidth : source.scrollWidth) + "px"; }
    proxy.addEventListener("scroll", function () { if (lock) return; lock = true; source.scrollLeft = proxy.scrollLeft; lock = false; });
    source.addEventListener("scroll", function () { if (lock) return; lock = true; proxy.scrollLeft = source.scrollLeft; lock = false; });
    window.addEventListener("resize", sync); setTimeout(sync, 0);
  });
})();
