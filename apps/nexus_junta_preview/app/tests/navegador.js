// Prueba funcional en navegador real (Chromium headless) de la Junta Operativa NEXUS.
// node navegador.js nota    PUERTO CARPETA  → guarda nota+adelanto, deja un borrador, cierra el navegador
// node navegador.js refresh PUERTO CARPETA ITEM → navegador NUEVO: persiste; pulsa Actualizar datos
const { chromium } = require("playwright");
const [mode, port, out, itemArg] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}`;

async function rowData(page, item) {
  const row = page.locator(`tr[data-item="${item}"]`);
  const cells = row.locator("td");
  const head = await page.locator("thead tr").nth(1).locator("th").allInnerTexts();
  return {
    item,
    frozen: (await cells.nth(6).innerText()).trim(),
    actual: (await cells.nth(9).innerText()).trim(),
    fg: (await cells.nth(11).innerText()).trim(),
    programa: (await cells.nth(7).innerText()).trim(),
    embarques: (await cells.nth(8).innerText()).trim(),
    faltante: (await cells.nth(12).innerText()).trim(),
    nota: await row.locator("textarea[data-note]").inputValue(),
    estado_nota: (await row.locator("[data-status]").innerText()).trim(),
    adelanto: await row.locator("input[data-adelanto]").inputValue(),
    cab_frozen: head[6].replace(/\s+/g, " "), cab_current: head[9].replace(/\s+/g, " "),
    pill: (await page.locator("#vistaPill").innerText()).trim(),
    selector_escenarios: await page.locator('a[href*="esc="], button:has-text("Escenario A"), button:has-text("Escenario B")').count(),
    texto_escenarios: (await page.locator("main").innerText()).match(/Escenario (A|B|DIAG)\b/g) || [],
  };
}

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(`${base}/junta`);
  if (mode === "nota") {
    const items = await page.locator("tr[data-item]").evaluateAll((rs) => rs.map((r) => r.dataset.item));
    let item = null;
    for (const it of items) { if ((await page.locator(`tr[data-item="${it}"] td`).nth(6).innerText()).trim() !== "N/D") { item = it; break; } }
    // búsqueda por ITEM sobre el conjunto completo
    await page.fill('input[name="q"]', item);
    await Promise.all([page.waitForNavigation(), page.click(".filters button")]);
    const visibles = await page.locator("tr[data-item]").count();
    const row = page.locator(`tr[data-item="${item}"]`);
    await row.locator("textarea[data-note]").fill("Nota de junta desde el navegador");
    await row.locator("input[data-adelanto]").fill("75");
    await row.locator("[data-save]").click();
    await page.waitForFunction((it) => document.querySelector(`tr[data-item="${it}"] [data-status]`).innerText.startsWith("Guardada"), item);
    await page.screenshot({ path: `${out}/1_nota_guardada.png` });
    // borrador local en otra fila: se distingue de lo guardado y sobrevive a la recarga
    await page.goto(`${base}/junta`);
    const other = (await page.locator("tr[data-item]").evaluateAll((rs) => rs.map((r) => r.dataset.item))).find((x) => x !== item);
    await page.locator(`tr[data-item="${other}"] textarea[data-note]`).fill("borrador sin guardar");
    await page.reload();
    const draft = (await page.locator(`tr[data-item="${other}"] [data-status]`).innerText()).trim();
    const data = await rowData(page, item);
    if (errors.length) throw new Error("errores de consola: " + errors.join(" | "));
    console.log(JSON.stringify({ ...data, visibles, borrador: draft }));
  } else {
    const item = itemArg;
    const antes = await rowData(page, item); // navegador recién abierto: la nota guardada persiste
    await page.screenshot({ path: `${out}/2_antes_refresh.png` });
    await page.click("#refreshNow");
    await page.waitForSelector("#refreshBanner:not(.hidden)");
    await page.waitForFunction(() => !document.querySelector("#refreshBanner").innerText.startsWith("Actualizando"), null, { timeout: 60000 });
    const banner = (await page.locator("#refreshBanner").innerText()).trim();
    await page.screenshot({ path: `${out}/3_resultado_refresh.png` });
    await page.waitForTimeout(2500);
    await page.waitForLoadState("load");
    const grupos = await page.locator("tr.group-head th").allInnerTexts();
    const data = await rowData(page, item);
    await page.locator(`tr[data-item="${item}"]`).scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${out}/4_despues_refresh.png` });
    await page.goto(`${base}/fuentes`);
    await page.screenshot({ path: `${out}/5_fuentes.png`, fullPage: true });
    if (errors.length) throw new Error("errores de consola: " + errors.join(" | "));
    console.log(JSON.stringify({ ...data, antes_nota: antes.nota, banner, grupos }));
  }
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
