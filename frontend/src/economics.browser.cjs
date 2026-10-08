const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const MSK = 'Москва', RF = 'Российская Федерация';
const monthNames = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
const short = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const roman = ['I', 'II', 'III', 'IV'];
const industries = ['Строительство', 'Транспортировка и хранение', 'Деятельность профессиональная, научная и техническая'];
const options = values => values.map(id => ({ id, label: String(id) }));
const source = { date: '05.07.2026', files: ['data/processed/source.pkl', 'data/derived/manual.csv'], issues: [['warning', 'Неполная история источника']], fileEvidence: 'candidates',
  recordedSources: ['old.xlsx', 'ручные данные за 2011-2012 (не обновляются)'], recordedSourceEvidence: 'source_file values in loaded rows, not verified raw selection',
  dateEvidence: 'Modification date of row-recorded local sources (Moscow), not a download date', downloadSummary: 'Дата скачивания неизвестна для 2 из 2 файлов',
  provenance: { columns: options(['Исходный файл', 'Скачан (МСК)', 'Дата файла (МСК)', 'Данные по', 'Статус']), rows: [
    { 'Исходный файл': 'downloads/old.xlsx', 'Скачан (МСК)': 'неизвестно', 'Дата файла (МСК)': '05.07.2026 10:00', 'Данные по': '2025', 'Статус': 'Дата скачивания не зафиксирована' },
    { 'Исходный файл': 'ручные данные за 2011-2012 (не обновляются)', 'Скачан (МСК)': 'неизвестно', 'Дата файла (МСК)': 'неизвестно', 'Данные по': '2012', 'Статус': 'Исходный файл не найден' },
  ] } };
