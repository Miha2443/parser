const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const metadata = { schemaVersion: 1, version: 'sales-v1', generatedAt: '2026-07-02T09:00:00Z', source: { date: '2026-06-30', files: ['data/raw/rasprodannost_20260630.xlsx'], issues: [['warning', 'Пропуски в исходном прогнозе']] } };
const periods = [{ id: '2026-04', label: 'Апрель 2026' }, { id: '2026-06', label: 'Июнь 2026' }, { id: '2026-05', label: 'Май 2026' }];
const catalog = { ...metadata, regions: [{ id: 'msk', label: 'Город Москва', periods }, { id: 'rf', label: 'Российская Федерация', periods: [periods[0], periods[2]] }] };
const definitions = [['volume', 'Объём жил. строительства', 'тыс. м²', 132456.789, 1], ['sold', 'Распроданность', '%', 0, 0], ['ready', 'Стройготовность', '%', null, 0], ['ratio', 'Отношение распроданности к стройготовности', '%', 108.125, 2]];
const sections = [['fed_okruga', 'Федеральные округа'], ['regions', 'Регионы'], ['developers', 'Девелоперы'], ['by_dev_volume', 'По объёму строительства'], ['by_population', 'По численности населения'], ['by_class', 'По классу недвижимости']];
function detail(region = 'msk', selected = '2026-06', meta = metadata) {
  const r = catalog.regions.find(r => r.id === region);
  const metrics = definitions.map(([id, label, unit, value, digits]) => ({ id, label, unit, value, digits }));
  const points = (base, years = false) => [0, 1, 2, 3].map((i) => ({ x: years ? String(2026 + i) : `2026-0${i + 1}`, y: i === 2 ? null : base + i * 2 }));
  return { ...meta, region: { id: r.id, label: r.label }, period: periods.find(p => p.id === selected), metrics, charts: [
    { id: 'forecast', title: 'Прогноз ввода по годам', kind: 'bar', unit: '', series: metrics.map((m, i) => ({ id: m.id, name: m.label, unit: m.unit, points: points([1000, 80, 50, 110][i], true) })) },
    { id: 'monthly_volume', title: 'Объём жилищного строительства, тыс. м²', kind: 'line', unit: 'тыс. м²', series: [{ id: 'volume', name: 'Объём строительства', unit: 'тыс. м²', points: points(132456) }] },
    { id: 'monthly_percent', title: 'Распроданность · Стройготовность · Отношение, %', kind: 'line', unit: '%', series: metrics.slice(1).map((m, i) => ({ id: m.id, name: m.label, unit: '%', points: points([80, 50, 110][i]) })) },
  ], tables: sections.map(([id, title]) => ({ id, title, columns: [{ id: 'name', label: 'Сегмент' }, { id: 'volume', label: 'Объём, м²' }, { id: 'sold', label: 'Распроданность' }, { id: 'ready', label: 'Стройготовность' }, { id: 'ratio', label: 'Отношение Р / С' }, { id: 'forecast', label: 'Прогноз_2028 — исходные ячейки' }], rows: Array.from({ length: 123 }, (_, i) => ({ name: i === 122 ? '=А+Б "последняя"' : `Сегмент ${i + 1}`, volume: i === 0 ? null : 123456.789, sold: i === 0 ? '12,3 %' : '40%', ready: '', ratio: i === 0 ? 0 : '—', forecast: i === 122 ? '01.07.2026' : 'не указано' })) })) };
}
module.exports = { metadata, catalog, periods, detail, sections };

