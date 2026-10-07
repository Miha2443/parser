const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const url = process.argv[2] || 'http://127.0.0.1:5173';
const output = path.resolve('outputs/dashboard-preview');
const widths = [360, 390, 430, 768, 1280, 1920];

async function inspect(page, label) {
  const result = await page.evaluate(() => {
    const overflowing = document.documentElement.scrollWidth > window.innerWidth + 1;
    const charts = [...document.querySelectorAll('.chart-canvas')].map(element => {
      const rect = element.getBoundingClientRect();
      const canvas = element.querySelector('canvas');
      let colors = 0;
      if (canvas && canvas.width && canvas.height) {
        const context = canvas.getContext('2d');
        if (context) {
          const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
          const palette = new Set();
          const step = Math.max(4, Math.floor(pixels.length / 20000 / 4) * 4);
          for (let i = 0; i < pixels.length; i += step) {
            if (pixels[i + 3]) palette.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]}`);
          }
          colors = palette.size;
        }
      }
      return {
        label: element.getAttribute('aria-label'),
        width: rect.width,
        height: rect.height,
        outside: rect.left < -1 || rect.right > window.innerWidth + 1,
        scrolling: element.scrollHeight > element.clientHeight + 1 || element.scrollWidth > element.clientWidth + 1,
        colors,
      };
    });
    return { width: window.innerWidth, overflowing, charts };
  });
  assert.equal(result.overflowing, false, `${label}: page horizontal overflow`);
  assert.ok(result.charts.length >= 2, `${label}: missing profile charts`);
  for (const chart of result.charts) {
    assert.ok(chart.width >= 100 && chart.height >= 100, `${label}: chart dimensions ${chart.label}`);
    assert.equal(chart.outside, false, `${label}: chart outside viewport ${chart.label}`);
    assert.equal(chart.scrolling, false, `${label}: chart scroll ${chart.label}`);
    assert.ok(chart.colors > 5, `${label}: blank chart ${chart.label}`);
  }
  return result;
}

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.DASHBOARD_BROWSER_CHANNEL || 'chrome' });
  const errors = [];
  const reports = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 });
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: 'Профиль застройщика', exact: true }).waitFor();
    await page.locator('.chart-canvas canvas').first().waitFor();
    for (const width of widths) {
      await page.setViewportSize({ width, height: 900 });
      await page.waitForTimeout(550);
      reports.push(await inspect(page, `dark-${width}`));
      await page.screenshot({ path: path.join(output, `dark-${width}.png`), fullPage: true });
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.getByTitle('Включить светлую тему', { exact: true }).click();
    await page.waitForTimeout(550);
    reports.push(await inspect(page, 'light-1280'));
    await page.screenshot({ path: path.join(output, 'light-1280.png'), fullPage: true });
    await page.reload({ waitUntil: 'networkidle' });
    assert.ok(await page.getByTitle('Включить тёмную тему', { exact: true }).isVisible(), 'Theme was not persisted');
    const market = page.getByRole('button', { name: 'Рынок недвижимости', exact: true });
    await market.click();
    assert.equal(await market.getAttribute('aria-expanded'), 'false');
    await page.reload({ waitUntil: 'networkidle' });
    assert.equal(await market.getAttribute('aria-expanded'), 'false', 'Navigation expansion was not persisted');
    await market.click();
    const csvToggle = page.getByRole('button', { name: 'Данные и скачивание', exact: true }).first();
    await csvToggle.click();
    const [csv] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: 'CSV', exact: true }).first().click(),
    ]);
    assert.ok(csv.suggestedFilename().endsWith('.csv'));
    await csv.saveAs(path.join(output, csv.suggestedFilename()));
    const [png] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: 'PNG', exact: true }).first().click(),
    ]);
    assert.ok(png.suggestedFilename().endsWith('.png'));
    await png.saveAs(path.join(output, png.suggestedFilename()));
    const seriesButton = page.getByTitle('Скрыть: Жилая площадь', { exact: true }).first();
    await seriesButton.click();
    assert.equal(await page.getByTitle('Показать: Жилая площадь', { exact: true }).first().getAttribute('aria-pressed'), 'false');
    await page.getByTitle('Показать: Жилая площадь', { exact: true }).first().click();
    await page.locator('.chart-canvas').first().scrollIntoViewIfNeeded();
    await page.waitForTimeout(450);
    const hoverPoints = await page.evaluate(async () => {
      const resource = performance.getEntriesByType('resource').find(entry => /echarts_core\.js/.test(entry.name));
      if (!resource) throw new Error('ECharts resource not found');
      const echarts = await import(resource.name);
      const element = document.querySelector('.chart-canvas');
      const chart = echarts.getInstanceByDom(element);
      const option = chart.getOption();
      const rect = element.getBoundingClientRect();
      const index = option.series[0].data.findIndex(value => typeof value === 'number' && value > 0);
      let cumulative = 0;
      const points = [];
      for (const series of option.series) {
        const value = series.data[index] || 0;
        if (value > 0) {
          const point = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [option.xAxis[0].data[index], cumulative + value / 2]);
          points.push({ x: rect.left + point[0], y: rect.top + point[1], name: series.name });
        }
        cumulative += value;
      }
      return points;
    });
    assert.ok(hoverPoints.length >= 2, 'Need multiple stacked segments for hover check');
    for (const point of hoverPoints) {
      await page.mouse.move(point.x, point.y);
      await page.waitForTimeout(200);
      assert.ok((await page.locator('.chart-canvas').first().innerText()).includes(point.name), `Missing segment tooltip: ${point.name}`);
    }
    const snapshot = JSON.parse(fs.readFileSync(path.resolve('frontend/public/profile-snapshot.json'), 'utf8'));
    const profileChecks = [];
    for (const profile of snapshot.profiles) {
      const query = new URLSearchParams({ developer: profile.developerKey, region: profile.region });
      await page.goto(`${url}/?${query}`, { waitUntil: 'networkidle' });
      await page.waitForTimeout(400);
      assert.equal(await page.getByRole('combobox', { name: 'Группа компаний', exact: true }).inputValue(), profile.developerKey);
      assert.equal(await page.getByRole('combobox', { name: 'Регион', exact: true }).inputValue(), profile.region);
      profileChecks.push(await inspect(page, profile.id));
    }
    await page.getByRole('combobox', { name: 'Группа компаний', exact: true }).selectOption(snapshot.profiles[0].developerKey);
    await page.getByRole('combobox', { name: 'Регион', exact: true }).selectOption('rf');
    await page.getByRole('combobox', { name: 'Начальный год', exact: true }).selectOption('2022');
    await page.reload({ waitUntil: 'networkidle' });
    assert.equal(await page.getByRole('combobox', { name: 'Регион', exact: true }).inputValue(), 'rf');
    assert.equal(await page.getByRole('combobox', { name: 'Начальный год', exact: true }).inputValue(), '2022');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByTitle('Открыть навигацию', { exact: true }).click();
    assert.ok(await page.locator('.sidebar.is-open').isVisible());
    await page.getByTitle('Закрыть навигацию', { exact: true }).click();
    assert.equal(await page.locator('.sidebar.is-open').count(), 0);
    const failed = await browser.newPage();
    await failed.route('**/profile-snapshot.json', route => route.abort());
    await failed.goto(url);
    await failed.getByRole('heading', { name: 'Нет доступного снимка', exact: true }).waitFor();
    await failed.unroute('**/profile-snapshot.json');
    await failed.getByRole('button', { name: 'Повторить', exact: true }).click();
    await failed.locator('.chart-canvas canvas').first().waitFor();
    await failed.close();
    assert.equal(errors.length, 0, `Browser errors: ${errors.join('; ')}`);
    fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ url, reports, profileChecks, hoverPoints, errors, downloads: [csv.suggestedFilename(), png.suggestedFilename()] }, null, 2));
    console.log(`Dashboard browser checks passed: ${reports.length} layouts, ${profileChecks.length} profiles, segment hover, filters, theme, navigation, CSV/PNG and failure recovery.`);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
