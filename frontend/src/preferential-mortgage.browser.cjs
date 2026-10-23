// Isolated browser checks using the normalized, unmodified official workbook slice.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');
const repo = path.resolve(__dirname, '../..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'preferential-browser-'));
const output = path.join(repo, 'outputs/preferential-mortgage-qa');
const source = path.resolve(process.env.PREFERENTIAL_BROWSER_SOURCE || path.join(repo, 'data/imports/preferential-mortgage-2026-10-11.json'));
const children = [];
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function freePort() {
  const server = net.createServer(); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port; await new Promise(resolve => server.close(resolve)); return port;
}
function start(executable, args, options) {
  const child = spawn(executable, args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], ...options });
  child.messages = '';
  for (const stream of [child.stdout, child.stderr]) stream.on('data', chunk => { child.messages = (child.messages + chunk).slice(-12000); });
  child.on('error', error => { child.messages += error.message; }); children.push(child); return child;
}
async function ready(url, child) {
  for (let i = 0; i < 180; i++) {
    if (child.exitCode !== null) throw new Error(child.messages);
    try { if ((await fetch(url)).ok) return; } catch { /* Our private service is still starting. */ }
    await pause(250);
  }
  throw new Error(`Service did not start: ${url}\n${child.messages}`);
}
async function main() {
  const destination = path.join(temporary, 'data/raw/realty/domrf/preferential_mortgage.json');
  fs.mkdirSync(path.dirname(destination), { recursive: true }); fs.copyFileSync(source, destination); fs.mkdirSync(output, { recursive: true });
  const apiPort = await freePort(), webPort = await freePort(), api = `http://127.0.0.1:${apiPort}`, web = `http://127.0.0.1:${webPort}`;
  const backend = start(process.env.DASHBOARD_TEST_PYTHON || 'python', ['-X', 'utf8', '-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', String(apiPort)], { cwd: repo, env: { ...process.env, PARSER_ROOT: temporary, PARSER_DOWNLOADS: path.join(temporary, 'downloads'), PARSER_USE_REALTY_MARTS: '0', PARSER_REQUIRE_REALTY_MARTS: '0' } });
  let browser;
  try {
    await ready(api + '/api/v1/preferential-mortgage/catalog', backend);
    const frontend = start(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], { cwd: path.join(repo, 'frontend'), env: { ...process.env, DASHBOARD_API_TARGET: api } });
    await ready(web, frontend);
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce', acceptDownloads: true });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    let echartsUrl; page.on('response', response => { if (new URL(response.url()).pathname.endsWith('/echarts_core.js')) echartsUrl = response.url(); });
    await page.goto(web + '/economics/preferential-mortgage');
    await page.locator('.pm-characteristic .chart-canvas').first().waitFor();
    assert.equal(await page.locator('.chart-canvas').count(), 10);
    assert.equal(await page.getByLabel('Регион льготной ипотеки').inputValue(), 'msk');
    assert.equal(await page.getByLabel('Накопленный период выдачи').inputValue(), '10');
    const catalog = await (await fetch(api + '/api/v1/preferential-mortgage/catalog')).json();
    const report = await (await fetch(api + '/api/v1/preferential-mortgage')).json();
    assert.equal(report.selection.characteristicMonth, 7);
    async function chartOptions() {
      return page.evaluate(async url => { const { getInstanceByDom } = await import(url); return [...document.querySelectorAll('.chart-canvas')].map(node => getInstanceByDom(node).getOption()); }, echartsUrl);
    }
    await page.waitForFunction(() => [...document.querySelectorAll('.chart-canvas')].every(node => node.querySelector('canvas')));
    let options = await chartOptions();
    assert.deepEqual(options[0].xAxis[0].data, Array.from({ length: 9 }, (_, i) => String(2018 + i)));
    assert.equal(options[0].series.length, 3);
    const totalLabels = await page.evaluate(async url => { const { getInstanceByDom } = await import(url), chart = getInstanceByDom(document.querySelector('.chart-canvas')); return chart.getZr().storage.getDisplayList().filter(item => item.type === 'tspan').map(item => item.style.text); }, echartsUrl);
    assert.ok(totalLabels.some(text => text === '899,2'), 'Annual total above 2023 Moscow bar must actually render');
    assert.equal(options[4].series[1].data.at(-1), report.characteristicsAnnual.at(-1).programs.family.averageAmountMln);
    await page.getByLabel('Накопленный период выдачи').selectOption('12');
    await page.waitForResponse(response => response.url().includes('/api/v1/preferential-mortgage?') && response.url().includes('month=12') && response.ok());
    await page.locator('.pm-characteristic .chart-canvas').first().waitFor();
    options = await chartOptions();
    assert.equal(options[2].xAxis[0].data.length, 9);
    await page.getByRole('button', { name: 'Накопленно', exact: true }).click();
    assert.equal(await page.getByLabel('Накопленный период характеристик').inputValue(), '7');
    await page.getByLabel('Накопленный период характеристик').selectOption('6');
    await page.waitForResponse(response => response.url().includes('characteristicMonth=6') && response.ok());
    await page.locator('.pm-characteristic .chart-canvas').first().waitFor();
    await page.getByLabel('Регион льготной ипотеки').selectOption('rf');
    await page.waitForResponse(response => response.url().includes('/api/v1/preferential-mortgage?region=rf') && response.ok());
    await page.locator('.pm-characteristic .chart-canvas').first().waitFor();
    assert.equal(await page.getByLabel('Накопленный период выдачи').inputValue(), '10');
    assert.equal(await page.getByLabel('Накопленный период характеристик').inputValue(), '7');
    const exportQuery = new URLSearchParams({ region: 'rf', month: '10', characteristicMonth: '7', required_version: catalog.version });
    const downloaded = await fetch(api + '/api/v1/preferential-mortgage/export?' + exportQuery);
    assert.equal(downloaded.status, 200); assert.equal(Buffer.from(await downloaded.arrayBuffer()).subarray(0, 2).toString(), 'PK');
    for (const width of [1920, 1440, 1024, 760, 390, 360]) {
      await page.setViewportSize({ width, height: 1000 }); await pause(350);
      const layout = await page.evaluate(() => ({ width: window.innerWidth, scroll: document.documentElement.scrollWidth, canvases: [...document.querySelectorAll('.chart-canvas canvas')].map(canvas => {
        const ctx = canvas.getContext('2d'), bytes = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
        let colored = 0; for (let i = 0; i < bytes.length; i += 16) if (bytes[i + 3] && Math.max(bytes[i], bytes[i + 1], bytes[i + 2]) - Math.min(bytes[i], bytes[i + 1], bytes[i + 2]) > 15) colored++;
        return { width: canvas.width, height: canvas.height, colored };
      }) }));
      assert.ok(layout.scroll <= width + 1, `Horizontal overflow at ${width}: ${layout.scroll}`);
      assert.equal(layout.canvases.length, 10); assert.ok(layout.canvases.every(canvas => canvas.width > 0 && canvas.height > 0 && canvas.colored > 50), `Blank chart at ${width}`);
      if ([1440, 390].includes(width)) await page.screenshot({ path: path.join(output, `preferential-${width}.png`), fullPage: true });
      console.log(`Layout ${width}px: ten nonblank charts, no overflow`);
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole('button', { name: 'По годам', exact: true }).click();
    await page.locator('.pm-flow').first().screenshot({ path: path.join(output, 'preferential-bar-detail.png') });
    await page.screenshot({ path: path.join(output, 'preferential-annual-1440.png'), fullPage: true });
    assert.deepEqual(errors, []); console.log('Preferential mortgage: source defaults, both regions, month controls, Excel and layouts passed.');
  } finally {
    await browser?.close();
    for (const child of children.reverse()) { child.kill(); await new Promise(resolve => child.exitCode !== null ? resolve() : child.once('exit', resolve)); }
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
