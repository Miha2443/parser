const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');

const base = process.env.DASHBOARD_TEST_URL || 'http://127.0.0.1:5180';
const output = path.join(process.env.TEMP || '.', 'dashboard-construction-tree');
const labels = ['Всего', 'Жилье', 'Нежилье в жилье', 'МОП', 'Жилая площадь', 'Инвесторы', 'Реновация', 'Нежилье', 'Бюджет', 'Внебюджет'];
const ids = ['total', 'housing', 'embedded', 'mop', 'living', 'investors', 'renovation', 'nonresidential', 'budget', 'nonbudget'];
const values = [51.65728716, 37.92555967, 5.09855928, 10.68700039, 22.14, 15.74, 6.4, 13.73172749, 11.20503593, 40.45225123];
const payload = () => ({ schemaVersion: 1, version: 'fixture-v1', generatedAt: '2026-10-16T12:00:00Z', unit: 'million_m2',
  source: { file: 'monitoring_2_0_20261016.xlsx', sheet: 'Дерево текущ стр во', column: 'Итог', downloadedAt: '2026-10-16T11:00:00Z', fileDate: '2026-10-16' },
  metrics: ids.map((id, i) => ({ id, label: labels[i], value: values[i], cell: `I${i < 8 ? i + 2 : i + 3}` })) });

async function ready(page) {
  await page.locator('main [data-metric="total"]').waitFor();
  await page.getByRole('heading', { name: 'Дерево текущего строительства', exact: true }).waitFor();
}
async function geometry(page) {
  const result = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth > innerWidth + 1,
    clipped: [...document.querySelectorAll('main [data-metric]')].some(node => {
      const r = node.getBoundingClientRect();
      return r.width > 0 && (r.left < -1 || r.right > innerWidth + 1 || node.scrollWidth > node.clientWidth + 1);
    }),
  }));
  assert.deepEqual(result, { overflow: false, clipped: false });
  assert.equal(await page.locator('main select, main input').count(), 0);
}
async function csv(page) {
  const details = page.locator('main details').first();
  if (!await details.getAttribute('open').then(value => value !== null)) await details.locator('summary').click();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: /CSV/ }).click();
  return fs.readFile(await (await download).path(), 'utf8');
}

(async () => {
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const errors = [], layouts = [];
  try {
    const page = await browser.newPage({ acceptDownloads: true });
    await page.clock.install();
    page.on('pageerror', error => errors.push(error.message));
    let current = payload(), fail = false, calls = 0;
    await page.route('**/api/v1/construction/tree', route => {
      calls++;
      assert.equal(new URL(route.request().url()).search, '');
      return route.fulfill({ status: fail ? 503 : 200, contentType: 'application/json', body: JSON.stringify(fail ? { detail: 'Source unavailable' } : current) });
    });
    for (const width of [320, 360, 768, 1280, 1920]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(base + '/construction/tree?okrug=ЦАО&developer=ignored');
      await ready(page); await geometry(page);
      assert.equal(await page.locator('main details[open]').count(), 0);
      await page.screenshot({ path: path.join(output, `fixture-${width}.png`), fullPage: true });
      layouts.push({ mode: 'fixture', width });
    }
    const content = await csv(page);
    for (const value of values) assert.ok(content.includes(String(value)), `CSV omitted ${value}`);
    assert.ok(content.includes('fixture-v1'));

    const before = calls;
    current = { ...payload(), version: 'fixture-v2', metrics: payload().metrics.map(row => row.id === 'total' ? { ...row, value: 52.123456789 } : row) };
    await page.clock.fastForward(61000);
    await page.waitForFunction(() => document.querySelector('main [data-metric="total"]')?.textContent.includes('52,12'));
    assert.ok(calls > before, 'Open page did not poll for updates');
    fail = true;
    await page.getByRole('button', { name: /Обновить/ }).click();
    await page.locator('main [role="alert"]').waitFor();
    assert.equal(await page.locator('main [data-metric="total"]').count(), 0, 'Error retained stale tree');
    fail = false;
    current = { ...payload(), metrics: payload().metrics.map(row => row.id === 'total' ? { ...row, value: null } : row.id === 'renovation' ? { ...row, value: 0 } : row) };
    await page.getByRole('button', { name: /Повторить/ }).click(); await ready(page);
    assert.ok((await page.locator('main [data-metric="total"]').innerText()).includes('—'));
    assert.ok((await page.locator('main [data-metric="renovation"]').innerText()).includes('0,00'));
    await geometry(page);

    await page.route('**/api/v1/updates', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ version: 'test', generatedAt: '2026-10-16T12:00:00Z', lastRun: {}, realtyStatus: {}, realtySummary: {}, indicators: [], events: [], monitoringChanges: [], latestMonitoring: {}, marts: [], telegramPreview: '' }) }));
    await page.goto(base + '/home');
    await page.locator('main').getByRole('link', { name: 'Дерево текущего строительства', exact: true }).click();
    await ready(page); assert.equal(new URL(page.url()).pathname, '/construction/tree');
    assert.equal(await page.locator('.sidebar a[aria-current="page"]').getAttribute('href'), '/construction/tree');

    if (process.env.DASHBOARD_CHECK_LIVE === '1') {
      const live = await browser.newPage({ acceptDownloads: true });
      live.on('pageerror', error => errors.push(error.message));
      const response = await live.request.get(base + '/api/v1/construction/tree');
      assert.equal(response.status(), 200);
      const actual = await response.json();
      for (const width of [360, 1280, 1920]) {
        await live.setViewportSize({ width, height: 900 }); await live.goto(base + '/construction/tree');
        await ready(live); await geometry(live);
        await live.screenshot({ path: path.join(output, `live-${width}.png`), fullPage: true });
        layouts.push({ mode: 'live', width });
      }
      const actualCsv = await csv(live);
      for (const row of actual.metrics) if (row.value !== null) assert.ok(actualCsv.includes(String(row.value)), `Live CSV omitted ${row.id}`);
      assert.ok(actualCsv.includes(actual.version));
      await live.close();
    }
    assert.deepEqual(errors, []);
    await fs.writeFile(path.join(output, 'checks.json'), JSON.stringify({ layouts, calls, errors }, null, 2));
    console.log(JSON.stringify({ layouts: layouts.length, calls, errors, output }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