function metadata(family, version = `economics-${family}-v1`) { return { schemaVersion: 1, version, generatedAt: '2026-07-02T09:00:00Z', source: structuredClone(source) }; }
const years = Array.from({ length: 16 }, (_, i) => 2011 + i);
function catalog(family, version) {
  const controls = [], defaults = {};
  function control(id, type, variants, value, extra = {}) { controls.push({ id, type, ...(variants ? { options: variants } : {}), default: value, ...extra }); defaults[id] = value; }
  if (family === 'accounts') {
    for (let i = 1; i <= 4; i++) control(`block${i}_regions`, 'multiselect', options([MSK, RF]), [MSK]);
    control('structure_region', 'select', options([MSK, RF]), MSK); control('index_region', 'select', options([MSK, RF]), MSK);
    control('structure_mode', 'select', [{ id: 'value', label: 'В рублях', unit: 'трлн руб' }, { id: 'share', label: 'Доля, %', unit: '%' }], 'value');
    const structureIndustries = { [MSK]: { value: options(industries), share: options(industries) }, [RF]: { value: options([...industries.slice(0, 2), 'Добыча полезных ископаемых']), share: options([...industries.slice(0, 2), 'Добыча полезных ископаемых']) } };
    const indexIndustries = { [MSK]: options(industries), [RF]: options([...industries.slice(0, 2), 'Добыча полезных ископаемых']) };
    control('structure_industries', 'multiselect', null, [industries[0]], { optionsByRegionMode: structureIndustries });
    control('index_industries', 'multiselect', null, [industries[0]], { optionsByRegion: indexIndustries }); control('show_total', 'checkbox', null, true);
    const names = ['ВРП Москвы и ВВП России', 'На душу населения', 'Индекс физического объема ВРП', 'Индекс физического объема на душу населения', 'Структура ВРП', 'Индекс физического объема ВРП по отраслям'];
    const blocks = names.map((title, i) => ({ id: `na_block${i + 1}`, title, kind: i === 2 || i === 3 || i === 5 ? 'line' : 'bar',
      ...(i === 4 ? { stack: true, industryControl: 'structure_industries' } : { unit: ['трлн руб', 'млн руб/чел', '%', '%', '', '%'][i] }),
      regionControl: i < 4 ? `block${i + 1}_regions` : i === 4 ? 'structure_region' : 'index_region', ...(i === 5 ? { industryControl: 'index_industries' } : {}) }));
    return { ...metadata(family, version), family, years, controls, defaults, minimumYear: 2011, blocks, structureIndustries, indexIndustries };
  }
  control('period', 'select', [{ id: 'year', label: 'Год' }, { id: 'quarter', label: 'Квартал' }, { id: 'month', label: 'Месяц' }], 'month');
  control('months', 'multiselect', monthNames.map((label, i) => ({ id: i + 1, label })), Array.from({ length: 12 }, (_, i) => i + 1), { periods: ['month'] });
  control('quarters', 'multiselect', roman.map((r, i) => ({ id: i + 1, label: `${r} квартал` })), [1, 2, 3, 4], { periods: ['quarter'] });
  if (family === 'salary') {
    control('region', 'select', options([MSK, RF]), MSK); control('views', 'multiselect', options(['Строительство', 'Всего']), ['Строительство', 'Всего']);
    control('ytd', 'checkbox', null, false, { periods: ['month', 'quarter'] });
    return { ...metadata(family, version), family, years, controls, defaults, regions: options([MSK, RF]), quarterOptionsByRegionMode: { [MSK]: { direct: [1, 2, 3, 4], ytd: [1, 2, 3, 4] }, [RF]: { direct: [1, 2, 3, 4], ytd: [1, 2, 3, 4] } } };
  }
  control('regions', 'multiselect', options([MSK, RF]), [MSK]); control('index_base', 'select', [{ id: 'month_to_month', label: 'К предыдущему месяцу' }, { id: 'ytd_to_yago', label: 'С начала года к АППГ' }], 'month_to_month', { periods: ['month', 'quarter'] });
  return { ...metadata(family, version), family, years, controls, defaults, regions: options([MSK, RF]) };
}
function report(family, selection = catalog(family).defaults, version) {
  const c = catalog(family, version), charts = [], tables = [];
  function add(id, title, kind, unit, series, rows, stack = false, totals) {
    const columns = options(Object.keys(rows[0] ?? { year: null, value: null }));
    const chart = { id, title, kind, unit, stack, series, columns, rows, ...(totals ? { totals } : {}) }; charts.push(chart);
    const slots = [...new Set(series.flatMap(s => s.points.map(p => p.x)))];
    tables.push({ id: `${id}_pivot`, title: 'Данные графика', columns: options(['Период', ...series.map(s => s.name)]), rows: slots.map(x => ({ 'Период': x, ...Object.fromEntries(series.map(s => [s.name, s.points.find(p => p.x === x)?.y ?? null])) })) });
  }
  function series(name, unit, points, id = name) { return { id, name, unit, points }; }
  if (family === 'accounts') {
    c.blocks.slice(0, 4).forEach((b, index) => {
      const selected = selection[`block${index + 1}_regions`]; if (!selected.length) return;
      const unit = b.unit, rows = [], items = selected.map((region, k) => {
        const name = index === 1 ? region === MSK ? 'Москва (ВРП на душу)' : 'Россия (ВВП на душу)' : region === MSK ? 'ВРП Москвы' : 'ВВП России';
        const points = years.map((year, i) => ({ x: year, y: i === 0 ? null : index < 2 ? (k + 1) * (i + 1) * (index === 1 ? .12345 : 1.2345) : 96 + i + k }));
        points.forEach(p => rows.push({ year: p.x, [unit]: p.y, 'Показатель': name })); return series(name, unit, points);
      }); add(b.id, b.title, b.kind, unit, items, rows);
    });
    if (selection.structure_industries.length) {
      const unit = selection.structure_mode === 'share' ? '%' : 'трлн руб', rows = [], items = selection.structure_industries.map((name, k) => {
        const points = years.map((year, i) => ({ x: year, y: i === 1 ? null : selection.structure_mode === 'share' ? 7 + k * 2 : (1.234567 + k) * (i + 1) }));
        points.forEach(p => rows.push({ year: p.x, view: name, region: selection.structure_region, [unit]: p.y })); return series(name, unit, points);
      });
      items.push(series('Остальные отрасли', unit, years.map(year => ({ x: year, y: selection.structure_mode === 'share' ? 93 - (selection.structure_industries.length - 1) * 9 : 15.5 })), 'rest'));
      const totals = years.map(year => ({ x: year, y: items.reduce((sum, s) => sum + (s.points.find(p => p.x === year)?.y ?? 0), 0) }));
      add('na_block5', c.blocks[4].title, 'bar', unit, items, rows, true, totals);
      const pivot = tables.at(-1); pivot.columns.push({ id: 'Всего', label: 'Всего' }); pivot.rows.forEach((r, i) => r['Всего'] = totals[i].y);
    }
    if (selection.index_industries.length) {
      const items = selection.index_industries.map((name, k) => series(name, '%', years.map((year, i) => ({ x: year, y: i === 2 ? null : 95 + i + k }))));
      const total = series('Всего по всем отраслям', '%', years.map((year, i) => ({ x: year, y: 101.12345 + i })), 'total');
      const rows = [...items, total].flatMap(s => s.points.map(p => ({ year: p.x, view: s.id === 'total' ? 'Всего' : s.name, region: selection.index_region, 'значение': p.y, unit: '%' })));
      if (selection.show_total) items.push(total);
      add('na_block6', c.blocks[5].title, 'line', '%', items, rows);
      if (!selection.show_total) { const pivot = tables.at(-1); pivot.columns.push({ id: 'Всего', label: 'Всего' }); pivot.rows.forEach((r, i) => r['Всего'] = total.points[i].y); }
    }
  } else {
    const names = family === 'salary' ? selection.views : selection.regions, dimension = selection.period === 'quarter' ? 'quarter' : 'month';
    const periods = selection.period === 'year' ? [null] : selection[selection.period === 'quarter' ? 'quarters' : 'months'];
    if (names.length && periods.length) {
      const unit = family === 'salary' ? 'руб.' : '%', rows = [], items = names.map((name, k) => {
        const points = years.flatMap((year, y) => periods.map((p, index) => {
          const x = selection.period === 'year' ? year : `${dimension === 'month' ? short[p - 1] : roman[p - 1] + ' кв'} ${String(year % 100).padStart(2, '0')}`;
          const value = y === 0 && index === 0 ? null : y === 0 && index === 1 ? 0 : family === 'salary' ? 12345.678 + y * 3000 + k * 5000 + (selection.ytd ? 123.45 : 0) : 99.12345 + y / 10 + k + (selection.index_base === 'ytd_to_yago' ? 2 : 0);
          rows.push({ year, ...(p === null ? {} : { [dimension]: p, 'период': x }), ...(family === 'salary' ? { view: name, region: selection.region, 'значение_руб': value } : { region: name, 'ипц_%': value }) });
          return { x, y: value };
        })); return series(name, unit, points);
      }); add(`${family}_${selection.period}`, family === 'salary' ? `Заработная плата · ${selection.region}` : 'Индексы потребительских цен', selection.period === 'year' ? 'bar' : 'line', unit, items, rows);
    }
  }
  return { ...metadata(family, version), family, selection: structuredClone(selection), charts, tables };
}
module.exports = { MSK, RF, industries, metadata, catalog, report };

