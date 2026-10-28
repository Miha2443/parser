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
    async function chartState() {
      return page.locator('.permit-chart .chart-canvas').evaluateAll(async elements => {
        const resource = performance.getEntriesByType('resource').find(entry => /echarts_core\.js/.test(entry.name));
        const { getInstanceByDom } = await import(resource.name);
        return elements.map(element => {
          const chart = getInstanceByDom(element), option = chart.getOption(), line = option.series.at(-1);
          const graphics = option.graphic.flatMap(group => group.elements ?? [group]);
          const canvas = element.querySelector('canvas'), ratio = canvas.width / element.clientWidth;
          const values = canvas.getContext('2d').getImageData(0, 0, canvas.width, Math.floor(130 * ratio)).data;
          let growthPixels = 0;
          for (let i = 0; i < values.length; i += 4) if (Math.abs(values[i] - 53) < 8 && Math.abs(values[i + 1] - 154) < 8 && Math.abs(values[i + 2] - 121) < 8 && values[i + 3]) growthPixels++;
          return { grids: option.grid, axes: option.yAxis.map(axis => ({ min: axis.min, max: axis.max, name: axis.name })),
            series: option.series.map(series => ({ id: series.id, type: series.type, smooth: series.smooth, connectNulls: series.connectNulls, xAxisIndex: series.xAxisIndex, yAxisIndex: series.yAxisIndex, data: series.data })),
            selected: option.legend[0].selected, totals: graphics.filter(item => item.id.startsWith('permit-total-')).map(item => item.id), growthPixels,
            positions: line.data.map((_, index) => [chart.convertToPixel({ xAxisIndex: 0 }, index), chart.convertToPixel({ xAxisIndex: 1 }, index)]),
          };
        });
      });
    }
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
      assert.equal(await page.locator('.permit-growth-canvas').count(), 0);
      assert.equal(await page.locator('.permit-chart canvas').count(), await page.locator('.permit-chart').count());
      assert.equal(await page.locator('.permit-growth-accessible').count(), await page.locator('.permit-chart').count());
      const state = await chartState();
      for (const chart of state) {
        assert.equal(chart.grids.length, 2);
        assert.equal(chart.axes[0].name, '%'); assert.equal(chart.axes[1].name, 'тыс. м²');
        assert.ok(chart.grids[0].top + chart.grids[0].height < chart.grids[1].top);
        assert.ok(chart.positions.every(([growthX, barX]) => Math.abs(growthX - barX) < .01), 'growth and bar year centers differ');
        assert.ok(chart.growthPixels > 3, `growth band missing from shared canvas at ${width}`);
        assert.equal(chart.series.at(-1).smooth, .25); assert.equal(chart.series.at(-1).connectNulls, false);
        assert.ok(chart.series.slice(0, -1).every(series => series.type === 'bar' && series.xAxisIndex === 1 && series.yAxisIndex === 1));
      }
      const growthLegend = page.locator('.permit-main .chart-legend').getByRole('button', { name: 'К прошлому году, %', exact: true });
      await growthLegend.hover();
      await page.locator('.construction-pages-tooltip').filter({ hasText: 'К прошлому году:' }).waitFor({ state: 'visible' });
      await growthLegend.click();
      let toggled = await chartState();
      assert.equal(toggled[0].selected['К прошлому году, %'], false);
      assert.deepEqual(toggled[0].totals, state[0].totals); assert.deepEqual(toggled[0].axes, state[0].axes);
      await growthLegend.click();
      const barLegend = page.locator('.permit-main .chart-legend').getByRole('button', { name: 'Выбранный период', exact: true });
      await barLegend.click();
      toggled = await chartState();
      assert.equal(toggled[0].totals.length, 0);
      assert.deepEqual(toggled[0].series.at(-1).data, state[0].series.at(-1).data);
      await barLegend.click();
      await page.mouse.move(0, 0);
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
        const pngGrowthPixels = await page.evaluate(async data => {
          const frame = new Image(); frame.src = data; await frame.decode();
          const canvas = document.createElement('canvas'); canvas.width = frame.naturalWidth; canvas.height = 260;
          const context = canvas.getContext('2d'); context.drawImage(frame, 0, 0);
          const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
          let count = 0;
          for (let i = 0; i < pixels.length; i += 4) if (Math.abs(pixels[i] - 53) < 8 && Math.abs(pixels[i + 1] - 154) < 8 && Math.abs(pixels[i + 2] - 121) < 8) count++;
          return count;
        }, `data:image/png;base64,${png.toString('base64')}`);
        assert.ok(pngGrowthPixels > 3, 'exported PNG has no growth pixels');
      }
    }
    await page.getByLabel('Вид объектов', { exact: true }).selectOption('total');
    await page.locator('.permit-main .chart-canvas canvas').waitFor();
    assert.equal(await page.getByLabel('Только жилая площадь', { exact: true }).isChecked(), true);
    await page.getByLabel('Режим структуры', { exact: true }).selectOption('annual');
    await page.locator('.permit-breakdowns .permit-growth-accessible caption').filter({ hasText: 'К прошлому году, % · За год' }).waitFor({ state: 'attached' });
    await page.getByLabel('Вид объектов', { exact: true }).selectOption('nonresidential');
    await page.locator('.permit-main .chart-canvas canvas').waitFor();
    assert.equal(await page.getByLabel('Только жилая площадь', { exact: true }).count(), 0);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ screenshots: output, widths: [320, 375, 768, 1440], checks: 'single canvas, aligned dual grids, smooth percent growth, legends/totals/hover, PNG growth pixels, checkbox, local selection, annual/period YoY, nonblank canvas, no overflow, no page errors' }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
