"use strict";
// Convenience only. Server-side validation in r3_admin.py is authoritative.
(function () {
  const config = document.getElementById('r3ContextData');
  if (!config) return;
  const catalog = JSON.parse(config.dataset.criterios || '{}');
  const protectedSSRS = new Set(JSON.parse(config.dataset.protected || '[]'));
  const number = document.getElementById('r3Number');
  const seq = document.getElementById('r3Sequence');
  const action = document.getElementById('r3Action');
  const submit = document.getElementById('r3Submit');
  const hint = document.getElementById('r3CriterionHint');
  if (!number || !seq || !action || !submit || !hint) return;
  function update() {
    const code = number.value.trim().toUpperCase();
    const suffix = seq.value.trim();
    action.replaceChildren();
    submit.disabled = true;
    if (!/^[A-Z0-9]{1,10}$/.test(code) || !/^[0-9]{3}$/.test(suffix)) {
      hint.textContent = 'Introduce cliente y secuencia válidos para determinar la acción permitida.';
      action.add(new Option('Primero indica la identidad ERP', ''));
      return;
    }
    const key = code.padEnd(10, ' ') + suffix;
    const criterion = Object.prototype.hasOwnProperty.call(catalog, key) ? catalog[key] : 'NO_EN_INVENTARIO';
    if (protectedSSRS.has(key)) {
      hint.textContent = 'SSRS protegido: no permite cambios ordinarios; requiere excepción comercial formal.';
      action.add(new Option('Sin acciones ordinarias disponibles', ''));
      return;
    }
    const options = criterion === 'PORTAL_CAT'
      ? [['RETIRAR_PORTAL_CAT', 'Solicitar retiro de Portal CAT / conservar SSRS']]
      : criterion === 'SSRS'
      ? [['AGREGAR_PORTAL_CAT', 'Solicitar incorporación a Portal CAT']]
      : [['CLASIFICAR_INICIAL_PORTAL_CAT', 'Clasificación inicial: Portal CAT'],
         ['CLASIFICAR_INICIAL_SSRS', 'Clasificación inicial: conservar SSRS']];
    hint.textContent = criterion === 'PORTAL_CAT' ? 'Criterio comercial actual: Portal CAT (no formalizado).'
      : criterion === 'SSRS' ? 'Criterio comercial actual: SSRS (no formalizado).'
      : criterion === 'NO_EN_INVENTARIO' ? 'Cuenta fuera del inventario de 84: la solicitud requiere verificar identidad ERP.'
      : 'Sin clasificación comercial: registra una propuesta de clasificación inicial.';
    for (const [value, label] of options) action.add(new Option(label, value));
    submit.disabled = options.length === 0;
  }
  number.addEventListener('input', update);
  seq.addEventListener('input', update);
  update();
})();
