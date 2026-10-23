const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');
const economics = require('./economics.browser.cjs');
const sales = require('./sales.browser.cjs');

const base = process.env.DASHBOARD_TEST_URL || 'http://127.0.0.1:5181';
const output = path.join(os.tmpdir(), 'dashboard-release-schedule');
let ipcMonth = 8;

function economicPeriods(family) {
  if (family === 'accounts') return [economics.MSK, economics.RF].flatMap((region, i) =>
    ['vrp_total', 'gdp_total', 'vrp_per_capita', 'gdp_pc_total', 'vrp_index', 'gdp_index', 'vrp_per_capita_index', 'gdp_pc_index', 'vds_value', 'vds_structure', 'vds_index']
      .map(metric => ({ region, metric, year: 2024 + i, month: null })));
  return [economics.MSK, economics.RF].map(region => ({ region, metric: family, year: 2026, month: family === 'ipc' ? ipcMonth : 8 }));
}

function domrf(region) {
  const metrics = { total_living_thousand_m2: 15000, sales_open_thousand_m2: 13000,
    sold_thousand_m2: 7000, sold_pct: 47, unsold_thousand_m2: 6000, unsold_pct: 40,
    sales_not_open_thousand_m2: 2000, sales_not_open_pct: 13, price_per_m2_rub: 440000, funds_million_rub: 3080000 };
  const history = ['2026-08', '2026-09'].map(period => ({ period, label: period, reportDate: '2026-10-11', sourceFile: 'fixture.json', metrics }));
  return { schemaVersion: 1, version: 'release-domrf-test', generatedAt: '2026-10-23T09:00:00Z',
    source: { date: '2026-10-23', files: ['fixture.json'], issues: [] }, region: { id: region, label: region },
    construction: { period: { id: '2026-10', label: 'Октябрь 2026', reportDate: '2026-10-23' },
      metrics: { living: 15000, total: 27000 }, history: [{ period: '2026-10', label: 'Октябрь 2026', reportDate: '2026-10-23', living: 15000, total: 27000, sourceFile: 'fixture.json' }] },
    sales: { period: { id: '2026-09', label: 'Сентябрь 2026', reportDate: '2026-10-11' }, metrics, history }, notes: [] };
}

const mortgageMeta = { schemaVersion: 1, version: 'release-mortgage-test', generatedAt: '2026-10-23T09:00:00Z',
  source: { url: 'https://наш.дом.рф/аналитика/ипотечное_кредитование', date: '2026-09-30', collectedAt: '2026-10-23T09:00:00Z', files: ['fixture.json'] } };
const mortgageRegions = [{ id: 'msk', label: 'Москва', periods: ['2026-07', '2026-08'] }, { id: 'rf', label: 'Российская Федерация', periods: ['2026-07'] }];

async function routes(page) {
  await page.route('**/api/v1/economics/**', async route => {
    const url = new URL(route.request().url()), family = url.pathname.split('/')[4];
    const catalog = economics.catalog(family), selection = structuredClone(catalog.defaults);
    for (const [key, raw] of url.searchParams) {
      if (!(key in selection)) continue;
      if (Array.isArray(selection[key])) {
        const values = raw.startsWith('[') ? JSON.parse(raw) : url.searchParams.getAll(key);
        selection[key] = ['months', 'quarters'].includes(key) ? values.map(Number) : values;
      } else selection[key] = typeof selection[key] === 'boolean' ? raw === 'true' : raw;
    }
    const data = url.pathname.endsWith('/catalog') ? catalog : economics.report(family, selection);
    data.releasePeriods = economicPeriods(family);
    await route.fulfill({ json: data });
  });
  await page.route('**/api/v1/construction/domrf?*', route => route.fulfill({ json: domrf(new URL(route.request().url()).searchParams.get('region') || 'msk') }));
  await page.route('**/api/v1/sales**', route => {
    const url = new URL(route.request().url());
    return route.fulfill({ json: url.pathname.endsWith('/catalog') ? sales.catalog : sales.detail(url.searchParams.get('region') || 'msk', url.searchParams.get('period') || '2026-06') });
  });
  await page.route('**/api/v1/mortgage**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/catalog')) return route.fulfill({ json: { ...mortgageMeta, regions: mortgageRegions, defaultRegion: 'msk', months: Array.from({ length: 12 }, (_, i) => ({ id: i + 1, label: String(i + 1) })) } });
    const region = url.searchParams.get('region') || 'msk', month = Number(url.searchParams.get('month')) || 8;
    const values = { volumeRub: 1e9, count: 1000, ratePct: 10, termMonths: 240, averageAmountRub: 1e6 };
    return route.fulfill({ json: { ...mortgageMeta, selection: { region, month }, regionLabel: region, annual: [],
      monthly: mortgageRegions.find(r => r.id === region).periods.map(period => ({ period, all: values, primary: values, secondary: values })), notes: [] } });
  });
}

