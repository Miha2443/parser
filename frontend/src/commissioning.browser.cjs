const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const metadata = { schemaVersion: 1, version: 'commissioning-v1', generatedAt: '2026-09-28T10:00:00Z', source: { date: '01.10.2026', files: ['data/raw/monitoring.xlsx', 'data/raw/static.xlsx'], issues: [['warning', 'Неполная месячная история']], fileEvidence: 'candidates', dateEvidence: 'Latest candidate modification date, not proof of selected input' } };
const linearMetadata = { ...metadata, source: { date: '2026-09-28', files: ['data/raw/linear_objects_2026-09-28.xlsx'], issues: [], fileEvidence: 'selected' } };
const operationalCatalog = { ...metadata, months: [{ id: 1, label: 'Январь' }, { id: 4, label: 'Январь–апрель' }, { id: 8, label: 'Январь–август' }, { id: 9, label: 'Январь–сентябрь' }], defaultMonth: 8, currentYear: 2026, years: [2026, 2025, 2024] };
const linearCatalog = { ...linearMetadata, years: [{ id: '2026', label: '2026', quarters: [1, 2, 3, 4], defaultQuarter: 3 }, { id: '2025', label: '2025', quarters: [1, 2], defaultQuarter: 2 }], indicators: [{ id: '1.1', label: 'Протяжённость дорог', unit: 'км' }, { id: '1.2', label: 'Количество искусственных сооружений', unit: 'ед' }, { id: '1.3', label: 'Количество внеуличных пешеходных переходов', unit: 'ед' }, { id: '1.4', label: 'Количество очистных сооружений', unit: 'ед' }] };
const treeLabels = [['total', 'Всего'], ['housing_objects', 'Жилые объекты, общая площадь'], ['residential_area', 'Жилая площадь'], ['mkd_total', 'МКД, общая площадь'], ['mkd_residential', 'Квартиры (жилая площадь МКД)'], ['izhs', 'ИЖС'], ['mop', 'МОП'], ['nonres_in_housing', 'Нежилые помещения в жилых объектах'], ['nonres_objects', 'Нежилые отдельно стоящие объекты'], ['offices', 'Офисы'], ['hotels', 'Гостиницы и апарт-отели'], ['industrial', 'Промышленные'], ['social', 'Социальные'], ['other', 'Прочее'], ['nonres_total', 'Всё нежильё']];
const operationalSelection = { month: 8, year: 2026, quarter: 1, cumulative: false, excludeMkd: false };
const linearSelection = { year: 2026, quarter: 3, cumulative: false, indicator: '1.1' };
function operationalReport(selection = operationalSelection, meta = metadata) {
  const columns = ['Год', 'За год, млн м²', 'За выбранный период, млн м²', 'Изменение полного года, %', 'Изменение к аналогичному периоду, %'];
  const tables = [['housing', 'Ввод жилья'], ['nonres', 'Ввод нежилой недвижимости']].map(([id, title]) => {
    const rows = Array.from({ length: 16 }, (_, i) => ({ [columns[0]]: 2011 + i, [columns[1]]: i === 15 ? null : 2.12345 + i / 10, [columns[2]]: id === 'nonres' && i < 11 ? null : (1.067 + i / 20) * (selection.excludeMkd && id === 'nonres' ? .5 : 1), [columns[3]]: i === 0 || i === 15 ? null : i === 1 ? 0 : -12.345, [columns[4]]: i === 0 || id === 'nonres' && i <= 11 ? null : i === 1 ? 0 : -8.765 }));
    return { id, title, columns: columns.map(id => ({ id, label: id })), rows, chart: { x: rows.map(r => String(r[columns[0]])), period: rows.map(r => ({ value: r[columns[2]] ?? 0 })), remainder: rows.map(r => ({ value: r[columns[1]] === null ? 0 : Math.max(0, r[columns[1]] - (r[columns[2]] ?? 0)) })), totals: rows.map(r => ({ value: r[columns[1]] ?? r[columns[2]] })), growth: rows.map(r => ({ value: r[columns[4]] })) } };
  });
  const treeRows = treeLabels.map(([id, label], i) => ({ id, label, value: i === 2 ? null : i === 6 ? 0 : i === 0 ? 99.1234 : i / 10 + selection.quarter / 100 }));
  return { ...meta, selection: { ...selection }, currentYear: 2026, periodLabel: operationalCatalog.months.find(m => m.id === selection.month)?.label ?? 'Январь', region: 'Москва', tables, tree: Object.fromEntries(treeRows.map(r => [r.id, r.value])), treeRows, sourceDetails: { file: 'selected/history.xlsx', date: '28.09.2026' }, notes: ['Структура — по РВ. Отсутствие месячной истории не означает нулевой ввод.'] };
}
function linearReport(selection = linearSelection, meta = linearMetadata) {
  const quarters = linearCatalog.years.find(y => y.id === String(selection.year)).quarters;
  const allPeriods = quarters.flatMap(q => linearCatalog.indicators.map((i, index) => ({ code: i.id, indicator: i.label, unit: i.unit, year: selection.year, quarter: q, plan: (index === 0 ? 10.234 + q : 4 + q) * (selection.cumulative ? q : 1), fact: q === 4 ? null : q === 2 && index === 3 ? 0 : (index === 0 ? 12.345 + q : 3 + q) * (selection.cumulative ? q : 1), percent: q === 4 ? null : q === 2 && index === 3 ? 0 : 91.2345 + index })));
  return { ...meta, selection: { ...selection }, summary: allPeriods.filter(r => r.quarter === selection.quarter), trend: allPeriods.filter(r => r.code === selection.indicator), allPeriods };
}
module.exports = { metadata, linearMetadata, operationalCatalog, linearCatalog, treeLabels, operationalSelection, linearSelection, operationalReport, linearReport };

