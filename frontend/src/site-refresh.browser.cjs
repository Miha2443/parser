const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '../..'), children = [];
const output = process.env.SITE_REFRESH_OUTPUT || path.join(os.tmpdir(), 'dashboard-site-refresh');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function freePort() {
  const server = net.createServer(); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port; await new Promise(resolve => server.close(resolve)); return port;
}
function start(executable, args, options) {
  const child = spawn(executable, args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], ...options });
  child.messages = '';
  for (const stream of [child.stdout, child.stderr]) stream.on('data', chunk => child.messages = (child.messages + chunk).slice(-10000));
  children.push(child); return child;
}
async function ready(url, child) {
  for (let attempt = 0; attempt < 720; attempt++) {
    if (child.exitCode !== null) throw new Error(child.messages);
    try { if ((await fetch(url)).ok) return; } catch {}
    await pause(250);
  }
  throw new Error(`Readiness failed: ${url}\n${child.messages}`);
}
async function inspect(page) {
  await page.waitForFunction(() => [...document.querySelectorAll('main .chart-canvas, main .permit-growth-canvas')].every(element => {
    const canvas = element.querySelector('canvas'), ratio = Math.max(1, devicePixelRatio * (visualViewport?.scale || 1));
    return canvas && Math.abs(canvas.width - element.clientWidth * ratio) < ratio + 2;
  }), undefined, { timeout: 10000 });
  await page.waitForTimeout(300);
  const result = await page.evaluate(async () => {
    const resource = performance.getEntriesByType('resource').find(entry => /echarts_core\.js/.test(entry.name));
    const echarts = await import(resource.name);
    const expected = Math.max(1, devicePixelRatio * (visualViewport?.scale || 1));
    return {
      overflow: document.documentElement.scrollWidth > innerWidth + 1,
      expected,
      charts: [...document.querySelectorAll('main .chart-canvas, main .permit-growth-canvas')].map(element => {
        const option = echarts.getInstanceByDom(element).getOption(), canvas = element.querySelector('canvas');
        const colors = new Set();
        if (canvas) {
          const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
          const step = Math.max(4, Math.floor(pixels.length / 8000 / 4) * 4);
          for (let i = 0; i < pixels.length; i += step) if (pixels[i + 3]) colors.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]}`);
        }
        return { width: canvas?.width, cssWidth: element.clientWidth, colors: colors.size,
          values: option.series.map(series => ({ name: series.name, data: series.data })) };
      }),
    };
  });
  assert.equal(result.overflow, false, 'Page overflow');
  assert.ok(result.charts.length > 0, 'Charts missing');
  for (const chart of result.charts) {
    assert.ok(chart.colors > 3, 'Blank chart');
    assert.ok(Math.abs(chart.width - chart.cssWidth * result.expected) < result.expected + 2, `${page.url()} Stale pixel ratio: ${JSON.stringify({ ...chart, values: undefined, expected: result.expected })}`);
  }
  return result;
}
async function main() {
  let browser;
  fs.mkdirSync(output, { recursive: true });
  try {
    const api = `http://127.0.0.1:${await freePort()}`, web = `http://127.0.0.1:${await freePort()}`;
    const backend = start(process.env.DASHBOARD_TEST_PYTHON || 'python', ['-X', 'utf8', '-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', new URL(api).port], { cwd: root, env: { ...process.env, PARSER_ROOT: root, PARSER_USE_REALTY_MARTS: '0', PARSER_REQUIRE_REALTY_MARTS: '0' } });
    await ready(api + '/api/v1/catalog', backend);
    const frontend = start(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', new URL(web).port, '--strictPort'], { cwd: path.join(root, 'frontend'), env: { ...process.env, DASHBOARD_API_TARGET: api } });
    await ready(web, frontend);
    browser = await chromium.launch({ headless: true, channel: process.env.DASHBOARD_BROWSER_CHANNEL || 'chrome' });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    const page = await context.newPage(), cdp = await context.newCDPSession(page), errors = [], checks = [];
    page.on('pageerror', error => errors.push(error.message));
    const routes = process.env.SITE_REFRESH_ROUTES ? JSON.parse(process.env.SITE_REFRESH_ROUTES) : ['/', '/apartments', '/apartments/developer', '/construction/permits', '/construction/domrf', '/commissioning/annual', '/commissioning/operational', '/commissioning/linear', '/economics/accounts', '/economics/ipc?period=year', '/economics/salary', '/economics/mortgage', '/economics/preferential-mortgage'];
    for (const route of routes) {
      console.log(`Checking ${route}`);
      await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
      await page.goto(web + route);
      await page.locator('main .chart-canvas canvas').first().waitFor({ timeout: 180000 });
      await page.waitForFunction(() => !document.querySelector('main .load-state, main .cp-state'), undefined, { timeout: 180000 });
      const before = await inspect(page);
      for (const ratio of [2, 1.25, 1]) {
        await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: ratio, mobile: false });
        const after = await inspect(page);
        assert.deepEqual(after.charts.map(chart => chart.values), before.charts.map(chart => chart.values), `${route}: zoom changed data`);
        checks.push({ route, ratio, charts: after.charts.length });
      }
      if (route === '/construction/permits') {
        await cdp.send('Emulation.setPageScaleFactor', { pageScaleFactor: 1.5 });
        const pinch = await inspect(page);
        assert.deepEqual(pinch.charts.map(chart => chart.values), before.charts.map(chart => chart.values), 'Pinch zoom changed data');
        await cdp.send('Emulation.setPageScaleFactor', { pageScaleFactor: 1 });
        await inspect(page);
        checks.push({ route, pinchScale: 1.5, charts: pinch.charts.length });
      }
      await page.setViewportSize({ width: 390, height: 900 });
      await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 900, deviceScaleFactor: 2, mobile: false });
      await inspect(page);
      if (['/', '/construction/permits', '/commissioning/annual'].includes(route)) await page.screenshot({ path: path.join(output, `${route.replaceAll('/', '-') || 'profile'}-390.png`), fullPage: true });
    }
    await cdp.send('Emulation.clearDeviceMetricsOverride');
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(web + '/');
    await page.locator('.developer-name h2').waitFor({ timeout: 120000 });
    const catalog = await (await page.request.get(web + '/api/v1/catalog')).json();
    const expected = catalog.controls.developers;
    assert.ok(expected.length > 3);
    const company = page.getByRole('combobox', { name: 'Группа компаний', exact: true });
    await company.click();
    assert.deepEqual(await page.getByRole('listbox', { name: 'Группы компаний' }).getByRole('option').allTextContents(), expected.map(row => row.developer));
    const fund = expected.find(row => row.developerKey.includes('реновац'));
    assert.ok(fund);
    await company.fill('РЕНОВАЦ');
    await page.getByRole('listbox', { name: 'Группы компаний' }).getByRole('option', { name: fund.developer, exact: true }).click();
    await page.waitForURL(url => url.searchParams.get('developer') === fund.developerKey);
    await page.locator('.developer-name h2').filter({ hasText: fund.developer }).waitFor();
    await company.click(); assert.equal(await company.getAttribute('aria-expanded'), 'true'); await company.fill('несуществующий застройщик');
    await page.getByText('Нет совпадений', { exact: true }).waitFor(); await company.press('Escape');
    assert.equal(await company.inputValue(), fund.developer);
    const brand = page.locator('.navigation-brand > a');
    await brand.click(); await page.waitForURL(url => url.pathname === '/home');
    assert.equal(await page.getByText('Последнее обновление', { exact: false }).count(), 0);
    const home = page.locator('.navigation-home a');
    const beforeScroll = await home.boundingBox();
    await page.locator('.navigation-sections').evaluate(element => element.scrollTop = element.scrollHeight);
    assert.deepEqual(await home.boundingBox(), beforeScroll);
    await page.goto(web + '/construction/tree');
    await page.locator('[data-metric="living"]').waitFor({ timeout: 120000 });
    const order = await page.locator('.ct-housing > .ct-children > li').evaluateAll(elements => elements.map(element => element.querySelector('[data-metric]')?.getAttribute('data-metric')));
    assert.deepEqual(order, ['living', 'embedded', 'mop']);
    assert.equal(await page.getByText('Отчётный период', { exact: false }).count(), 0);
    await page.screenshot({ path: path.join(output, 'tree-1440.png'), fullPage: true });
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ checks, errors }, null, 2));
    console.log(JSON.stringify({ passed: true, checks: checks.length, output, errors }));
    if (process.env.SITE_REFRESH_DEMO === '1') {
      process.env.DEMO_TEST_URL = web;
      await require('./demo.browser.cjs').main();
    }
  } finally {
    await browser?.close();
    for (const child of children.reverse()) { child.kill(); await new Promise(resolve => child.exitCode !== null ? resolve() : child.once('exit', resolve)); }
  }
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
