const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { annualCatalog, annualForecastReport } = require('./annualConstruction.browser.cjs');

async function main() {
  const { chromium } = require('playwright');
  const url = (process.argv[2] || 'http://127.0.0.1:5173').replace(/\/$/, '');
  const output = process.env.ANNUAL_FORECAST_OUTPUT || path.join(os.tmpdir(), 'annual-forecast-checks');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.DASHBOARD_BROWSER_CHANNEL || 'chrome' });
  const errors = [], results = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.on('pageerror', error => errors.push(error.message));
    let emptyForecast = false;
    await page.route('**/api/v1/commissioning/annual**', route => {
      const u = new URL(route.request().url());
      if (u.pathname.endsWith('/catalog')) return route.fulfill({ json: annualCatalog });
      const payload = annualForecastReport({ region: u.searchParams.get('region') || 'msk' });
      if (emptyForecast) payload.charts.forEach(chart => delete chart.forecast);
      return route.fulfill({ json: payload });
    });
    // The application currently pins its theme to light in App.tsx.
    for (const theme of ['light']) {
      await page.addInitScript(theme => localStorage.setItem('dashboard.theme', theme), theme);
      for (const width of [360, 390, 768, 1280, 1920]) {
        await page.setViewportSize({ width, height: 900 });
        await page.goto(url + '/commissioning/annual?region=msk');
        await page.locator('#annual-b1 canvas').waitFor();
        await page.waitForTimeout(700);
        assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
        const subtitle = await page.locator('.annual-subtitle').innerText();
        assert.match(subtitle, /Москва.*млн м².*янв–сен 2026.*21\.10\.2026/);
        assert.equal(await page.locator('.snapshot-label, .annual-method-notes').count(), 0);
        assert.equal(await page.locator('#annual-sources details').getAttribute('open'), null);
        assert.equal(await page.getByText(/Основание единиц:/).isVisible(), false);
        const state = await page.evaluate(async () => {
          const resource = performance.getEntriesByType('resource').find(entry => /echarts_core\.js/.test(entry.name));
          const ec = await import(resource.name);
          return { overflow: document.documentElement.scrollWidth > innerWidth + 1,
            charts: [...document.querySelectorAll('.annual-chart-scroll')].map(scroll => {
              const el = scroll.querySelector('.chart-canvas'), chart = ec.getInstanceByDom(el), option = chart.getOption();
              const canvas = el.querySelector('canvas'), pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
              const colors = new Set();
              for (let index = 0; index < pixels.length; index += 64) if (pixels[index + 3]) colors.add(`${pixels[index]},${pixels[index + 1]},${pixels[index + 2]}`);
              const forecast = option.series.at(-1), factIndex = option.xAxis[0].data.indexOf('2026\nянв–сен');
              const fact = chart.getModel().getSeriesByIndex(0).getData().getItemLayout(factIndex);
              const prediction = chart.getModel().getSeriesByIndex(option.series.length - 1).getData().getItemLayout(factIndex + 1);
              const captions = chart.getZr().storage.getDisplayList().filter(item => item.type === 'tspan').map(item => item.style.text);
              const captionBounds = chart.getZr().storage.getDisplayList().filter(item => item.type === 'tspan' && ['янв–сен', 'прогноз'].includes(item.style.text)).map(item => {
                const bounds = item.getBoundingRect().clone();
                if (item.transform) bounds.applyTransform(item.transform);
                return { left: bounds.x, right: bounds.x + bounds.width };
              }).sort((a, b) => a.left - b.left);
              const rect = scroll.getBoundingClientRect();
              return { colors: colors.size, outside: rect.left < -1 || rect.right > innerWidth + 1,
                categories: option.xAxis[0].data, forecastName: forecast.name,
                dashed: forecast.itemStyle.borderType, hatched: !!forecast.itemStyle.decal,
                separate: prediction.x > fact.x + fact.width, factCaptionVisible: captions.some(text => text.includes('янв–сен')),
                captionsSpaced: captionBounds.every((bounds, index) => index === 0 || bounds.left - captionBounds[index - 1].right >= 2), factData: option.series[0].data,
                forecastData: forecast.data, scrolled: scroll.scrollLeft > 0, clipped: scroll.scrollWidth > scroll.clientWidth };
            }) };
        });
        assert.equal(state.overflow, false);
        assert.equal(state.charts.length, 7);
        for (const chart of state.charts) {
          assert.ok(chart.colors > 5); assert.equal(chart.outside, false);
          assert.equal(chart.forecastName, 'Прогноз'); assert.equal(chart.dashed, 'dashed'); assert.equal(chart.hatched, true);
          assert.equal(chart.separate, true);
          assert.equal(chart.factCaptionVisible, true);
          if (width >= 768) assert.equal(chart.captionsSpaced, true);
          assert.deepEqual(chart.categories.slice(-4), ['2026\nянв–сен', '2026\nпрогноз', '2027\nпрогноз', '2030\nпрогноз']);
          assert.deepEqual(chart.forecastData.slice(-3), [14, 0, 17]);
          assert.ok(chart.factData.slice(-3).every(value => value === null));
          assert.equal(chart.clipped, false);
          assert.equal(chart.scrolled, false);
        }
        await page.screenshot({ path: path.join(output, `${theme}-viewport-${width}.png`) });
        const section = page.locator('#annual-b1');
        await section.screenshot({ path: path.join(output, `${theme}-${width}.png`) });
        await section.getByRole('button', { name: 'Прогноз', exact: true }).click();
        assert.equal(await section.getByRole('button', { name: 'Прогноз', exact: true }).getAttribute('aria-pressed'), 'false');
        await section.getByRole('button', { name: 'Прогноз', exact: true }).click();
        await section.getByRole('button', { name: 'Данные и скачивание', exact: true }).click();
        assert.match(await section.locator('.chart-data-panel').innerText(), /2026 · прогноз/);
        const pending = page.waitForEvent('download');
        await section.getByRole('button', { name: 'CSV', exact: true }).click();
        const download = await pending, chunks = [];
        for await (const chunk of await download.createReadStream()) chunks.push(chunk);
        const csv = Buffer.concat(chunks).toString('utf8');
        assert.match(csv, /2026 · факт · янв–сен/); assert.match(csv, /2027 · прогноз/);
        await page.locator('#annual-sources summary').click();
        assert.equal(await page.getByText(/Основание единиц: assumed_thousand_m2/).isVisible(), true);
        results.push({ theme, width, charts: state.charts.length, csvBytes: Buffer.byteLength(csv) });
      }
    }
    emptyForecast = true;
    await page.reload(); await page.locator('#annual-b1 canvas').waitFor();
    assert.equal(await page.getByRole('button', { name: 'Прогноз', exact: true }).count(), 0);
    assert.doesNotMatch(await page.locator('.annual-subtitle').innerText(), /прогноз от/);
    emptyForecast = false;
    await page.getByRole('combobox', { name: 'Регион', exact: true }).selectOption('rf');
    await page.waitForFunction(() => document.querySelectorAll('.annual-report-section').length === 2);
    assert.equal(await page.getByRole('button', { name: 'Прогноз', exact: true }).count(), 0);
    assert.equal(await page.getByText(/Основание единиц:/).count(), 0);
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ results, errors }, null, 2));
    console.log(JSON.stringify({ result: 'passed', cases: results.length, output }));
  } finally {
    await browser.close();
  }
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
