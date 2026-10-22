// Isolated QA. An optional real source is copied read-only into a temporary root.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const repo = path.resolve(__dirname, '../..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'mortgage-browser-'));
const source = path.join(temporary, 'data/raw/realty/nashdom/mortgage_history.json');
const output = path.join(os.tmpdir(), 'mortgage-browser-checks');
const realSource = process.env.MORTGAGE_BROWSER_SOURCE;
const sourceBytes = realSource ? fs.readFileSync(path.resolve(realSource)) : null;
const sourceDocument = sourceBytes ? JSON.parse(sourceBytes.toString('utf8')) : fixture();
const outputPrefix = realSource ? 'mortgage-live' : 'mortgage-fixture';
const firstMarketPeriod = sourceDocument.records.filter(row => row.region === 'msk'
  && row.segment !== 'all' && row.volumeRub !== null).map(row => row.period).sort()[0];
const children = [];
function fixture() {
  const records = [];
  for (const region of ['msk', 'rf']) for (let year = 2011; year <= 2026; year++) {
    for (let month = 1; month <= (year === 2026 ? 5 : 12); month++) for (const segment of ['all', 'primary', 'secondary']) {
      const factor = (region === 'rf' ? 20 : 1) * (segment === 'all' ? 1 : segment === 'primary' ? .45 : .55);
      records.push({ region, period: `${year}-${String(month).padStart(2, '0')}`, segment, basis: 'month',
        volumeRub: (4e10 + (year - 2011) * 9e8 + month * 3e8) * factor,
        count: (6500 + (year - 2011) * 110 + month * 35) * factor,
        ratePct: (segment === 'primary' ? 5.5 : segment === 'secondary' ? 12.4 : 9.2) + Math.sin(month / 2 + year) * .8,
        termMonths: 240 + month, averageAmountRub: 5e6 + month * 5e4 });
    }
  }
  return { schemaVersion: 1, sourceUrl: 'https://наш.дом.рф/аналитика/ипотечное_кредитование',
    sourceDate: null, collectedAt: '2026-10-21T10:00:00Z', records,
    notes: ['Synthetic browser-test fixture; not official mortgage data.'] };
}
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
  for (const stream of [child.stdout, child.stderr]) stream.on('data', chunk => { child.messages = (child.messages + chunk).slice(-20000); });
  child.on('error', error => { child.messages += error.message; });
  children.push(child);
  return child;
}
async function ready(url, child) {
  for (let attempt = 0; attempt < 180; attempt++) {
    if (child.exitCode !== null) throw new Error(child.messages);
    try { if ((await fetch(url)).ok) return; } catch { /* Wait for our private test service. */ }
    await pause(250);
  }
  throw new Error(`Test service did not start: ${url}\n${child.messages}`);
}
async function main() {
  fs.mkdirSync(path.dirname(source), { recursive: true });
  fs.mkdirSync(output, { recursive: true });
  fs.writeFileSync(source, JSON.stringify(sourceDocument));
  const apiPort = await freePort(), webPort = await freePort();
  const api = `http://127.0.0.1:${apiPort}`, web = `http://127.0.0.1:${webPort}`;
  const backend = start(process.env.DASHBOARD_TEST_PYTHON || 'python', ['-X', 'utf8', '-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', String(apiPort)],
    { cwd: repo, env: { ...process.env, PARSER_ROOT: temporary, PARSER_DOWNLOADS: path.join(temporary, 'downloads'), PARSER_USE_REALTY_MARTS: '0', PARSER_REQUIRE_REALTY_MARTS: '0' } });
  let browser;
  const errors = [], layouts = [];
  try {
    await ready(api + '/api/v1/mortgage/catalog', backend);
    const frontend = start(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'],
      { cwd: path.join(repo, 'frontend'), env: { ...process.env, DASHBOARD_API_TARGET: api } });
    await ready(web, frontend);
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce', acceptDownloads: true });
    let echartsModuleUrl;
    page.on('response', response => { if (new URL(response.url()).pathname.endsWith('/echarts_core.js')) echartsModuleUrl = response.url(); });
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(web + '/economics/mortgage');
    await page.locator('#mortgage-page .mortgage-term-grid').waitFor();
    const terms = page.locator('.mortgage-terms');
    const termPeriods = sourceDocument.records.filter(row => row.region === 'msk' && row.segment === 'all'
      && (row.termMonths !== null || row.averageAmountRub !== null)).map(row => row.period).sort();
    const latestTermPeriod = termPeriods.at(-1);
    const format = value => value === null ? '—' : new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(value);
    async function checkTerms(period) {
      const expected = sourceDocument.records.find(row => row.region === 'msk' && row.segment === 'all' && row.period === period);
      assert.deepEqual(await terms.locator('dt').allTextContents(), ['Средний срок кредита', 'Средняя сумма кредита']);
      assert.equal(await terms.locator('dd').count(), 2);
      assert.equal(await terms.locator('h4').count(), 0);
      assert.deepEqual(await terms.locator('dd').evaluateAll(nodes => nodes.map(node => node.firstChild.textContent)),
        [format(expected.termMonths), format(expected.averageAmountRub === null ? null : expected.averageAmountRub / 1e6)]);
    }
    assert.equal(await page.getByLabel('Месяц параметров кредита', { exact: true }).inputValue(), latestTermPeriod);
    await checkTerms(latestTermPeriod);
    await page.getByLabel('Месяц параметров кредита', { exact: true }).selectOption(termPeriods[0]);
    await checkTerms(termPeriods[0]);
    await page.getByLabel('Месяц параметров кредита', { exact: true }).selectOption(latestTermPeriod);
    assert.equal(await page.locator('#mortgage-page .chart-canvas').count(), 5);
    await checkChartAnnotations();
    assert.equal(await page.getByLabel('Регион ипотечного кредитования', { exact: true }).inputValue(), 'msk');
    assert.equal(await page.getByLabel('Объём кредитов: с', { exact: true }).inputValue(), firstMarketPeriod);
    const countFrom = await page.getByLabel('Количество договоров: с', { exact: true }).inputValue();
    await page.getByLabel('Объём кредитов: с', { exact: true }).selectOption('2025-01');
    assert.equal(await page.getByLabel('Количество договоров: с', { exact: true }).inputValue(), countFrom);
    const annualLoaded = page.waitForResponse(response => response.url().includes('/api/v1/mortgage?') && response.status() === 200);
    await page.getByLabel('Накопленный период ипотеки', { exact: true }).selectOption('5');
    await annualLoaded;
    await page.locator('#mortgage-page .mortgage-term-grid').waitFor();
    assert.equal(await page.getByLabel('Объём кредитов: с', { exact: true }).inputValue(), '2025-01');
    await page.getByLabel('Объём кредитов: с', { exact: true }).selectOption(firstMarketPeriod);
    async function checkChartAnnotations() {
      assert.ok(echartsModuleUrl, 'Loaded ECharts module is available to inspect rendered chart geometry');
      const state = await page.evaluate(async url => {
        const echarts = await import(url);
        const annual = [...document.querySelectorAll('.mortgage-annual-chart .chart-canvas')].map(element => {
          const chart = echarts.getInstanceByDom(element), model = chart.getModel(), option = chart.getOption();
          const labels = model.getComponent('xAxis', 1).axis.getViewLabels();
          const totals = model.getComponent('graphic').option.elements.filter(item => item.id.startsWith('mortgage-total-'));
          const data = model.getSeriesByIndex(0).getData();
          return {
            years: labels.map(label => label.rawLabel), expectedYears: option.xAxis[1].data,
            topCount: totals.length, expectedTopCount: option.series[0].data.filter(value => value !== null).length,
            inside: option.series[0].data.map((value, index) => value === null || Boolean(data.getItemGraphicEl(index)?.getTextContent()?.style.text)),
            positions: totals.map(label => {
              const year = label.id.slice('mortgage-total-'.length), index = option.xAxis[1].data.indexOf(year);
              const height = option.series[0].data[index] + (option.series[1].data[index] ?? 0);
              return { dx: label.x - chart.convertToPixel({ xAxisIndex: 1 }, year), dy: label.y + 7 - chart.convertToPixel({ yAxisIndex: 1 }, height) };
            }),
          };
        });
        const monthly = [...document.querySelectorAll('.mortgage-monthly-section .chart-canvas, .mortgage-rates-section > .chart-block .chart-canvas')].map(element => {
          const chart = echarts.getInstanceByDom(element), axis = chart.getModel().getComponent('xAxis'), periods = chart.getOption().xAxis[0].data;
          const ticks = axis.axis.getTicksCoords({ tickModel: axis.getModel('splitLine') }).slice(1, -1);
          const expected = periods.map((period, index) => period.endsWith('-01') && index > 0 ? index : null).filter(index => index !== null);
          return { actual: ticks.map(tick => tick.tickValue), expected, positions: ticks.map(tick => {
            const start = chart.convertToPixel({ xAxisIndex: 0 }, periods[tick.tickValue]);
            const previous = chart.convertToPixel({ xAxisIndex: 0 }, periods[tick.tickValue - 1]);
            return axis.axis.toGlobalCoord(tick.coord) - (start + previous) / 2;
          }) };
        });
        return { annual, monthly };
      }, echartsModuleUrl);
      for (const chart of state.annual) {
        assert.deepEqual(chart.years, chart.expectedYears);
        assert.equal(chart.topCount, chart.expectedTopCount);
        assert.ok(chart.inside.every(Boolean), 'Every measured selected-period value is rendered');
        for (const position of chart.positions) assert.ok(Math.abs(position.dx) < 1.1 && position.dy < 1.1, JSON.stringify(position));
      }
      assert.equal(state.monthly.length, 3);
      for (const chart of state.monthly) {
        assert.deepEqual(chart.actual, chart.expected);
        assert.ok(chart.positions.every(distance => Math.abs(distance) < .01), 'Year dividers fall between December and January');
      }
    }
    for (const width of [320, 360, 768, 1280, 1920]) {
      await page.setViewportSize({ width, height: 1000 });
      await pause(350);
      const state = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth > innerWidth + 1,
        clipped: [...document.querySelectorAll('.mortgage-page h1, .mortgage-page h2, .mortgage-page select, .mortgage-term-stat dt, .mortgage-term-stat dd')].filter(el => el.scrollWidth > el.clientWidth + 1).map(el => el.textContent),
        termFontSizes: [...document.querySelectorAll('.mortgage-term-stat dd')].map(el => parseFloat(getComputedStyle(el).fontSize)),
        charts: [...document.querySelectorAll('#mortgage-page .chart-canvas')].map(el => {
          const rect = el.getBoundingClientRect(), canvas = el.querySelector('canvas'), colors = new Set();
          if (canvas?.width && canvas.height) {
            const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
            const step = Math.max(4, Math.floor(pixels.length / 20000 / 4) * 4);
            for (let i = 0; i < pixels.length; i += step) if (pixels[i + 3]) colors.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]}`);
          }
          return { colors: colors.size, outside: rect.left < -1 || rect.right > innerWidth + 1,
            scroll: el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1 };
        }),
      }));
      assert.equal(state.overflow, false, `${width}: page overflow`);
      assert.deepEqual(state.clipped, [], `${width}: clipped text`);
      assert.deepEqual(state.termFontSizes, [48, 48], `${width}: prominent overall credit parameters`);
      await checkTerms(latestTermPeriod);
      await checkChartAnnotations();
      assert.equal(state.charts.length, 5);
      for (const chart of state.charts) { assert.ok(chart.colors > 3); assert.equal(chart.outside, false); assert.equal(chart.scroll, false); }
      assert.equal(await page.locator('#mortgage-page details[open], #mortgage-page .chart-data-panel').count(), 0);
      layouts.push(width);
      if ([360, 1280, 1920].includes(width)) {
        await page.screenshot({ path: path.join(output, `${outputPrefix}-${width}.png`), fullPage: true });
        await terms.screenshot({ path: path.join(output, `${outputPrefix}-terms-${width}.png`) });
        await page.locator('.mortgage-annual-chart').first().screenshot({ path: path.join(output, `${outputPrefix}-annual-${width}.png`) });
        await page.locator('.mortgage-monthly-section').first().screenshot({ path: path.join(output, `${outputPrefix}-monthly-${width}.png`) });
      }
    }
    await page.getByLabel('Объём кредитов: с', { exact: true }).selectOption('2025-11');
    await page.getByLabel('Объём кредитов: по', { exact: true }).selectOption('2026-02');
    await checkChartAnnotations();
    await page.getByLabel('Объём кредитов: по', { exact: true }).selectOption('2025-12');
    await checkChartAnnotations();
    await page.getByLabel('Объём кредитов: с', { exact: true }).selectOption(firstMarketPeriod);
    await page.getByLabel('Объём кредитов: по', { exact: true }).selectOption(sourceDocument.records.filter(row => row.region === 'msk').map(row => row.period).sort().at(-1));
    await page.setViewportSize({ width: 1280, height: 1000 });
    const canvas = page.locator('.mortgage-rates-section .chart-canvas').first();
    await canvas.scrollIntoViewIfNeeded();
    const rect = await canvas.boundingBox();
    await page.mouse.move(rect.x + rect.width * .65, rect.y + rect.height * .5);
    await page.locator('.mortgage-tooltip:visible').waitFor();
    assert.match(await page.locator('.mortgage-tooltip:visible').innerText(), /%/);
    await page.mouse.move(0, 0);
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Скачать все ипотечные данные Excel', exact: true }).click();
    const file = await download;
    assert.equal(await file.failure(), null);
    await file.saveAs(path.join(output, `${outputPrefix}.xlsx`));
    const rfLoaded = page.waitForResponse(response => response.url().includes('/api/v1/mortgage?region=rf') && response.status() === 200);
    await page.getByLabel('Регион ипотечного кредитования', { exact: true }).selectOption('rf');
    await rfLoaded;
    await page.locator('#mortgage-page .mortgage-term-grid').waitFor();
    assert.match(await page.locator('.mortgage-source-stamp').innerText(), /Российская Федерация/);
    fs.unlinkSync(source);
    await page.reload();
    await page.locator('#mortgage-page [role="alert"]').waitFor();
    assert.equal(await page.locator('#mortgage-page .chart-canvas').count(), 0);
    fs.writeFileSync(source, JSON.stringify(sourceDocument));
    await page.getByRole('button', { name: /Повторить/ }).click();
    await page.locator('#mortgage-page .mortgage-term-grid').waitFor();
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ layouts, charts: 5, independentFilters: true, hover: true, excel: true, sourceFailureAndRecovery: true, errors, fixtureOnly: !realSource, output }));
  } finally {
    if (browser) await browser.close();
    for (const child of children) {
      if (child.exitCode !== null) continue;
      child.kill();
      await Promise.race([new Promise(resolve => child.once('exit', resolve)), pause(5000)]);
    }
    assert.equal(path.dirname(path.resolve(temporary)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(temporary).startsWith('mortgage-browser-'));
    if (sourceBytes) assert.deepEqual(fs.readFileSync(path.resolve(realSource)), sourceBytes);
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
