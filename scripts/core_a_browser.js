// Verificación de la interfaz en Chromium headless contra `nexus web` en loopback (datos sintéticos).
// Uso: node scripts/core_a_browser.js http://127.0.0.1:PUERTO DIRECTORIO_CAPTURAS
const { chromium } = require('playwright');
const [base, shots] = process.argv.slice(2);
const results = [];
function check(name, ok, extra) {
  results.push({ name, ok }); console.log(JSON.stringify({ name, ok, ...(extra ? { extra } : {}) }));
  if (!ok) process.exitCode = 1;
}
const VIEWPORTS = { desktop1920: [1920, 1080], laptop1366: [1366, 768], tablet820: [820, 1180], mobile390: [390, 844] };

async function login(browser, user, viewport = [1366, 768]) {
  const ctx = await browser.newContext({ viewport: { width: viewport[0], height: viewport[1] } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error' && !/status of 40[039]/.test(m.text())) errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`${base}/login`);
  await page.fill('input[name=username]', user);
  await page.fill('input[name=password]', `${user}-demo`);
  await Promise.all([page.waitForURL((u) => !u.pathname.startsWith('/login')), page.click('button[type=submit]')]);
  await page.waitForLoadState('load');
  return { ctx, page, errors };
}

// Contraste WCAG medido sobre colores renderizados (fondo efectivo = primer ancestro opaco).
const CONTRAST = () => {
  const parse = (c) => (c.match(/[\d.]+/g) || []).map(Number);
  const lum = ([r, g, b]) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const bgOf = (el) => { for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c.length === 3 || (c.length === 4 && c[3] > 0.9)) return c.slice(0, 3); } return [255, 255, 255]; };
  const out = [];
  const sel = 'main h1, main .sub, .kpi .label, .kpi .value, .kpi .foot, .notice p, .badge, .status-chip, table.data th, table.data td, .btn, .app-nav a, .brand-name, .cut .label, .small.muted, .legend, .sd, .cs-line, .env, label.field';
  document.querySelectorAll(sel).forEach((el) => {
    if (!el.textContent.trim() || el.offsetParent === null) return;
    const s = getComputedStyle(el); const fg = parse(s.color).slice(0, 3); const bg = bgOf(el);
    const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x); const ratio = (a + 0.05) / (b + 0.05);
    const size = parseFloat(s.fontSize); const bold = parseInt(s.fontWeight, 10) >= 700;
    const need = size >= 24 || (size >= 18.66 && bold) ? 3 : 4.5;
    if (ratio < need) out.push({ el: el.className || el.tagName, text: el.textContent.trim().slice(0, 30), ratio: +ratio.toFixed(2), need });
  });
  return out;
};

