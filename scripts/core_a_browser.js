// Verificación en navegador headless (Playwright/Chromium) contra `nexus web` en loopback.
// Uso: node scripts/core_a_browser.js http://127.0.0.1:PUERTO DIRECTORIO_CAPTURAS
const { chromium } = require('playwright');
const [base, shots] = process.argv.slice(2);
const results = [];
function check(name, ok) { results.push({ name, ok }); console.log(JSON.stringify({ name, ok })); if (!ok) process.exitCode = 1; }
async function login(browser, user) {
  const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`${base}/login`);
  await page.fill('input[name=username]', user);
  await page.fill('input[name=password]', `${user}-demo`);
  await Promise.all([page.waitForURL((u) => !u.pathname.startsWith('/login')), page.click('button[type=submit]')]);
  await page.waitForLoadState('load');
  return { page, errors };
}
(async () => {
  const browser = await chromium.launch();
  const r = await login(browser, 'lector');
  check('lector: junta visible con banner sintético', (await r.page.content()).includes('DATOS SINTÉTICOS'));
  check('lector: sin botón Refresh', (await r.page.locator('form[action="/refresh"]').count()) === 0);
  await r.page.screenshot({ path: `${shots}/board_lector.png`, fullPage: true });
  const f = await login(browser, 'refresher');
  await f.page.click('form[action="/refresh"] button');
  check('refresher: Refresh → página de estado', f.page.url().includes('/refresh/status'));
  let done = false;
  for (let i = 0; i < 40 && !done; i++) {
    done = (await f.page.content()).includes('<strong>COMPLETED</strong>');
    if (!done) { await f.page.waitForTimeout(250); await f.page.click('text=Actualizar estado'); }
  }
  check('refresher: solicitud completada (recargando el estado)', done);
  const e = await login(browser, 'editorcs');
  await e.page.goto(`${base}/item?i=SYN-B`);
  const create = e.page.locator('form.card.grid2');
  await create.locator('select[name=kind]').selectOption('NOTE');
  await create.locator('input[name=value]').fill('Junta: confirmar embarque parcial (sintético)');
  await create.locator('button[type=submit]').click();
  check('editorcs: nota creada y visible', (await e.page.content()).includes('confirmar embarque parcial'));
  await e.page.screenshot({ path: `${shots}/item_editorcs.png`, fullPage: true });
  await e.page.goto(`${base}/item?i=SYN-A`);
  await e.page.screenshot({ path: `${shots}/item_syn_a.png`, fullPage: true });
  const m = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await m.goto(`${base}/login`);
  await m.fill('input[name=username]', 'lector'); await m.fill('input[name=password]', 'lector-demo');
  await Promise.all([m.waitForURL((u) => !u.pathname.startsWith('/login')), m.click('button[type=submit]')]);
  await m.waitForLoadState('load');
  const overflow = await m.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
  check('móvil 390px: sin scroll horizontal de página', overflow);
  await m.screenshot({ path: `${shots}/board_mobile.png`, fullPage: true });
  const csp = (await (await r.page.request.get(`${base}/login`)).headers())['content-security-policy'] || '';
  check('CSP sin unsafe-inline', csp.length > 0 && !csp.includes('unsafe-inline'));
  const errs = [...r.errors, ...f.errors, ...e.errors];
  if (errs.length) console.log(JSON.stringify({ console_errors: errs }));
  check('sin errores de consola/JS', errs.length === 0);
  await browser.close();
  console.log(JSON.stringify({ browser_checks: results.length, failed: results.filter((x) => !x.ok).length }));
})();
