const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');
const { catalog, report, MSK, RF } = require('./economics.browser.cjs');

const root = path.resolve(__dirname, '../..'), children = [];
const output = process.env.BAR_VALUES_OUTPUT || path.join(os.tmpdir(), 'dashboard-bar-values');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function freePort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}
function start(executable, args, options) {
  const child = spawn(executable, args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], ...options });
  child.messages = '';
  for (const stream of [child.stdout, child.stderr]) stream.on('data', chunk => child.messages = (child.messages + chunk).slice(-10000));
  children.push(child);
  return child;
}
async function ready(url, child) {
  for (let attempt = 0; attempt < 120; attempt++) {
    if (child.exitCode !== null) throw new Error(child.messages);
    try { if ((await fetch(url)).ok) return; } catch {}
    await pause(250);
  }
  throw new Error(`Readiness failed: ${url}\n${child.messages}`);
}
async function inspect(page, selector) {
  await page.locator(`${selector} canvas`).first().waitFor();
  await page.waitForTimeout(450);
  const results = await page.locator(selector).evaluateAll(async elements => {
    const resource = performance.getEntriesByType('resource').find(entry => /echarts_core\.js/.test(entry.name));
    const { getInstanceByDom } = await import(resource.name);
    return elements.map(element => {
      const chart = getInstanceByDom(element), option = chart.getOption(), labels = [], errors = [];
      const selected = option.legend[0].selected;
      const expected = [];
      const allValues = option.series.flatMap(series => series.data).filter(value => value !== null);
      const linear = !!element.closest('[data-history]');
      const digits = allValues.some(value => Math.abs(value) >= 1000) || linear && option.yAxis[0].name !== 'км' ? 0 : 1;
      const format = value => new Intl.NumberFormat('ru-RU', { maximumFractionDigits: digits }).format(value);
      option.series.forEach((series, seriesIndex) => {
        if (series.type !== 'bar' || selected[series.name] === false) return;
        const data = chart.getModel().getSeriesByIndex(seriesIndex).getData();
        series.data.forEach((value, index) => {
          if (value === null) return;
          const bar = data.getItemGraphicEl(index), label = bar?.getTextContent();
          const top = option.series.findLastIndex(s => selected[s.name] !== false && s.data[index] !== null);
          if (!series.stack || seriesIndex === top) {
            if (series.stack) {
              const visible = option.series.filter(s => selected[s.name] !== false).map(s => s.data[index]);
              if (!visible.includes(null)) expected.push(format(visible.reduce((sum, part) => sum + part, 0)));
              // Source totals can exist even when an individual component is absent.
              else if (label?.style.text) expected.push(label.style.text);
            } else expected.push(format(value));
          }
          if (!label?.style.text) return;
          if (label.ignore) errors.push('Hidden numeric label');
          const rect = label.getBoundingRect().clone();
          rect.applyTransform(label.getComputedTransform());
          const layout = data.getItemLayout(index);
          if (rect.x < -1 || rect.y < -1 || rect.x + rect.width > element.clientWidth + 1 || rect.y + rect.height > element.clientHeight + 1) errors.push(`Clipped ${label.style.text}: ${JSON.stringify(rect)}`);
          if (rect.y + rect.height > layout.y + Math.min(0, layout.height) + 1) errors.push(`Not above bar: ${label.style.text}`);
          labels.push({ text: label.style.text, x: rect.x, y: rect.y, width: rect.width, height: rect.height });
        });
      });
      for (let i = 0; i < labels.length; i++) for (let j = i + 1; j < labels.length; j++) {
        const a = labels[i], b = labels[j];
        if (Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x) > .5 && Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y) > .5) errors.push(`Overlapping labels: ${a.text}, ${b.text}`);
      }
      const canvas = element.querySelector('canvas'), pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      const colors = new Set();
      for (let i = 0; i < pixels.length; i += 64) if (pixels[i + 3]) colors.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]}`);
      return { labels: labels.map(label => label.text).sort(), expected: expected.sort(), errors, colors: colors.size };
    });
  });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, 'Page overflow');
  for (const [index, result] of results.entries()) {
    assert.deepEqual(result.errors, [], `Chart ${index}: label geometry`);
    assert.deepEqual(result.labels, result.expected, `Chart ${index}: every observed bar labeled`);
    assert.ok(result.colors > 5, `Chart ${index}: nonblank canvas`);
  }
  return results.map(result => ({ labels: result.labels.length, colors: result.colors }));
}
async function main() {
  let browser;
  fs.mkdirSync(output, { recursive: true });
  try {
    const api = `http://127.0.0.1:${await freePort()}`, web = `http://127.0.0.1:${await freePort()}`;
    const backend = start('python', ['-X', 'utf8', '-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', new URL(api).port], { cwd: root, env: { ...process.env, PARSER_ROOT: root, PARSER_USE_REALTY_MARTS: '0', PARSER_REQUIRE_REALTY_MARTS: '0' } });
    await ready(api + '/api/v1/linear/dashboard', backend);
    const frontend = start(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', new URL(web).port, '--strictPort'], { cwd: path.join(root, 'frontend'), env: { ...process.env, DASHBOARD_API_TARGET: api } });
    await ready(web, frontend);
    browser = await chromium.launch({ headless: true, channel: process.env.DASHBOARD_BROWSER_CHANNEL || 'chrome' });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' }), errors = [], checks = [];
    page.on('pageerror', error => errors.push(error.message));
    let useFixtures = true;
    await page.route('**/api/v1/economics/**', async route => {
      if (!useFixtures) return route.continue();
      const url = new URL(route.request().url()), family = url.pathname.split('/')[4], data = catalog(family);
      if (url.pathname.endsWith('/catalog')) return route.fulfill({ json: data });
      const selection = Object.fromEntries(Object.entries(data.defaults).map(([key, value]) => [key, Array.isArray(value) ? url.searchParams.getAll(key).filter(Boolean).map(item => typeof value[0] === 'number' ? Number(item) : item) : typeof value === 'boolean' ? url.searchParams.get(key) === 'true' : url.searchParams.get(key)]));
      const payload = report(family, selection);
      if (family === 'ipc' && selection.period === 'year') payload.charts[0].series[0].points[1].y = 0;
      return route.fulfill({ json: payload });
    });
    const cases = ['accounts', 'ipc', 'linear'].map(family => ({ family, fixture: true }));
    if (process.env.BAR_VALUES_LIVE === '1') cases.push({ family: 'accounts', fixture: false }, { family: 'ipc', fixture: false });
    for (const { family, fixture } of cases) {
      useFixtures = fixture;
      const query = family === 'accounts' ? `?block1_regions=${encodeURIComponent(JSON.stringify([MSK, RF]))}&block2_regions=${encodeURIComponent(JSON.stringify([MSK, RF]))}` : family === 'ipc' ? `?period=year&regions=${encodeURIComponent(JSON.stringify([MSK, RF]))}` : '';
      await page.goto(web + (family === 'linear' ? '/commissioning/linear' : `/economics/${family}`) + query);
      const selector = family === 'linear' ? '.linear-year-chart .chart-canvas' : '.economics-chart-section .chart-canvas';
      await page.locator(`${selector} canvas`).first().waitFor({ timeout: 120000 });
      for (const theme of ['light']) {
        await page.setViewportSize({ width: 1440, height: 1000 });
        for (const width of [360, 390, 768, 1280, 1440, 1920]) {
          await page.setViewportSize({ width, height: 1000 });
          checks.push({ family, fixture, theme, width, charts: await inspect(page, selector) });
          if ([390, 1440].includes(width)) await page.locator(family === 'linear' ? '[data-history="roads"]' : family === 'accounts' ? '#econ-na_block1' : '.economics-chart-section').first().screenshot({ path: path.join(output, `${fixture ? '' : 'live-'}${family}-${theme}-${width}.png`) });
        }
      }
      if (family === 'accounts' && fixture) {
        const legend = page.locator('#econ-na_block5 .legend button');
        await legend.last().click();
        await inspect(page, selector);
        await legend.first().click();
        await inspect(page, selector);
        await legend.last().click();
        await inspect(page, selector);
      }
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify(checks, null, 2));
    console.log(JSON.stringify({ passed: true, layouts: checks.length, output, errors }));
  } finally {
    await browser?.close();
    for (const child of children.reverse()) {
      child.kill();
      await new Promise(resolve => child.exitCode !== null ? resolve() : child.once('exit', resolve));
    }
  }
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
