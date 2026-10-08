const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');
const fixture = require('./apartmentFixtures.cjs');
const url = (process.argv[2] || 'http://127.0.0.1:5173').replace(/\/$/, '');
const output = process.env.APARTMENTS_OUTPUT || path.join(os.tmpdir(), 'dashboard-apartments-checks');
const title = 'Объём строительства по девелоперам';
const selected = fixture.developers.at(-1).id;
const waitData = page => page.locator('.chart-canvas canvas').first().waitFor({ timeout: 120000 });
const metric = (page, label) => page.locator('.metric').filter({ has: page.locator('.metric-label', { hasText: new RegExp(`^${label}$`) }) }).locator('.metric-number');
const format = (n, digits) => n === null ? '—' : new Intl.NumberFormat('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);
async function bytes(download) {
  const chunks = [], stream = await download.createReadStream();
  for await (const chunk of stream) chunks.push(chunk);
  return Buffer.concat(chunks);
}
async function inspect(page, label) {
  await page.waitForTimeout(450);
  const state = await page.evaluate(() => ({
    width: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth + 1,
    charts: [...document.querySelectorAll('.chart-canvas')].map(el => {
      const rect = el.getBoundingClientRect(), canvas = el.querySelector('canvas'), colors = new Set();
      if (canvas) {
        const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        for (let i = 0; i < pixels.length; i += 64) if (pixels[i + 3]) colors.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]}`);
      }
      return { width: rect.width, height: rect.height, outside: rect.left < -1 || rect.right > innerWidth + 1, scrolling: el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1, colors: colors.size };
    }),
  }));
  assert.equal(state.overflow, false, `${label}: page overflow`);
  assert.ok(state.charts.length > 0, `${label}: missing chart`);
  for (const chart of state.charts) {
    assert.ok(chart.colors > 5 && chart.width > 100 && chart.height > 100, `${label}: blank chart`);
    assert.equal(chart.outside || chart.scrolling, false, `${label}: chart overflow/scrolling ${JSON.stringify(chart)}`);
  }
  return { label, ...state };
}
async function hover(page, pie = false) {
  await page.locator('.chart-canvas').first().scrollIntoViewIfNeeded();
  const points = await page.evaluate(async pie => {
    const resource = performance.getEntriesByType('resource').find(e => /echarts_core\.js/.test(e.name));
    const echarts = await import(resource.name), el = document.querySelector('.chart-canvas'), chart = echarts.getInstanceByDom(el), rect = el.getBoundingClientRect(), options = chart.getOption();
    if (pie) {
      const data = chart.getModel().getSeriesByIndex(0).getData();
      return options.series[0].data.map((r, i) => {
        const l = data.getItemLayout(i), angle = (l.startAngle + l.endAngle) / 2, radius = (l.r + l.r0) / 2;
        return { x: rect.left + l.cx + Math.cos(angle) * radius, y: rect.top + l.cy + Math.sin(angle) * radius, text: r.name, value: r.value, color: r.itemStyle.color };
      });
    }
    return options.series[0].data.flatMap((value, i) => {
      if (!(value > 0)) return [];
      const p = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [i, value / 2]);
      return [{ x: rect.left + p[0], y: rect.top + p[1], text: options.xAxis[0].data[i], value }];
    }).slice(0, 3);
  }, pie);
  assert.ok(points.length >= 2);
  for (const point of points) {
    await page.mouse.move(point.x, point.y); await page.waitForTimeout(220);
    const text = await page.locator('.apartment-tooltip:visible').innerText();
    assert.ok(text.includes(point.text) && text.includes(format(point.value, 1)), `Missing source-value hover: ${JSON.stringify(point)} ${text}`);
  }
  if (pie) assert.deepEqual(points.map(p => p.color), ['#2c9869', '#398dcc', '#cf5d64'], 'Missing amber room remapped red room color');
  await page.mouse.move(0, 0);
  return points;
}

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.DASHBOARD_BROWSER_CHANNEL || 'chrome' });
  fs.mkdirSync(output, { recursive: true });
  const errors = [], requests = [], layouts = [], hovers = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.on('pageerror', e => errors.push(e.message));
    let catalogFailure = 503, dataFailure = 0, exportFailure = 0, wrongRegion = false;
    let activeCatalog = structuredClone(fixture.catalog), dataMetadata = fixture.metadata, emptyOverview = false;
    let slowId = '', slowGate, releaseSlow;
    await page.route('**/api/v1/apartments**', async route => {
      const u = new URL(route.request().url()); requests.push(u.pathname + u.search);
      if (u.pathname.endsWith('/catalog')) return route.fulfill({ status: catalogFailure || 200, json: catalogFailure ? { detail: 'offline' } : activeCatalog });
      if (u.pathname.endsWith('/export')) return route.fulfill({ status: exportFailure || 200, contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', body: exportFailure ? 'error' : Buffer.from('PK\x03\x04fixture-download') });
      const region = u.searchParams.get('region'), id = u.searchParams.get('developer');
      if (id && id === slowId) await slowGate;
      if (dataFailure) return route.fulfill({ status: dataFailure, json: { detail: 'offline' } });
      if (!activeCatalog.regions.some(r => r.id === region) || (id && !activeCatalog.developersByRegion[region]?.some(d => d.id === id))) return route.fulfill({ status: 404, json: { detail: 'removed filter' } });
      const value = id ? fixture.detail(region, id, dataMetadata) : fixture.overview(region, dataMetadata);
      if (!id && emptyOverview) Object.assign(value, { apartments: [], distribution: [], developers: [], regions: [], developerCount: 0, regionCount: 0 });
      return route.fulfill({ json: { ...value, region: wrongRegion ? 'wrong-region' : region } }).catch(() => {});
    });
    await page.route('**/profile-snapshot.json', () => { throw new Error('Apartments attempted snapshot fallback'); });
    await page.goto(`${url}/apartments`);
    await page.getByRole('heading', { name: 'Квартирография API недоступна', exact: true }).waitFor();
    assert.equal(await page.locator('.chart-canvas').count(), 0);
    catalogFailure = 0;
    await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await waitData(page);
    assert.equal(new URL(page.url()).searchParams.get('region'), 'msk');
    assert.ok((await page.locator('.snapshot-label').innerText()).includes('02.07.2026'));
    assert.ok((await page.locator('#apartment-sources').innerText()).includes('Неполные доли комнатности источника'));
    const devTable = page.getByRole('region', { name: `Таблица: ${title}`, exact: true });
    assert.equal(await devTable.locator('tbody tr').count(), 50);
    assert.equal(await devTable.locator('tbody tr').first().locator('td').nth(1).innerText(), '—');
    assert.equal(await page.getByRole('region', { name: 'Таблица: Объём строительства по регионам', exact: true }).locator('tbody th').first().innerText(), 'Город Москва');
    await page.getByRole('button', { name: `Следующая страница: ${title}`, exact: true }).click();
    assert.equal(await devTable.locator('tbody tr').count(), 23);
    assert.equal(new URL(page.url()).searchParams.get('developersPage'), '2');
    await page.goBack(); assert.equal(await devTable.locator('tbody tr').count(), 50);
    await page.goForward(); assert.equal(await devTable.locator('tbody tr').count(), 23);
    await page.getByRole('searchbox', { name: `Поиск: ${title}`, exact: true }).fill('А+Б');
    assert.equal(await devTable.locator('tbody tr').count(), 1);
    const csvPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: `Скачать все строки CSV: ${title}`, exact: true }).click();
    const csv = (await bytes(await csvPromise)).toString('utf8');
    assert.equal(csv.split('\r\n').length, 74); assert.ok(csv.includes('30";"20";"";"10'));
    await page.getByRole('searchbox', { name: `Поиск: ${title}`, exact: true }).fill('');
    await page.getByRole('combobox', { name: `Строк на странице: ${title}`, exact: true }).selectOption('20');
    assert.equal(await devTable.locator('tbody tr').count(), 20);
    await page.getByRole('combobox', { name: `Строк на странице: ${title}`, exact: true }).selectOption('50');
    hovers.push(...await hover(page));
    await page.getByRole('button', { name: 'Данные и скачивание', exact: true }).click();
    for (const name of ['CSV', 'PNG']) {
      const pending = page.waitForEvent('download'); await page.getByRole('button', { name, exact: true }).click();
      const file = await pending, data = await bytes(file); assert.ok(data.length > 40);
      if (name === 'PNG') assert.ok(data.subarray(1, 4).equals(Buffer.from('PNG')));
      else assert.ok(data.toString('utf8').includes('100–120";""'));
    }
    await page.getByRole('button', { name: 'Данные и скачивание', exact: true }).click();
    let downloadCount = 0; page.on('download', () => { downloadCount++; });
    exportFailure = 409;
    await page.getByRole('button', { name: 'Скачать полную квартирографию Excel', exact: true }).click();
    await page.locator('.apartment-export [role="alert"]').waitFor();
    assert.equal(downloadCount, 0);
    assert.ok((await page.locator('.apartment-export').innerText()).includes('Перезагрузите страницу'));
    await page.setViewportSize({ width: 360, height: 900 });
    layouts.push(await inspect(page, 'overview-export-error-360'));
    exportFailure = 0;
    const xlsxPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Скачать полную квартирографию Excel', exact: true }).click();
    assert.ok((await bytes(await xlsxPromise)).subarray(0, 2).equals(Buffer.from('PK')));
    let exportQuery = new URL(requests.findLast(r => r.includes('/export')), url).searchParams;
    assert.deepEqual([...exportQuery.keys()], ['region', 'required_version']);
    assert.equal(exportQuery.get('required_version'), fixture.metadata.version);
    for (const width of [360, 390, 430, 768, 1280, 1920]) {
      await page.setViewportSize({ width, height: 900 }); layouts.push(await inspect(page, `overview-dark-${width}`));
      if (width === 360) hovers.push(...await hover(page));
      if ([360, 1280].includes(width)) await page.screenshot({ path: path.join(output, `overview-dark-${width}.png`), fullPage: true });
    }
    await page.setViewportSize({ width: 360, height: 900 });
    await page.getByTitle('Открыть навигацию', { exact: true }).click();
    await page.getByRole('link', { name: 'Квартирография по застройщику', exact: true }).click(); await waitData(page);
    assert.equal(await page.locator('.sidebar.is-open').count(), 0);
    assert.equal(await page.locator('.nav-link[aria-current="page"]').innerText(), 'Квартирография по застройщику');
    await page.getByRole('combobox', { name: 'Девелопер', exact: true }).selectOption(selected); await waitData(page);
    assert.equal(await page.locator('.developer-name h2').innerText(), selected);
    assert.equal(await page.locator('.selected-developer').count(), 1);
    assert.ok((await page.locator('.selected-developer').innerText()).includes('73'));
    assert.ok((await metric(page, 'Средняя площадь квартиры').innerText()).includes('58,9'));
    assert.ok((await metric(page, 'Доля рынка региона').innerText()).includes('3,25'));
    assert.ok((await metric(page, 'Площадь').innerText()).startsWith('251'));
    assert.equal(await page.locator('.apartment-rooms-grid tbody tr').nth(2).locator('td').innerText(), '—');
    assert.ok((await page.locator('.apartment-room-strip').first().getAttribute('aria-label')).includes('4+ комн: 10,0%'));
    const roomWidths = await page.locator('.apartment-rooms-grid .apartment-room-strip i').evaluateAll(els => els.map(el => el.style.width));
    assert.equal(roomWidths[0], '50%');
    for (const width of [360, 390, 430, 768, 1280, 1920]) {
      await page.setViewportSize({ width, height: 900 }); layouts.push(await inspect(page, `detail-dark-${width}`));
      if (width === 360) hovers.push(...await hover(page, true));
      if ([360, 1280].includes(width)) await page.screenshot({ path: path.join(output, `detail-dark-${width}.png`), fullPage: true });
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    hovers.push(...await hover(page, true));
    await page.getByTitle('Включить светлую тему', { exact: true }).click();
    layouts.push(await inspect(page, 'detail-light-1280'));
    await page.screenshot({ path: path.join(output, 'detail-light-1280.png'), fullPage: true });
    await page.getByRole('button', { name: 'Данные и скачивание', exact: true }).click();
    const roomCsvPromise = page.waitForEvent('download'); await page.getByRole('button', { name: 'CSV', exact: true }).click();
    assert.ok((await bytes(await roomCsvPromise)).toString('utf8').includes('3 комн";""'));
    await page.getByRole('button', { name: 'Данные и скачивание', exact: true }).click();
    const detailXlsxPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Скачать полную квартирографию Excel', exact: true }).click(); await detailXlsxPromise;
    exportQuery = new URL(requests.findLast(r => r.includes('/export')), url).searchParams;
    assert.deepEqual([...exportQuery.keys()], ['region', 'developer', 'required_version']); assert.equal(exportQuery.get('developer'), selected);
    const company = page.getByRole('combobox', { name: 'Девелопер', exact: true }), regionControl = page.getByRole('combobox', { name: 'Регион', exact: true });
    await regionControl.selectOption('rf'); await waitData(page);
    assert.equal(await company.locator('option').count(), fixture.rfDevelopers.length); assert.equal(await company.inputValue(), 'РФ Компания');
    await page.reload(); await waitData(page); assert.equal(await regionControl.inputValue(), 'rf');
    await regionControl.selectOption('msk'); await waitData(page);
    await company.selectOption(fixture.developers[1].id); await waitData(page);
    await regionControl.selectOption('rf'); await waitData(page);
    assert.equal(await company.inputValue(), fixture.developers[1].id, 'Compatible raw developer was lost on region change');
    await regionControl.selectOption('msk'); await waitData(page);
    assert.equal(await company.inputValue(), fixture.developers[1].id);
    await page.getByRole('link', { name: 'Квартирография', exact: true }).click(); await waitData(page);
    assert.equal(new URL(page.url()).searchParams.get('developer'), fixture.developers[1].id);
    await page.getByRole('searchbox', { name: `Поиск: ${title}`, exact: true }).fill('Девелопер');
    await page.getByRole('button', { name: `Следующая страница: ${title}`, exact: true }).click();
    await page.getByRole('link', { name: 'Квартирография по застройщику', exact: true }).click(); await waitData(page);
    assert.equal(await company.inputValue(), fixture.developers[1].id);
    const comparisonTitle = 'Сравнение с топ-10 девелоперов региона';
    await page.getByRole('searchbox', { name: `Поиск: ${comparisonTitle}`, exact: true }).fill('Девелопер 2');
    await page.getByRole('combobox', { name: `Строк на странице: ${comparisonTitle}`, exact: true }).selectOption('20');
    await page.getByRole('link', { name: 'Квартирография', exact: true }).click(); await waitData(page);
    assert.equal(await page.getByRole('searchbox', { name: `Поиск: ${title}`, exact: true }).inputValue(), 'Девелопер');
    assert.equal(new URL(page.url()).searchParams.get('developersPage'), '2');
    await page.getByRole('link', { name: 'Квартирография по застройщику', exact: true }).click(); await waitData(page);
    assert.equal(await page.getByRole('searchbox', { name: `Поиск: ${comparisonTitle}`, exact: true }).inputValue(), 'Девелопер 2');
    assert.equal(await page.getByRole('combobox', { name: `Строк на странице: ${comparisonTitle}`, exact: true }).inputValue(), '20');
    await page.getByRole('link', { name: 'Профиль', exact: true }).click();
    await page.getByRole('combobox', { name: 'Группа компаний', exact: true }).waitFor();
    await page.getByRole('link', { name: 'Квартирография по застройщику', exact: true }).click(); await waitData(page);
    assert.equal(await company.inputValue(), fixture.developers[1].id, 'Normalized profile ID leaked into apartment filters');
    await page.getByRole('searchbox', { name: `Поиск: ${comparisonTitle}`, exact: true }).fill('');
    slowId = selected; slowGate = new Promise(resolve => { releaseSlow = resolve; });
    await company.selectOption(selected); await page.getByRole('status').waitFor();
    assert.equal(await page.locator('.chart-canvas').count(), 0);
    await company.selectOption(fixture.developers[1].id); await waitData(page);
    releaseSlow(); slowId = ''; await page.waitForTimeout(400);
    assert.equal(await page.locator('.developer-name h2').innerText(), fixture.developers[1].id);
    // A newer generation reconciles the catalog before accepting data.
    activeCatalog = { ...activeCatalog, version: 'apartments-v2' }; dataMetadata = { ...fixture.metadata, version: 'apartments-v2' };
    const beforeVersion = requests.filter(r => r.endsWith('/catalog')).length;
    await company.selectOption(fixture.developers[2].id); await waitData(page);
    assert.equal(requests.filter(r => r.endsWith('/catalog')).length, beforeVersion + 1);
    await page.locator('#apartment-sources summary').click();
    assert.ok((await page.locator('#apartment-sources').innerText()).includes('apartments-v2'));
    // Removed region refreshes once and falls back to available Moscow.
    activeCatalog = { ...activeCatalog, regions: [activeCatalog.regions[0]] };
    const before404 = requests.filter(r => r.endsWith('/catalog')).length;
    await regionControl.selectOption('rf'); await waitData(page);
    assert.equal(await regionControl.inputValue(), 'msk');
    assert.equal(requests.filter(r => r.endsWith('/catalog')).length, before404 + 1);
    for (const code of [404, 503]) {
      dataFailure = code; const before = requests.filter(r => r.endsWith('/catalog')).length;
      await company.selectOption(code === 404 ? fixture.developers[5].id : fixture.developers[6].id);
      await page.getByRole('heading', { name: 'Квартирография API недоступна', exact: true }).waitFor();
      assert.equal(await page.locator('.chart-canvas').count(), 0);
      assert.equal(requests.filter(r => r.endsWith('/catalog')).length, before + (code === 404 ? 1 : 0));
      dataFailure = 0; await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await waitData(page);
    }
    wrongRegion = true; await company.selectOption(fixture.developers[7].id);
    await page.getByRole('heading', { name: 'Квартирография API недоступна', exact: true }).waitFor();
    assert.equal(await page.locator('.chart-canvas').count(), 0);
    wrongRegion = false; await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await waitData(page);
    await page.getByRole('link', { name: 'Квартирография', exact: true }).click(); await waitData(page);
    layouts.push(await inspect(page, 'overview-light-1280'));
    await page.screenshot({ path: path.join(output, 'overview-light-1280.png'), fullPage: true });
    emptyOverview = true; await page.reload();
    await page.getByRole('heading', { name: 'Типы квартир', exact: true }).waitFor();
    assert.equal(await page.locator('.chart-canvas').count(), 0);
    assert.equal(await page.locator('.load-state[role="alert"]').count(), 0);
    emptyOverview = false;
    activeCatalog = { ...activeCatalog, developersByRegion: { ...activeCatalog.developersByRegion, msk: [] } };
    await page.getByRole('link', { name: 'Квартирография по застройщику', exact: true }).click();
    await page.getByText('Нет девелоперов для выбранного региона.', { exact: true }).waitFor();
    assert.equal(await page.locator('.load-state[role="alert"]').count(), 0);
    assert.equal(await page.getByRole('combobox', { name: 'Девелопер', exact: true }).isDisabled(), true);
    await page.getByRole('link', { name: 'Профиль', exact: true }).click();
    assert.equal(new URL(page.url()).pathname, '/');
    assert.equal(await page.locator('.nav-link[aria-current="page"]').innerText(), 'Профиль');
    // Live smoke checks use the proxy and unmocked API, with exact summary values.
    let liveResult = null;
    if (process.env.DASHBOARD_CHECK_LIVE === '1') {
      const live = await browser.newPage({ viewport: { width: 1280, height: 900 } });
      live.on('pageerror', e => errors.push(e.message));
      const response = await live.request.get(`${url}/api/v1/apartments/catalog`); assert.ok(response.ok());
      const catalog = await response.json(), cases = [], downloads = [];
      for (const r of catalog.regions) {
        const overviewResponse = await live.request.get(`${url}/api/v1/apartments?region=${r.id}`); assert.ok(overviewResponse.ok());
        const overview = await overviewResponse.json();
        await live.goto(`${url}/apartments?region=${r.id}`); await waitData(live);
        const table = live.getByRole('region', { name: `Таблица: ${title}`, exact: true });
        assert.equal(await table.locator('tbody tr').count(), Math.min(50, overview.developers.length));
        assert.ok((await live.locator('.snapshot-label').innerText()).includes(overview.reportDate));
        const allCsv = live.waitForEvent('download'); await live.getByRole('button', { name: `Скачать все строки CSV: ${title}`, exact: true }).click();
        assert.equal((await bytes(await allCsv)).toString('utf8').split('\r\n').length, overview.developers.length + 1);
        layouts.push(await inspect(live, `live-overview-${r.id}`));
        const exportOverview = live.waitForEvent('download'); await live.getByRole('button', { name: 'Скачать полную квартирографию Excel', exact: true }).click();
        const overviewBytes = await bytes(await exportOverview); assert.ok(overviewBytes.length > 1000 && overviewBytes.subarray(0, 2).equals(Buffer.from('PK')));
        downloads.push({ region: r.id, scope: 'overview', workbookBytes: overviewBytes.length });
        const list = catalog.developersByRegion[r.id];
        for (const d of [list[0], list[Math.min(10, list.length - 1)], list.at(-1)]) {
          const query = new URLSearchParams({ region: r.id, developer: d.id });
          const detailResponse = await live.request.get(`${url}/api/v1/apartments/developer?${query}`); assert.ok(detailResponse.ok());
          const detail = await detailResponse.json();
          await live.goto(`${url}/apartments/developer?${query}`); await waitData(live);
          assert.equal(await live.getByRole('combobox', { name: 'Девелопер', exact: true }).locator('option').count(), list.length);
          for (const [label, field, digits] of [['Квартиры', 'countThousand', 1], ['Площадь', 'areaThousandM2', 0], ['Средняя площадь квартиры', 'averageAreaM2', 1], ['Доля рынка региона', 'marketSharePercent', 2]]) {
            assert.ok((await metric(live, label).innerText()).startsWith(format(detail.summary[field], digits)), `Live KPI mismatch ${d.id}: ${label}`);
          }
          assert.equal(await live.locator('.selected-developer').count(), detail.comparison.some(row => row.id === d.id) ? 1 : 0);
          layouts.push(await inspect(live, `live-detail-${r.id}-${d.id}`));
          cases.push({ region: r.id, developer: d.id, place: detail.summary.place });
        }
        const exportDetail = live.waitForEvent('download'); await live.getByRole('button', { name: 'Скачать полную квартирографию Excel', exact: true }).click();
        const detailBytes = await bytes(await exportDetail); assert.ok(detailBytes.length > 1000 && detailBytes.subarray(0, 2).equals(Buffer.from('PK')));
        downloads.push({ region: r.id, scope: 'developer', workbookBytes: detailBytes.length });
        for (const width of [360, 390, 430, 768, 1280, 1920]) { await live.setViewportSize({ width, height: 900 }); layouts.push(await inspect(live, `live-${r.id}-${width}`)); }
        await live.screenshot({ path: path.join(output, `live-detail-${r.id}.png`), fullPage: true });
      }
      liveResult = { version: catalog.version, cases, downloads }; await live.close();
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ url, layouts, hovers, requests, liveResult, errors }, null, 2));
    console.log(JSON.stringify({ result: 'passed', layouts: layouts.length, hoverItems: hovers.length, liveResult, output, checks: ['errors + retry', 'no snapshot fallback', 'complete pagination + search + CSV', 'Moscow-first API order', 'URL + back/forward + refresh', 'mobile navigation + active route', 'compatible developer region switch', 'sidebar page-local filters + isolated profile IDs', 'backend KPI values + precision', 'independent nulls + fixed room colors', 'selected outside top10', 'version reconciliation', '404 refresh bounded', 'stale response abort', 'mismatch rejection', 'partial availability empty states', 'CSV + PNG + pinned XLSX + 409', 'responsive nonblank canvas + no internal scroll'] }));
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
