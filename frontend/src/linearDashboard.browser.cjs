const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

async function main(playwright = require('playwright')) {
  const url = process.env.LINEAR_URL || 'http://127.0.0.1:5175';
  const output = path.join(os.tmpdir(), 'linear-dashboard-checks');
  fs.mkdirSync(output, { recursive: true });
  const browser = await playwright.chromium.launch({ headless: true, channel: 'chrome' });
  const errors = [], checks = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${url}/commissioning/linear`);
    await page.locator('.linear-kpi').first().waitFor({ timeout: 120000 });
    const response = await page.request.get(`${url}/api/v1/linear/dashboard`), payload = await response.json();
    const ids = ['roads', 'bridges', 'crossings', 'metro_stations', 'metro_tunnels', 'depots'];
    assert.deepEqual(await page.locator('[data-metric]').evaluateAll(nodes => nodes.map(node => node.dataset.metric)), ids);
    assert.deepEqual(await page.locator('[data-history]').evaluateAll(nodes => nodes.map(node => node.dataset.history)), ids);
    for (const metric of payload.metrics) {
      const expected = metric.fact === null ? '—' : new Intl.NumberFormat('ru-RU', { minimumFractionDigits: metric.unit === 'км' ? 1 : 0, maximumFractionDigits: metric.unit === 'км' ? 1 : 0 }).format(metric.fact);
      assert.equal(await page.locator(`[data-metric="${metric.id}"] .linear-kpi-fact strong`).textContent(), expected);
    }
    const themes = await page.getByTitle('Включить тёмную тему', { exact: true }).count() ? ['light', 'dark'] : ['light'];
    for (const theme of themes) {
      await page.setViewportSize({ width: 1440, height: 1000 });
      if (await page.locator('html').getAttribute('data-theme') !== theme) await page.getByTitle(theme === 'light' ? 'Включить светлую тему' : 'Включить тёмную тему', { exact: true }).click();
      for (const width of [360, 390, 768, 1280, 1440, 1920]) {
        await page.setViewportSize({ width, height: 1000 });
        await page.waitForTimeout(450);
        const layout = await page.evaluate(() => {
          const tiles = [...document.querySelectorAll('.linear-kpi')];
          const rows = new Map();
          for (const tile of tiles) { const y = Math.round(tile.getBoundingClientRect().top); rows.set(y, (rows.get(y) || 0) + 1); }
          const clipped = [...document.querySelectorAll('.linear-kpi h2, .linear-kpi-fact strong, .linear-kpi dd')].filter(node => node.scrollWidth > node.clientWidth + 1).map(node => node.textContent);
          const canvases = [...document.querySelectorAll('.linear-year-chart canvas')].map(canvas => {
            const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
            let different = 0;
            for (let i = 4; i < data.length; i += 4) if (data[i] !== data[0] || data[i + 1] !== data[1] || data[i + 2] !== data[2]) different++;
            return { width: canvas.width, height: canvas.height, different };
          });
          return { overflow: document.documentElement.scrollWidth > innerWidth, rows: [...rows.values()], clipped, canvases };
        });
        assert.equal(layout.overflow, false, `${theme} ${width}: page overflow`);
        assert.deepEqual(layout.clipped, [], `${theme} ${width}: clipped text`);
        if (width >= 1280) assert.deepEqual(layout.rows, [3, 3]);
        if (width <= 390) assert.deepEqual(layout.rows, [1, 1, 1, 1, 1, 1]);
        assert.equal(layout.canvases.length, 6);
        assert.ok(layout.canvases.every(canvas => canvas.width > 0 && canvas.height > 0 && canvas.different > 150));
        checks.push({ theme, width, ...layout });
        if ([360, 1440].includes(width)) {
          await page.screenshot({ path: path.join(output, `${theme}-${width}.png`) });
          await page.screenshot({ path: path.join(output, `${theme}-${width}-full.png`), fullPage: true });
        }
      }
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator('.linear-year-chart .data-toggle').first().click();
    assert.equal(await page.locator('.linear-year-chart').first().locator('tbody tr').count(), payload.metrics[0].years.length);
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Скачать факты, прогнозы и источники Excel', exact: true }).click();
    const file = await download, stream = await file.createReadStream(), chunks = [];
    for await (const chunk of stream) chunks.push(chunk);
    assert.equal(Buffer.concat(chunks).subarray(0, 2).toString(), 'PK');
    await page.route('**/api/v1/linear/dashboard/export?*', route => route.fulfill({ status: 409, contentType: 'application/json', body: '{"detail":"Source changed"}' }));
    await page.getByRole('button', { name: 'Скачать факты, прогнозы и источники Excel', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'Источники изменились' }).waitFor();
    await page.route('**/api/v1/linear/dashboard', route => route.fulfill({ status: 503 }));
    await page.getByRole('button', { name: 'Обновить линейные объекты', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'API недоступен' }).waitFor();
    await page.unroute('**/api/v1/linear/dashboard');
    await page.getByRole('button', { name: 'Повторить', exact: true }).click();
    await page.locator('.linear-kpi').first().waitFor();
    const absent = structuredClone(payload);
    for (const metric of absent.metrics) {
      for (const key of ['fact', 'factPeriod', 'factSource', 'annualPlan', 'forecast', 'target', 'targetKind', 'percent', 'remaining']) metric[key] = null;
      metric.years = [];
    }
    absent.sources.forEach(source => { source.file = null; source.date = null; });
    await page.route('**/api/v1/linear/dashboard', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify(absent) }));
    await page.getByRole('button', { name: 'Обновить линейные объекты', exact: true }).click();
    await page.locator('.linear-kpi-fact strong').first().getByText('—', { exact: true }).waitFor();
    assert.deepEqual(await page.locator('.linear-kpi-fact strong').allTextContents(), ['—', '—', '—', '—', '—', '—']);
    assert.equal(await page.locator('[data-history]').count(), 6);
    assert.equal(await page.locator('.linear-progress[role="progressbar"]').count(), 0);
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify(checks, null, 2));
    console.log(JSON.stringify({ passed: true, layouts: checks.length, screenshots: output, exports: 'Excel + 409', recovery: '503 + retry', errors }));
  } finally { await browser.close(); }
}

module.exports = { main };
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