async function main() {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const errors = [], checks = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    page.on('pageerror', error => errors.push(error.message));
    await routes(page);
    await page.goto(base + '/economics/ipc');
    await page.locator('.next-release time').waitFor();
    assert.equal(await page.locator('.next-release time').first().getAttribute('datetime'), '2026-10-15');
    ipcMonth = 9;
    await page.getByRole('button', { name: 'Обновить показатели', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.next-release time')?.getAttribute('datetime') === '2026-11-15');
    await page.goto(base + '/economics/ipc?period=year');
    await page.locator('.next-release time').waitFor();
    assert.equal(await page.locator('.next-release time').first().getAttribute('datetime'), '2026-11-15');
    await page.goto(base + '/economics/accounts');
    await page.locator('#econ-na_block1 .next-release time').waitFor();
    assert.equal(await page.locator('#econ-na_block1 .next-release time').first().getAttribute('datetime'), '2027-04-10');
    await page.goto(base + '/economics/mortgage?annualMonth=1');
    await page.locator('.next-release time').waitFor();
    assert.equal(await page.locator('.next-release time').getAttribute('datetime'), '2026-10-30');
    await page.goto(base + '/construction/domrf?period=2026-04&realization_period=2026-08');
    await page.locator('.domrf-realization .next-release time').waitFor();
    await page.locator('.domrf-readiness .next-release time').waitFor();
    assert.equal(await page.locator('.domrf-realization .next-release time').getAttribute('datetime'), '2026-11-11');
    assert.equal(await page.locator('.domrf-readiness .next-release time').getAttribute('datetime'), '2026-08-11');

    for (const route of ['/economics/salary', '/economics/ipc', '/economics/accounts', '/economics/mortgage', '/construction/domrf']) {
      await page.goto(base + route);
      await page.locator('.next-release time').first().waitFor();
      if (route.endsWith('salary')) assert.equal(await page.locator('.next-release time').first().getAttribute('datetime'), '2026-12-03');
      for (const width of [1440, 390, 320]) {
        await page.setViewportSize({ width, height: 1000 });
        await page.waitForTimeout(300);
        const layout = await page.evaluate(() => ({ overflow: document.documentElement.scrollWidth > innerWidth + 1,
          dates: [...document.querySelectorAll('.next-release')].map(el => {
            const rect = el.getBoundingClientRect();
            return { text: el.textContent, clipped: el.scrollWidth > el.clientWidth + 1, outside: rect.left < 0 || rect.right > innerWidth + 1, font: parseFloat(getComputedStyle(el).fontSize) };
          }),
          canvases: [...document.querySelectorAll('main canvas')].map(el => {
            const data = el.getContext('2d').getImageData(0, 0, el.width, el.height).data, colors = new Set();
            for (let i = 0; i < data.length; i += 400) if (data[i + 3]) colors.add(`${data[i]},${data[i + 1]},${data[i + 2]}`);
            return colors.size;
          }),
        }));
        assert.equal(layout.overflow, false, route);
        assert.ok(layout.dates.length);
        layout.dates.forEach(date => { assert.equal(date.clipped, false); assert.equal(date.outside, false); assert.ok(date.font >= 13); });
        assert.ok(layout.canvases.length); layout.canvases.forEach(colors => assert.ok(colors > 3));
        if (width === 1440) await page.screenshot({ path: path.join(output, `${route.split('/').at(-1)}-${width}.png`) });
        else await page.locator('.next-release').first().screenshot({ path: path.join(output, `${route.split('/').at(-1)}-${width}.png`) });
        checks.push({ route, width, dates: layout.dates.length, canvases: layout.canvases.length });
      }
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ checks, errors, updatedPeriodAdvancesDate: true, historicFiltersKeepDate: true, output }));
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
