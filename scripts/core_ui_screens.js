// Capturas comparables (antes/después) de las pantallas de la junta con datos sintéticos.
// Uso: node scripts/core_ui_screens.js http://127.0.0.1:PUERTO DIR_SALIDA
const { chromium } = require('playwright');
const [base, out] = process.argv.slice(2);
const VIEWPORTS = { desktop1920: [1920, 1080], laptop1366: [1366, 768], tablet: [820, 1180], mobile390: [390, 844] };
async function login(ctx, user) {
  const page = await ctx.newPage();
  await page.goto(`${base}/login`);
  await page.fill('input[name=username]', user);
  await page.fill('input[name=password]', `${user}-demo`);
  await Promise.all([page.waitForURL((u) => !u.pathname.startsWith('/login')), page.click('button[type=submit]')]);
  await page.waitForLoadState('load');
  return page;
}
(async () => {
  const browser = await chromium.launch();
  const report = [];
  for (const [name, [w, h]] of Object.entries(VIEWPORTS)) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
    const page = await login(ctx, 'admin');
    const shots = [['resumen', '/'], ['item', '/item?i=SYN-A'], ['historial', '/history']];
    const item = await (await ctx.request.get(`${base}/item?i=SYN-A`)).text();
    const t = (item.match(/\/cs\/history\?t=([0-9a-f]{32})/) || [])[1];
    if (t) shots.push(['cs_historial', `/cs/history?t=${t}`]);
    for (const [label, path] of shots) {
      await page.goto(`${base}${path}`); await page.waitForLoadState('load');
      const m = await page.evaluate(() => ({ docOverflow: document.documentElement.scrollWidth - window.innerWidth, height: document.documentElement.scrollHeight }));
      report.push({ viewport: name, screen: label, ...m });
      await page.screenshot({ path: `${out}/${name}_${label}.png`, fullPage: label !== 'item' || w > 800 });
    }
    await page.goto(`${base}/`);
    const btn = page.locator('form[action="/refresh"] button[type=submit]');
    if (await btn.count()) {
      await Promise.all([page.waitForURL(/refresh\/status/), btn.first().click()]);
      await page.waitForTimeout(1500); await page.reload();
      await page.screenshot({ path: `${out}/${name}_refresh.png`, fullPage: true });
    }
    await ctx.close();
  }
  console.log(JSON.stringify(report));
  await browser.close();
})();