const format = (value, digits = 2) => value === null ? '—' : new Intl.NumberFormat('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
async function bytes(download) { const chunks = []; for await (const chunk of await download.createReadStream()) chunks.push(chunk); return Buffer.concat(chunks); }
async function harness(page) {
  await page.route('**/src/main.tsx*', async route => {
    const original = await route.fetch(), body = await original.text();
    const reactPath = body.match(/from "([^\"]*\/react\.js[^\"]*)"/)[1], domPath = body.match(/from "([^\"]*\/react-dom_client\.js[^\"]*)"/)[1];
    await route.fulfill({ contentType: 'application/javascript', body: `
      import React from ${JSON.stringify(reactPath)}; import ReactDOM from ${JSON.stringify(domPath)};
      import Economics from '/src/Economics.tsx'; import '/src/styles.css';
      function Harness(){
        const [query,setQuery]=React.useState(location.search),[family,setFamily]=React.useState(location.pathname.split('/').at(-1)),[theme,setTheme]=React.useState('dark');
        React.useEffect(()=>{document.documentElement.dataset.theme=theme},[theme]);
        React.useEffect(()=>{const h=()=>{setQuery(location.search);setFamily(location.pathname.split('/').at(-1))};addEventListener('popstate',h);return()=>removeEventListener('popstate',h)},[]);
        function change(values,replace=false){const next=new URLSearchParams(location.search);for(const [k,v]of Object.entries(values))v===null?next.delete(k):next.set(k,v);history[replace?'replaceState':'pushState'](null,'',location.pathname+'?'+next+location.hash);setQuery(location.search)}
        return React.createElement('div',{className:'app-shell'},React.createElement('aside',{className:'sidebar'},React.createElement('div',{className:'brand'},'Аналитика Москвы')),React.createElement('div',{className:'workspace'},React.createElement('header',{className:'topbar'},'Экономика',React.createElement('button',{'aria-label':'Сменить тему',onClick:()=>setTheme(theme==='dark'?'light':'dark')},theme)),React.createElement(Economics,{key:family,family,theme,query,change})))}
      ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(Harness));
    ` });
  });
}
const waitData = page => page.locator('.economics-chart-section .chart-canvas canvas').first().waitFor({ timeout: 120000 });
async function inspect(page, label, expected) {
  await page.waitForTimeout(450);
  const state = await page.evaluate(async () => {
    const resource = performance.getEntriesByType('resource').find(e => /echarts_core\.js/.test(e.name)), echarts = await import(resource.name);
    const visible = [...document.querySelectorAll('.economics-page summary,.economics-field > span,.economics-page h1,.economics-page h2')].filter(el => el.getClientRects().length);
    return { width: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth + 1,
      textOutside: visible.filter(el => { const r = el.getBoundingClientRect(); return r.left < -1 || r.right > innerWidth + 1; }).map(el => el.textContent),
      charts: [...document.querySelectorAll('.economics-page .chart-canvas')].map(el => {
        const rect = el.getBoundingClientRect(), canvas = el.querySelector('canvas'), colors = new Set(), chart = echarts.getInstanceByDom(el), option = chart.getOption();
        if (canvas) { const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data; for (let i = 0; i < pixels.length; i += 64) if (pixels[i + 3]) colors.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]}`); }
        const labels = new Set(option.xAxis[0].data);
        const croppedLabels = chart.getZr().storage.getDisplayList().filter(item => item.type === 'tspan' && labels.has(item.style.text)).flatMap(item => { const r = item.getBoundingRect().clone(); if (item.transform) r.applyTransform(item.transform); return r.x < -1 || r.x + r.width > el.clientWidth + 1 ? [{ text: item.style.text, x: r.x, width: r.width }] : []; });
        return { width: rect.width, height: rect.height, colors: colors.size, croppedLabels, outside: rect.left < -1 || rect.right > innerWidth + 1, scrolling: el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1 };
      }) };
  });
  assert.equal(state.overflow, false, label + ': page overflow'); assert.deepEqual(state.textOutside, [], label + ': text outside viewport'); assert.equal(state.charts.length, expected, label + ': chart count');
  for (const chart of state.charts) { assert.ok(chart.width > 100 && chart.height > 100 && chart.colors > 5, label + ': blank chart'); assert.equal(chart.outside || chart.scrolling, false, label + ': chart overflow'); assert.deepEqual(chart.croppedLabels, [], label + ': cropped labels'); }
  return { label, ...state };
}
async function hover(page, chartIndex = 0) {
  const el = page.locator('.economics-chart-section .chart-canvas').nth(chartIndex); await el.scrollIntoViewIfNeeded(); await page.waitForTimeout(450);
  const targets = await page.evaluate(async index => {
    const resource = performance.getEntriesByType('resource').find(e => /echarts_core\.js/.test(e.name)), echarts = await import(resource.name);
    const el = document.querySelectorAll('.economics-chart-section .chart-canvas')[index], chart = echarts.getInstanceByDom(el), rect = el.getBoundingClientRect(), option = chart.getOption();
    return option.series.flatMap((series, seriesIndex) => {
      const data = chart.getModel().getSeriesByIndex(seriesIndex).getData(), indices = series.data.map((y, i) => y !== null && y > 0 ? i : -1).filter(i => i >= 0);
      const dataIndex = indices[Math.floor(indices.length / 2)]; if (dataIndex === undefined) return [];
      const layout = data.getItemLayout(dataIndex), point = series.type === 'bar' ? [layout.x + layout.width / 2, layout.y + layout.height / 2] : chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [dataIndex, series.data[dataIndex]]);
      return [{ x: rect.left + point[0], y: rect.top + point[1], name: series.name, value: series.data[dataIndex], unit: option.yAxis[0].name }];
    });
  }, chartIndex);
  for (const target of targets) {
    await page.mouse.move(0, 0); await page.mouse.move(target.x, target.y); const tooltip = page.locator('.economics-tooltip:visible');
    try { await tooltip.waitFor({ timeout: 5000 }); } catch (error) { await page.screenshot({ path: path.join(process.env.ECONOMICS_OUTPUT || path.join(os.tmpdir(), 'dashboard-economics-checks'), 'hover-failure.png'), fullPage: true }); console.error({ target, viewport: page.viewportSize(), url: page.url() }); throw error; }
    const text = await tooltip.innerText(); assert.ok(text.includes(target.name) && text.includes(format(target.value)) && text.includes(target.unit), `${JSON.stringify(target)}: ${text}`);
    const box = await tooltip.boundingBox(); assert.ok(box.x >= -1 && box.x + box.width <= page.viewportSize().width + 1, 'Tooltip cropped');
  }
  await page.mouse.move(0, 0); return targets;
}
async function selectMulti(page, label, values) {
  const target = page.locator(`summary[aria-label="${label}"]`); if (!(await target.evaluate(el => el.parentElement.open))) await target.click();
  const details = target.locator('..'); await details.getByRole('button', { name: `Очистить: ${label}`, exact: true }).click();
  for (const value of values) await details.getByRole('checkbox', { name: String(value), exact: true }).check();
  await target.press('Escape'); await page.waitForTimeout(180);
}
async function main() {
  const { chromium } = require('playwright'), url = (process.argv[2] || 'http://127.0.0.1:5173').replace(/\/$/, ''), output = process.env.ECONOMICS_OUTPUT || path.join(os.tmpdir(), 'dashboard-economics-checks');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.DASHBOARD_BROWSER_CHANNEL || 'chrome' });
  const layouts = [], hovers = [], requests = [], errors = [], liveCases = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, acceptDownloads: true }); page.on('pageerror', e => errors.push(e.message));
    if (process.env.ECONOMICS_HARNESS !== '0') await harness(page);
    let catalogError = 503, reportError = 0, exportError = 0, next404 = false, mismatch = false, empty = false, wrongScope = false;
    let catalogVersion = 'v1', reportVersion = 'v1', slowRegion = '', releaseSlow, gate;
    let removedRegion = false, count404 = 0, fallback = 0;
    await page.route('**/profile-snapshot.json', route => { fallback++; return route.fulfill({ status: 500 }); });
    await page.route('**/api/v1/economics/**', async route => {
      const u = new URL(route.request().url()), family = u.pathname.split('/')[4]; requests.push(u.pathname + u.search);
      if (u.pathname.endsWith('/catalog')) {
        const c = catalog(family, catalogVersion);
        if (removedRegion && family === 'salary') { c.regions = c.regions.filter(r => r.id === RF); c.controls.find(c => c.id === 'region').options = c.regions; c.controls.find(c => c.id === 'region').default = RF; c.defaults.region = RF; }
        return route.fulfill({ status: catalogError || 200, json: catalogError ? { detail: 'offline' } : c });
      }
      if (u.pathname.endsWith('/export')) return route.fulfill({ status: exportError || 200, headers: { 'X-Data-Version': reportVersion }, contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', body: exportError ? 'error' : Buffer.from('PK\x03\x04fixture') });
      if (reportError || next404 || removedRegion && u.searchParams.get('region') === MSK) { const code = reportError || 404; next404 = false; count404++; return route.fulfill({ status: code, json: { detail: 'unavailable' } }); }
      const c = catalog(family), selected = Object.fromEntries(Object.entries(c.defaults).map(([key, value]) => [key, Array.isArray(value) ? u.searchParams.getAll(key).filter(Boolean).map(v => typeof value[0] === 'number' ? Number(v) : v) : typeof value === 'boolean' ? u.searchParams.get(key) === 'true' : u.searchParams.get(key)]));
      const data = report(family, selected, mismatch ? 'permanent-mismatch' : reportVersion);
      if (wrongScope) data.selection.period = 'year';
      if (empty) { data.charts = []; data.tables = []; data.source.date = null; }
      if (selected.region === slowRegion) await gate;
      return route.fulfill({ json: data }).catch(() => {});
    });
    await page.goto(`${url}/economics/salary`); await page.getByRole('heading', { name: 'Экономические показатели API недоступны', exact: true }).waitFor(); assert.equal(await page.locator('.chart-canvas').count(), 0);
    catalogError = 0; await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await waitData(page);
    assert.ok((await page.locator('#economics-sources').innerText()).includes(source.downloadSummary));
    for (const family of ['salary', 'ipc', 'accounts']) {
      await page.goto(`${url}/economics/${family}`); await waitData(page);
      for (const theme of ['dark', 'light']) {
        if (await page.locator('html').getAttribute('data-theme') !== theme) await page.getByRole('button', { name: 'Сменить тему', exact: true }).click();
        for (const width of [360, 390, 430, 768, 1280, 1920]) {
          await page.setViewportSize({ width, height: 900 }); layouts.push(await inspect(page, `${family}-${theme}-${width}`, family === 'accounts' ? 6 : 1));
          if ([360, 1280].includes(width)) { hovers.push(...await hover(page, family === 'accounts' ? 4 : 0)); await page.screenshot({ path: path.join(output, `${family}-${theme}-${width}.png`), fullPage: true }); }
        }
      }
      await page.setViewportSize({ width: 1280, height: 900 });
    }
    await page.goto(`${url}/economics/salary`); await waitData(page);
    for (const period of ['year', 'quarter', 'month']) {
      await page.getByRole('combobox', { name: 'Период', exact: true }).selectOption(period); await waitData(page);
      if (period !== 'year') { await page.getByRole('button', { name: 'С начала года', exact: true }).click(); await waitData(page); assert.equal(new URL(page.url()).searchParams.get('ytd'), 'true'); }
    }
    await selectMulti(page, 'Месяцы', ['март', 'декабрь']); await waitData(page); assert.deepEqual(JSON.parse(new URL(page.url()).searchParams.get('months')), [3, 12]);
    await page.goBack(); await waitData(page); await page.goForward(); await waitData(page);
    await page.getByRole('combobox', { name: 'Регион', exact: true }).selectOption(RF); await waitData(page);
    await page.locator('.economics-tables > summary').click(); await page.getByRole('tab', { name: 'Данные выгрузки', exact: true }).click();
    await page.getByRole('combobox', { name: 'Строк на странице: salary_month', exact: true }).selectOption('20'); assert.equal(await page.locator('.economics-chart-section .economics-table tbody tr').count(), 20);
    await page.getByRole('button', { name: 'Следующая страница: salary_month', exact: true }).click(); assert.equal(new URL(page.url()).searchParams.get('econ_salary_month_page'), '2');
    await page.getByRole('searchbox', { name: 'Поиск: salary_month', exact: true }).fill('Строительство');
    let pending = page.waitForEvent('download'); await page.getByRole('button', { name: 'Скачать все строки CSV: salary_month', exact: true }).click(); const csv = (await bytes(await pending)).toString('utf8'); assert.equal(csv.split('\r\n').length, 65); assert.ok(csv.includes('значение_руб'));
    await page.locator('.chart-data .data-toggle').click();
    for (const name of ['CSV', 'PNG']) { pending = page.waitForEvent('download'); await page.locator('.chart-data-panel').getByRole('button', { name, exact: true }).click(); const buffer = await bytes(await pending); if (name === 'CSV') assert.equal(buffer.toString('utf8').split('\r\n').length, 65); else assert.ok(buffer.length > 1000 && buffer.subarray(1, 4).equals(Buffer.from('PNG'))); }
    pending = page.waitForEvent('download'); await page.getByRole('button', { name: 'Скачать полный отчёт Excel', exact: true }).click(); assert.ok((await bytes(await pending)).subarray(0, 2).equals(Buffer.from('PK')));
    const exported = new URL(requests.filter(u => u.includes('/export')).at(-1), url); assert.equal(exported.searchParams.get('required_version'), 'v1'); assert.deepEqual(exported.searchParams.getAll('months'), ['3', '12']);
    exportError = 409; let downloads = 0; page.on('download', () => downloads++); await page.getByRole('button', { name: 'Скачать полный отчёт Excel', exact: true }).click(); await page.getByText('Источники изменились.', { exact: false }).waitFor(); assert.equal(downloads, 0);
    exportError = 0; catalogVersion = reportVersion = 'v2'; await page.getByRole('button', { name: 'Обновить данные', exact: true }).click(); await waitData(page);
    slowRegion = MSK; gate = new Promise(resolve => releaseSlow = resolve);
    await page.getByRole('combobox', { name: 'Регион', exact: true }).selectOption(MSK); await page.waitForTimeout(100); await page.getByRole('combobox', { name: 'Регион', exact: true }).selectOption(RF); await waitData(page); releaseSlow(); slowRegion = ''; await page.waitForTimeout(300); assert.equal(await page.getByRole('combobox', { name: 'Регион', exact: true }).inputValue(), RF);
    removedRegion = true; await page.goto(`${url}/economics/salary?region=${encodeURIComponent(MSK)}`); await waitData(page); assert.equal(await page.getByRole('combobox', { name: 'Регион', exact: true }).inputValue(), RF); removedRegion = false;
    next404 = true; await page.getByRole('button', { name: 'Обновить показатели', exact: true }).click(); await waitData(page); assert.ok(count404 >= 1);
    mismatch = true; await page.getByRole('button', { name: 'Обновить показатели', exact: true }).click(); await page.getByRole('heading', { name: 'Экономические показатели API недоступны', exact: true }).waitFor(); mismatch = false; await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await waitData(page);
    wrongScope = true; await page.getByRole('button', { name: 'Обновить показатели', exact: true }).click(); await page.getByRole('heading', { name: 'Экономические показатели API недоступны', exact: true }).waitFor(); wrongScope = false; await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await waitData(page);
    await page.goto(`${url}/economics/ipc`); await waitData(page); await selectMulti(page, 'Регионы', [MSK, RF]); await waitData(page);
    for (const period of ['quarter', 'year', 'month']) { await page.getByRole('combobox', { name: 'Период', exact: true }).selectOption(period); await waitData(page); if (period !== 'year') { await page.getByRole('combobox', { name: 'Тип индекса', exact: true }).selectOption('ytd_to_yago'); await waitData(page); } }
    await page.goto(`${url}/economics/accounts`); await waitData(page);
    for (let i = 1; i <= 4; i++) { await selectMulti(page, `Регионы блока ${i}`, i % 2 ? [MSK, RF] : [RF]); await waitData(page); }
    await selectMulti(page, 'Отрасли структуры', industries.slice(0, 2)); await page.getByRole('combobox', { name: 'Регион структуры', exact: true }).selectOption(RF); await page.getByRole('combobox', { name: 'Показатель структуры', exact: true }).selectOption('share'); await waitData(page); assert.deepEqual(JSON.parse(new URL(page.url()).searchParams.get('structure_industries')), industries.slice(0, 2));
    await page.getByRole('combobox', { name: 'Регион индекса', exact: true }).selectOption(MSK); await selectMulti(page, 'Отрасли индекса', industries.slice(0, 2)); await page.getByRole('checkbox', { name: 'Всего по всем отраслям', exact: true }).uncheck(); await waitData(page);
    assert.equal(await page.locator('#econ-na_block6 .legend button').filter({ hasText: 'Всего по всем отраслям' }).count(), 0);
    const last = page.locator('#econ-na_block6'); await last.locator('.economics-tables > summary').click(); await last.getByRole('tab', { name: 'Данные выгрузки', exact: true }).click(); assert.ok((await last.locator('tbody').innerText()).includes('Всего'));
    hovers.push(...await hover(page, 4));
    await page.reload(); await waitData(page); assert.equal(await page.getByRole('combobox', { name: 'Регион структуры', exact: true }).inputValue(), RF); assert.equal(await page.getByRole('checkbox', { name: 'Всего по всем отраслям', exact: true }).isChecked(), false);
    await selectMulti(page, 'Регионы блока 1', []); await page.waitForTimeout(400); assert.equal(await page.locator('#econ-na_block1 .chart-canvas').count(), 0);
    await page.goto(`${url}/economics/salary?views=%5B%5D`); await page.getByText('Нет данных для выбранных параметров.', { exact: true }).waitFor(); assert.equal(await page.locator('.chart-canvas').count(), 0);
    empty = true; await page.goto(`${url}/economics/ipc`); await page.getByText('Нет данных для выбранных параметров.', { exact: true }).waitFor(); empty = false;
    assert.equal(fallback, 0);
    if (process.env.DASHBOARD_CHECK_LIVE === '1') {
      const live = await browser.newPage({ viewport: { width: 1280, height: 900 }, acceptDownloads: true }); live.on('pageerror', e => errors.push(e.message)); if (process.env.ECONOMICS_HARNESS !== '0') await harness(live);
      for (const family of ['salary', 'ipc', 'accounts']) {
        for (const q of family === 'accounts' ? ['', 'structure_region=' + encodeURIComponent(RF) + '&structure_mode=share&show_total=false'] : ['', 'period=year', 'period=quarter', family === 'salary' ? 'period=month&ytd=true' : 'period=month&index_base=ytd_to_yago']) {
          await live.goto(`${url}/economics/${family}?${q}`); await waitData(live);
          const apiRequests = live.waitForResponse(r => r.url().includes(`/economics/${family}/report`)); await live.getByRole('button', { name: 'Обновить показатели', exact: true }).click(); const payload = await (await apiRequests).json(); await waitData(live);
          const plotted = await live.evaluate(async () => { const resource = performance.getEntriesByType('resource').find(e => /echarts_core\.js/.test(e.name)), echarts = await import(resource.name); return [...document.querySelectorAll('.economics-chart-section .chart-canvas')].map(el => { const option = echarts.getInstanceByDom(el).getOption(); return { unit: option.yAxis[0].name, x: option.xAxis[0].data, series: option.series.map(s => ({ name: s.name, data: s.data })) }; }); });
          for (let i = 0; i < payload.charts.length; i++) {
            const expected = payload.charts[i], actual = plotted[i]; assert.equal(actual.unit, expected.unit); assert.equal(actual.series.length, expected.series.length);
            expected.series.forEach((series, index) => { assert.equal(actual.series[index].name, series.name); const occurrences = new Map(); for (const point of series.points) { const x = String(point.x), occurrence = occurrences.get(x) || 0; occurrences.set(x, occurrence + 1); const slot = actual.x.map((value, i) => value === x ? i : -1).filter(i => i >= 0)[occurrence]; assert.notEqual(slot, undefined); assert.equal(actual.series[index].data[slot], point.y); } });
          }
          layouts.push(await inspect(live, `live-${family}-${q}`, payload.charts.length));
          pending = live.waitForEvent('download'); await live.getByRole('button', { name: 'Скачать полный отчёт Excel', exact: true }).click(); const workbook = await bytes(await pending); assert.ok(workbook.length > 1000 && workbook.subarray(0, 2).equals(Buffer.from('PK')));
          liveCases.push({ family, q, version: payload.version, charts: payload.charts.length, workbookBytes: workbook.length });
        }
        for (const theme of ['dark', 'light']) { if (await live.locator('html').getAttribute('data-theme') !== theme) await live.getByRole('button', { name: 'Сменить тему', exact: true }).click(); for (const width of [360, 390, 430, 768, 1280, 1920]) { await live.setViewportSize({ width, height: 900 }); layouts.push(await inspect(live, `live-${family}-${theme}-${width}`, family === 'accounts' ? 6 : 1)); } }
        await live.setViewportSize({ width: 1280, height: 900 }); hovers.push(...await hover(live, family === 'accounts' ? 4 : 0));
      }
      await live.close();
    }
    assert.deepEqual(errors, []); fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ layouts, hovers, requests, liveCases, errors }, null, 2));
    console.log(JSON.stringify({ result: 'passed', layouts: layouts.length, hoverTargets: hovers.length, liveCases, output, harness: process.env.ECONOMICS_HARNESS !== '0' }));
  } finally { await browser.close(); }
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
