const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');
const fixtures = require('./demoFixtures.cjs');

const output = process.env.DEMO_PRESENTATION_OUTPUT || path.join(os.tmpdir(), 'dashboard-demo-presentation');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function freePort() {
  const server = net.createServer(); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port; await new Promise(resolve => server.close(resolve)); return port;
}
function response(url) {
  const value = structuredClone(fixtures.response(url));
  if (new URL(url).pathname === '/api/v1/preferential-mortgage') {
    const segments = [[0, .9, 0], [0, 5.5, 0], [41.8, 9.1, 0], [36.9, 15.6, 0], [34.2, 22.3, 1.3], [41.8, 43.1, 11.2], [11.1, 43.4, 9], [0, 56.8, 0], [0, 25.4, 0]];
    value.annual = segments.map((parts, i) => {
      const programs = Object.fromEntries(['preferential', 'family', 'it'].map((id, j) => [id, { count: parts[j] * 1000, volumeMln: parts[j] * 9800 }]));
      return { year: 2018 + i, throughMonth: i === 8 ? 5 : 12, programs, total: { count: parts.reduce((a, b) => a + b, 0) * 1000, volumeMln: parts.reduce((a, b) => a + b, 0) * 9800 } };
    });
    value.cumulative = value.annual.map(row => ({ ...row, throughMonth: 5 }));
    value.characteristicsAnnual = value.annual.map((row, i) => ({ ...structuredClone(value.characteristicsAnnual[0]), year: row.year, throughMonth: row.throughMonth,
      programs: Object.fromEntries(['preferential', 'family', 'it'].map((id, j) => [id, { averageAmountMln: 4 + i * .7 + j, ratePct: 5 + j, termMonths: 210 + i * 10, ownFundsPct: 30 + i, areaM2: 50 + j * 3, priceThousandM2: 150 + i * 20 }])) }));
    value.characteristicsCumulative = value.characteristicsAnnual.map(row => ({ ...row, throughMonth: 5 }));
  }
  if (new URL(url).pathname === '/api/v1/mortgage') {
    const annualValue = (i, multiplier) => ({ annual: i === 17 ? null : (150 + i * 25) * multiplier, selected: (70 + i * 10) * multiplier, actualThroughMonth: 5, annualYoYPct: i === 0 || i === 17 ? null : i % 3 === 0 ? -20 : 25, selectedYoYPct: i === 0 ? null : 10 });
    value.annual = Array.from({ length: 18 }, (_, i) => ({ year: 2009 + i, volume: annualValue(i, 1e9), count: annualValue(i, 1000) }));
    const cell = (i, factor) => ({ volumeRub: (2e10 + i * 1e8) * factor, count: Math.round((1000 + i * 15) * factor), ratePct: 8 + factor * 4 + Math.sin(i / 5), termMonths: null, averageAmountRub: null });
    value.monthly = Array.from({ length: 125 }, (_, i) => ({ period: `${2016 + Math.floor(i / 12)}-${String(i % 12 + 1).padStart(2, '0')}`, all: cell(i, 1), primary: cell(i, .4), secondary: cell(i, .6) }));
    value.monthly[25].primary.volumeRub = null;
  }
  return value;
}
async function inspect(page) {
  return page.evaluate(async () => {
    const resource = performance.getEntriesByType('resource').find(entry => /echarts_core\.js/.test(entry.name));
    const echarts = await import(resource.name), element = document.querySelector('.demo-stage .chart-canvas'), chart = echarts.getInstanceByDom(element);
    const option = chart.getOption(), canvas = element.querySelector('canvas');
    const texts = chart.getZr().storage.getDisplayList().filter(item => item.type === 'tspan' && !item.ignore && item.style.text).map(item => {
      const rect = item.getBoundingRect().clone(); rect.applyTransform(item.getComputedTransform());
      return { text: item.style.text, font: item.style.font, x: rect.x, y: rect.y, width: rect.width, height: rect.height };
    });
    const bars = [];
    chart.getModel().eachSeries(series => { if (series.subType === 'bar') {
      const data = series.getData(), axis = series.coordinateSystem.getAxis('x');
      for (let i = 0; i < data.count(); i++) { const layout = data.getItemLayout(i); if (layout && Math.abs(layout.height) > 1 && Number.isFinite(layout.x)) bars.push({ ...layout, ...(axis.type === 'category' ? { band: axis.getBandWidth(), center: axis.toGlobalCoord(axis.dataToCoord(i)) } : {}) }); }
    } });
    const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data, colors = new Set();
    for (let i = 0; i < pixels.length; i += Math.max(4, Math.floor(pixels.length / 32000 / 4) * 4)) if (pixels[i + 3]) colors.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]}`);
    return { width: element.clientWidth, height: element.clientHeight, canvasWidth: canvas.width, dpr: devicePixelRatio, colors: colors.size, texts, bars,
      titleSize: parseFloat(getComputedStyle(document.querySelector('#demo-slide-title')).fontSize),
      overflow: document.documentElement.scrollWidth > innerWidth + 1 || document.querySelector('.demo-viewer').scrollWidth > innerWidth + 1,
      values: option.series.map(series => ({ name: series.name, data: series.data })),
      series: option.series.map(series => ({ name: series.name, type: series.type, barWidth: series.barWidth, labelSize: series.label?.fontSize, totals: (series.markPoint?.data || []).map(point => ({ coord: point.coord, text: typeof point.label?.formatter === 'function' ? point.label.formatter() : null })) })),
      axes: option.yAxis?.map(axis => ({ name: axis.name, gridIndex: axis.gridIndex })),
    };
  });
}
async function main() {
  let browser, child, backend;
  const checks = [], errors = [];
  fs.mkdirSync(output, { recursive: true });
  try {
    const port = await freePort(), base = `http://127.0.0.1:${port}`, root = path.resolve(__dirname, '../..'), env = { ...process.env };
    if (process.env.DEMO_PRESENTATION_API) env.DASHBOARD_API_TARGET = process.env.DEMO_PRESENTATION_API;
    else if (process.env.DEMO_PRESENTATION_REAL === '1') {
      const apiPort = await freePort(); env.DASHBOARD_API_TARGET = `http://127.0.0.1:${apiPort}`;
      backend = spawn(process.env.DASHBOARD_TEST_PYTHON || 'python', ['-X', 'utf8', '-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', String(apiPort)], { cwd: root, windowsHide: true, stdio: 'ignore', env: { ...env, PARSER_ROOT: root, PARSER_USE_REALTY_MARTS: '0', PARSER_REQUIRE_REALTY_MARTS: '0' } });
      let ready = false;
      for (let i = 0; i < 720; i++) { try { if ((await fetch(env.DASHBOARD_API_TARGET + '/api/v1/catalog')).ok) { ready = true; break; } } catch {} await pause(250); }
      assert.ok(ready, 'Private backend did not start');
    }
    child = spawn(process.execPath, [path.join(root, 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'], { cwd: path.join(root, 'frontend'), windowsHide: true, stdio: 'ignore', env });
    let ready = false;
    for (let i = 0; i < 120; i++) { try { if ((await fetch(base)).ok) { ready = true; break; } } catch {} await pause(250); }
    assert.ok(ready, 'Private Vite did not start');
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, reducedMotion: 'reduce', deviceScaleFactor: 1 });
    if (!backend && !process.env.DEMO_PRESENTATION_API) await context.route('**/api/**', route => route.fulfill({ json: response(route.request().url()) }));
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/demo');
    await page.waitForFunction(() => document.querySelectorAll('.demo-source[aria-busy="false"]').length === 13, undefined, { timeout: 180000 });
    if ((backend || process.env.DEMO_PRESENTATION_API) && await page.locator('.demo-error').count()) {
      console.log(JSON.stringify({ coldSourceErrors: await page.locator('.demo-error').allTextContents() }));
      await page.locator('.demo-error button').evaluateAll(buttons => buttons.forEach(button => button.click()));
      await page.waitForFunction(() => document.querySelectorAll('.demo-source[aria-busy="false"]').length === 13, undefined, { timeout: 90000 });
    }
    assert.deepEqual(await page.locator('.demo-error').allTextContents(), []);
    const ids = ['profile/structure/0', 'permits/main', 'sales/forecast', 'mortgage/volume/annual', 'mortgage/volume/monthly', 'mortgage/rates', 'preferential/count/annual', 'preferential/averageAmountMln/annual'];
    const available = [];
    for (const id of ids) {
      const card = page.locator(`.demo-chart-choice:has([data-demo-chart="${id}"])`);
      if (await card.count()) { await card.getByRole('checkbox').check(); available.push(id); }
    }
    assert.ok(available.length >= 7);
    await page.getByRole('checkbox', { name: 'Полный экран браузера', exact: true }).uncheck();
    await page.getByRole('button', { name: 'Начать показ', exact: true }).click();
    await page.getByRole('dialog').waitFor();
    const snapshots = new Map();
    for (const viewport of [{ width: 1920, height: 1080 }, { width: 3840, height: 2160 }, { width: 390, height: 844 }, { width: 1440, height: 900 }, { width: 1000, height: 560 }, { width: 1920, height: 800 }]) {
      await page.setViewportSize(viewport);
      const seen = new Set();
      for (let i = 0; i < available.length; i++) {
        const id = await page.locator('.demo-full-chart').getAttribute('data-demo-chart'); seen.add(id);
        await page.waitForFunction(() => !!document.querySelector('.demo-stage .chart-canvas canvas'));
        await page.waitForTimeout(180);
        const state = await inspect(page);
        assert.ok(!state.overflow, `${id} overflow`);
        assert.ok(state.colors > 5, `${id} blank canvas`);
        assert.ok(Math.abs(state.canvasWidth - state.width * state.dpr) < 3, `${id} stale canvas resolution`);
        const clipped = state.texts.filter(text => /^[+−-]?[\d\s.,]+\s*%?$/.test(String(text.text)) && (text.x < -3 || text.y < -3 || text.x + text.width > state.width + 3 || text.y + text.height > state.height + 3));
        assert.deepEqual(clipped, [], `${id} clipped numeric labels at ${viewport.width}`);
        for (const bar of state.bars.filter(bar => bar.band)) assert.ok(bar.x >= bar.center - bar.band / 2 - 2 && bar.x + bar.width <= bar.center + bar.band / 2 + 2, `${id} bar exceeds category band at ${viewport.width}`);
        if (snapshots.has(id)) assert.deepEqual(state.values, snapshots.get(id), `${id} data changed while resizing`); else snapshots.set(id, state.values);
        if (viewport.width >= 1900 && viewport.height >= 1000) {
          assert.ok(state.titleSize >= (viewport.width > 3000 ? 70 : 40), `${id} small title`);
          assert.ok(state.texts.some(text => Number.parseFloat(text.font.match(/([\d.]+)px/)?.[1]) >= (viewport.width > 3000 ? 30 : 20)), `${id} no readable chart text`);
          if (id === 'preferential/count/annual') assert.ok(state.bars.some(bar => bar.width >= (viewport.width > 3000 ? 200 : 100)), 'Preferential bars still thin');
          if (id === 'profile/structure/0') assert.ok(state.texts.some(text => Number.parseFloat(text.font.match(/([\d.]+)px/)?.[1]) >= (viewport.width > 3000 ? 100 : 70)), 'Ring total is too small');
          if (id === 'mortgage/volume/annual') {
            assert.equal(state.axes.length, 2);
            assert.ok(state.texts.some(text => String(text.text).includes('%')), 'Growth labels missing');
          }
        }
        if (id === 'permits/main') {
          assert.equal(await page.locator('.demo-stage canvas').count(), 1, 'Permits growth still uses a separate canvas');
          if (!backend && !process.env.DEMO_PRESENTATION_API && viewport.width === 1920 && viewport.height === 1080) {
            const forecast = page.locator('.demo-full-chart .chart-legend').getByRole('button', { name: 'Остаток до годового прогноза', exact: true });
            await forecast.click(); await page.waitForTimeout(100);
            const hidden = await inspect(page);
            assert.equal(hidden.series.find(series => series.name === 'Остаток до годового прогноза').totals.length, 0);
            assert.ok(hidden.series.some(series => series.name !== 'Остаток до годового прогноза' && series.totals.some(total => total.text === '200')), 'Hiding forecast lost an unrelated actual-year total');
            await forecast.click();
          }
        }
        await page.screenshot({ path: path.join(output, `${id.replaceAll('/', '-')}-${viewport.width}-${viewport.height}.png`) });
        checks.push({ id, viewport, titleSize: state.titleSize, width: state.width, height: state.height, maxBarWidth: Math.max(0, ...state.bars.map(bar => bar.width)) });
        await page.keyboard.press('ArrowRight');
      }
      assert.equal(seen.size, available.length);
    }
    await page.keyboard.press('Escape');
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ checks, errors }, null, 2));
    console.log(JSON.stringify({ passed: checks.length, output, errors }));
  } finally {
    await browser?.close();
    if (child && child.exitCode === null) { child.kill(); await new Promise(resolve => child.once('exit', resolve)); }
    if (backend && backend.exitCode === null) { backend.kill(); await new Promise(resolve => backend.once('exit', resolve)); }
  }
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { main };
