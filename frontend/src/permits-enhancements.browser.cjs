const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const { permitPageFixture } = await import('./permitPageFixtures.mjs');
  const output = path.join(process.env.TEMP || '.', 'dashboard-permits-enhancements');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const errors = [];
  try {
    const page = await browser.newPage({ acceptDownloads: true });
    page.on('pageerror', error => errors.push(error.message));
    let requests = 0;
    await page.route('**/api/v1/construction/permits?**', async route => {
      requests += 1;
      const params = new URL(route.request().url()).searchParams, data = permitPageFixture(params.get('permit_kind') || 'housing');
      if (params.has('month')) data.selection.month = Number(params.get('month'));
      data.selection.structure.mode = params.get('structure_mode') || 'period';
      if (params.has('structure_month_to')) data.selection.structure.monthTo = Number(params.get('structure_month_to'));
      if (data.selection.structure.mode === 'annual') data.breakdowns[0].periodLabel = 'За год';
      await route.fulfill({ json: data });
    });
    for (const width of [320, 375, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto((process.env.DASHBOARD_TEST_URL || 'http://127.0.0.1:5180') + '/construction/permits?permit_kind=housing&month=5');
      await page.locator('.permit-main .chart-canvas canvas').waitFor();
      await page.waitForTimeout(200);
      assert.equal(await page.getByLabel('Только жилая площадь', { exact: true }).isChecked(), true);
      assert.equal(await page.locator('.permit-growth').count(), await page.locator('.permit-chart').count());
      assert.equal(await page.locator('.permit-growth-canvas canvas').count(), await page.locator('.permit-chart').count());
      const pixels = await page.locator('.permit-chart canvas').evaluateAll(canvases => canvases.map(canvas => {
        const values = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data, colors = new Set();
        for (let i = 0; i < values.length; i += 64) if (values[i + 3]) colors.add(`${values[i]},${values[i + 1]},${values[i + 2]}`);
        return colors.size;
      }));
      assert.ok(pixels.every(count => count > 5), `blank canvas at ${width}`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, `page overflow at ${width}`);
      await page.screenshot({ path: path.join(output, `permits-${width}.png`), fullPage: true });
      const beforeToggle = requests;
      await page.getByLabel('Только жилая площадь', { exact: true }).uncheck();
      await page.locator('.permit-main .data-toggle').click();
      assert.match(await page.locator('.permit-main tbody tr').last().innerText(), /170/);
      assert.equal(requests, beforeToggle, 'local area selection refetched unchanged source');
      await page.getByLabel('Только жилая площадь', { exact: true }).check();
      await page.locator('.permit-main .data-toggle').click();
      assert.match(await page.locator('.permit-main .chart-data-panel tbody tr').last().innerText(), /120/);
      if (width === 1440) {
        const pending = page.waitForEvent('download');
        await page.locator('.permit-main').getByRole('button', { name: 'PNG', exact: true }).click();
        const download = await pending, file = path.join(output, 'permits-with-growth.png');
        await download.saveAs(file);
        const png = fs.readFileSync(file);
        assert.ok(png.readUInt32BE(20) >= (390 + 120) * 2, 'PNG omitted growth band or export DPR');
      }
    }
    await page.getByLabel('Вид объектов', { exact: true }).selectOption('total');
    await page.locator('.permit-main .chart-canvas canvas').waitFor();
    assert.equal(await page.getByLabel('Только жилая площадь', { exact: true }).isChecked(), true);
    await page.getByLabel('Режим структуры', { exact: true }).selectOption('annual');
    await page.locator('.permit-breakdowns .permit-growth p').filter({ hasText: 'К прошлому году, % · За год' }).waitFor();
    await page.getByLabel('Вид объектов', { exact: true }).selectOption('nonresidential');
    await page.locator('.permit-main .chart-canvas canvas').waitFor();
    assert.equal(await page.getByLabel('Только жилая площадь', { exact: true }).count(), 0);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ screenshots: output, widths: [320, 375, 768, 1440], checks: 'checkbox, local selection, annual/period YoY strips, nonblank canvas, no overflow, no page errors' }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
