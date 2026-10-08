const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { metadata } = require('./commissioning.browser.cjs');
const annualCatalog = { ...metadata, regions: [{ id: 'msk', label: 'Москва' }, { id: 'rf', label: 'РФ' }] };
const constructionCatalog = { ...annualCatalog, permitKinds: [{ id: 'total', label: 'Всего' }, { id: 'housing', label: 'Жильё' }, { id: 'nonresidential', label: 'Нежильё' }], monthsByKind: { total: [1, 2, 3], housing: [1, 3], nonresidential: [2] } };
function annualReport(selection = { region: 'msk' }, meta = metadata) {
  const names = ['Ввод недвижимости', 'МКД и ИЖС', 'Бюджетное и небюджетное жильё · Москва', 'Реновация · Москва', 'Нежильё и нежилые помещения в жилье · Москва', 'Бюджетное и небюджетное нежильё · Москва', 'Нежильё по отраслям · Москва'], ids = ['b1', 'b2_1', 'b2_2', 'b2_3', 'b3_1', 'b3_2', 'b3_3'];
  const charts = names.slice(0, selection.region === 'msk' ? 7 : 2).map((title, index) => {
    const series = (index === 3 ? ['Реновация'] : index === 6 ? ['Офисы', 'Социальные объекты', 'Промышленные', 'Гостиницы', 'Торговля', 'Транспорт', 'Прочее / без детализации'] : index === 0 ? ['Жильё', 'МОП', 'Нежилье в жилье', 'Нежильё'] : ['МКД', 'ИЖС']).map((name, j) => ({ id: `s${j}`, name, color: ['#2c9869', '#d6a23f', '#329d9c', '#7b8794', '#a63876', '#398dcc', '#6743a7'][j], points: [] }));
    const from = index === 3 ? 2017 : 2011;
    const rows = Array.from({ length: 2027 - from }, (_, i) => ({ year: from + i, ...Object.fromEntries(series.map((s, j) => [s.id, selection.region === 'rf' && from + i === 2026 || j === 1 && from + i < 2015 ? 0 : 1.12345 + j / 2 + i / 10])) }));
    series.forEach(s => s.points = rows.map(r => ({ x: String(r.year), y: r[s.id] })));
    return { id: ids[index], title, unit: 'млн м²', totals: index !== 3, columns: [{ id: 'year', label: 'Год' }, ...series.map(s => ({ id: s.id, label: s.name }))], rows, series, summaries: [2025, 2026].map(to => ({ from, to, values: series.map((s, j) => ({ id: s.id, label: s.name, value: 99.1234 + j })) })) };
  });
  return { ...structuredClone(meta), region: structuredClone(annualCatalog.regions.find(r => r.id === selection.region)), charts, notes: ['Заполнение пропусков нулём сохранено по прежнему правилу.', 'ИЖС до 2015 года не показывается.', ...(selection.region === 'rf' ? ['2026 год по РФ — нет данных; нулевой столбец не означает нулевой фактический ввод.'] : ['Исторические МОП и реновация сохранены.'])] };
}
function constructionReport(selection = { region: 'msk', permitKind: 'total', month: 3 }, meta = metadata) {
  selection = { ...selection }; meta = structuredClone(meta);
  const rows = Array.from({ length: 16 }, (_, i) => ({ 'Год': 2011 + i, 'За год, тыс. м²': i === 15 ? null : 1234.567 + i * 40, 'С начала года, тыс. м²': i < 2 ? null : 234.567 + i * 20 + selection.month, 'Изменение, %': i <= 2 ? null : i === 3 ? 0 : -12.345 }));
  return { ...meta, selection, constructionPeriod: 'Октябрь 2026', salesReadinessPeriod: { year: 2026, month: 5 }, metrics: [['living', 'Жилая площадь', 15.64321, 'млн м²', 2], ['total', 'Общая площадь', null, 'млн м²', 2], ['sold', 'Распроданность', 47.234, '%', 0], ['ready', 'Стройготовность', 0, '%', 0], ['ratio', 'Отношение распроданности к стройготовности', null, '%', 0]].map(([id, label, value, unit, digits]) => ({ id, label, value, unit, digits })), sales: { region_key: selection.region, report_period: 'Август 2026', total_living_thousand_m2: 15743.123, sales_open_thousand_m2: 14140.123, sold_pct: 47.234, sold_thousand_m2: 7366.123, unsold_pct: 0, unsold_thousand_m2: 0, sales_not_open_pct: null, sales_not_open_thousand_m2: null, price_per_m2_rub: 441306.78, funds_million_rub: 3250869.12 }, permits: { region: 'Москва', unit: 'тыс. м²', periodLabel: `Январь–месяц ${selection.month}`, columns: Object.keys(rows[0]).map(id => ({ id, label: id })), rows, chart: { x: rows.map(r => String(r['Год'])), period: rows.map(r => ({ value: r['С начала года, тыс. м²'] ?? 0 })), remainder: rows.map(r => ({ value: r['За год, тыс. м²'] === null ? 0 : r['За год, тыс. м²'] - (r['С начала года, тыс. м²'] ?? 0) })), totals: rows.map(r => ({ value: r['За год, тыс. м²'] })), growth: rows.map(r => ({ value: r['Изменение, %'] })) } }, sourceDetails: { file: 'selected/construction.json', date: null }, notes: ['Разрешения на строительство всегда относятся к Москве, независимо от выбранного региона.', 'Жилая площадь может использовать последний KPI при отсутствии оперативного значения.'] };
}
module.exports = { annualCatalog, constructionCatalog, annualReport, constructionReport };
const fmt = (n, digits = 0) => n === null ? '—' : new Intl.NumberFormat('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);
async function bytes(d) { const stream = await d.createReadStream(), chunks = []; for await (const c of stream) chunks.push(c); return Buffer.concat(chunks); }
const roots = { annual: '/api/v1/commissioning/annual', construction: '/api/v1/construction' }, pages = { annual: '/commissioning/annual', construction: '/construction' }, captions = { annual: 'Годовой ввод', construction: 'Текущее строительство' };
const waitData = page => page.locator('.commissioning-page .chart-canvas canvas').first().waitFor({ timeout: 120000 });
async function setTheme(page, value) { if (await page.locator('html').getAttribute('data-theme') === value) return; const mobile = page.viewportSize().width <= 960; if (mobile) await page.getByTitle('Открыть навигацию', { exact: true }).click(); await page.getByTitle(value === 'light' ? 'Включить светлую тему' : 'Включить тёмную тему', { exact: true }).click(); if (mobile) await page.locator('.sidebar .mobile-close').click(); }
async function inspect(page, label, count) {
  await page.waitForTimeout(450);
  const state = await page.evaluate(async () => {
    const resource = performance.getEntriesByType('resource').find(e => /echarts_core\.js/.test(e.name)), ec = await import(resource.name);
    return { width: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth + 1, charts: [...document.querySelectorAll('.commissioning-page .chart-canvas')].map(el => {
      const rect = el.getBoundingClientRect(), canvas = el.querySelector('canvas'), colors = new Set(), chart = ec.getInstanceByDom(el), o = chart.getOption(), labels = new Set((o.xAxis?.[0]?.data ?? []).map(String));
      if (canvas) { const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data; for (let i = 0; i < data.length; i += 64) if (data[i + 3]) colors.add(`${data[i]},${data[i + 1]},${data[i + 2]}`); }
      const cropped = chart.getZr().storage.getDisplayList().filter(v => v.type === 'tspan' && labels.has(v.style.text)).flatMap(v => { const r = v.getBoundingRect().clone(); if (v.transform) r.applyTransform(v.transform); return r.x < -1 || r.x + r.width > el.clientWidth + 1 ? [v.style.text] : []; });
      return { colors: colors.size, cropped, width: rect.width, height: rect.height, outside: rect.left < -1 || rect.right > innerWidth + 1, scroll: el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1 };
    }) };
  });
  assert.equal(state.overflow, false, `${label} overflow`); assert.equal(state.charts.length, count, `${label} chart count`); for (const c of state.charts) { assert.ok(c.colors > 5 && c.width > 100 && c.height > 100, `${label} blank canvas`); assert.equal(c.outside || c.scroll, false, `${label} chart scroll`); assert.deepEqual(c.cropped, [], `${label} cropped labels`); } return { label, ...state };
}
async function hover(page) {
  const result = [], canvases = page.locator('.commissioning-page .chart-canvas');
  for (let index = 0; index < await canvases.count(); index++) {
    await canvases.nth(index).scrollIntoViewIfNeeded(); await page.waitForTimeout(400);
    const points = await page.evaluate(async index => {
      const resource = performance.getEntriesByType('resource').find(e => /echarts_core\.js/.test(e.name)), ec = await import(resource.name), el = document.querySelectorAll('.commissioning-page .chart-canvas')[index], chart = ec.getInstanceByDom(el), rect = el.getBoundingClientRect(), o = chart.getOption(), annual = !!document.querySelector('#annual-page');
      return o.series.flatMap((s, seriesIndex) => {
        const data = s.data, i = data.reduce((best, v, index) => (typeof v === 'number' ? v : v?.value) > (best < 0 ? 0 : typeof data[best] === 'number' ? data[best] : data[best]?.value) ? index : best, -1); if (i < 0) return [];
        const l = chart.getModel().getSeriesByIndex(seriesIndex).getData().getItemLayout(i), v = data[i];
        if (s.type === 'pie') { const a = (l.startAngle + l.endAngle) / 2, r = (l.r + l.r0) / 2; return [{ x: rect.left + l.cx + Math.cos(a) * r, y: rect.top + l.cy + Math.sin(a) * r, text: v.name, value: v.value, digits: 0, index }]; }
        return [{ x: rect.left + l.x + l.width / 2, y: rect.top + l.y + l.height / 2, text: s.name, value: v, digits: annual ? 1 : 0, index }];
      });
    }, index);
    for (const p of points) { assert.ok(p.x > 0 && p.x < page.viewportSize().width && p.y > 0 && p.y < page.viewportSize().height, `Hover outside viewport ${JSON.stringify(p)}`); await page.mouse.move(0, 0); await page.mouse.move(p.x, p.y); await page.waitForTimeout(250); const tip = canvases.nth(index).locator('.commissioning-tooltip:visible'); try { await tip.waitFor({ timeout: 5000 }); } catch (e) { await page.screenshot({ path: path.join(os.tmpdir(), 'annual-construction-hover-failure.png') }); throw new Error(`Hover unavailable ${JSON.stringify(p)}: ${e.message}`); } const text = await tip.innerText(); assert.ok(text.includes(p.text) && text.includes(fmt(p.value, p.digits)), `Hover ${JSON.stringify(p)}: ${text}`); const b = await tip.boundingBox(); assert.ok(b.x >= -1 && b.x + b.width <= page.viewportSize().width + 1, 'Confined tooltip'); result.push({ ...p, tooltip: text }); }
  }
  await page.mouse.move(0, 0); return result;
}
async function downloadCharts(page, records) {
  const charts = page.locator('.commissioning-page .chart-block');
  for (let index = 0; index < await charts.count(); index++) { const chart = charts.nth(index); await chart.getByRole('button', { name: 'Данные и скачивание', exact: true }).click(); for (const type of ['CSV', 'PNG']) { const pending = page.waitForEvent('download'); await chart.getByRole('button', { name: type, exact: true }).click(); const d = await pending, data = await bytes(d); assert.ok(data.length > 20); if (type === 'PNG') assert.ok(data.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))); records.push({ filename: d.suggestedFilename(), bytes: data.length }); } await chart.getByRole('button', { name: 'Данные и скачивание', exact: true }).click(); }
}
async function main() {
  const { chromium } = require('playwright'), url = (process.argv[2] || 'http://127.0.0.1:5173').replace(/\/$/, ''), output = process.env.ANNUAL_CONSTRUCTION_OUTPUT || path.join(os.tmpdir(), 'dashboard-annual-construction-checks'); fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.DASHBOARD_BROWSER_CHANNEL || 'chrome' }), layouts = [], hovers = [], downloads = [], requests = [], errors = [], liveCases = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } }); page.on('pageerror', e => errors.push(e.message)); await page.addInitScript(() => localStorage.setItem('dashboard.navigation', JSON.stringify({ market: true, commissioning: true, construction: true, service: true })));
    const fresh = kind => ({ catalog: structuredClone(kind === 'annual' ? annualCatalog : constructionCatalog), meta: structuredClone(metadata), catalogFailure: 503, failure: 0, exportFailure: 0, wrong: false, empty: false, slow: '', gate: null });
    const states = { annual: fresh('annual'), construction: fresh('construction') };
    await page.route('**/api/v1/**', async route => {
      const u = new URL(route.request().url()), kind = u.pathname.startsWith(roots.annual) ? 'annual' : u.pathname.startsWith(roots.construction) ? 'construction' : null; if (!kind) return route.continue(); const s = states[kind]; requests.push({ kind, path: u.pathname + u.search });
      if (u.pathname.endsWith('/catalog')) return route.fulfill(s.catalogFailure ? { status: s.catalogFailure, json: { detail: 'Catalog unavailable fixture' } } : { json: s.catalog });
      if (u.pathname.endsWith('/export')) return route.fulfill(s.exportFailure ? { status: s.exportFailure, json: { detail: 'generation changed' } } : { body: Buffer.from('PKfixture-workbook'), contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
      const region = u.searchParams.get('region'), selection = kind === 'annual' ? { region } : { region, permitKind: u.searchParams.get('permit_kind'), month: Number(u.searchParams.get('month')) };
      if (s.slow && (kind === 'annual' ? region : String(selection.month)) === s.slow) { await new Promise(resolve => s.gate = resolve); s.gate = null; }
      if (s.failure) return route.fulfill({ status: s.failure, json: { detail: 'Report unavailable fixture' } });
      const value = kind === 'annual' ? annualReport(selection, s.meta) : constructionReport(selection, s.meta);
      if (s.wrong) value.schemaVersion = 2;
      if (s.empty) { if (kind === 'annual') value.charts = []; else { value.sales = null; value.metrics = []; value.permits.rows = []; value.permits.columns = []; value.permits.chart = null; } }
      return route.fulfill({ json: value });
    });
    for (const kind of ['annual', 'construction']) {
      const s = states[kind]; await page.goto(url + pages[kind]); await page.getByRole('heading', { name: `${captions[kind]} API недоступен`, exact: true }).waitFor(); s.catalogFailure = 0; await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await waitData(page);
      assert.equal(new URL(page.url()).searchParams.get('region'), 'msk'); assert.ok((await page.locator('.snapshot-label').innerText()).includes('Дата файлов-кандидатов'));
      if (kind === 'annual') { assert.equal(await page.locator('.annual-report-section').count(), 7); assert.ok((await page.locator('.annual-summaries').first().innerText()).includes(fmt(99.1234, 1))); }
      else { assert.equal(new URL(page.url()).searchParams.get('month'), '3'); assert.ok((await page.locator('.construction-area-metrics').first().innerText()).includes('15,64')); assert.ok((await page.locator('.construction-area-metrics').first().innerText()).includes('—')); assert.ok((await page.locator('.construction-sales-metrics').innerText()).includes('0')); assert.ok((await page.locator('.construction-donuts').innerText()).includes('Доля в источнике не указана.')); }
      for (const theme of ['light', 'dark']) for (const width of [360, 390, 430, 768, 1280, 1920]) { await page.setViewportSize({ width, height: 900 }); await setTheme(page, theme); layouts.push(await inspect(page, `fixture-${kind}-${theme}-${width}`, kind === 'annual' ? 7 : 3)); if (width === 360 || width === 1280) { hovers.push(...await hover(page)); await page.screenshot({ path: path.join(output, `fixture-${kind}-${theme}-${width}.png`), fullPage: true }); } }
      await page.setViewportSize({ width: 1280, height: 900 }); await downloadCharts(page, downloads);
      const table = page.locator('.commissioning-table').last(), search = table.getByRole('searchbox'); await search.fill('not-a-row'); assert.equal(await table.locator('tbody tr').count(), 0); const csvPending = page.waitForEvent('download'); await table.getByRole('button', { name: /Скачать все строки CSV/ }).click(); const csv = (await bytes(await csvPending)).toString('utf8'); assert.ok(csv.includes('2011') && csv.includes(kind === 'annual' ? '1.12345' : '1234.567')); await search.fill('');
      const excelCaption = kind === 'annual' ? 'Скачать годовой ввод Excel' : 'Скачать текущее строительство Excel', beforeDownloads = downloads.length; s.exportFailure = 409; let count = 0; const record = () => count++; page.on('download', record); await page.getByRole('button', { name: excelCaption, exact: true }).click(); await page.getByRole('alert').filter({ hasText: 'Данные изменились' }).waitFor(); assert.equal(count, 0); page.off('download', record); s.exportFailure = 0; const pending = page.waitForEvent('download'); await page.getByRole('button', { name: excelCaption, exact: true }).click(); assert.ok((await bytes(await pending)).subarray(0, 2).equals(Buffer.from('PK'))); assert.equal(new URL(requests.findLast(r => r.kind === kind && r.path.includes('/export')).path, url).searchParams.get('required_version'), s.meta.version); assert.equal(downloads.length, beforeDownloads);
      const control = page.getByRole('combobox', { name: kind === 'annual' ? 'Регион' : 'Период с начала года', exact: true }), a = kind === 'annual' ? 'msk' : '1', b = kind === 'annual' ? 'rf' : '2', catalogs = () => requests.filter(r => r.kind === kind && r.path.endsWith('/catalog')).length;
      for (const version of ['v2', 'v3']) { await control.selectOption(a); await waitData(page); s.catalog.version = s.meta.version = version; const before = catalogs(); await control.selectOption(b); await waitData(page); assert.equal(catalogs(), before + 1, 'Sequential version recovery'); }
      s.failure = 404; let before = catalogs(); await control.selectOption(a); await page.getByRole('heading', { name: `${captions[kind]} API недоступен`, exact: true }).waitFor(); assert.equal(catalogs(), before + 1); s.failure = 0; await control.selectOption(b); await waitData(page); s.catalog.version = s.meta.version = 'v4'; await page.reload(); await waitData(page); s.failure = 404; before = catalogs(); await control.selectOption(a); await page.getByRole('heading', { name: `${captions[kind]} API недоступен`, exact: true }).waitFor(); assert.equal(catalogs(), before + 1); s.failure = 0; await control.selectOption(b); await waitData(page); s.failure = 404; before = catalogs(); await control.selectOption(a); await page.getByRole('heading', { name: `${captions[kind]} API недоступен`, exact: true }).waitFor(); assert.equal(catalogs(), before); s.failure = 0; await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await waitData(page);
      s.slow = b; await control.selectOption(b); await page.waitForTimeout(100); await control.selectOption(a); await waitData(page); if (s.gate) s.gate(); await page.waitForTimeout(200); assert.equal(await control.inputValue(), a); s.slow = '';
      for (const failure of [503, 'schema', 'version']) { s.failure = failure === 503 ? 503 : 0; s.wrong = failure === 'schema'; if (failure === 'version') s.meta.version = 'unpublished'; await control.selectOption(b); await page.getByRole('heading', { name: `${captions[kind]} API недоступен`, exact: true }).waitFor(); s.failure = 0; s.wrong = false; s.meta.version = s.catalog.version; await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await waitData(page); await control.selectOption(a); await waitData(page); }
      await control.selectOption(b); await waitData(page); await page.goBack(); await waitData(page); assert.equal(await control.inputValue(), a); await page.reload(); await waitData(page); assert.equal(await control.inputValue(), a);
      s.empty = true; await control.selectOption(b); await page.locator('.commissioning-page .empty-state').first().waitFor(); assert.equal(await page.locator('.chart-canvas').count(), 0); s.empty = false;
      s.catalog.regions = []; await page.reload(); await page.getByText(kind === 'annual' ? 'Нет доступных регионов.' : 'Нет доступных фильтров.', { exact: true }).waitFor(); states[kind] = fresh(kind); states[kind].catalogFailure = 0;
    }
    await page.goto(url + pages.annual + '?region=rf&annualb1Search=2026'); await waitData(page); await page.getByRole('link', { name: 'Оперативные данные', exact: true }).click(); await waitData(page); await page.getByRole('combobox', { name: 'Регион показателей', exact: true }).selectOption('rf'); await waitData(page); await page.getByRole('link', { name: 'Годовой ввод', exact: true }).click(); await waitData(page); assert.equal(new URL(page.url()).searchParams.get('annualb1Search'), '2026'); assert.equal(new URL(page.url()).searchParams.get('region'), 'rf');
    if (process.env.DASHBOARD_CHECK_LIVE === '1') {
      const live = await browser.newPage({ viewport: { width: 1280, height: 900 } }); live.on('pageerror', e => errors.push(e.message));
      for (const kind of ['annual', 'construction']) {
        const catalog = await (await live.request.get(url + roots[kind] + '/catalog')).json();
        for (const region of catalog.regions) for (const permitKind of kind === 'annual' ? [null] : catalog.permitKinds) {
          const month = kind === 'annual' ? null : catalog.monthsByKind[permitKind.id].at(-1), query = new URLSearchParams(kind === 'annual' ? { region: region.id } : { region: region.id, permit_kind: permitKind.id, month: String(month) });
          await live.goto(`${url}${pages[kind]}?${query}`); await waitData(live); const data = await (await live.request.get(`${url}${roots[kind]}?${query}`)).json();
          if (kind === 'annual') { assert.equal(await live.locator('.annual-report-section').count(), data.charts.length); for (const c of data.charts) { const text = await live.locator(`#annual-${c.id} .annual-summaries`).innerText(); for (const summary of c.summaries) for (const v of summary.values) assert.ok(text.includes(fmt(v.value, 1)), 'Supplied summaries'); } }
          else { assert.ok((await live.locator('.construction-permits').innerText()).includes('Москва')); const metrics = (await live.locator('.construction-area-metrics, .construction-sales-metrics').allInnerTexts()).join('\n'); for (const m of data.metrics) assert.ok(metrics.includes(fmt(m.value, m.digits))); }
          const pending = live.waitForEvent('download'); await live.getByRole('button', { name: kind === 'annual' ? 'Скачать годовой ввод Excel' : 'Скачать текущее строительство Excel', exact: true }).click(); const workbook = await bytes(await pending); assert.ok(workbook.length > 1000 && workbook.subarray(0, 2).equals(Buffer.from('PK'))); liveCases.push({ kind, query: query.toString(), version: data.version, workbookBytes: workbook.length });
          if (permitKind && permitKind.id !== catalog.permitKinds[0].id) continue;
          for (const theme of ['light', 'dark']) for (const width of [360, 390, 430, 768, 1280, 1920]) { await live.setViewportSize({ width, height: 900 }); await setTheme(live, theme); const count = kind === 'annual' ? data.charts.length : 1 + ['sold', 'unsold', 'sales_not_open'].filter(k => typeof data.sales?.[`${k}_pct`] === 'number').length; layouts.push(await inspect(live, `live-${kind}-${region.id}-${theme}-${width}`, count)); if (width === 360 || width === 1280) { hovers.push(...await hover(live)); await live.screenshot({ path: path.join(output, `live-${kind}-${region.id}-${theme}-${width}.png`), fullPage: true }); } }
        }
      }
      await live.close();
    }
    assert.deepEqual(errors, []); fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ layouts, hovers, downloads, liveCases, requests, errors }, null, 2)); console.log(JSON.stringify({ result: 'passed', layouts: layouts.length, hoverTargets: hovers.length, downloads: downloads.length, liveCases, output }));
  } finally { await browser.close(); }
}
if (require.main === module) main().catch(e => { console.error(e); process.exitCode = 1; });
