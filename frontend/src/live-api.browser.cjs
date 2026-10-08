const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const url = process.argv[2] || 'http://127.0.0.1:5173';
const fixture = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../public/profile-snapshot.json'), 'utf8'));
const metadata = {
  ...fixture, version: 'a'.repeat(64), profiles: undefined,
  controls: { ...fixture.controls, frozen: false },
  provenance: { sources: Object.fromEntries(Object.entries(fixture.provenance.sources).map(([family, s]) => [family, { candidateRawFiles: s.candidateRawFiles }])) },
};
const newDeveloper = { developer: 'NEW COMPANY', developerKey: 'new company', regions: [] };
const catalog = { ...metadata, controls: { ...metadata.controls, developers: [...metadata.controls.developers, newDeveloper] } };
const output = path.resolve(__dirname, '../../outputs/dashboard-api-preview');

async function inspect(page, label, expectCharts = true) {
  const state = await page.evaluate(() => ({
    width: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth + 1,
    charts: [...document.querySelectorAll('.chart-canvas')].map(el => {
      const rect = el.getBoundingClientRect(), canvas = el.querySelector('canvas');
      const palette = new Set();
      if (canvas) {
        const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        const step = Math.max(4, Math.floor(pixels.length / 20000 / 4) * 4);
        for (let i = 0; i < pixels.length; i += step) if (pixels[i + 3]) palette.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]}`);
      }
      return { width: rect.width, height: rect.height, outside: rect.left < -1 || rect.right > innerWidth + 1, scrolling: el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1, colors: palette.size };
    }),
  }));
  assert.equal(state.overflow, false, `${label}: horizontal overflow`);
  if (expectCharts) assert.ok(state.charts.length >= 2, `${label}: charts missing`);
  for (const chart of state.charts) {
    assert.ok(chart.width >= 100 && chart.height >= 100 && chart.colors > 5, `${label}: blank/small chart`);
    assert.equal(chart.outside || chart.scrolling, false, `${label}: clipped or scrolling chart`);
  }
  return { label, ...state };
}

async function downloadBytes(download) {
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  return Buffer.concat(chunks);
}

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.DASHBOARD_BROWSER_CHANNEL || 'chrome' });
  fs.mkdirSync(output, { recursive: true });
  const errors = [], requests = [], layouts = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.on('pageerror', e => errors.push(e.message));
    let catalogFailure = true, profileFailure = 0, exportFailure = 0, mismatch = false, slowDeveloper = '';
    let activeCatalog = catalog;
    let releaseSlow;
    let slowGate;
    await page.route('**/api/v1/**', async route => {
      const u = new URL(route.request().url()); requests.push(u.pathname + u.search);
      if (u.pathname === '/api/v1/catalog') return route.fulfill({ status: catalogFailure ? 503 : 200, json: catalogFailure ? { detail: 'offline' } : activeCatalog });
      if (u.pathname.endsWith('/export')) return route.fulfill({ status: exportFailure || 200, contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', body: exportFailure ? 'offline' : Buffer.from('PK\x03\x04fixture-download') });
      const developer = u.searchParams.get('developer'), region = u.searchParams.get('region');
      if (developer === slowDeveloper) await slowGate;
      if (profileFailure) return route.fulfill({ status: profileFailure, json: { detail: 'no profile' } });
      const control = activeCatalog.controls.developers.find(d => d.developerKey === developer);
      if (!control || !(control.regions.length ? control.regions : ['msk']).includes(region)) return route.fulfill({ status: 404, json: { detail: 'removed filter' } });
      const p = fixture.profiles.find(p => p.developerKey === developer && p.region === region) || { ...fixture.profiles[0], developer: newDeveloper.developer, developerKey: developer, region };
      return route.fulfill({ json: { ...activeCatalog, profiles: [{ ...p, developerKey: mismatch ? 'wrong' : developer }] } }).catch(() => {});
    });
    await page.route('**/profile-snapshot.json', () => { throw new Error('API mode attempted static fallback'); });
    await page.goto(url);
    await page.getByRole('heading', { name: 'Данные API недоступны', exact: true }).waitFor();
    assert.equal(await page.locator('.chart-canvas').count(), 0);
    assert.ok(requests.length > 0 && requests.every(p => p === '/api/v1/catalog'));
    catalogFailure = false;
    await page.getByRole('button', { name: 'Повторить', exact: true }).click();
    await page.locator('.chart-canvas canvas').first().waitFor();
    assert.equal(await page.locator('.snapshot-label').innerText().then(t => t.includes('Данные API')), true);
    const company = page.getByRole('combobox', { name: 'Группа компаний', exact: true });
    const regionControl = page.getByRole('combobox', { name: 'Регион', exact: true });
    assert.equal(await company.locator('option').count(), catalog.controls.developers.length);
    const first = fixture.profiles[0];
    const originalFrom = await page.getByRole('combobox', { name: 'Начальный год', exact: true }).inputValue();
    const requestCount = requests.length;
    await page.getByRole('combobox', { name: 'Начальный год', exact: true }).selectOption('2022');
    await page.waitForTimeout(250);
    assert.equal(requests.length, requestCount, 'Local years refetched profile');
    await page.goBack();
    assert.equal(await page.getByRole('combobox', { name: 'Начальный год', exact: true }).inputValue(), originalFrom);
    await page.goForward();
    assert.equal(await page.getByRole('combobox', { name: 'Начальный год', exact: true }).inputValue(), '2022');

    const added = { developer: 'ADDED COMPANY', developerKey: 'added company', regions: ['msk'] };
    activeCatalog = { ...catalog, version: 'b'.repeat(64), controls: { ...catalog.controls, developers: [...catalog.controls.developers, added] }, provenance: { ...catalog.provenance, issues: [['warning', 'Fallback mart selected']] } };
    await regionControl.selectOption('rf');
    await company.locator('option[value="added company"]').waitFor({ state: 'attached' });
    await page.locator('.chart-canvas canvas').first().waitFor();
    assert.equal(await company.locator('option').count(), catalog.controls.developers.length + 1, 'New generation catalog not reconciled');
    await page.locator('#sources summary').click();
    assert.ok((await page.locator('#sources').innerText()).includes('Fallback mart selected'));
    await regionControl.selectOption('msk');
    await page.locator('.chart-canvas canvas').first().waitFor();
    // A removed region triggers one catalog refresh and selection reconciliation.
    activeCatalog = { ...activeCatalog, version: 'c'.repeat(64), controls: { ...activeCatalog.controls, developers: activeCatalog.controls.developers.map(d => d.developerKey === first.developerKey ? { ...d, regions: ['msk'] } : d) } };
    const before404 = requests.filter(p => p === '/api/v1/catalog').length;
    await regionControl.selectOption('rf');
    await page.waitForFunction(() => document.querySelector('select[aria-label="Регион"]')?.value === 'msk' && document.querySelectorAll('.chart-canvas').length > 0);
    assert.equal(requests.filter(p => p === '/api/v1/catalog').length, before404 + 1, '404 did not refresh catalog exactly once');
    assert.equal(await regionControl.locator('option').count(), 1);
    // Remove a company via profile controls and restore available RF regions.
    activeCatalog = { ...catalog, version: 'd'.repeat(64) };
    await company.selectOption(newDeveloper.developerKey);
    await page.locator('.chart-canvas canvas').first().waitFor();
    await page.waitForFunction(() => !document.querySelector('option[value="added company"]'));
    await company.selectOption(first.developerKey);
    await page.locator('.chart-canvas canvas').first().waitFor();
    await page.getByRole('combobox', { name: 'Начальный год', exact: true }).selectOption('2022');
    await regionControl.selectOption('rf');
    await page.locator('.chart-canvas canvas').first().waitFor();
    assert.equal(await page.getByRole('combobox', { name: 'Начальный год', exact: true }).inputValue(), '2022');
    await page.reload();
    await page.locator('.chart-canvas canvas').first().waitFor();
    assert.equal(await regionControl.inputValue(), 'rf');
    assert.equal(await page.getByRole('combobox', { name: 'Начальный год', exact: true }).inputValue(), '2022');

    for (const code of [404, 503]) {
      profileFailure = code;
      await regionControl.selectOption(code === 404 ? 'msk' : 'rf');
      await page.getByRole('heading', { name: 'Данные API недоступны', exact: true }).waitFor();
      assert.equal(await page.locator('.chart-canvas').count(), 0, 'Old profile shown after failure');
      assert.ok(await company.isEnabled()); assert.ok(await regionControl.isEnabled());
      profileFailure = 0;
      await page.getByRole('button', { name: 'Повторить', exact: true }).click();
      await page.locator('.chart-canvas canvas').first().waitFor();
    }
    mismatch = true;
    await regionControl.selectOption('msk');
    await page.getByRole('heading', { name: 'Данные API недоступны', exact: true }).waitFor();
    assert.equal(await page.locator('.chart-canvas').count(), 0);
    mismatch = false;
    await page.getByRole('button', { name: 'Повторить', exact: true }).click();
    await page.locator('.chart-canvas canvas').first().waitFor();

    slowDeveloper = newDeveloper.developerKey;
    slowGate = new Promise(resolve => { releaseSlow = resolve; });
    await company.selectOption(newDeveloper.developerKey);
    await page.getByRole('status').waitFor();
    assert.equal(await page.locator('.chart-canvas').count(), 0, 'Old profile shown while filters mismatch');
    assert.equal(await regionControl.inputValue(), 'msk');
    await company.selectOption(first.developerKey);
    await page.locator('.chart-canvas canvas').first().waitFor();
    releaseSlow(); slowDeveloper = '';
    await page.waitForTimeout(350);
    assert.equal(await page.locator('.developer-name h2').innerText(), first.developer);
    await company.selectOption(newDeveloper.developerKey);
    await page.locator('.chart-canvas canvas').first().waitFor();
    assert.equal(await page.locator('.developer-name h2').innerText(), newDeveloper.developer);
    assert.equal(await regionControl.locator('option').count(), 1);
    await company.selectOption(first.developerKey);
    await page.locator('.chart-canvas canvas').first().waitFor();
    await page.getByRole('combobox', { name: 'Начальный год', exact: true }).selectOption('2022');

    let downloadCount = 0;
    page.on('download', () => { downloadCount++; });
    exportFailure = 409;
    await page.getByRole('button', { name: 'Скачать полный профиль Excel', exact: true }).click();
    await page.locator('.profile-export [role="alert"]').waitFor();
    assert.ok((await page.locator('.profile-export [role="alert"]').innerText()).includes('Перезагрузите страницу'));
    await page.waitForTimeout(150);
    assert.equal(downloadCount, 0, '409 created a download');
    exportFailure = 503;
    await page.getByRole('button', { name: 'Скачать полный профиль Excel', exact: true }).click();
    await page.locator('.profile-export [role="alert"]').waitFor();
    await page.setViewportSize({ width: 360, height: 900 });
    layouts.push(await inspect(page, 'export-error-360'));
    exportFailure = 0;
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Скачать полный профиль Excel', exact: true }).click();
    const download = await downloadPromise;
    assert.ok(download.suggestedFilename().endsWith('-msk-profile.xlsx'));
    assert.ok((await downloadBytes(download)).subarray(0, 2).equals(Buffer.from('PK')));
    const exportRequest = new URL(requests.findLast(p => p.includes('/export')), url);
    assert.deepEqual([...exportRequest.searchParams.keys()], ['developer', 'region', 'required_version']);
    assert.equal(exportRequest.searchParams.get('developer'), first.developerKey);
    assert.equal(exportRequest.searchParams.get('required_version'), activeCatalog.version);

    await page.locator('#sources summary').click();
    assert.ok((await page.locator('#sources').innerText()).includes('не журнал чтений'));
    for (const width of [360, 390, 430, 768, 1280, 1920]) {
      await page.setViewportSize({ width, height: 900 }); await page.waitForTimeout(450);
      layouts.push(await inspect(page, `fixture-dark-${width}`));
      if (width === 360 || width === 1280) await page.screenshot({ path: path.join(output, `api-dark-${width}.png`), fullPage: true });
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.getByTitle('Включить светлую тему', { exact: true }).click();
    await page.waitForTimeout(450);
    layouts.push(await inspect(page, 'fixture-light-1280'));
    await page.screenshot({ path: path.join(output, 'api-light-1280.png'), fullPage: true });
    await page.locator('.chart-canvas').first().scrollIntoViewIfNeeded();
    const hoverPoints = await page.evaluate(async () => {
      const resource = performance.getEntriesByType('resource').find(e => /echarts_core\.js/.test(e.name));
      const echarts = await import(resource.name);
      const el = document.querySelector('.chart-canvas'), chart = echarts.getInstanceByDom(el), options = chart.getOption(), rect = el.getBoundingClientRect();
      const index = options.series[0].data.findIndex(v => v > 0);
      const points = []; let total = 0;
      for (const series of options.series) {
        const value = series.data[index] || 0;
        if (value > 0) {
          const point = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [options.xAxis[0].data[index], total + value / 2]);
          points.push({ x: rect.left + point[0], y: rect.top + point[1], name: series.name });
        }
        total += value;
      }
      return points;
    });
    assert.ok(hoverPoints.length >= 2);
    for (const point of hoverPoints) {
      await page.mouse.move(point.x, point.y); await page.waitForTimeout(220);
      assert.ok((await page.locator('.chart-canvas').first().innerText()).includes(point.name), `Missing hover ${point.name}`);
    }

    // Optional real backend smoke check uses the same Vite proxy, not fixtures.
    let liveResult = null;
    if (process.env.DASHBOARD_CHECK_LIVE === '1') {
      const livePage = await browser.newPage({ viewport: { width: 1280, height: 900 } });
      livePage.on('pageerror', e => errors.push(e.message));
      const catalogResponse = await livePage.request.get(`${url}/api/v1/catalog`);
      assert.ok(catalogResponse.ok());
      const liveCatalog = await catalogResponse.json();
      await livePage.goto(url);
      await livePage.locator('.chart-canvas canvas').first().waitFor({ timeout: 120000 });
      const count = await livePage.getByRole('combobox', { name: 'Группа компаний', exact: true }).locator('option').count();
      assert.equal(count, liveCatalog.controls.developers.length);
      assert.ok(count > 1000);
      for (const p of fixture.profiles) {
        await livePage.goto(`${url}/?${new URLSearchParams({ developer: p.developerKey, region: p.region, from: '2022' })}`);
        await livePage.locator('.chart-canvas canvas').first().waitFor({ timeout: 120000 });
        await livePage.waitForTimeout(450);
        assert.equal(await livePage.getByRole('combobox', { name: 'Группа компаний', exact: true }).inputValue(), p.developerKey);
        assert.equal(await livePage.getByRole('combobox', { name: 'Регион', exact: true }).inputValue(), p.region);
        layouts.push(await inspect(livePage, `live-${p.developerKey}-${p.region}`));
      }
      const liveDownloadPromise = livePage.waitForEvent('download', { timeout: 120000 });
      await livePage.getByRole('button', { name: 'Скачать полный профиль Excel', exact: true }).click();
      const liveDownload = await liveDownloadPromise, bytes = await downloadBytes(liveDownload);
      assert.ok(bytes.subarray(0, 2).equals(Buffer.from('PK')) && bytes.length > 1000, 'Live Excel is not a ZIP workbook');
      liveResult = { developers: count, profiles: fixture.profiles.length, filename: liveDownload.suggestedFilename(), workbookBytes: bytes.length, version: liveCatalog.version };
      await livePage.screenshot({ path: path.join(output, 'live-profile.png'), fullPage: true });
      await livePage.close();
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ url, layouts, hoverPoints, requests, liveResult, errors }, null, 2));
    console.log(JSON.stringify({ result: 'passed', layouts: layouts.length, hoverSegments: hoverPoints.length, liveResult, checks: ['catalog/profile errors + retry', 'catalog new version', 'removed region 404 recovery', 'provenance warnings', 'no static fallback', 'stale profile abort', 'mismatch rejection', 'URL + back/forward', 'local years', 'Excel version + 409 no download + retry', 'overflow + canvas', 'light/dark'] }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
