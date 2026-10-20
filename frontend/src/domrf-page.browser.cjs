const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const base = process.env.DASHBOARD_TEST_URL || 'http://127.0.0.1:5180';
const output = path.join(process.env.TEMP || '.', 'dashboard-domrf-presentation');

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
  const page = await browser.newPage(), errors = [], checks = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/v1/construction/domrf?*', async route => {
    const response = await route.fetch(), data = await response.json();
    if (!data.sales?.metrics) return route.fulfill({ response });
    // Test-only dense history exercises the same 82-month layout as production.
    data.sales.history = Array.from({ length: 82 }, (_, i) => {
      const period = `${2020 + Math.floor(i / 12)}-${String(i % 12 + 1).padStart(2, '0')}`;
      return { period, label: period, reportDate: null, sourceFile: 'browser-fixture.json', metrics: { ...data.sales.metrics } };
    });
    await route.fulfill({ response, json: data });
  });
  try {
    for (const [width, height] of [[1440, 1000], [1024, 900], [390, 844]]) {
      await page.setViewportSize({ width, height });
      await page.goto(base + '/construction/domrf');
      await page.locator('.domrf-construction .chart-canvas canvas').waitFor({ timeout: 120000 });
      await page.locator('.domrf-readiness .chart-canvas canvas').waitFor({ timeout: 120000 });
      await page.locator('.domrf-realization .cp-donut canvas').first().waitFor({ timeout: 120000 });
      await page.waitForTimeout(450);
      assert.equal(await page.getByRole('heading', { name: 'Объём жилищного строительства, тыс. м²', exact: true }).count(), 0);
      assert.equal(await page.locator('.cp-donut-area').count(), 0);
      assert.equal(await page.locator('main details[open], main .chart-data-panel').count(), 0);
      const layout = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth > innerWidth + 1,
        kpiFont: getComputedStyle(document.querySelector('.domrf-construction .cp-metric > strong')).fontSize,
        charts: [...document.querySelectorAll('.domrf-page .chart-canvas')].map(element => {
          const rect = element.getBoundingClientRect(), canvas = element.querySelector('canvas'), colors = new Set();
          const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
          const step = Math.max(4, Math.floor(pixels.length / 12000 / 4) * 4);
          for (let i = 0; i < pixels.length; i += step) if (pixels[i + 3]) colors.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]}`);
          return { colors: colors.size, outside: rect.left < -1 || rect.right > innerWidth + 1, scroll: element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1 };
        }),
      }));
      assert.equal(layout.overflow, false, `${width}: page overflow`);
      assert.ok(parseFloat(layout.kpiFont) >= 38, `${width}: KPI not emphasized`);
      for (const chart of layout.charts) {
        assert.ok(chart.colors > 3, `${width}: blank chart`);
        assert.equal(chart.outside, false, `${width}: chart outside viewport`);
        assert.equal(chart.scroll, false, `${width}: chart has internal scroll`);
      }
      const chart = page.locator('.domrf-realization .cp-subsection .chart-canvas');
      await chart.scrollIntoViewIfNeeded();
      const rect = await chart.boundingBox();
      let tooltip = '';
      for (let x = 0.1; x <= 0.9 && !tooltip; x += 0.1) {
        await page.mouse.move(rect.x + rect.width * x, rect.y + rect.height * 0.65);
        await page.waitForTimeout(60);
        tooltip = (await page.locator('.construction-pages-tooltip:visible').allTextContents()).join(' ');
      }
      assert.match(tooltip, /млн м².*%/, `${width}: sales hover lost area or percentage`);
      await page.mouse.move(0, 0);
      await page.screenshot({ path: path.join(output, `domrf-${width}.png`), fullPage: true });
      checks.push({ width, kpiFont: layout.kpiFont, charts: layout.charts.length });
    }
    assert.deepEqual(errors, []);
    process.stdout.write(JSON.stringify({ checks, output }) + '\n');
  } finally { await browser.close(); }
})().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
