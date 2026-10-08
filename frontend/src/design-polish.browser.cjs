const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const base = process.env.DASHBOARD_TEST_URL || 'http://127.0.0.1:5180';
const output = path.join(process.env.TEMP || '.', 'dashboard-design-polish');
async function ready(page) {
  await page.waitForFunction(() => document.querySelector('main h1') && !document.querySelector('main .load-state'), { timeout: 120000 });
}
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ acceptDownloads: true });
    const errors = [], layouts = []; page.on('pageerror', error => errors.push(error.message));
    for (const theme of ['dark', 'light']) for (const width of [360, 1280, 1920]) {
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript(theme => localStorage.setItem('dashboard.theme', theme), theme);
      for (const route of ['/home', '/construction', '/commissioning/operational', '/commissioning/annual', '/apartments', '/sales', '/economics/accounts', '/economics/ipc']) {
        await page.goto(base + route); await ready(page);
        assert.equal(await page.locator('.chart-data-panel').count(), 0, `${route}: data initially open`);
        assert.equal(await page.locator('.visual-data-disclosure[open], .annual-summary-disclosure[open]').count(), 0);
        const geometry = await page.evaluate(() => ({ overflow: document.documentElement.scrollWidth > innerWidth + 1,
          charts: [...document.querySelectorAll('.chart-canvas')].map(element => { const r = element.getBoundingClientRect(); return { outside: r.left < -1 || r.right > innerWidth + 1, scroll: element.scrollHeight > element.clientHeight + 1 || element.scrollWidth > element.clientWidth + 1 }; }) }));
        assert.equal(geometry.overflow, false, `${route}/${width}: horizontal overflow`);
        geometry.charts.forEach(chart => { assert.equal(chart.outside, false); assert.equal(chart.scroll, false); });
        layouts.push({ route, theme, width });
        await page.screenshot({ path: path.join(output, `${route.replaceAll('/', '-')}-${theme}-${width}.png`), fullPage: true });
      }
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto(base + '/home'); await ready(page);
    assert.equal(await page.locator('.home-market-panel').count(), 3);
    assert.equal(await page.locator('#home-market-title').innerText(), 'Рынок недвижимости');
    const fonts = await page.evaluate(() => ['.nav-group > .nav-heading', '.nav-subgroup > .nav-heading', '.sidebar .nav-link'].map(selector => parseFloat(getComputedStyle(document.querySelector(selector)).fontSize)));
    assert.ok(fonts[0] > fonts[1] && fonts[1] > fonts[2]);
    await page.goto(base + '/commissioning/operational?housingMonth=12&nonresMonth=3&year=2021&quarter=2'); await ready(page);
    const housing = page.locator('.operational-series').filter({ has: page.getByRole('heading', { name: 'Ввод жилья', exact: true }) });
    assert.equal(await page.getByLabel('Период: Ввод жилья', { exact: true }).locator('option').count(), 12);
    assert.equal(await page.getByLabel('Период: Ввод жилья', { exact: true }).inputValue(), '12');
    await housing.locator('.chart-canvas').evaluate(element => element.dataset.retained = 'true');
    await page.getByLabel('Период: Ввод нежилой недвижимости', { exact: true }).selectOption('6'); await ready(page);
    assert.equal(await housing.locator('.chart-canvas').getAttribute('data-retained'), 'true');
    assert.equal(await page.getByLabel('Период: Ввод жилья', { exact: true }).inputValue(), '12');
    await page.getByLabel('Квартал', { exact: true }).selectOption('3'); await ready(page);
    assert.equal(await housing.locator('.chart-canvas').getAttribute('data-retained'), 'true');
    assert.equal(await page.locator('.quarter-infographic').evaluate(element => new Set([...element.querySelectorAll('[data-structure-id]')].map(row => row.dataset.structureId)).size), 15);
    assert.equal(await page.locator('.operational-series .commissioning-table').count(), 0);
    await housing.getByRole('button', { name: 'Данные и скачивание', exact: true }).click();
    assert.equal(await housing.locator('tbody tr').count(), 16);
    const csv = page.waitForEvent('download'); await housing.getByRole('button', { name: 'CSV', exact: true }).click(); assert.ok((await csv).suggestedFilename().endsWith('.csv'));
    const excel = page.waitForEvent('download'); await housing.getByRole('button', { name: 'Скачать ввод жилья Excel', exact: true }).click(); assert.ok((await excel).suggestedFilename().endsWith('.xlsx'));
    await page.screenshot({ path: path.join(output, 'operational-interactions.png'), fullPage: true });
    await page.goto(base + '/construction'); await ready(page);
    assert.equal(await page.locator('.construction-permits').getByLabel('Назначение', { exact: true }).count(), 1);
    const donut = page.locator('.construction-donuts .chart-canvas').first(); await donut.evaluate(element => element.dataset.retained = 'true');
    const before = await page.locator('.construction-volumes').innerText();
    await page.getByLabel('Назначение', { exact: true }).selectOption('housing'); await ready(page);
    assert.equal(await donut.getAttribute('data-retained'), 'true'); assert.equal(await page.locator('.construction-volumes').innerText(), before);
    assert.equal(await page.locator('.construction-permits .commissioning-table').count(), 0);
    await page.goto(base + '/apartments'); await ready(page);
    const stripe = page.locator('.apartment-room-strip > i').first(); const hint = await stripe.getAttribute('aria-label');
    await stripe.hover(); await page.getByRole('tooltip').waitFor(); assert.equal(await page.getByRole('tooltip').innerText(), hint);
    await page.goto(base + '/economics/ipc'); await ready(page);
    assert.equal(await page.locator('.economics-chart-section .economics-filters').count(), 1);
    assert.equal(await page.locator('.economics-tables').count(), 0);
    await page.getByRole('button', { name: 'Данные и скачивание', exact: true }).click();
    assert.equal(await page.locator('.economics-tables').count(), 1);
    assert.ok(await page.locator('.economics-tables tbody tr').count() > 0);
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ layouts, errors, fonts }, null, 2));
    console.log(JSON.stringify({ layouts: layouts.length, errors, output }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