const format = (n, digits = 0) => n === null ? '—' : new Intl.NumberFormat('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);
async function bytes(download) { const chunks = [], stream = await download.createReadStream(); for await (const chunk of stream) chunks.push(chunk); return Buffer.concat(chunks); }
const waitData = page => page.locator('.commissioning-page .chart-canvas canvas').first().waitFor({ timeout: 120000 });
const chartCount = kind => kind === 'operational' ? 2 : 5;
async function inspect(page, kind, label) {
  await page.waitForTimeout(450);
  const state = await page.evaluate(async () => {
    const resource = performance.getEntriesByType('resource').find(e => /echarts_core\.js/.test(e.name)), echarts = await import(resource.name);
    return { width: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth + 1, charts: [...document.querySelectorAll('.commissioning-page .chart-canvas')].map(el => {
      const rect = el.getBoundingClientRect(), canvas = el.querySelector('canvas'), colors = new Set(), chart = echarts.getInstanceByDom(el), option = chart.getOption();
      if (canvas) { const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data; for (let i = 0; i < pixels.length; i += 64) if (pixels[i + 3]) colors.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]}`); }
      const labels = new Set([...(option.xAxis[0].data ?? []), ...(option.yAxis[0].data ?? [])]);
      const croppedLabels = chart.getZr().storage.getDisplayList().filter(item => item.type === 'tspan' && labels.has(item.style.text)).flatMap(item => { const r = item.getBoundingRect().clone(); if (item.transform) r.applyTransform(item.transform); return r.x < -1 || r.x + r.width > el.clientWidth + 1 ? [{ text: item.style.text, x: r.x, width: r.width }] : []; });
      return { width: rect.width, height: rect.height, colors: colors.size, croppedLabels, outside: rect.left < -1 || rect.right > innerWidth + 1, scrolling: el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1 };
    }) };
  });
  assert.equal(state.overflow, false, `${label}: page overflow`); assert.equal(state.charts.length, chartCount(kind), `${label}: chart count`);
  for (const c of state.charts) { assert.ok(c.colors > 5 && c.width > 100 && c.height > 100, `${label}: blank chart`); assert.equal(c.outside || c.scrolling, false, `${label}: chart overflow ${JSON.stringify(c)}`); assert.deepEqual(c.croppedLabels, [], `${label}: cropped labels`); }
  return { label, kind, ...state };
}
async function hover(page) {
  const result = [], count = await page.locator('.commissioning-page .chart-canvas').count();
  for (let index = 0; index < count; index++) {
    await page.locator('.commissioning-page .chart-canvas').nth(index).scrollIntoViewIfNeeded(); await page.waitForTimeout(400);
    const points = await page.evaluate(async index => {
      const resource = performance.getEntriesByType('resource').find(e => /echarts_core\.js/.test(e.name)), echarts = await import(resource.name), el = document.querySelectorAll('.commissioning-page .chart-canvas')[index], chart = echarts.getInstanceByDom(el), rect = el.getBoundingClientRect(), option = chart.getOption();
      const unit = option.yAxis[0].name || option.xAxis[0].name;
      return option.series.flatMap((s, seriesIndex) => {
        const i = s.data.findIndex(v => typeof v === 'number' && v > 0); if (i === -1) return [];
        const l = chart.getModel().getSeriesByIndex(seriesIndex).getData().getItemLayout(i);
        return [{ x: rect.left + l.x + l.width / 2, y: rect.top + l.y + l.height / 2, text: s.name, value: s.data[i], digits: unit === 'млн м²' ? 2 : unit === 'км' ? 1 : 0, seriesIndex, index }];
      });
    }, index);
    for (const p of points) { await page.mouse.move(0, 0); await page.mouse.move(p.x, p.y); await page.waitForTimeout(230); const tip = page.locator('.commissioning-page .chart-canvas').nth(index).locator('.commissioning-tooltip:visible'), text = await tip.innerText(); assert.ok(text.includes(p.text) && text.includes(format(p.value, p.digits)), `Missing series hover ${JSON.stringify(p)}: ${text}`); const b = await tip.boundingBox(); assert.ok(b.x >= -1 && b.x + b.width <= page.viewportSize().width + 1, `Cropped tooltip ${JSON.stringify(b)}`); result.push({ ...p, tooltip: text }); }
  }
  await page.mouse.move(0, 0); return result;
}
async function theme(page, value) {
  if (await page.locator('html').getAttribute('data-theme') === value) return;
  const mobile = page.viewportSize().width <= 960;
  if (mobile) await page.getByTitle('Открыть навигацию', { exact: true }).click();
  await page.getByTitle(value === 'light' ? 'Включить светлую тему' : 'Включить тёмную тему', { exact: true }).click();
  if (mobile) await page.locator('.sidebar .mobile-close').click();
}
async function main() {
  const { chromium } = require('playwright'), url = (process.argv[2] || 'http://127.0.0.1:5173').replace(/\/$/, ''), output = process.env.COMMISSIONING_OUTPUT || path.join(os.tmpdir(), 'dashboard-commissioning-checks');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.DASHBOARD_BROWSER_CHANNEL || 'chrome' });
  const requests = [], errors = [], layouts = [], hovers = [], exports = [], liveCases = [];
  const roots = { operational: '/api/v1/commissioning/operational', linear: '/api/v1/linear' }, captions = { operational: 'Оперативный ввод', linear: 'Ввод линейных объектов' };
  const fresh = kind => ({ catalog: structuredClone(kind === 'operational' ? operationalCatalog : linearCatalog), metadata: structuredClone(kind === 'operational' ? metadata : linearMetadata), catalogFailure: 503, dataFailure: 0, exportFailure: 0, wrong: false, empty: false, slowQuarter: 0, slowGate: null, exportGate: null });
  const states = { operational: fresh('operational'), linear: fresh('linear') };
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } }); page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('dashboard.navigation', JSON.stringify({ market: true, commissioning: true, profile: true, construction: true })));
    let fallbacks = 0; await page.route('**/profile-snapshot.json', route => { fallbacks++; return route.fulfill({ status: 500 }); });
    const routeData = async route => {
      const u = new URL(route.request().url()), kind = u.pathname.includes('/linear') ? 'linear' : 'operational', s = states[kind]; requests.push({ kind, path: u.pathname + u.search });
      if (u.pathname.endsWith('/catalog')) return route.fulfill({ status: s.catalogFailure || 200, json: s.catalogFailure ? { detail: 'offline' } : s.catalog });
      if (u.pathname.endsWith('/export')) { const failure = s.exportFailure; if (s.exportGate) await s.exportGate; return route.fulfill({ status: failure || 200, contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', body: failure ? 'error' : Buffer.from('PK\x03\x04fixture-download') }).catch(() => {}); }
      const q = u.searchParams, selection = kind === 'operational' ? { month: Number(q.get('month')), excludeMkd: q.get('exclude_mkd') === 'true', year: Number(q.get('year')), quarter: Number(q.get('quarter')), cumulative: q.get('cumulative') === 'true' } : { year: Number(q.get('year')), quarter: Number(q.get('quarter')), cumulative: q.get('cumulative') === 'true', indicator: q.get('indicator') };
      if (s.dataFailure) return route.fulfill({ status: s.dataFailure, json: { detail: 'offline' } });
      const valid = kind === 'operational' ? s.catalog.years.includes(selection.year) && s.catalog.months.some(m => m.id === selection.month) : s.catalog.years.find(y => y.id === String(selection.year))?.quarters.includes(selection.quarter) && s.catalog.indicators.some(i => i.id === selection.indicator);
      if (!valid) return route.fulfill({ status: 404, json: { detail: 'removed filter' } });
      const value = kind === 'operational' ? operationalReport(selection, s.metadata) : linearReport(selection, s.metadata);
      if (s.wrong) value.selection.quarter = selection.quarter === 4 ? 1 : 4;
      if (s.empty) { value.source = { ...value.source, date: null }; if (kind === 'operational') { value.tables.forEach(t => { t.rows = []; Object.keys(t.chart).forEach(key => t.chart[key] = []); }); value.treeRows = []; value.tree = {}; } else value.summary = value.trend = value.allPeriods = []; }
      if (selection.quarter === s.slowQuarter) await s.slowGate;
      return route.fulfill({ json: value }).catch(() => {});
    };
    await page.route('**/api/v1/commissioning/operational**', routeData); await page.route('**/api/v1/linear**', routeData);
    for (const kind of ['operational', 'linear']) {
      const s = states[kind], catalogCount = () => requests.filter(r => r.kind === kind && r.path.endsWith('/catalog')).length, qControl = page.getByRole('combobox', { name: 'Квартал', exact: true });
      await page.goto(`${url}/commissioning/${kind}`); await page.getByRole('heading', { name: `${captions[kind]} API недоступен`, exact: true }).waitFor(); assert.equal(await page.locator('.chart-canvas').count(), 0);
      s.catalogFailure = 0; await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await waitData(page);
      assert.equal(await page.getByRole('combobox', { name: 'Год', exact: true }).inputValue(), '2026'); assert.equal(await qControl.inputValue(), kind === 'linear' ? '3' : '1');
      const source = page.locator(`#${kind}-sources`); assert.ok((await source.innerText()).includes(kind === 'linear' ? 'Дата отчёта' : 'Дата файлов-кандидатов'));
      if (kind === 'operational') {
        assert.equal(await page.getByRole('combobox', { name: 'Период с начала года', exact: true }).inputValue(), '8'); assert.equal(await page.locator('.operational-structure [data-structure-id]').count(), 15);
        for (const r of operationalReport().treeRows) assert.equal(await page.locator(`[data-structure-id="${r.id}"] dd`).textContent(), format(r.value, 2) + 'млн м²');
        const nonres = page.getByRole('region', { name: 'Таблица: Ввод нежилой недвижимости', exact: true }); assert.equal(await nonres.locator('tbody tr').count(), 16); assert.equal(await nonres.locator('tbody tr').first().locator('td').nth(1).innerText(), '—');
        await page.getByRole('checkbox', { name: 'Без нежилых помещений в жилых объектах', exact: true }).check(); await waitData(page); assert.equal(new URL(page.url()).searchParams.get('exclude_mkd'), 'true'); await page.goBack(); await waitData(page); assert.equal(await page.getByRole('checkbox').isChecked(), false); await page.goForward(); await waitData(page); assert.equal(await page.getByRole('checkbox').isChecked(), true);
        await page.getByRole('combobox', { name: 'Период с начала года', exact: true }).selectOption('4'); await waitData(page);
      } else {
        const km = page.locator('[data-indicator="1.1"] .metric-number'); assert.deepEqual(await km.allTextContents(), ['13,2км', '15,3км', '91,2%']);
        await qControl.selectOption('4'); await waitData(page); assert.equal(await page.locator('.linear-missing-fact').count(), 1); assert.ok((await page.locator('.linear-indicator .metric-number').allTextContents()).filter((_, i) => i % 3 === 1).every(text => text.startsWith('—')));
        await qControl.selectOption('2'); await waitData(page); assert.equal(await page.locator('[data-indicator="1.4"] .metric-number').nth(1).textContent(), '0ед');
        await page.getByRole('combobox', { name: 'Показатель динамики', exact: true }).selectOption('1.4'); await waitData(page);
      }
      await qControl.selectOption('2'); await waitData(page); await page.getByRole('radio', { name: 'С начала года', exact: true }).check(); await waitData(page);
      await page.getByRole('combobox', { name: 'Год', exact: true }).selectOption('2025'); await waitData(page); assert.equal(await qControl.inputValue(), '2');
      await page.reload(); await waitData(page); assert.equal(new URL(page.url()).searchParams.get('cumulative'), 'true');
      await page.getByRole('combobox', { name: 'Год', exact: true }).selectOption('2026'); await waitData(page); await page.getByRole('radio', { name: 'За квартал', exact: true }).check(); await waitData(page); await qControl.selectOption('3'); await waitData(page);
      const title = kind === 'operational' ? 'Ввод жилья' : 'Все показатели по кварталам';
      await page.getByRole('searchbox', { name: `Поиск: ${title}`, exact: true }).fill(kind === 'operational' ? '2011' : 'пешеходных');
      const tableCsv = page.waitForEvent('download'); await page.getByRole('button', { name: `Скачать все строки CSV: ${title}`, exact: true }).click(); const csv = (await bytes(await tableCsv)).toString('utf8'); assert.equal(csv.split('\r\n').length, 17); assert.ok(csv.includes(kind === 'operational' ? '2.12345' : '11.234'));
      await page.getByRole('combobox', { name: `Строк на странице: ${title}`, exact: true }).selectOption('20');
      if (kind === 'operational') { const download = page.waitForEvent('download'); await page.getByRole('button', { name: 'Скачать структуру ввода CSV', exact: true }).click(); assert.equal((await bytes(await download)).toString('utf8').split('\r\n').length, 16); }
      for (const mode of ['dark', 'light']) { await theme(page, mode); for (const width of [360, 390, 430, 768, 1280, 1920]) { await page.setViewportSize({ width, height: 900 }); layouts.push(await inspect(page, kind, `fixture-${kind}-${mode}-${width}`)); if ([360, 1280].includes(width)) { hovers.push(...await hover(page)); await page.screenshot({ path: path.join(output, `${kind}-${mode}-${width}.png`), fullPage: true }); } } }
      for (let i = 0; i < chartCount(kind); i++) {
        const chart = page.locator('.commissioning-page .chart-block').nth(i); await chart.getByRole('button', { name: 'Данные и скачивание', exact: true }).click();
        for (const name of ['CSV', 'PNG']) { const pending = page.waitForEvent('download'); await chart.getByRole('button', { name, exact: true }).click(); const content = await bytes(await pending); if (name === 'PNG') assert.ok(content.length > 1000 && content.subarray(1, 4).equals(Buffer.from('PNG'))); else assert.ok(content.toString('utf8').includes(';')); exports.push({ kind, chart: i, type: name, bytes: content.length }); }
        await chart.getByRole('button', { name: 'Данные и скачивание', exact: true }).click();
      }
      const excelName = kind === 'operational' ? 'Скачать оперативный ввод Excel' : 'Скачать линейные объекты Excel';
      let downloads = 0; const countDownload = () => downloads++; page.on('download', countDownload); s.exportFailure = 409;
      await page.getByRole('button', { name: excelName, exact: true }).click(); await page.locator('.commissioning-export [role="alert"]').waitFor(); assert.equal(downloads, 0);
      await page.setViewportSize({ width: 360, height: 900 }); layouts.push(await inspect(page, kind, `${kind}-export-error-360`));
      s.exportFailure = 0; const pending = page.waitForEvent('download'); await page.getByRole('button', { name: excelName, exact: true }).click(); assert.ok((await bytes(await pending)).subarray(0, 2).equals(Buffer.from('PK')));
      const exportQuery = new URL(requests.findLast(r => r.kind === kind && r.path.includes('/export')).path, url).searchParams; assert.equal(exportQuery.get('required_version'), s.metadata.version); assert.deepEqual([...exportQuery.keys()], kind === 'operational' ? ['month', 'exclude_mkd', 'year', 'quarter', 'cumulative', 'required_version'] : ['year', 'quarter', 'cumulative', 'indicator', 'required_version']);
      downloads = 0; let releaseExport; s.exportGate = new Promise(resolve => releaseExport = resolve); await page.getByRole('button', { name: excelName, exact: true }).click(); await page.getByRole('button', { name: excelName, exact: true }).isDisabled(); await qControl.selectOption('1'); await waitData(page); releaseExport(); s.exportGate = null; await page.waitForTimeout(300); assert.equal(downloads, 0); page.off('download', countDownload);
      let release; s.slowQuarter = 2; s.slowGate = new Promise(resolve => release = resolve); await qControl.selectOption('2'); await page.getByRole('status').waitFor(); assert.equal(await page.locator('.chart-canvas').count(), 0); await qControl.selectOption('3'); await waitData(page); release(); s.slowQuarter = 0; await page.waitForTimeout(300); assert.equal(await qControl.inputValue(), '3');
      for (const version of ['commissioning-v2', 'commissioning-v3']) { await qControl.selectOption('1'); await waitData(page); s.catalog.version = s.metadata.version = version; const before = catalogCount(); await qControl.selectOption('2'); await waitData(page); assert.equal(catalogCount(), before + 1, 'Sequential generation refresh'); }
      s.dataFailure = 404; let before = catalogCount(); await qControl.selectOption('1'); await page.getByRole('heading', { name: `${captions[kind]} API недоступен`, exact: true }).waitFor(); assert.equal(catalogCount(), before + 1);
      s.dataFailure = 0; await qControl.selectOption('2'); await waitData(page); s.catalog.version = s.metadata.version = 'commissioning-v4'; await qControl.selectOption('3'); await waitData(page);
      s.dataFailure = 404; before = catalogCount(); await qControl.selectOption('1'); await page.getByRole('heading', { name: `${captions[kind]} API недоступен`, exact: true }).waitFor(); assert.equal(catalogCount(), before + 1);
      s.dataFailure = 0; await qControl.selectOption('2'); await waitData(page); s.dataFailure = 404; before = catalogCount(); await qControl.selectOption('1'); await page.getByRole('heading', { name: `${captions[kind]} API недоступен`, exact: true }).waitFor(); assert.equal(catalogCount(), before); s.dataFailure = 0; await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await waitData(page);
      for (const code of [503]) { s.dataFailure = code; await qControl.selectOption('2'); await page.getByRole('heading', { name: `${captions[kind]} API недоступен`, exact: true }).waitFor(); assert.equal(await page.locator('.chart-canvas').count(), 0); s.dataFailure = 0; await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await waitData(page); }
      s.wrong = true; await qControl.selectOption('3'); await page.getByRole('heading', { name: `${captions[kind]} API недоступен`, exact: true }).waitFor(); s.wrong = false; await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await waitData(page);
      s.metadata.version = 'unpublished'; before = catalogCount(); await qControl.selectOption('2'); await page.getByRole('heading', { name: `${captions[kind]} API недоступен`, exact: true }).waitFor(); assert.equal(catalogCount(), before + 1); s.metadata.version = s.catalog.version; await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await waitData(page);
      before = catalogCount(); s.catalog.years = s.catalog.years.slice(0, 1); await page.getByRole('combobox', { name: 'Год', exact: true }).selectOption('2025'); await waitData(page); assert.equal(await page.getByRole('combobox', { name: 'Год', exact: true }).inputValue(), '2026'); assert.equal(catalogCount(), before + 1);
      s.empty = true; await page.reload(); await page.getByText('Нет данных для выбранного периода.', { exact: true }).first().waitFor(); assert.equal(await page.locator('.chart-canvas').count(), 0); assert.equal(await page.locator('.load-state[role="alert"]').count(), 0); assert.ok((await page.locator('.snapshot-label').innerText()).includes('Дата не указана'));
      s.catalog.years = []; await page.reload(); await page.getByText(kind === 'operational' ? 'Нет доступных периодов.' : 'Нет доступных периодов или показателей.', { exact: true }).waitFor(); assert.equal(await page.locator('.chart-canvas').count(), 0);
      states[kind] = fresh(kind); states[kind].catalogFailure = 0;
    }
    // Each local route owns its entire query, including table search and page size.
    await page.goto(`${url}/commissioning/operational?month=4&year=2025&quarter=2&cumulative=true&exclude_mkd=true&operationalhousingSearch=2011&operationalhousingSize=20`); await waitData(page);
    await page.getByTitle('Открыть навигацию', { exact: true }).click(); await page.getByRole('link', { name: 'Линейные объекты', exact: true }).click(); await waitData(page); assert.equal(await page.locator('.sidebar.is-open').count(), 0);
    await page.getByRole('combobox', { name: 'Показатель динамики', exact: true }).selectOption('1.3'); await waitData(page); await page.getByRole('searchbox', { name: 'Поиск: Все показатели по кварталам', exact: true }).fill('пешеходных');
    await page.getByTitle('Открыть навигацию', { exact: true }).click(); await page.getByRole('link', { name: 'Оперативный ввод', exact: true }).click(); await waitData(page); assert.equal(new URL(page.url()).searchParams.get('month'), '4'); assert.equal(await page.getByRole('searchbox', { name: 'Поиск: Ввод жилья', exact: true }).inputValue(), '2011');
    await page.getByTitle('Открыть навигацию', { exact: true }).click(); await page.getByRole('link', { name: 'Линейные объекты', exact: true }).click(); await waitData(page); assert.equal(new URL(page.url()).searchParams.get('indicator'), '1.3'); assert.equal(await page.getByRole('searchbox', { name: 'Поиск: Все показатели по кварталам', exact: true }).inputValue(), 'пешеходных'); assert.equal(await page.locator('.nav-link[aria-current="page"]').innerText(), 'Линейные объекты');
    assert.equal(fallbacks, 0);
    if (process.env.DASHBOARD_CHECK_LIVE === '1') {
      const live = await browser.newPage({ viewport: { width: 1280, height: 900 } }); live.on('pageerror', e => errors.push(e.message));
      for (const kind of ['operational', 'linear']) {
        const response = await live.request.get(`${url}${roots[kind]}/catalog`, { timeout: 120000 }); assert.ok(response.ok()); const catalog = await response.json();
        const selections = kind === 'operational' ? [false, true].flatMap(excludeMkd => [false, true].map(cumulative => ({ month: catalog.defaultMonth, excludeMkd, year: catalog.currentYear, quarter: cumulative ? 2 : 1, cumulative }))) : [false, true].flatMap(cumulative => catalog.years[0].quarters.map((quarter, i) => ({ year: Number(catalog.years[0].id), quarter, cumulative, indicator: catalog.indicators[i % catalog.indicators.length].id })));
        for (const selection of selections) {
          const params = new URLSearchParams(Object.fromEntries(Object.entries(selection).map(([k, v]) => [k === 'excludeMkd' ? 'exclude_mkd' : k, String(v)])));
          const response = await live.request.get(`${url}${roots[kind]}?${params}`, { timeout: 120000 }); assert.ok(response.ok()); const data = await response.json(); await live.goto(`${url}/commissioning/${kind}?${params}`); await waitData(live);
          if (kind === 'operational') {
            assert.equal(await live.locator('[data-structure-id]').count(), data.treeRows.length); for (const r of data.treeRows) assert.equal(await live.locator(`[data-structure-id="${r.id}"] dd`).textContent(), format(r.value, 2) + 'млн м²');
            for (const t of data.tables) { assert.equal(await live.getByRole('region', { name: `Таблица: ${t.title}`, exact: true }).locator('tbody tr').count(), t.rows.length); const pending = live.waitForEvent('download'); await live.getByRole('button', { name: `Скачать все строки CSV: ${t.title}`, exact: true }).click(); assert.equal((await bytes(await pending)).toString('utf8').split('\r\n').length, t.rows.length + 1); }
          } else {
            for (const r of data.summary) assert.deepEqual(await live.locator(`[data-indicator="${r.code}"] .metric-number`).allTextContents(), [format(r.plan, r.unit === 'км' ? 1 : 0) + r.unit, format(r.fact, r.unit === 'км' ? 1 : 0) + r.unit, format(r.percent, 1) + '%']);
            const pending = live.waitForEvent('download'); await live.getByRole('button', { name: 'Скачать все строки CSV: Все показатели по кварталам', exact: true }).click(); assert.equal((await bytes(await pending)).toString('utf8').split('\r\n').length, data.allPeriods.length + 1);
          }
          const excel = live.waitForEvent('download'); await live.getByRole('button', { name: kind === 'operational' ? 'Скачать оперативный ввод Excel' : 'Скачать линейные объекты Excel', exact: true }).click(); const workbook = await bytes(await excel); assert.ok(workbook.length > 1000 && workbook.subarray(0, 2).equals(Buffer.from('PK'))); liveCases.push({ kind, selection, version: data.version, workbookBytes: workbook.length });
        }
        for (const mode of ['dark', 'light']) { await theme(live, mode); for (const width of [360, 390, 430, 768, 1280, 1920]) { await live.setViewportSize({ width, height: 900 }); layouts.push(await inspect(live, kind, `live-${kind}-${mode}-${width}`)); if ([360, 1280].includes(width)) { await live.screenshot({ path: path.join(output, `live-${kind}-${mode}-${width}.png`), fullPage: true }); hovers.push(...await hover(live)); } } }
      }
      await live.close();
    }
    assert.deepEqual(errors, []); fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ url, layouts, hovers, exports, requests, liveCases, errors }, null, 2));
    console.log(JSON.stringify({ result: 'passed', layouts: layouts.length, hoverTargets: hovers.length, liveCases, output, checks: ['source evidence + notes + dates', 'sixteen years + raw nulls vs chart zeros', 'all fifteen supplied structure values', 'linear km/count precision + missing fact + zero', 'catalog defaults + quarter scopes + cumulative mode', 'URL history + reload + isolated local navigation', 'full tables + chart CSV/PNG + mandatory pinned Excel/409/abort', 'stale response abort + sequential generations + generation-bounded 404', 'removed options + persistent mismatch + schema errors + retry', 'empty data and catalogs + no fallback', 'both themes + 360–1920 + nonblank canvas + no scrolling/cropped labels + every available series hover'] }));
  } finally { await browser.close(); }
}
if (require.main === module) main().catch(e => { console.error(e); process.exitCode = 1; });
