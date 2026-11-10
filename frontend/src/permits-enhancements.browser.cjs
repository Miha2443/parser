const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { parse } = require('papaparse');

(async () => {
  const { permitPageFixture } = await import('./permitPageFixtures.mjs');
  const output = path.join(process.env.TEMP || '.', 'dashboard-permits-enhancements');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const errors = [];
  try {
    const page = await browser.newPage({ acceptDownloads: true });
    page.on('pageerror', error => errors.push(error.message));
    const area = page.getByRole('group', { name: 'Площадь разрешений', exact: true });
    const livingArea = area.getByRole('button', { name: 'Жилая площадь', exact: true });
    const fullArea = area.getByRole('button', { name: 'Общая площадь в жилье', exact: true });
    const base = process.env.DASHBOARD_TEST_URL || 'http://127.0.0.1:5180';
    function selectionUrl() {
      const url = new URL(page.url());
      url.searchParams.delete('permit_living_only');
      url.searchParams.sort();
      return url.href;
    }
    const breakdownState = charts => charts.slice(1).map(({ growthPixels, ...chart }) => chart);
    async function assertArea(livingOnly) {
      await page.waitForFunction(expected => {
        const buttons = document.querySelectorAll('.permit-area-toggle button');
        return buttons.length === 2 && buttons[0].getAttribute('aria-pressed') === String(expected) && buttons[1].getAttribute('aria-pressed') === String(!expected);
      }, livingOnly);
      await page.locator('.permit-main .chart-canvas canvas').waitFor();
      assert.equal(new URL(page.url()).searchParams.get('permit_living_only'), livingOnly ? null : '0');
      assert.equal((await chartState())[0].series.find(series => series.id === 'selected').data.at(-1), livingOnly ? 120 : 170);
    }
    async function assertKeyboardFocus(button) {
      assert.equal(await button.evaluate(element => element === document.activeElement && element.matches(':focus-visible') && getComputedStyle(element).outlineStyle !== 'none' && parseFloat(getComputedStyle(element).outlineWidth) >= 2), true);
    }
    async function csvDownload(livingOnly) {
      const pending = page.waitForEvent('download');
      await page.locator('.permit-main').getByRole('button', { name: 'CSV', exact: true }).click();
      const download = await pending, filename = `permits-housing-${livingOnly ? 'living' : 'full'}-m5-structure-period-2011-2026-m1-5.csv`;
      assert.equal(download.suggestedFilename(), filename);
      const file = path.join(output, filename);
      await download.saveAs(file);
      const csv = parse(fs.readFileSync(file, 'utf8'), { delimiter: ';', skipEmptyLines: true });
      assert.deepEqual(csv.errors, []);
      assert.equal(csv.data.at(-1)[0], '2026');
      assert.equal(csv.data.at(-1)[1], livingOnly ? '120' : '170');
      assert.equal(csv.data.at(-1)[3], livingOnly ? '250' : '300');
      assert.equal(csv.data.at(-1)[13], 'test');
      assert.match(csv.data[0].at(-1), /legacy/);
      assert.equal(csv.data.find(row => row[0] === '2025').at(-1), '200');
    }
    async function sourceDownload(livingOnly) {
      const pending = page.waitForEvent('download');
      await page.getByRole('button', { name: 'Скачать исходные разрешения Excel: утверждённый ряд и оба варианта площади', exact: true }).click();
      const download = await pending;
      assert.equal(download.suggestedFilename(), `permits-housing-${livingOnly ? 'living' : 'full'}-m5-structure-period-2011-2026-m1-5.xlsx`);
      const file = path.join(output, download.suggestedFilename());
      await download.saveAs(file);
      assert.equal(fs.readFileSync(file, 'utf8'), 'permit-source-fixture');
    }
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
    const exportRequests = [];
    await page.route('**/api/v1/construction/permits/export?**', async route => {
      exportRequests.push(route.request().url());
      await route.fulfill({ contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', body: 'permit-source-fixture' });
    });
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
      await page.goto(base + '/construction/permits?permit_kind=housing&month=5&unrelated=keep#permits-page');
      await page.locator('.permit-main .chart-canvas canvas').waitFor();
      await page.waitForURL(url => url.searchParams.get('structure_month_to') === '5');
      await page.waitForTimeout(200);
      await assertArea(true);
      assert.deepEqual(await area.getByRole('button').allTextContents(), ['Жилая площадь', 'Общая площадь в жилье']);
      assert.equal(await page.locator('.permit-main input[type="checkbox"]').count(), 0);
      const layout = await area.evaluate(element => {
        const group = element.getBoundingClientRect();
        return [...element.querySelectorAll('button')].map(button => {
          const rect = button.getBoundingClientRect(), range = document.createRange();
          range.selectNodeContents(button);
          return { left: rect.left, right: rect.right, top: rect.top, height: rect.height,
            fits: button.scrollWidth <= button.clientWidth && button.scrollHeight <= button.clientHeight && rect.left >= group.left && rect.right <= group.right && [...range.getClientRects()].every(text => text.left >= rect.left && text.right <= rect.right && text.top >= rect.top && text.bottom <= rect.bottom) };
        });
      });
      assert.ok(layout.every(button => button.fits && button.height >= 44), `clipped area selector at ${width}`);
      assert.equal(layout[0].top, layout[1].top);
      assert.equal(layout[0].height, layout[1].height);
      assert.ok(layout[0].right <= layout[1].left, `area choices are not left/right at ${width}`);
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
      const beforeToggle = requests, beforeUrl = selectionUrl(), beforeBreakdowns = breakdownState(state);
      await page.getByLabel('Месяц разрешений', { exact: true }).focus();
      await page.keyboard.press('Tab');
      await assertKeyboardFocus(livingArea);
      await page.keyboard.press('Tab');
      await assertKeyboardFocus(fullArea);
      await page.keyboard.press('Space');
      await assertArea(false);
      await assertKeyboardFocus(fullArea);
      assert.equal(selectionUrl(), beforeUrl, 'area choice changed unrelated URL filters or hash');
      assert.deepEqual(breakdownState(await chartState()), beforeBreakdowns, 'area choice changed breakdowns');
      await page.screenshot({ path: path.join(output, `permits-${width}-full.png`), fullPage: true });
      await page.locator('.permit-main .data-toggle').click();
      assert.equal(await page.locator('.permit-main tbody tr').last().locator('td').nth(1).innerText(), '170');
      if (width === 1440) { await csvDownload(false); await sourceDownload(false); }
      assert.equal(requests, beforeToggle, 'local area selection refetched unchanged source');
      await livingArea.focus();
      await page.keyboard.press('Enter');
      await assertArea(true);
      await assertKeyboardFocus(livingArea);
      assert.equal(selectionUrl(), beforeUrl, 'living default did not restore original URL');
      assert.deepEqual(breakdownState(await chartState()), beforeBreakdowns);
      assert.equal(requests, beforeToggle);
      await page.locator('.permit-main .data-toggle').click();
      assert.equal(await page.locator('.permit-main .chart-data-panel tbody tr').last().locator('td').nth(1).innerText(), '120');
      if (width === 1440) {
        await csvDownload(true); await sourceDownload(true);
        assert.equal(exportRequests.length, 2);
        assert.equal(exportRequests[0], exportRequests[1], 'area choice changed source export identity');
        assert.deepEqual(Object.fromEntries(new URL(exportRequests[0]).searchParams), { permit_kind: 'housing', month: '5', structure_mode: 'period', structure_month_from: '1', structure_month_to: '5', structure_year_from: '2011', structure_year_to: '2026', required_version: 'test' });
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
    await assertArea(true);
    const beforeTotalToggle = requests;
    await fullArea.click();
    await assertArea(false);
    assert.equal(requests, beforeTotalToggle);
    await page.getByLabel('Режим структуры', { exact: true }).selectOption('annual');
    await page.locator('.permit-breakdowns .permit-growth-accessible caption').filter({ hasText: 'К прошлому году, % · За год' }).waitFor({ state: 'attached' });
    await page.getByLabel('Вид объектов', { exact: true }).selectOption('nonresidential');
    await page.locator('.permit-main .chart-canvas canvas').waitFor();
    assert.equal(await area.count(), 0);
    assert.equal(new URL(page.url()).searchParams.get('permit_living_only'), '0');
    assert.equal((await chartState())[0].series.find(series => series.id === 'selected').data.at(-1), 120);
    await page.getByLabel('Вид объектов', { exact: true }).selectOption('housing');
    await assertArea(false);
    await livingArea.click();
    await assertArea(true);
    await page.goto(base + '/construction/permits?permit_kind=housing&month=12&structure_mode=annual&structure_month_to=12&permit_living_only=0&unrelated=keep#permits-page');
    await assertArea(false);
    const directUrl = selectionUrl(), beforeDirectToggle = requests;
    assert.equal(await page.getByLabel('Месяц разрешений', { exact: true }).inputValue(), '12');
    assert.equal(await page.getByLabel('Режим структуры', { exact: true }).inputValue(), 'annual');
    await livingArea.click();
    await assertArea(true);
    assert.equal(selectionUrl(), directUrl);
    await page.goBack();
    await assertArea(false);
    await page.goForward();
    await assertArea(true);
    assert.equal(requests, beforeDirectToggle, 'area URL history refetched source');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ screenshots: output, widths: [320, 375, 768, 1440], checks: 'single canvas, aligned dual grids, smooth percent growth, legends/totals/hover, PNG growth pixels, ordered pressed area buttons, Tab/Space/Enter and visible focus, unclipped labels, local selection, URL/filter/hash/history identity, CSV/source export identity, housing/total/nonresidential, direct full-area URL, annual/period YoY, nonblank canvas, no overflow, no page errors' }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
