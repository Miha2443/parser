import assert from 'node:assert/strict';
import test from 'node:test';
import fixture from './economics.browser.cjs';
import { economicsChartOption, economicsChartRows, economicsFilterKey, economicsFilters, economicsOptions, economicsQueryValue, economicsRepairs, economicsTablePage, economicsTableRows, economicsUrl, loadEconomicsCatalog, loadEconomicsPage, loadEconomicsReport, readEconomicsCatalog, readEconomicsReport } from './economicsData.ts';
const families = ['salary', 'ipc', 'accounts'];

test('annual residual-only years remain chronologically aligned with missing industry observations', () => {
  const chart = { ...fixture.report('accounts').charts[4], rows: [{ year: 2020 }, { year: 2022 }],
    series: [{ id: 'industry', name: 'Industry', unit: '%', points: [{ x: 2020, y: 10 }, { x: 2022, y: 30 }] },
      { id: 'residual', name: 'Residual', unit: '%', points: [{ x: 2020, y: 90 }, { x: 2021, y: 100 }, { x: 2022, y: 70 }] }] };
  const option = economicsChartOption(chart, 'dark');
  assert.deepEqual(option.xAxis.data, ['2020', '2021', '2022']);
  assert.deepEqual(option.series[0].data, [10, null, 30]);
  assert.deepEqual(option.series[1].data, [90, 100, 70]);
});