(async () => {
  const browser = await chromium.launch();
  const allErrors = [];
  // 1. Junta: cuatro tamaños de pantalla, desbordamientos, legibilidad y contraste
  for (const [name, vp] of Object.entries(VIEWPORTS)) {
    const s = await login(browser, 'lector', vp);
    const m = await s.page.evaluate(() => {
      const W = document.documentElement.clientWidth;
      const tw = document.querySelector('.table-wrap');
      const fonts = [...document.querySelectorAll('main td, main p, .kpi .label')].map((e) => parseFloat(getComputedStyle(e).fontSize));
      return { doc: document.documentElement.scrollWidth - W, table: tw ? tw.scrollWidth - tw.clientWidth : 0, minFont: Math.min(...fonts) };
    });
    check(`${name}: sin desbordamiento horizontal de página`, m.doc <= 0, m);
    if (vp[0] >= 1366) check(`${name}: tabla de la junta completa sin desplazamiento horizontal`, m.table <= 0, m);
    check(`${name}: tamaño mínimo de texto legible (≥ 11 px)`, m.minFont >= 11, m);
    const low = await s.page.evaluate(CONTRAST);
    check(`${name}: contraste WCAG AA en textos de la junta`, low.length === 0, low.slice(0, 5));
    await s.page.screenshot({ path: `${shots}/${name}_resumen.png`, fullPage: true });
    allErrors.push(...s.errors); await s.ctx.close();
  }
  // 2. Lector: sin Refresh ni formularios CS; teclado y foco
  const r = await login(browser, 'lector');
  check('lector: sin botón Refresh', (await r.page.locator('form[action="/refresh"]').count()) === 0);
  await r.page.keyboard.press('Tab');
  const skip = await r.page.evaluate(() => ({ text: document.activeElement.textContent.trim(), outline: getComputedStyle(document.activeElement).outlineStyle, top: document.activeElement.getBoundingClientRect().top }));
  check('teclado: el primer Tab enfoca "Saltar al contenido" visible con foco', skip.text === 'Saltar al contenido' && skip.outline !== 'none' && skip.top >= 0, skip);
  await r.page.keyboard.press('Enter');
  check('teclado: "Saltar al contenido" mueve el foco al contenido', await r.page.evaluate(() => document.activeElement.id === 'contenido'));
  await r.page.goto(`${base}/item?i=SYN-A`);
  check('lector: detalle sin formularios de edición CS', (await r.page.locator('form[action="/cs"]').count()) === 0);
  await r.page.goto(`${base}/?q=syn-a`);
  check('filtro por ITEM (servidor)', (await r.page.locator('td.item a').allTextContents()).join(',') === 'SYN-A');
  await r.page.goto(`${base}/?cobertura=faltante&orden=sin_cubrir`);
  const faltantes = await r.page.locator('td.item a').count();
  check('filtro por cobertura: solo ITEM con faltante', faltantes >= 1 && (await r.page.locator('td.n.good').count()) === 0);
  allErrors.push(...r.errors); await r.ctx.close();

  // 3. Refresh: foco visible y estado comprobado (no se supone la publicación)
  const f = await login(browser, 'refresher');
  const btn = f.page.locator('form[action="/refresh"] button[type=submit]');
  await btn.focus();
  const focusStyle = await btn.evaluate((e) => getComputedStyle(e).outlineStyle);
  check('teclado: botón Refresh con foco visible', focusStyle !== 'none', focusStyle);
  await Promise.all([f.page.waitForURL(/refresh\/status/), btn.click()]);
  let phase = '';
  for (let i = 0; i < 40; i++) {
    phase = (await f.page.locator('.state-card h2').textContent()).trim();
    if (['Publicado', 'Sin cambios', 'No publicado', 'Fallido'].includes(phase)) break;
    await f.page.waitForTimeout(500); await f.page.reload();
  }
  check('Refresh: estado final comprobado contra el historial (nunca supuesto)', ['Publicado', 'Sin cambios', 'No publicado'].includes(phase), phase);
  await f.page.screenshot({ path: `${shots}/refresh_estado.png`, fullPage: true });
  allErrors.push(...f.errors); await f.ctx.close();

  // 4. Customer Service: formularios separados, guardado, error 400 y conflicto 409
  const e1 = await login(browser, 'editorcs');
  const e2 = await login(browser, 'editorcs2');
  await e1.page.goto(`${base}/item?i=SYN-B`);
  const general = e1.page.locator('article.cs-assoc').first();
  if (!(await general.locator('details.edit').evaluate((d) => d.open))) await general.locator('details.edit > summary').click();
  check('CS: nota en textarea, estatus en selector, fecha en selector de fecha, adelanto con cantidad y fecha',
    (await general.locator('form:has(input[name=kind][value=NOTE]) textarea[name=value]').count()) === 1
    && (await general.locator('form:has(input[name=kind][value=CS_STATUS]) select[name=value]').count()) === 1
    && (await general.locator('form:has(input[name=kind][value=DELIVERY_DATE]) input[type=date][name=value]').count()) === 1
    && (await general.locator('form:has(input[name=kind][value=ADVANCE]) input[type=number][name=value]').count()) === 1
    && (await general.locator('form:has(input[name=kind][value=ADVANCE]) input[type=date][name=date]').count()) === 1);
  // e2 abre la misma versión antes de que e1 guarde → conflicto controlado
  await e2.page.goto(`${base}/item?i=SYN-B`);
  const g2 = e2.page.locator('article.cs-assoc').first();
  if (!(await g2.locator('details.edit').evaluate((d) => d.open))) await g2.locator('details.edit > summary').click();
  const noteForm = general.locator('form:has(input[name=kind][value=NOTE])');
  await noteForm.locator('textarea').fill('Junta: confirmar embarque parcial (sintético)');
  await Promise.all([e1.page.waitForURL(/ok=NOTE/), noteForm.locator('button[type=submit]').click()]);
  check('CS: nota guardada con confirmación visible', (await e1.page.locator('.notice.ok').textContent()).includes('Guardado: Nota'));
  check('CS: última nota visible en el detalle', (await e1.page.locator('.cs-note').first().textContent()).includes('confirmar embarque parcial'));
  const statusForm = e1.page.locator('article.cs-assoc').first().locator('form:has(input[name=kind][value=CS_STATUS])');
  if (!(await e1.page.locator('article.cs-assoc').first().locator('details.edit').evaluate((d) => d.open))) await e1.page.locator('article.cs-assoc').first().locator('details.edit > summary').click();
  await statusForm.locator('select').selectOption('EN_RIESGO');
  await Promise.all([e1.page.waitForURL(/ok=CS_STATUS/), statusForm.locator('button[type=submit]').click()]);
  check('CS: estatus guardado y mostrado', (await e1.page.locator('article.cs-assoc .status-chip').first().textContent()).includes('En riesgo'));
  await e1.page.screenshot({ path: `${shots}/item_customer_service.png`, fullPage: true });
  const g2form = g2.locator('form:has(input[name=kind][value=NOTE])');
  await g2form.locator('textarea').fill('Edición simultánea de otro usuario');
  await g2form.locator('button[type=submit]').click(); await e2.page.waitForLoadState('load');
  const conflict = await e2.page.locator('main').textContent();
  check('CS: edición simultánea → aviso claro de conflicto sin pérdida', conflict.includes('Otro usuario guardó un cambio antes') && conflict.includes('Nada se perdió'));
  await e2.page.screenshot({ path: `${shots}/cs_conflicto.png`, fullPage: true });
  const csrf = await e1.page.locator('input[name=csrf]').first().inputValue();
  const bad = await e1.page.request.post(`${base}/cs`, { form: { csrf, kind: 'CS_STATUS', item: 'SYN-B', value: 'INVENTADO', expected_version: '99' } });
  check('CS: dato inválido → mensaje de error comprensible (400)', bad.status() === 400 && (await bad.text()).includes('Dato no válido'));
  const hist = e1.page.locator('a:has-text("Historial (")').first();
  await Promise.all([e1.page.waitForURL(/cs\/history/), hist.click()]);
  check('CS: historial de versiones con autor y fecha', (await e1.page.locator('td[data-label="Autor"]').count()) >= 2);
  await e1.page.screenshot({ path: `${shots}/cs_historial.png`, fullPage: true });
  allErrors.push(...e1.errors, ...e2.errors); await e1.ctx.close(); await e2.ctx.close();

  // 5. Historial y rollback con confirmación (administrador)
  const a = await login(browser, 'admin');
  await a.page.goto(`${base}/history`);
  await a.page.screenshot({ path: `${shots}/historial.png`, fullPage: true });
  const back = a.page.locator('a:has-text("Volver a este corte")').first();
  await Promise.all([a.page.waitForURL(/admin\/rollback/), back.click()]);
  check('rollback: pantalla de confirmación explícita', (await a.page.locator('h1').textContent()).includes('Volver a un corte anterior'));
  await a.page.screenshot({ path: `${shots}/rollback_confirmar.png`, fullPage: true });
  await Promise.all([a.page.waitForURL(/\/history/), a.page.locator('button.danger').click()]);
  check('rollback: registrado como vigente', (await a.page.locator('tbody tr').first().textContent()).includes('Rollback'));
  await a.page.goto(`${base}/item?i=SYN-A`);
  await a.page.screenshot({ path: `${shots}/item_syn_a.png`, fullPage: true });
  const csp = (await (await a.page.request.get(`${base}/login`)).headers())['content-security-policy'] || '';
  check('CSP sin unsafe-inline', csp.length > 0 && !csp.includes('unsafe-inline'));
  allErrors.push(...a.errors); await a.ctx.close();
  check('sin errores de consola, JavaScript ni violaciones de CSP', allErrors.length === 0, allErrors.slice(0, 3));
  await browser.close();
  console.log(JSON.stringify({ browser_checks: results.length, failed: results.filter((x) => !x.ok).length }));
})();
