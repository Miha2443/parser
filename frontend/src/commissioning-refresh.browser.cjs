const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const base = process.env.DASHBOARD_TEST_URL || 'http://127.0.0.1:5174';
const output = path.join(os.tmpdir(), 'commissioning-refresh-checks');
async function main() {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const errors = [], layouts = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce', acceptDownloads: true });
    page.on('pageerror', error => errors.push(error.message));
    const ready = async route => {
      await page.goto(base + route);
      await page.locator('main h1').waitFor();
      if (route.includes('operational')) await page.locator('.commissioning-structure-main .large').waitFor({ timeout: 180000 });
      if (route.includes('annual')) await page.locator('.annual-report-section canvas').first().waitFor({ timeout: 180000 });
      if (route.includes('linear')) await page.locator('.linear-kpi').first().waitFor({ timeout: 180000 });
      await page.waitForTimeout(600);
    };
    await ready('/commissioning/operational?year=2021');
    const structureYear = page.getByLabel('Год структуры ввода', { exact: true });
    assert.deepEqual(await structureYear.locator('option').evaluateAll(options => options.map(option => option.value)), ['2026', '2025', '2024', '2023']);
    assert.equal(await structureYear.inputValue(), '2026');
    await page.waitForURL(url => url.searchParams.get('year') === '2026');
    assert.equal(await page.locator('main > section').first().locator('h2').innerText(), 'Структура ввода');
    assert.equal(await page.locator('.operational-series .chart-legend').getByRole('button', { name: 'Годовой прогноз', exact: true }).count(), 2);
    assert.equal(await page.locator('.operational-series .chart-legend').getByRole('button', { name: 'Годовые значения', exact: true }).count(), 2);
    assert.equal(await page.getByLabel('Квартал', { exact: true }).count(), 0);
    const nonres = await page.getByLabel('Период: Ввод нежилой недвижимости', { exact: true }).inputValue();
    await page.getByLabel('Период: Ввод жилья', { exact: true }).selectOption('5');
    await page.locator('.operational-series canvas').first().waitFor();
    assert.equal(await page.getByLabel('Период структуры ввода', { exact: true }).inputValue(), '12');
    assert.equal(await page.getByLabel('Период: Ввод нежилой недвижимости', { exact: true }).inputValue(), nonres);
    await page.getByLabel('Период структуры ввода', { exact: true }).selectOption('9');
    await page.locator('.commissioning-structure-main .large').waitFor();
    assert.equal(await page.getByLabel('Период: Ввод жилья', { exact: true }).inputValue(), '5');
    for (const route of ['/commissioning/operational', '/commissioning/annual', '/commissioning/linear']) {
      await ready(route);
      for (const theme of ['light']) {
        await page.setViewportSize({ width: 1440, height: 1000 });
        assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
        for (const width of [320, 360, 768, 1280, 1920]) {
          await page.setViewportSize({ width, height: 1000 });
          await page.waitForTimeout(350);
          const state = await page.evaluate(() => ({ overflow: document.documentElement.scrollWidth > innerWidth + 1,
            charts: [...document.querySelectorAll('main .chart-canvas')].map(el => {
              const r = el.getBoundingClientRect(), canvas = el.querySelector('canvas'), colors = new Set();
              if (canvas?.width && canvas.height) {
                const p = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
                const step = Math.max(4, Math.floor(p.length / 20000 / 4) * 4);
                for (let i = 0; i < p.length; i += step) if (p[i + 3]) colors.add(`${p[i]},${p[i + 1]},${p[i + 2]}`);
              }
              return { colors: colors.size, outside: r.left < -1 || r.right > innerWidth + 1,
                scroll: el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1 };
            }),
            clipped: [...document.querySelectorAll('.commissioning-structure-row > span, .commissioning-structure-amount.large, .linear-kpi h2')].filter(el => el.scrollWidth > el.clientWidth + 1).map(el => el.textContent),
            annualScroll: [...document.querySelectorAll('.annual-chart-scroll')].some(el => el.scrollWidth > el.clientWidth + 1),
          }));
          const label = `${route.split('/').at(-1)}-${theme}-${width}`;
          assert.equal(state.overflow, false, `${label}: page overflow`);
          assert.equal(state.annualScroll, false, `${label}: annual chart scrolling`);
          assert.deepEqual(state.clipped, [], `${label}: clipped values`);
          assert.ok(state.charts.length >= 2);
          for (const chart of state.charts) { assert.ok(chart.colors > 3, `${label}: blank canvas`); assert.equal(chart.outside, false); assert.equal(chart.scroll, false); }
          assert.equal(await page.locator('main details[open], main .chart-data-panel').count(), 0);
          layouts.push({ label, charts: state.charts.length });
          if ([320, 1280].includes(width)) await page.screenshot({ path: path.join(output, `${label}.png`), fullPage: true });
        }
      }
    }
    await ready('/commissioning/operational');
    await page.setViewportSize({ width: 1280, height: 1000 });
    const chart = page.locator('.operational-series .chart-canvas').first();
    await chart.scrollIntoViewIfNeeded();
    const r = await chart.boundingBox();
    let found = false;
    for (let x = .7; x < .98 && !found; x += .025) for (let y = .2; y < .9 && !found; y += .08) {
      await page.mouse.move(r.x + r.width * x, r.y + r.height * y);
      await page.waitForTimeout(25);
      const text = await page.locator('.commissioning-tooltip:visible').allTextContents();
      found = text.some(value => value.includes('Годовой прогноз') && value.includes('млн м²'));
    }
    assert.ok(found, 'Forecast segment tooltip unavailable');
    await page.mouse.move(0, 0);
    const pending = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Скачать структуру ввода Excel', exact: true }).click();
    const file = await pending;
    await file.saveAs(path.join(output, 'operational-structure.xlsx'));
    assert.equal(await file.failure(), null);
    await ready('/home');
    assert.deepEqual(await page.locator('.home-market-panel[aria-label="Ввод недвижимости"] a').allTextContents(), ['Оперативные данные', 'Годовой ввод', 'Ввод линейных объектов']);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ layouts: layouts.length, checks: layouts, errors, independentFilters: true, forecastHover: true, excel: true, output }));
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