test('all three catalogs and reports retain controls, units, rows and provenance', () => {
  for (const family of families) {
    const c = readEconomicsCatalog(fixture.catalog(family), family), d = readEconomicsReport(fixture.report(family), family, c.defaults);
    assert.deepEqual(c, fixture.catalog(family)); assert.deepEqual(d, fixture.report(family));
    assert.equal(c.controls.length, family === 'accounts' ? 10 : family === 'salary' ? 6 : 5);
    assert.equal(d.source.fileEvidence, 'candidates'); assert.equal(d.source.recordedSources.length, 2);
    assert.equal(d.source.provenance.rows[1]['Дата файла (МСК)'], 'неизвестно');
  }
});
test('salary and CPI reconcile periods, exact regions, calendar subsets, YTD and index bases', () => {
  for (const family of ['salary', 'ipc']) {
    const c = readEconomicsCatalog(fixture.catalog(family), family);
    assert.deepEqual(economicsFilters(c, new URLSearchParams()), c.defaults);
    const p = new URLSearchParams({ period: 'quarter', months: '[1,12]', quarters: '[2,4]', region: fixture.RF, regions: JSON.stringify([fixture.MSK, fixture.RF]), ytd: 'true', index_base: 'ytd_to_yago' });
    const chosen = economicsFilters(c, p);
    assert.equal(chosen.period, 'quarter'); assert.deepEqual(chosen.months, [1, 12]); assert.deepEqual(chosen.quarters, [2, 4]);
    if (family === 'salary') { assert.equal(chosen.region, fixture.RF); assert.equal(chosen.ytd, true); }
    else { assert.deepEqual(chosen.regions, [fixture.MSK, fixture.RF]); assert.equal(chosen.index_base, 'ytd_to_yago'); }
  }
});
test('URL lists accept repeated values and JSON, encode empty explicitly, reject ad-hoc CSV', () => {
  const c = fixture.catalog('salary');
  const repeated = new URLSearchParams(); repeated.append('months', '12'); repeated.append('months', '1'); repeated.append('months', '12');
  assert.deepEqual(economicsFilters(c, repeated).months, [12, 1]);
  assert.deepEqual(economicsFilters(c, new URLSearchParams({ months: '' })).months, []);
  assert.deepEqual(economicsFilters(c, new URLSearchParams({ views: '[]' })).views, []);
  assert.deepEqual(economicsFilters(c, new URLSearchParams({ months: '1,2' })).months, c.defaults.months);
  assert.equal(economicsQueryValue([]), '[]');
  assert.equal(economicsQueryValue(['А, Б', 'В/Г']), '["А, Б","В/Г"]');
});
test('six accounts blocks preserve separate regions, structure/index industries, mode and checkbox', () => {
  const c = fixture.catalog('accounts'), p = new URLSearchParams({ block1_regions: JSON.stringify([fixture.MSK, fixture.RF]), block2_regions: JSON.stringify([fixture.RF]), block3_regions: '[]', block4_regions: JSON.stringify([fixture.MSK]),
    structure_region: fixture.RF, structure_mode: 'share', structure_industries: JSON.stringify(fixture.industries.slice(0, 2)), index_region: fixture.MSK, index_industries: JSON.stringify([fixture.industries[2]]), show_total: 'false' });
  const selected = economicsFilters(c, p);
  assert.deepEqual(selected.block1_regions, [fixture.MSK, fixture.RF]); assert.deepEqual(selected.block2_regions, [fixture.RF]); assert.deepEqual(selected.block3_regions, []); assert.deepEqual(selected.block4_regions, [fixture.MSK]);
  assert.deepEqual(selected.structure_industries, fixture.industries.slice(0, 2)); assert.deepEqual(selected.index_industries, [fixture.industries[2]]); assert.equal(selected.show_total, false);
  const data = readEconomicsReport(fixture.report('accounts', selected), 'accounts', selected);
  assert.equal(data.charts.length, 5); assert.equal(data.charts.find(c => c.id === 'na_block5').unit, '%');
  const index = data.charts.find(c => c.id === 'na_block6'); assert.equal(index.series.some(s => s.id === 'total'), false); assert.ok(index.rows.some(r => r.view === 'Всего'));
});
test('compatible industries survive region/mode changes; invalid selections fall back but explicit empties stay empty', () => {
  const c = fixture.catalog('accounts'), p = new URLSearchParams({ structure_region: fixture.RF, structure_mode: 'share', structure_industries: JSON.stringify(fixture.industries) });
  assert.deepEqual(economicsFilters(c, p).structure_industries, fixture.industries.slice(0, 2));
  p.set('structure_industries', JSON.stringify(['removed'])); assert.deepEqual(economicsFilters(c, p).structure_industries, ['Строительство']);
  p.set('structure_industries', '[]'); assert.deepEqual(economicsFilters(c, p).structure_industries, []);
  c.structureIndustries[fixture.RF].share = [{ id: 'Добыча', label: 'Добыча' }]; p.delete('structure_industries'); assert.deepEqual(economicsFilters(c, p).structure_industries, ['Добыча']);
});
test('salary quarters use available region/mode options; months remain full calendar options', () => {
  const c = fixture.catalog('salary'); c.quarterOptionsByRegionMode[fixture.RF].ytd = [1, 2];
  const selected = economicsFilters(c, new URLSearchParams({ region: fixture.RF, ytd: 'true', period: 'quarter', quarters: '[1,4]' }));
  assert.deepEqual(selected.quarters, [1]); assert.deepEqual(economicsOptions(c, 'quarters', selected).map(o => o.id), [1, 2]); assert.equal(economicsOptions(c, 'months', selected).length, 12);
});
test('URL reconciliation changes only explicitly supplied filters and retains table/search keys', () => {
  const c = fixture.catalog('salary'), p = new URLSearchParams({ region: 'deleted', views: '[]', months: '[1,13]', period: 'week', econ_salary_month_page: '3', unrelated: 'keep' });
  const selected = economicsFilters(c, p), repairs = economicsRepairs(c, p, selected);
  assert.deepEqual(repairs, { period: 'month', region: fixture.MSK, months: '[1]' });
  assert.deepEqual(economicsRepairs(c, new URLSearchParams(), c.defaults), {});
  const key = economicsFilterKey('salary', p); p.set('econ_salary_month_page', '4'); assert.equal(economicsFilterKey('salary', p), key); p.set('ytd', 'true'); assert.notEqual(economicsFilterKey('salary', p), key);
});
test('API URLs repeat exact filter lists, carry independent accounts blocks and pin export version', () => {
  for (const family of families) {
    const selected = fixture.catalog(family).defaults, u = new URL(economicsUrl(family, selected, 'v +1'), 'http://localhost');
    assert.equal(u.pathname, `/api/v1/economics/${family}/export`); assert.equal(u.searchParams.get('required_version'), 'v +1');
    for (const [key, value] of Object.entries(selected)) assert.deepEqual(u.searchParams.getAll(key), Array.isArray(value) ? value.length ? value.map(String) : [''] : [String(value)]);
  }
  const selected = economicsFilters(fixture.catalog('accounts'), new URLSearchParams({ block1_regions: '[]', structure_industries: '[]' }));
  const u = new URL(economicsUrl('accounts', selected), 'http://localhost'); assert.equal(u.searchParams.get('block1_regions'), ''); assert.equal(u.searchParams.get('structure_industries'), '');
  assert.equal(economicsUrl('ipc'), '/api/v1/economics/ipc/catalog');
});
test('full tables and CSV rows ignore search/pagination and preserve nulls and source precision', () => {
  const report = fixture.report('salary'), chart = report.charts[0];
  assert.equal(chart.rows.length, 384); assert.equal(economicsTablePage(chart, '', '2', '20').shown.length, 20);
  assert.equal(economicsTablePage(chart, 'Строительство', '999', '20').current, 10); assert.equal(economicsTablePage(chart, 'never', '12', '100').current, 1);
  assert.equal(economicsTablePage(chart, '', 'NaN', '7').pageSize, 50);
  assert.equal(economicsTableRows(chart).length, 385); assert.equal(economicsChartRows(chart).length, 385);
  assert.ok(economicsTableRows(chart).flat().includes(12345.678)); assert.equal(chart.rows[0]['значение_руб'], null); assert.equal(chart.rows[1]['значение_руб'], 0);
});
test('chronological chart alignment retains duplicate occurrences, missing periods and exact century labels', () => {
  const chart = fixture.report('salary').charts[0]; chart.series = [
    { id: 'a', name: 'Строительство', unit: 'руб.', points: [{ x: 'дек 11', y: 4 }, { x: 'дек 11', y: null }, { x: 'янв 12', y: 5 }] },
    { id: 'b', name: 'Всего', unit: 'руб.', points: [{ x: 'янв 11', y: 0 }, { x: 'дек 11', y: 10 }] },
  ]; chart.rows = [{ year: 2012, month: 1, 'период': 'янв 12' }, { year: 2011, month: 12, 'период': 'дек 11' }, { year: 2011, month: 1, 'период': 'янв 11' }];
  const option = economicsChartOption(chart, 'dark');
  assert.deepEqual(option.xAxis.data, ['янв 11', 'дек 11', 'дек 11', 'янв 12']); assert.deepEqual(option.series[0].data, [null, 4, null, 5]); assert.deepEqual(option.series[1].data, [0, 10, null, null]);
  assert.ok(option.series.every(s => s.connectNulls === false)); assert.equal(economicsChartRows(chart).length, 6);
});
test('quarter labels sort by exact year/quarter rows, not alphabetical Roman numeral order', () => {
  const s = fixture.catalog('salary').defaults; s.period = 'quarter'; const chart = fixture.report('salary', s).charts[0];
  chart.series[0].points.reverse(); const option = economicsChartOption(chart, 'light');
  assert.deepEqual(option.xAxis.data.slice(0, 4), ['I кв 11', 'II кв 11', 'III кв 11', 'IV кв 11']);
});
test('structure stacks exact values; segment tooltip identifies hovered industry, unit and total safely', () => {
  const chart = fixture.report('accounts').charts.find(c => c.id === 'na_block5'), option = economicsChartOption(chart, 'dark');
  assert.ok(option.series.every(s => s.stack === 'structure')); assert.equal(option.tooltip.trigger, 'item'); assert.equal(option.series[0].data[0], 1.234567);
  const text = option.tooltip.formatter({ seriesIndex: 0, seriesName: '<script>', name: '2011', value: 1.234567, dataIndex: 0 });
  assert.ok(text.includes('&lt;script&gt;') && text.includes('1,23 трлн руб') && text.includes('Всего:'));
  assert.equal(option.series[1].itemStyle.color, '#9ca5b1');
});
test('line and bar hovers are segment-specific, values are not scaled by frontend, units and colors stay stable', () => {
  for (const family of families) for (const theme of ['dark', 'light']) {
    const chart = fixture.report(family).charts[0], option = economicsChartOption(chart, theme);
    assert.equal(option.tooltip.trigger, 'item'); assert.equal(option.yAxis.name, chart.unit);
    assert.deepEqual(option.series[0].data, chart.series[0].points.map(p => p.y));
    assert.equal(option.series[0].itemStyle.color, economicsChartOption({ ...chart, series: [chart.series[0]] }, theme).series[0].itemStyle.color);
  }
});
test('industry colors avoid collisions with construction, totals and residuals in either theme', () => {
  const chart = fixture.report('accounts').charts.find(c => c.id === 'na_block5');
  chart.series = ['Строительство', 'Транспортировка и хранение', 'Деятельность профессиональная, научная и техническая', 'Остальные отрасли', 'Всего'].map((name, i) => ({ id: String(i), name, unit: chart.unit, points: [{ x: 2011, y: i }] }));
  for (const theme of ['light', 'dark']) {
    const option = economicsChartOption(chart, theme), colors = option.series.map(s => s.itemStyle.color);
    assert.equal(new Set(colors).size, chart.series.length);
    const reversed = economicsChartOption({ ...chart, series: [...chart.series].reverse() }, theme);
    assert.deepEqual(reversed.series.map(s => s.itemStyle.color).reverse(), colors);
  }
});

