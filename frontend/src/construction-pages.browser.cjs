const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const base = process.env.DASHBOARD_TEST_URL || 'http://127.0.0.1:5180';
const output = path.join(process.env.TEMP || '.', 'dashboard-construction-pages');
const checks = [], errors = [];
const paths = ['/construction/tree', '/construction/permits', '/construction/domrf', '/apartments', '/map'];
async function ready(page, route) {
  const pathname = route.split('?')[0];
  await page.goto(base + route);
  await page.locator('main h1').waitFor();
  if (route.includes('permits')) await page.locator('.permit-main .chart-canvas canvas').waitFor({ timeout: 120000 });
  if (pathname.includes('domrf') || pathname === '/sales' || pathname === '/construction') {
    await page.locator('.domrf-construction .chart-canvas canvas').first().waitFor({ timeout: 120000 });
    await page.locator('.domrf-forecast .chart-canvas canvas').waitFor({ timeout: 120000 });
  }
  await page.waitForTimeout(400);
}
async function layout(page, label) {
  const state = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth > innerWidth + 1,
    charts: [...document.querySelectorAll('main .chart-canvas')].map(el => {
      const rect = el.getBoundingClientRect(), canvas = el.querySelector('canvas'), colors = new Set();
      if (canvas?.width && canvas?.height) {
        const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        const step = Math.max(4, Math.floor(pixels.length / 12000 / 4) * 4);
        for (let i = 0; i < pixels.length; i += step) if (pixels[i + 3]) colors.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]}`);
      }
      return { colors: colors.size, outside: rect.left < -1 || rect.right > innerWidth + 1,
        scroll: el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1 };
    }),
  }));
  assert.equal(state.overflow, false, `${label}: page overflow`);
  for (const c of state.charts) {
    assert.ok(c.colors > 3, `${label}: blank canvas`);
    assert.equal(c.outside, false, `${label}: chart outside viewport`);
    assert.equal(c.scroll, false, `${label}: internal chart scrolling`);
  }
  assert.equal(await page.locator('main details[open], main .chart-data-panel').count(), 0, `${label}: data open by default`);
  checks.push({ label, charts: state.charts.length });
  await page.screenshot({ path: path.join(output, `${label}.png`), fullPage: true });
}
async function download(page, button, filename) {
  const pending = page.waitForEvent('download');
  await button.click();
  const item = await pending, target = path.join(output, filename || item.suggestedFilename());
  await item.saveAs(target);
  assert.equal(await item.failure(), null);
  assert.ok(fs.statSync(target).size > 30);
  return target;
}
async function hoverSeries(page, chart, names) {
  await chart.scrollIntoViewIfNeeded();
  const rect = await chart.boundingBox(), found = new Set();
  for (let x = 0.07; x < 1 && found.size < names.length; x += 0.04) {
    for (let y = 0.2; y < 0.92 && found.size < names.length; y += 0.08) {
      await page.mouse.move(rect.x + rect.width * x, rect.y + rect.height * y);
      await page.waitForTimeout(35);
      const text = await page.locator('.commissioning-tooltip:visible, .sales-tooltip:visible, .construction-pages-tooltip:visible').allTextContents();
      for (const name of names) if (text.some(t => t.includes(name))) found.add(name);
    }
  }
  for (const name of names) assert.ok(found.has(name), `Missing segment hover ${name}`);
  await page.mouse.move(0, 0);
  checks.push({ hover: [...found] });
}
async function permitSelect(page, label, value) {
  await page.getByLabel(label, { exact: true }).selectOption(String(value));
  await page.locator('.permit-main canvas').waitFor({ timeout: 120000 });
  await page.waitForFunction(() => !document.querySelector('.permits-page .cp-state'), undefined, { timeout: 120000 });
}

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ acceptDownloads: true });
    page.on('pageerror', e => errors.push(e.message));
    for (const width of [320, 360, 768, 1280, 1920]) {
      await page.setViewportSize({ width, height: 900 });
      for (const route of ['/home', '/construction/permits', '/construction/domrf']) {
        await ready(page, route);
        assert.equal(await page.locator('main [role="alert"]').count(), 0, route);
        await layout(page, `${route.split('/').at(-1)}-${width}`);
      }
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    await ready(page, '/home');
    assert.equal(await page.locator('main h1').innerText(), 'Аналитика строительной отрасли Москвы');
    assert.equal(await page.locator('.home-industry > h2').innerText(), 'Отраслевые показатели');
    assert.deepEqual(await page.locator('.home-industry [aria-label="Росстат"] a').evaluateAll(a => a.map(e => e.getAttribute('href'))), ['/economics/salary', '/economics/ipc', '/economics/accounts']);
    assert.equal(await page.locator('.home-industry [aria-label="ЦБ РФ"] [aria-disabled="true"]').count(), 2);
    assert.equal(await page.locator('.home-industry [aria-label="ЦБ РФ"] a').count(), 0);
    const group = page.locator('.nav-subgroup').filter({ has: page.getByRole('button', { name: 'Текущее строительство', exact: true }) });
    const button = group.getByRole('button', { name: 'Текущее строительство', exact: true });
    if (await button.getAttribute('aria-expanded') !== 'true') await button.click();
    assert.deepEqual(await group.locator('a').evaluateAll(a => a.map(e => e.getAttribute('href'))), paths);
    for (const alias of ['/sales', '/construction']) {
      await ready(page, alias);
      assert.equal(await page.locator('main h1').innerText(), 'Данные ДОМ.РФ');
      assert.equal(await page.locator('.sidebar a[href="/construction/domrf"]').getAttribute('aria-current'), 'page');
    }
    await ready(page, '/sales?region=msk&period=2026-01');
    assert.equal(await page.getByLabel('Период распроданности', { exact: true }).inputValue(), '2026-01');
    assert.equal(await page.getByLabel('Период прогноза и сегментов', { exact: true }).inputValue(), '2026-01');
    await page.getByLabel('Период распроданности', { exact: true }).selectOption('2026-05');
    await page.locator('.domrf-readiness canvas').waitFor();
    assert.equal(await page.getByLabel('Период прогноза и сегментов', { exact: true }).inputValue(), '2026-01');
    await page.route('**/api/v1/construction/permits?**', r => new URL(r.request().url()).searchParams.get('month') === '12' ? r.fulfill({ status: 404, json: { detail: 'fixture month unavailable' } }) : r.continue());
    await ready(page, '/construction/permits?permit_kind=housing&month=12&keep=fixture');
    assert.notEqual(await page.getByLabel('Месяц разрешений', { exact: true }).inputValue(), '12');
    assert.equal(new URL(page.url()).searchParams.get('keep'), 'fixture');
    await page.unroute('**/api/v1/construction/permits?**');
    await page.getByLabel('Месяц разрешений', { exact: true }).selectOption('1');
    await page.locator('.permit-main canvas').waitFor();
    await page.getByLabel('Вид объектов', { exact: true }).selectOption('nonresidential');
    await page.locator('.permit-main canvas').waitFor();
    assert.equal(await page.getByLabel('Месяц разрешений', { exact: true }).inputValue(), '1');
    let notFoundRequests = 0;
    await page.route('**/api/v1/construction/permits?**', r => { notFoundRequests++; return r.fulfill({ status: 404, json: { detail: 'fixture missing permits' } }); });
    await page.goto(base + '/construction/permits?permit_kind=housing&month=12');
    await page.locator('.permits-page [role="alert"]').waitFor();
    await page.waitForTimeout(1000);
    assert.ok(notFoundRequests <= 2, `Unbounded 404 recovery: ${notFoundRequests}`);
    await page.unroute('**/api/v1/construction/permits?**');
    await ready(page, '/construction/permits');
    assert.deepEqual(await page.getByLabel('Вид объектов', { exact: true }).locator('option').evaluateAll(a => a.map(e => e.value)), ['housing', 'nonresidential', 'total']);
    for (const kind of ['nonresidential', 'total', 'housing']) {
      await page.getByLabel('Вид объектов', { exact: true }).selectOption(kind);
      await page.locator('.permit-main canvas').waitFor();
      await page.waitForFunction(kind => new URLSearchParams(location.search).get('permit_kind') === kind, kind);
      await page.waitForTimeout(450);
      assert.equal(await page.locator('main [role="alert"]').count(), 0);
      checks.push({ kind, breakdowns: await page.locator('.permit-breakdowns .chart-canvas').count() });
      for (const width of [320, 1280]) {
        await page.setViewportSize({ width, height: 900 });
        await page.waitForTimeout(400);
        await layout(page, `permits-${kind}-${width}`);
      }
    }
    await permitSelect(page, 'Месяц разрешений', 5);
    const structureTo = await page.getByLabel('Структура: месяц по', { exact: true }).inputValue();
    await permitSelect(page, 'Структура: год с', 2020);
    await permitSelect(page, 'Структура: год по', 2022);
    await permitSelect(page, 'Структура: месяц с', 3);
    await permitSelect(page, 'Структура: месяц по', 5);
    assert.equal(await page.getByLabel('Месяц разрешений', { exact: true }).inputValue(), '5');
    const currentQuery = new URL(page.url()).search;
    const report = await (await page.request.get(base + '/api/v1/construction/permits' + currentQuery)).json();
    const actualYear = report.permits.presentation.rows.find(r => r.year === 2026);
    assert.equal(actualYear.basis, 'forecast');
    assert.equal(actualYear.remainder, Math.max(0, actualYear.annualForecast - actualYear.selected));
    assert.ok(report.breakdowns.every(b => b.rows.every(r => r.year >= 2020 && r.year <= 2022)));
    assert.equal(report.selection.structure.monthFrom, 3);
    assert.equal(report.selection.structure.monthTo, 5);
    await permitSelect(page, 'Месяц разрешений', 1);
    assert.equal(await page.getByLabel('Структура: месяц с', { exact: true }).inputValue(), '3');
    assert.equal(await page.getByLabel('Структура: месяц по', { exact: true }).inputValue(), '5');
    await permitSelect(page, 'Режим структуры', 'annual');
    assert.equal(await page.getByLabel('Структура: месяц с', { exact: true }).isDisabled(), true);
    await layout(page, 'permits-annual-2020-2022');
    await permitSelect(page, 'Структура: год по', 2026);
    await permitSelect(page, 'Структура: год с', 2026);
    assert.equal(await page.locator('.permit-breakdowns .chart-canvas').count(), 0, 'Partial current year cannot be an annual total');
    assert.ok((await page.locator('.permit-breakdowns').innerText()).includes('Полный год не подтверждён'));
    await ready(page, '/construction/permits?permit_kind=housing&month=5');
    assert.equal(await page.getByLabel('Структура: месяц по', { exact: true }).inputValue(), structureTo);
    await hoverSeries(page, page.locator('.permit-main .chart-canvas'), ['Годовой прогноз', 'Остаток:']);
    const first = page.locator('.permit-main .chart-block');
    await first.locator('.data-toggle').click();
    const csv = await download(page, first.getByRole('button', { name: 'CSV', exact: true }), 'permits.csv');
    const csvText = fs.readFileSync(csv, 'utf8');
    assert.ok(csvText.toLowerCase().includes('прогноз') && csvText.includes('Факт') && csvText.includes('assumed_thousand_m2'));
    await download(page, first.getByRole('button', { name: 'PNG', exact: true }), 'permits.png');
    const excel = await download(page, page.getByRole('button', { name: 'Скачать разрешения Excel', exact: true }), 'permits.xlsx');
    assert.equal(fs.readFileSync(excel).subarray(0, 2).toString(), 'PK');
    await page.route('**/api/v1/construction/permits/export?**', r => r.fulfill({ status: 409, json: { detail: 'changed' } }));
    let unexpected = false; const mark = () => { unexpected = true; }; page.on('download', mark);
    await page.getByRole('button', { name: 'Скачать разрешения Excel', exact: true }).click();
    await page.getByText('Версия источника изменилась.', { exact: false }).waitFor();
    assert.equal(unexpected, false); page.off('download', mark);
    await page.unroute('**/api/v1/construction/permits/export?**');

    await ready(page, '/construction/domrf');
    const constructionPeriod = await page.getByLabel('Период строительства ДОМ.РФ', { exact: true }).inputValue();
    const realizationPeriod = await page.getByLabel('Период реализации ДОМ.РФ', { exact: true }).inputValue();
    const forecastPeriod = await page.getByLabel('Период прогноза и сегментов', { exact: true }).inputValue();
    const readinessSelect = page.getByLabel('Период распроданности', { exact: true });
    const periods = await readinessSelect.locator('option').evaluateAll(a => a.map(e => e.value));
    if (periods.length > 1) {
      await readinessSelect.selectOption(periods[0]);
      await page.locator('.domrf-readiness .chart-canvas canvas').waitFor();
      assert.equal(await page.getByLabel('Период строительства ДОМ.РФ', { exact: true }).inputValue(), constructionPeriod);
      assert.equal(await page.getByLabel('Период реализации ДОМ.РФ', { exact: true }).inputValue(), realizationPeriod);
      assert.equal(await page.getByLabel('Период прогноза и сегментов', { exact: true }).inputValue(), forecastPeriod);
    }
    await hoverSeries(page, page.locator('.domrf-realization .cp-subsection .chart-canvas'), ['Проданная площадь', 'Непроданная площадь', 'Продажи не открыты']);
    const bExcel = await download(page, page.getByRole('button', { name: 'Скачать реализацию ДОМ.РФ Excel', exact: true }), 'domrf.xlsx');
    assert.equal(fs.readFileSync(bExcel).subarray(0, 2).toString(), 'PK');
    await page.locator('.domrf-segments summary').click();
    assert.equal(await page.locator('.domrf-segments [role="tab"]').count(), 6);
    await page.locator('.domrf-segments [role="tab"]').first().focus();
    await page.keyboard.press('End');
    assert.equal(await page.locator('.domrf-segments [role="tab"]').last().getAttribute('aria-selected'), 'true');
    await page.getByLabel('Регион показателей', { exact: true }).selectOption('rf');
    await page.locator('.domrf-realization .chart-canvas canvas').first().waitFor();
    await page.waitForFunction(() => document.querySelector('.domrf-construction h2') && document.querySelector('.domrf-construction').innerText.includes('Российская Федерация'));

    await page.route('**/api/v1/construction/domrf?**', r => r.fulfill({ status: 503, json: { detail: 'fixture DOM.RF unavailable' } }));
    await page.goto(base + '/construction/domrf');
    await page.locator('.domrf-construction [role="alert"]').waitFor();
    await page.locator('.domrf-readiness canvas').waitFor();
    await page.locator('.domrf-forecast canvas').waitFor();
    assert.equal(await page.locator('.domrf-readiness [role="alert"]').count(), 0);
    await page.unroute('**/api/v1/construction/domrf?**');
    await page.locator('.domrf-construction').getByRole('button', { name: 'Повторить', exact: true }).click();
    await page.locator('.domrf-construction canvas').first().waitFor();
    await page.route('**/api/v1/sales**', r => r.fulfill({ status: 503, json: { detail: 'fixture sales unavailable' } }));
    await page.goto(base + '/construction/domrf');
    await page.locator('.domrf-readiness [role="alert"]').waitFor();
    await page.locator('.domrf-realization canvas').first().waitFor();
    assert.equal(await page.locator('.domrf-realization [role="alert"]').count(), 0);
    await page.unroute('**/api/v1/sales**');
    const snapshot = await (await page.request.get(base + '/api/v1/construction/domrf?region=msk')).json();
    const constructionFixture = { ...snapshot.construction.history[0], period: '2026-09', label: 'Fixture September', living: 0, total: null, reportDate: '2026-09-03' };
    const salesFixture = { ...snapshot.sales.history[0], period: '2026-07', label: 'Fixture July', reportDate: '2026-08-04', metrics: { ...snapshot.sales.history[0].metrics, sold_thousand_m2: 0, sold_pct: 0, price_per_m2_rub: 0 } };
    snapshot.construction.history = [constructionFixture, ...snapshot.construction.history.filter(h => h.period !== constructionFixture.period)];
    snapshot.sales.history = [salesFixture, ...snapshot.sales.history.filter(h => h.period !== salesFixture.period)];
    await page.route('**/api/v1/construction/domrf?**', async r => {
      if (new URL(r.request().url()).searchParams.get('region') === 'rf') {
        await new Promise(resolve => setTimeout(resolve, 900));
        return r.continue();
      }
      return r.fulfill({ json: snapshot });
    });
    await ready(page, '/construction/domrf');
    await page.getByLabel('Период строительства ДОМ.РФ', { exact: true }).selectOption('2026-09');
    assert.equal(await page.locator('.domrf-construction .cp-metric strong').first().innerText(), '0,00\nмлн м²');
    assert.ok((await page.locator('.domrf-construction .cp-metric strong').nth(1).innerText()).includes('—'));
    assert.equal(await page.getByLabel('Период реализации ДОМ.РФ', { exact: true }).inputValue(), snapshot.sales.period.id);
    await page.getByLabel('Период реализации ДОМ.РФ', { exact: true }).selectOption('2026-07');
    assert.ok((await page.locator('.domrf-realization .cp-metric').filter({ hasText: 'Стоимость 1 м²' }).innerText()).includes('0'));
    assert.equal(await page.getByLabel('Период строительства ДОМ.РФ', { exact: true }).inputValue(), '2026-09');
    await page.getByLabel('Регион показателей', { exact: true }).selectOption('rf');
    assert.equal(await page.locator('.domrf-construction .cp-metrics').count(), 0, 'Old region must be hidden immediately');
    await page.getByLabel('Регион показателей', { exact: true }).selectOption('msk');
    await page.locator('.domrf-construction canvas').first().waitFor();
    await page.waitForTimeout(1100);
    assert.equal(await page.getByLabel('Регион показателей', { exact: true }).inputValue(), 'msk');
    assert.ok((await page.locator('.domrf-construction').innerText()).includes('Город Москва'));
    await page.unroute('**/api/v1/construction/domrf?**');
    checks.push({ annualForecastRemainder: true, arbitraryMonthInterval: true, independentStructure: true, incompleteAnnualHidden: true, independentProviders: true, independentPeriods: true, legacyPeriod: true, permits404: true, validMonthRetained: true, nullAndZero: true, abortedRegion: true, exports: 4, conflict409: true });
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ checks, errors }, null, 2));
    console.log(JSON.stringify({ layouts: checks.filter(c => c.label).length, checks, errors, output }));
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
