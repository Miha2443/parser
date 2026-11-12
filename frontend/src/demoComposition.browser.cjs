const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');
const { response } = require('./demoPresentation.browser.cjs');

const output = path.join(os.tmpdir(), 'dashboard-demo-composition');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function freePort() {
  const server = net.createServer(); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port; await new Promise(resolve => server.close(resolve)); return port;
}
function data(input) {
  const url = new URL(input), value = response(input);
  if (url.pathname === '/api/v1/construction/permits') {
    value.selection.permitKind = url.searchParams.get('permit_kind') || 'total';
    if (url.searchParams.has('month')) value.selection.month = Number(url.searchParams.get('month'));
    if (url.searchParams.has('structure_month_to')) value.selection.structure.monthTo = Number(url.searchParams.get('structure_month_to'));
    for (const kind of Object.keys(value.controls.monthsByKind)) value.controls.monthsByKind[kind] = Array.from({ length: 12 }, (_, i) => i + 1);
  }
  if (url.pathname === '/api/v1/preferential-mortgage') {
    for (const rows of [value.characteristicsAnnual, value.characteristicsCumulative]) {
      const latest = rows.at(-1);
      for (const program of ['preferential', 'it']) for (const metric of Object.keys(latest.programs[program])) {
        latest.programs[program][metric] = null; latest.programThroughMonths[program][metric] = null;
      }
      latest.programs.family.ownFundsPct = null;
    }
  }
  return value;
}
async function chartState(page) {
  return page.evaluate(async () => {
    const resource = performance.getEntriesByType('resource').find(entry => /echarts_core\.js/.test(entry.name));
    const { getInstanceByDom } = await import(resource.name);
    return [...document.querySelectorAll('.demo-stage .chart-canvas')].map(element => {
      const chart = getInstanceByDom(element), option = chart.getOption(), canvas = element.querySelector('canvas');
      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      let ink = 0;
      // A known zero share is a valid gray ring, not a blank colored canvas.
      for (let i = 0; i < pixels.length; i += 32) if (pixels[i + 3] && Math.min(pixels[i], pixels[i + 1], pixels[i + 2]) < 200) ink++;
      return { id: element.closest('[data-demo-chart]').dataset.demoChart, width: element.clientWidth, height: element.clientHeight,
        pixelWidth: canvas.width, dpr: devicePixelRatio, ink, selected: option.legend[0].selected,
        source: option.series.map(series => series.data) };
    });
  });
}
async function main() {
  let browser, server;
  const checks = [], errors = [], snapshots = new Map(); fs.mkdirSync(output, { recursive: true });
  try {
    const port = await freePort(), root = path.resolve(__dirname, '../..'), base = `http://127.0.0.1:${port}`;
    server = spawn(process.execPath, [path.join(root, 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'], { cwd: path.join(root, 'frontend'), windowsHide: true, stdio: 'ignore' });
    let ready = false;
    for (let i = 0; i < 120; i++) { try { if ((await fetch(base)).ok) { ready = true; break; } } catch {} await pause(250); }
    assert(ready);
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, reducedMotion: 'reduce' });
    await context.route('**/api/**', route => route.fulfill({ json: data(route.request().url()) }));
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/demo');
    await page.waitForFunction(() => document.querySelectorAll('.demo-source[aria-busy="false"]').length === 13);
    assert.deepEqual(await page.locator('.demo-error').allTextContents(), []);
    // The full-area primary and its housing breakdown have matching definitions.
    await page.getByRole('button', { name: 'Общая площадь в жилье', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('[data-demo-source="permits"]').getAttribute('aria-busy') === 'false');
    const ids = ['profile/annual', 'profile/structure/0', 'profile/structure/1', 'profile/structure/2',
      'domrf/realization', 'domrf/sold', 'domrf/unsold', 'domrf/sales_not_open', 'permits/main', 'permits/housing_components',
      ...['annual', 'cumulative'].flatMap(period => ['volumeMln', 'count', 'averageAmountMln', 'ratePct', 'termMonths', 'ownFundsPct', 'areaM2', 'priceThousandM2'].map(metric => `preferential/${metric}/${period}`))];
    const available = [];
    for (const id of ids) {
      const card = page.locator(`.demo-chart-choice:has([data-demo-chart="${id}"])`);
      if (await card.count() && await card.getByRole('checkbox').isEnabled()) { await card.getByRole('checkbox').check(); available.push(id); }
    }
    await page.getByRole('checkbox', { name: 'Полный экран браузера', exact: true }).uncheck();
    await page.getByRole('button', { name: 'Начать показ', exact: true }).click();
    await page.getByRole('button', { name: 'Приостановить показ', exact: true }).click();
    const count = Number((await page.locator('.demo-slide-count').innerText()).split('/')[1]);
    assert(count < available.length);
    const sizes = [[1920, 1080], [3840, 2160], [1440, 900], [1000, 560], [390, 844], [320, 740]];
    for (const [width, height] of sizes) {
      await page.setViewportSize({ width, height });
      let focused = 0, pairs = 0;
      for (let i = 0; i < count; i++) {
        await page.waitForTimeout(100);
        const stage = page.locator('.demo-stage'), id = await stage.getAttribute('data-demo-slide');
        const states = await chartState(page);
        for (const chart of states) {
          assert(chart.ink > 5, `${id}/${chart.id}: blank chart`);
          assert(Math.abs(chart.pixelWidth - chart.width * chart.dpr) < 3);
          if (snapshots.has(chart.id)) assert.deepEqual(chart.source, snapshots.get(chart.id)); else snapshots.set(chart.id, chart.source);
        }
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1 || document.querySelector('.demo-viewer').scrollWidth > innerWidth + 1), false);
        if (await stage.evaluate(element => element.classList.contains('demo-layout-focus'))) {
          focused++;
          assert.equal(await stage.locator('[data-demo-role="primary"]').count(), 1);
          const primary = await stage.locator('[data-demo-role="primary"]').boundingBox();
          const detail = await stage.locator('[data-demo-role="detail"]').first().boundingBox();
          if (width >= 1280) { assert(primary.width > detail.width * 1.6); assert(primary.x + primary.width <= detail.x + 1); }
          else assert(primary.y + primary.height <= detail.y + 1, JSON.stringify({ id, width, height, primary, detail }));
        }
        if (await stage.evaluate(element => element.classList.contains('demo-layout-pair'))) {
          pairs++;
          const panels = await stage.locator('.demo-panel').evaluateAll(elements => elements.map(element => { const r = element.getBoundingClientRect(); return { width: r.width, y: r.y }; }));
          assert(Math.abs(panels[0].width - panels[1].width) < 1);
          if (width > 680) assert(Math.abs(panels[0].y - panels[1].y) < 1);
          assert.equal(await page.locator('.demo-shared-legend').count(), 1);
          assert.equal(await stage.locator('.chart-legend').count(), 0);
          const summary = await stage.locator('.demo-summary').allTextContents();
          assert.equal(summary.length, 2); assert(summary.every(text => /Всего/.test(text)));
          if (width === 1920 && id === 'preferential/flows/annual') {
            const family = page.locator('.demo-shared-legend').getByRole('button', { name: 'Семейная ипотека', exact: true });
            await family.click(); await page.waitForTimeout(80);
            assert((await chartState(page)).every(chart => chart.selected['Семейная ипотека'] === false));
            assert.deepEqual(await stage.locator('.demo-summary').allTextContents(), summary);
            await family.focus(); await page.keyboard.press('Enter'); await page.waitForTimeout(80);
            assert((await chartState(page)).every(chart => chart.selected['Семейная ипотека'] === true));
          }
        }
        if (width >= 1280 && id.startsWith('preferential/characteristics/')) {
          assert.equal(await stage.evaluate(element => element.scrollHeight > element.clientHeight + 1), false);
          const rows = await stage.locator('.demo-panel').evaluateAll(panels => panels.map(panel => ({ top: panel.offsetTop, canvas: panel.querySelector('.chart-canvas').getBoundingClientRect().y })));
          for (const row of rows) for (const other of rows.filter(other => Math.abs(other.top - row.top) < 1)) assert(Math.abs(row.canvas - other.canvas) < 1);
        }
        checks.push({ id, width, height, panels: states.length });
        if (width === 1920 || width === 390 || width === 3840) await page.screenshot({ path: path.join(output, `${id.replaceAll('/', '-')}-${width}.png`) });
        await page.keyboard.press('ArrowRight');
      }
      assert(focused >= 3); assert.equal(pairs, 2);
    }
    await page.keyboard.press('Escape');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: checks.length, output, errors }));
  } finally {
    await browser?.close();
    if (server && server.exitCode === null) { server.kill(); await new Promise(resolve => server.once('exit', resolve)); }
  }
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { main };