test('malformed schemas, source metadata, numeric values, scope echoes and duplicate IDs reject', () => {
  for (const family of families) {
    for (const update of [c => c.schemaVersion = 2, c => c.family = 'wrong', c => c.generatedAt = 'bad', c => c.controls.pop(), c => c.source.provenance.rows[0]['Статус'] = true]) {
      const c = fixture.catalog(family); update(c); assert.throws(() => readEconomicsCatalog(c, family));
    }
    for (const update of [d => d.version = '', d => d.charts[0].series[0].points[0].y = Infinity, d => d.charts[0].stack = 'true', d => d.charts.push(d.charts[0]), d => d.tables[0].rows[0]['Период'] = false, d => d.charts[0].columns.push(d.charts[0].columns[0])]) {
      const d = fixture.report(family); update(d); assert.throws(() => readEconomicsReport(d, family));
    }
  }
  assert.throws(() => readEconomicsReport(fixture.report('salary'), 'ipc'));
  assert.throws(() => readEconomicsReport(fixture.report('salary'), 'salary', { ...fixture.catalog('salary').defaults, region: fixture.RF }));
});
test('empty selections and missing dates stay empty without substituting fallback observations', () => {
  const c = fixture.catalog('salary'), selected = { ...c.defaults, views: [] }, raw = fixture.report('salary', selected); raw.source.date = null;
  const data = readEconomicsReport(raw, 'salary', selected); assert.deepEqual(data.charts, []); assert.deepEqual(data.tables, []); assert.equal(data.source.date, null);
});
test('loaders forward abort signals and no-store; failures never request frozen source data', async () => {
  const original = globalThis.fetch, controller = new AbortController(), calls = [];
  try {
    globalThis.fetch = async (url, options) => { calls.push([url, options]); return new Response(JSON.stringify(url.endsWith('/catalog') ? fixture.catalog('salary') : fixture.report('salary'))); };
    await loadEconomicsCatalog('salary', controller.signal); await loadEconomicsReport('salary', fixture.catalog('salary').defaults, controller.signal);
    assert.ok(calls.every(([url, options]) => url.startsWith('/api/v1/economics/salary') && options.signal === controller.signal && options.cache === 'no-store'));
    globalThis.fetch = async () => new Response('offline', { status: 503 }); await assert.rejects(loadEconomicsCatalog('salary', controller.signal), /HTTP 503/);
  } finally { globalThis.fetch = original; }
});
test('404 triggers one catalog refresh, removes unavailable region, and retries new exact selection', async () => {
  const original = globalThis.fetch, controller = new AbortController(), calls = [], old = fixture.catalog('salary');
  try {
    const refreshed = fixture.catalog('salary', 'v2'); refreshed.regions = refreshed.regions.filter(r => r.id === fixture.RF); refreshed.controls.find(c => c.id === 'region').options = refreshed.regions; refreshed.defaults.region = fixture.RF;
    globalThis.fetch = async url => {
      calls.push(url); if (url.endsWith('/catalog')) return new Response(JSON.stringify(refreshed));
      if (new URL(url, 'http://localhost').searchParams.get('region') === fixture.MSK) return new Response('removed', { status: 404 });
      return new Response(JSON.stringify(fixture.report('salary', { ...refreshed.defaults, region: fixture.RF }, 'v2')));
    };
    const result = await loadEconomicsPage('salary', new URLSearchParams({ region: fixture.MSK }), controller.signal, old);
    assert.equal(result.selection.region, fixture.RF); assert.equal(result.data.version, 'v2'); assert.equal(calls.filter(u => u.endsWith('/catalog')).length, 1);
  } finally { globalThis.fetch = original; }
});
test('report/catalog version mismatch refreshes once, while persistent mismatch and 404 terminate', async () => {
  const original = globalThis.fetch, controller = new AbortController();
  try {
    let refreshes = 0;
    globalThis.fetch = async url => { if (url.endsWith('/catalog')) { refreshes++; return new Response(JSON.stringify(fixture.catalog('ipc', 'v2'))); } return new Response(JSON.stringify(fixture.report('ipc', fixture.catalog('ipc').defaults, 'v2'))); };
    const result = await loadEconomicsPage('ipc', new URLSearchParams(), controller.signal, fixture.catalog('ipc', 'v1')); assert.equal(result.data.version, 'v2'); assert.equal(refreshes, 1);
    refreshes = 0; globalThis.fetch = async url => { if (url.endsWith('/catalog')) { refreshes++; return new Response(JSON.stringify(fixture.catalog('ipc', 'v1'))); } return new Response(JSON.stringify(fixture.report('ipc', fixture.catalog('ipc').defaults, 'v2'))); };
    await assert.rejects(loadEconomicsPage('ipc', new URLSearchParams(), controller.signal, fixture.catalog('ipc', 'v1')), /разные версии/); assert.equal(refreshes, 1);
    refreshes = 0; globalThis.fetch = async url => { if (url.endsWith('/catalog')) { refreshes++; return new Response(JSON.stringify(fixture.catalog('ipc'))); } return new Response('removed', { status: 404 }); };
    await assert.rejects(loadEconomicsPage('ipc', new URLSearchParams(), controller.signal, fixture.catalog('ipc')), /HTTP 404/); assert.equal(refreshes, 1);
  } finally { globalThis.fetch = original; }
});
test('aborted delayed response cannot be published and does not trigger catalog recovery', async () => {
  const original = globalThis.fetch, controller = new AbortController();
  try {
    let release, reads = 0; const gate = new Promise(resolve => release = resolve);
    globalThis.fetch = async () => { reads++; await gate; return new Response(JSON.stringify(fixture.report('salary'))); };
    const result = loadEconomicsPage('salary', new URLSearchParams(), controller.signal, fixture.catalog('salary')); controller.abort(); release(); await assert.rejects(result, /Aborted/); assert.equal(reads, 1);
  } finally { globalThis.fetch = original; }
});