async function bytes(download) { const chunks = [], stream = await download.createReadStream(); for await (const chunk of stream) chunks.push(chunk); return Buffer.concat(chunks); }
const format = (n, digits = 1) => n === null ? '—' : new Intl.NumberFormat('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);
const waitData = page => page.locator('#sales-page .chart-canvas canvas').first().waitFor({ timeout: 120000 });
async function inspect(page, label) {
  await page.waitForTimeout(450);
  const axes = await page.evaluate(async () => {
    const resource = performance.getEntriesByType('resource').find(e => /echarts_core\.js/.test(e.name)), echarts = await import(resource.name);
    return [...document.querySelectorAll('#sales-page .chart-canvas')].flatMap(el => {
      const chart = echarts.getInstanceByDom(el), labels = new Set(chart.getOption().xAxis[0].data);
      return chart.getZr().storage.getDisplayList().filter(item => item.type === 'tspan' && labels.has(item.style.text)).map(item => { const rect = item.getBoundingRect().clone(); if (item.transform) rect.applyTransform(item.transform); return { text: item.style.text, left: rect.x, right: rect.x + rect.width, width: el.clientWidth }; });
    });
  });
  for (const axis of axes) assert.ok(axis.left >= -1 && axis.right <= axis.width + 1, `${label}: cropped axis ${JSON.stringify(axis)}`);
  const state = await page.evaluate(() => ({ width: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth + 1, charts: [...document.querySelectorAll('#sales-page .chart-canvas')].map(el => {
    const rect = el.getBoundingClientRect(), canvas = el.querySelector('canvas'), colors = new Set();
    if (canvas) { const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data; for (let i = 0; i < pixels.length; i += 64) if (pixels[i + 3]) colors.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]}`); }
    return { width: rect.width, height: rect.height, colors: colors.size, outside: rect.left < -1 || rect.right > innerWidth + 1, scrolling: el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1 };
  }) }));
  assert.equal(state.overflow, false, `${label}: page overflow`);
  assert.equal(state.charts.length, 3);
  for (const c of state.charts) { assert.ok(c.colors > 5 && c.width > 100 && c.height > 100, `${label}: blank canvas`); assert.equal(c.outside || c.scrolling, false, `${label}: chart overflow ${JSON.stringify(c)}`); }
  return { label, ...state, axisLabels: axes.length };
}
async function hover(page) {
  const results = [];
  for (let chartIndex = 0; chartIndex < 3; chartIndex++) {
    await page.locator('.sales-chart-section .chart-canvas').nth(chartIndex).scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);
    const targets = await page.evaluate(async index => {
      const resource = performance.getEntriesByType('resource').find(e => /echarts_core\.js/.test(e.name));
      const echarts = await import(resource.name), el = document.querySelectorAll('.sales-chart-section .chart-canvas')[index], chart = echarts.getInstanceByDom(el), rect = el.getBoundingClientRect(), option = chart.getOption();
      return option.series.flatMap((s, seriesIndex) => {
        const data = chart.getModel().getSeriesByIndex(seriesIndex).getData();
        const candidates = s.data.map((value, i) => value !== null && Number.isFinite(value) ? i : -1).filter(i => i !== -1);
        const dataIndex = s.type === 'line' ? candidates.find(i => i > 0 && i < s.data.length - 1) ?? candidates[0] ?? -1 : candidates[0] ?? -1;
        if (dataIndex === -1) return [];
        const layout = data.getItemLayout(dataIndex);
        const point = s.type === 'bar' ? [layout.x + layout.width / 2, layout.y + layout.height / 2] : chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [dataIndex, s.data[dataIndex]]);
        return [{ x: rect.left + point[0], y: rect.top + point[1], text: s.name, value: s.data[dataIndex], type: s.type, color: s.itemStyle.color }];
      });
    }, chartIndex);
    for (const target of targets) {
      await page.mouse.move(0, 0); await page.mouse.move(target.x, target.y); await page.waitForTimeout(250);
      const tip = page.locator('.sales-tooltip:visible');
      const text = await tip.innerText();
      assert.ok(text.includes(target.text) && text.includes(format(target.value)), `Missing series hover ${JSON.stringify(target)}: ${text}`);
      const box = await tip.boundingBox(), width = page.viewportSize().width;
      assert.ok(box.x >= -1 && box.x + box.width <= width + 1, `Tooltip cropped ${JSON.stringify(box)}`);
      results.push({ chartIndex, ...target, text });
    }
  }
  await page.mouse.move(0, 0);
  return results;
}
async function main() {
  const { chromium } = require('playwright');
  const url = (process.argv[2] || 'http://127.0.0.1:5173').replace(/\/$/, '');
  const output = process.env.SALES_OUTPUT || path.join(os.tmpdir(), 'dashboard-sales-checks');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.DASHBOARD_BROWSER_CHANNEL || 'chrome' });
  const errors = [], requests = [], layouts = [], hovers = [], liveCases = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.on('pageerror', e => errors.push(e.message));
    let catalogFailure = 503, dataFailure = 0, exportFailure = 0, wrongPeriod = false, empty = false;
    let activeCatalog = structuredClone(catalog), dataMetadata = structuredClone(metadata), slowPeriod = '', slowGate, releaseSlow;
    await page.route('**/api/v1/sales**', async route => {
      const u = new URL(route.request().url()); requests.push(u.pathname + u.search);
      if (u.pathname.endsWith('/catalog')) return route.fulfill({ status: catalogFailure || 200, json: catalogFailure ? { detail: 'offline' } : activeCatalog });
      if (u.pathname.endsWith('/export')) return route.fulfill({ status: exportFailure || 200, contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', body: exportFailure ? 'error' : Buffer.from('PK\x03\x04fixture-download') });
      const region = u.searchParams.get('region'), period = u.searchParams.get('period');
      if (dataFailure) return route.fulfill({ status: dataFailure, json: { detail: 'offline' } });
      if (!activeCatalog.regions.find(r => r.id === region)?.periods.some(p => p.id === period)) return route.fulfill({ status: 404, json: { detail: 'removed filter' } });
      const value = detail(region, period, dataMetadata);
      if (wrongPeriod) value.period = periods[0];
      if (empty) { value.metrics.forEach(m => m.value = null); value.charts.forEach(c => c.series.forEach(s => s.points.forEach(p => p.y = null))); value.tables.forEach(t => t.rows = []); value.source.date = null; }
      if (period === slowPeriod) await slowGate;
      return route.fulfill({ json: value }).catch(() => {});
    });
    let fallbacks = 0;
    await page.route('**/profile-snapshot.json', route => { fallbacks++; return route.fulfill({ status: 500 }); });
    await page.goto(`${url}/sales`);
    await page.getByRole('heading', { name: 'Распроданность API недоступна', exact: true }).waitFor();
    assert.equal(await page.locator('.chart-canvas').count(), 0);
    catalogFailure = 0; await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await waitData(page);
    const regionControl = page.getByRole('combobox', { name: 'Регион', exact: true }), periodControl = page.getByRole('combobox', { name: 'Отчётный период', exact: true });
    assert.equal(await regionControl.inputValue(), 'msk'); assert.equal(await periodControl.inputValue(), '2026-06');
    assert.equal(await page.getByRole('slider', { name: 'Месяц отчёта' }).getAttribute('aria-valuetext'), 'Июнь 2026');
    assert.deepEqual(await page.locator('.sales-kpis .metric-number').allTextContents(), definitions.map(([, , unit, value, digits]) => format(value, digits) + unit));
    assert.ok((await page.locator('#sales-sources').innerText()).includes(metadata.source.issues[0][1]));
    assert.ok((await page.locator('#sales-sources').innerText()).includes('Дата файлов-кандидатов'));
    assert.equal(await page.getByRole('tab').count(), 6);
    const table = page.locator('.sales-segment-table');
    assert.equal(await table.locator('tbody tr').count(), 50);
    assert.deepEqual(await table.locator('tbody tr').first().locator('td').allTextContents(), ['—', '12,3 %', '', '0', 'не указано']);
    const title = sections[0][1];
    await page.getByRole('button', { name: `Следующая страница: ${title}`, exact: true }).click();
    assert.equal(new URL(page.url()).searchParams.get('salesfed_okrugaPage'), '2');
    await page.goBack(); assert.equal(new URL(page.url()).searchParams.has('salesfed_okrugaPage'), false);
    await page.goForward(); assert.equal(new URL(page.url()).searchParams.get('salesfed_okrugaPage'), '2');
    await page.getByRole('searchbox', { name: `Поиск: ${title}`, exact: true }).fill('А+Б');
    assert.equal(await table.locator('tbody tr').count(), 1);
    const csvPromise = page.waitForEvent('download'); await page.getByRole('button', { name: `Скачать все строки CSV: ${title}`, exact: true }).click();
    const csv = (await bytes(await csvPromise)).toString('utf8');
    assert.equal(csv.split('\r\n').length, 124); assert.ok(csv.includes('"";"12,3 %";"";"0"')); assert.ok(csv.includes('"\'=А+Б ""последняя"""')); assert.ok(csv.includes('123456.789'));
    await page.getByRole('tab', { name: sections[2][1], exact: true }).click();
    await page.getByRole('searchbox', { name: `Поиск: ${sections[2][1]}`, exact: true }).fill('Сегмент');
    await page.getByRole('combobox', { name: `Строк на странице: ${sections[2][1]}`, exact: true }).selectOption('20');
    await page.getByRole('tab', { name: sections[0][1], exact: true }).click();
    assert.equal(await page.getByRole('searchbox', { name: `Поиск: ${title}`, exact: true }).inputValue(), 'А+Б');
    await page.getByRole('tab', { name: title, exact: true }).press('End');
    assert.equal(await page.getByRole('tab', { selected: true }).innerText(), sections.at(-1)[1]);
    await page.getByRole('tab', { selected: true }).press('Home');
    assert.equal(await page.getByRole('tab', { selected: true }).innerText(), title);
    for (const [id, tableTitle] of sections) {
      await page.getByRole('tab', { name: tableTitle, exact: true }).click();
      const pending = page.waitForEvent('download'); await page.getByRole('button', { name: `Скачать все строки CSV: ${tableTitle}`, exact: true }).click();
      assert.equal((await bytes(await pending)).toString('utf8').split('\r\n').length, 124, id);
    }
    await page.getByRole('tab', { name: title, exact: true }).click();
    for (const theme of ['dark', 'light']) {
      if (theme === 'light') await page.getByTitle('Включить светлую тему', { exact: true }).click();
      for (const width of [360, 390, 430, 768, 1280, 1920]) {
        await page.setViewportSize({ width, height: 900 }); layouts.push(await inspect(page, `${theme}-${width}`));
        if ([360, 1280].includes(width)) { hovers.push(...await hover(page)); await page.screenshot({ path: path.join(output, `${theme}-${width}.png`), fullPage: true }); }
      }
    }
    for (let i = 0; i < 3; i++) {
      const section = page.locator('.sales-chart-section').nth(i); await section.getByRole('button', { name: 'Данные и скачивание', exact: true }).click();
      for (const name of ['CSV', 'PNG']) {
        const pending = page.waitForEvent('download'); await section.getByRole('button', { name, exact: true }).click(); const data = await bytes(await pending);
        if (name === 'PNG') assert.ok(data.length > 1000 && data.subarray(1, 4).equals(Buffer.from('PNG')));
        else { const rows = detail().charts[i].series.flatMap(s => s.points); assert.equal(data.toString('utf8').split('\r\n').length, rows.length + 1); assert.ok(data.toString('utf8').includes(';""')); }
      }
      await section.getByRole('button', { name: 'Данные и скачивание', exact: true }).click();
    }
    let downloadCount = 0; page.on('download', () => downloadCount++); exportFailure = 409;
    await page.getByRole('button', { name: 'Скачать распроданность Excel', exact: true }).click(); await page.locator('.sales-export [role="alert"]').waitFor();
    assert.equal(downloadCount, 0); assert.ok((await page.locator('.sales-export').innerText()).includes('Перезагрузите страницу'));
    await page.setViewportSize({ width: 360, height: 900 }); layouts.push(await inspect(page, 'export-error-360'));
    exportFailure = 0; const excel = page.waitForEvent('download'); await page.getByRole('button', { name: 'Скачать распроданность Excel', exact: true }).click(); assert.ok((await bytes(await excel)).subarray(0, 2).equals(Buffer.from('PK')));
    const exportQuery = new URL(requests.findLast(r => r.includes('/export')), url).searchParams;
    assert.deepEqual([...exportQuery.keys()], ['region', 'period', 'required_version']); assert.equal(exportQuery.get('required_version'), metadata.version);
    await regionControl.selectOption('rf'); await waitData(page); assert.equal(await periodControl.inputValue(), '2026-05');
    await periodControl.selectOption('2026-04'); await waitData(page);
    await page.reload(); await waitData(page); assert.equal(await periodControl.inputValue(), '2026-04');
    await page.getByTitle('Открыть навигацию', { exact: true }).click(); await page.getByRole('link', { name: 'Квартирография', exact: true }).click();
    await page.getByTitle('Открыть навигацию', { exact: true }).click(); await page.getByRole('link', { name: 'Распроданность', exact: true }).click(); await waitData(page);
    assert.equal(new URL(page.url()).searchParams.get('period'), '2026-04'); assert.equal(await page.getByRole('searchbox', { name: `Поиск: ${title}`, exact: true }).inputValue(), 'А+Б');
    assert.equal(await page.locator('.sidebar.is-open').count(), 0); assert.equal(await page.locator('.nav-link[aria-current="page"]').innerText(), 'Распроданность');
    await regionControl.selectOption('msk'); await waitData(page);
    slowPeriod = '2026-04'; slowGate = new Promise(resolve => releaseSlow = resolve);
    await periodControl.selectOption(slowPeriod); await page.getByRole('status').waitFor(); assert.equal(await page.locator('.chart-canvas').count(), 0);
    await periodControl.selectOption('2026-05'); await waitData(page); releaseSlow(); slowPeriod = ''; await page.waitForTimeout(400);
    assert.ok((await page.locator('.sales-heading').innerText()).includes('Май 2026'));
    activeCatalog.version = 'sales-v2'; dataMetadata = { ...metadata, version: 'sales-v2' };
    let before = requests.filter(r => r.endsWith('/catalog')).length;
    await periodControl.selectOption('2026-06'); await waitData(page); assert.equal(requests.filter(r => r.endsWith('/catalog')).length, before + 1);
    await page.locator('#sales-sources summary').click(); assert.ok((await page.locator('#sales-sources').innerText()).includes('sales-v2'));
    await periodControl.selectOption('2026-05'); await waitData(page);
    activeCatalog.version = 'sales-v3'; dataMetadata = { ...metadata, version: 'sales-v3' };
    before = requests.filter(r => r.endsWith('/catalog')).length;
    await periodControl.selectOption('2026-06'); await waitData(page); assert.equal(requests.filter(r => r.endsWith('/catalog')).length, before + 1);
    assert.ok((await page.locator('#sales-sources').innerText()).includes('sales-v3'));
    activeCatalog.regions = [activeCatalog.regions[0]]; before = requests.filter(r => r.endsWith('/catalog')).length;
    await regionControl.selectOption('rf'); await waitData(page); assert.equal(await regionControl.inputValue(), 'msk'); assert.equal(requests.filter(r => r.endsWith('/catalog')).length, before + 1);
    for (const code of [404, 503]) {
      dataFailure = code; before = requests.filter(r => r.endsWith('/catalog')).length;
      await periodControl.selectOption(code === 404 ? '2026-04' : '2026-05'); await page.getByRole('heading', { name: 'Распроданность API недоступна', exact: true }).waitFor();
      assert.equal(await page.locator('.chart-canvas').count(), 0); assert.equal(requests.filter(r => r.endsWith('/catalog')).length, before + (code === 404 ? 1 : 0));
      dataFailure = 0; await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await waitData(page);
    }
    // A failed filter may refresh again after another selection advances generation.
    dataFailure = 404; before = requests.filter(r => r.endsWith('/catalog')).length;
    await periodControl.selectOption('2026-04'); await page.getByRole('heading', { name: 'Распроданность API недоступна', exact: true }).waitFor();
    assert.equal(requests.filter(r => r.endsWith('/catalog')).length, before + 1);
    dataFailure = 0; await periodControl.selectOption('2026-05'); await waitData(page);
    activeCatalog.version = 'sales-v4'; dataMetadata = { ...metadata, version: 'sales-v4' };
    await periodControl.selectOption('2026-06'); await waitData(page);
    dataFailure = 404; before = requests.filter(r => r.endsWith('/catalog')).length;
    await periodControl.selectOption('2026-04'); await page.getByRole('heading', { name: 'Распроданность API недоступна', exact: true }).waitFor();
    assert.equal(requests.filter(r => r.endsWith('/catalog')).length, before + 1, 'New generation must refresh previously failed filter');
    dataFailure = 0; await periodControl.selectOption('2026-05'); await waitData(page);
    dataFailure = 404; before = requests.filter(r => r.endsWith('/catalog')).length;
    await periodControl.selectOption('2026-04'); await page.getByRole('heading', { name: 'Распроданность API недоступна', exact: true }).waitFor();
    assert.equal(requests.filter(r => r.endsWith('/catalog')).length, before, 'Same-generation 404 must remain bounded');
    dataFailure = 0; await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await waitData(page);
    wrongPeriod = true; await periodControl.selectOption('2026-06'); await page.getByRole('heading', { name: 'Распроданность API недоступна', exact: true }).waitFor();
    assert.equal(await page.locator('.chart-canvas').count(), 0); wrongPeriod = false; await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await waitData(page);
    dataMetadata.version = 'unpublished-v3'; before = requests.filter(r => r.endsWith('/catalog')).length;
    await periodControl.selectOption('2026-04'); await page.getByRole('heading', { name: 'Распроданность API недоступна', exact: true }).waitFor();
    assert.equal(requests.filter(r => r.endsWith('/catalog')).length, before + 1); dataMetadata.version = activeCatalog.version;
    await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await waitData(page);
    empty = true; await page.reload(); await page.getByText('Нет данных для выбранного периода.', { exact: true }).waitFor();
    assert.equal(await page.locator('.chart-canvas').count(), 0); assert.deepEqual(await page.locator('.sales-kpis .metric-number').allTextContents(), definitions.map(([, , unit]) => '—' + unit));
    assert.ok((await page.locator('.snapshot-label').innerText()).includes('Дата не указана')); assert.equal(await page.locator('.load-state[role="alert"]').count(), 0);
    activeCatalog.regions[0].periods = []; await page.reload(); await page.getByText('Нет доступных отчётных периодов.', { exact: true }).waitFor();
    assert.equal(await periodControl.isDisabled(), true); assert.equal(new URL(page.url()).searchParams.has('period'), false); assert.equal(fallbacks, 0);
    if (process.env.DASHBOARD_CHECK_LIVE === '1') {
      const live = await browser.newPage({ viewport: { width: 1280, height: 900 } }); live.on('pageerror', e => errors.push(e.message));
      const c = await live.request.get(`${url}/api/v1/sales/catalog`, { timeout: 120000 }); assert.ok(c.ok()); const liveCatalog = await c.json();
      for (const r of liveCatalog.regions) {
        for (const p of [r.periods[0], r.periods.at(-1)]) {
          const q = new URLSearchParams({ region: r.id, period: p.id });
          const response = await live.request.get(`${url}/api/v1/sales?${q}`, { timeout: 120000 }); assert.ok(response.ok()); const data = await response.json();
          await live.goto(`${url}/sales?${q}`); await waitData(live);
          assert.deepEqual(await live.locator('.sales-kpis .metric-number').allTextContents(), data.metrics.map(m => format(m.value, m.digits) + m.unit));
          for (const t of data.tables) { await live.getByRole('tab', { name: t.title, exact: true }).click(); const pending = live.waitForEvent('download'); await live.getByRole('button', { name: `Скачать все строки CSV: ${t.title}`, exact: true }).click(); const csv = (await bytes(await pending)).toString('utf8'); assert.equal(csv.split('\r\n').length, t.rows.length + 1); }
          const excel = live.waitForEvent('download'); await live.getByRole('button', { name: 'Скачать распроданность Excel', exact: true }).click(); const workbook = await bytes(await excel); assert.ok(workbook.length > 1000 && workbook.subarray(0, 2).equals(Buffer.from('PK')));
          liveCases.push({ region: r.id, period: p.id, version: data.version, tableRows: data.tables.map(t => [t.id, t.rows.length]), workbookBytes: workbook.length });
        }
        for (const theme of ['dark', 'light']) {
          if ((await live.locator('html').getAttribute('data-theme')) !== theme) { if (live.viewportSize().width <= 960) await live.getByTitle('Открыть навигацию', { exact: true }).click(); await live.getByTitle(theme === 'light' ? 'Включить светлую тему' : 'Включить тёмную тему', { exact: true }).click(); if (live.viewportSize().width <= 960) await live.getByRole('button', { name: 'Закрыть навигацию', exact: true }).first().click(); }
          for (const width of [360, 390, 430, 768, 1280, 1920]) { await live.setViewportSize({ width, height: 900 }); layouts.push(await inspect(live, `live-${r.id}-${theme}-${width}`)); if ([360, 1280].includes(width)) await live.screenshot({ path: path.join(output, `live-${r.id}-${theme}-${width}.png`), fullPage: true }); }
        }
        hovers.push(...await hover(live));
      }
      await live.close();
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ url, layouts, hovers, requests, liveCases, errors }, null, 2));
    console.log(JSON.stringify({ result: 'passed', layouts: layouts.length, hoverItems: hovers.length, liveCases, output, checks: ['full raw cells + six CSV exports', 'KPI precision + units + independent nulls', 'latest regional period + slider', 'URL history + reload + navigation + tab keyboard', 'stale response cancellation', 'bounded 404 + version reconciliation', 'persistent mismatch + schema errors + retry', 'empty data + missing dates', 'full chart CSV + PNG + pinned Excel + 409', 'both themes + canvas pixels + responsive overflow + every series hover'] }));
  } finally { await browser.close(); }
}
if (require.main === module) main().catch(e => { console.error(e); process.exitCode = 1; });
