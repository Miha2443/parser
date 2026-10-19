import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { ApiError, request } from './data.ts';
import { permitChartOption } from './annualConstructionData.ts';
import { breakdownChart, breakdownOption, constructionHistoryChart, constructionHistoryRows, constructionRegion, domRfForecastSelection, domRfUrl, forecastBasisNote, forecastFields, legacySalesForecastSeed, loadDomRfData, loadPermitsData, loadPermitsSelection, observedRange, permitKindChange, permitRequestFilters, permitsMainOption, permitsMainRows, permitsUrl, readDomRfData, readPermitsData, readinessChartOption, realizationDonutOption, realizationHistoryOption, realizationHistoryRows } from './constructionPagesData.ts';

const metadata = () => ({ schemaVersion: 1, version: 'unit-test + version', generatedAt: '2026-10-19T08:00:00.123456+03:00', source: { date: '2026-10-16', files: ['selected/source.xlsx'], fileEvidence: 'selected', issues: [{ level: 'warning', message: 'Source warning <not markup>' }] } });
function block(id, fields) {
  const rows = [{ year: 2011, ...Object.fromEntries(fields.map((field, i) => [field, i === 0 ? 0 : i === 1 ? null : 1234.567890123])) }, { year: 2026, ...Object.fromEntries(fields.map((field, i) => [field, 1000 + i])) }];
  return { id, title: id, unit: 'тыс. м²', periodLabel: 'Январь–сентябрь', columns: [{ id: 'year', label: 'Год' }, ...fields.map(id => ({ id, label: id }))], rows, series: fields.map(id => ({ id, name: id === 'mop_embedded' ? 'МОП и встроенное нежильё' : id, points: rows.map(row => ({ x: String(row.year), y: row[id] })) })), notes: ['Independent historical basis, not equal to monthly main report.'] };
}
const breakdowns = () => [block('housing_components', ['living', 'mop_embedded', 'housing_total']), block('nonresidential_historical', ['nonresidential', 'offices', 'industry', 'social', 'trade', 'hotels', 'other']), block('nonresidential_native', ['nonresidential', 'native_a', 'native_b', 'unknown']), block('area_composition', ['living', 'mop_embedded', 'nonresidential', 'total'])];
function permitsFixture() {
  const rows = [{ 'Год': 2011, 'За год, тыс. м²': 1234.567890123, 'С начала года, тыс. м²': null, 'Изменение, %': null }, { 'Год': 2026, 'За год, тыс. м²': null, 'С начала года, тыс. м²': 0, 'Изменение, %': -100 }];
  return { ...metadata(), selection: { permitKind: 'housing', month: 9 }, controls: { permitKinds: [{ id: 'housing', label: 'Жильё' }, { id: 'nonresidential', label: 'Нежильё' }, { id: 'total', label: 'Всего' }], monthsByKind: { housing: [1, 9], nonresidential: [9], total: [1, 9] } }, permits: { region: 'Москва', unit: 'тыс. м²', periodLabel: 'Январь–сентябрь', columns: Object.keys(rows[0]).map(id => ({ id, label: id })), rows, chart: { x: ['2011', '2026'], period: [{ value: null }, { value: 0 }], remainder: [{ value: 1234.567890123 }, { value: null }], totals: [{ value: 1234.567890123 }, { value: 0 }], growth: [{ value: null }, { value: -100 }] } }, forecast: { unit: 'тыс. м²', unitBasis: 'assumed_thousand_m2', source: { file: 'selected/forecast.xlsx', sheet: 'Прогнозы' }, rows: [2026, 2027].map((year, i) => ({ year, ...Object.fromEntries(forecastFields.map((f, j) => [f, i ? null : j === 0 ? 9876.543210123 : j === 1 ? 0 : null])), cells: { year: 'D2', housing_living: 'D3' } })), notes: ['No explicit unit header.'] }, breakdowns: breakdowns(), notes: ['Main notes'] };
}
function domrfFixture(region = 'msk') {
  const metrics = { total_living_thousand_m2: 10001.123456789, sales_open_thousand_m2: 7500.25, sold_thousand_m2: 1234.567890123, sold_pct: 12, unsold_thousand_m2: 0, unsold_pct: 0, sales_not_open_thousand_m2: null, sales_not_open_pct: null, price_per_m2_rub: 441306.789, funds_million_rub: 3250869.123 };
  return { ...metadata(), region: { id: region, label: region }, construction: { period: { id: '2026-10', label: 'Октябрь 2026', reportDate: '2026-10-01' }, metrics: { living: 15000.123456789, total: 0 }, history: [{ period: '2026-10', label: 'Октябрь 2026', reportDate: '2026-10-01', living: 15000.123456789, total: 0, sourceFile: 'construction.json' }] }, sales: { period: { id: '2026-08', label: 'Август 2026', reportDate: '2026-09-11' }, metrics, history: [{ period: '2026-08', label: 'Август 2026', reportDate: '2026-09-11', metrics: { ...metrics }, sourceFile: 'sales.json' }] }, notes: ['Independent construction/sales reporting periods.'] };
}

test('new metadata accepts backend issue objects and legacy pairs, selected evidence is optional', () => {
  for (const raw of [permitsFixture(), domrfFixture()]) {
    const read = 'permits' in raw ? readPermitsData : value => readDomRfData(value, 'msk');
    assert.deepEqual(read(raw).source.issues, [['warning', 'Source warning <not markup>']]);
    raw.source.issues = [['info', 'tuple']];
    assert.deepEqual(read(raw).source.issues, raw.source.issues);
    delete raw.source.fileEvidence; assert.equal(read(raw).source.fileEvidence, undefined);
    raw.source.issues = [{ level: 'warning', message: 1 }]; assert.throws(() => read(raw));
    raw.source.issues = [['warning', 'message', 'extra']]; assert.throws(() => read(raw));
  }
});

test('permits schema preserves raw values, null/zero, cells and independent breakdown provenance', () => {
  const raw = permitsFixture(), before = structuredClone(raw), data = readPermitsData(raw, raw.selection);
  assert.deepEqual(raw, before);
  assert.deepEqual(data.permits.chart, raw.permits.chart);
  assert.deepEqual(data.permits.rows, raw.permits.rows);
  assert.deepEqual(data.forecast, raw.forecast);
  assert.deepEqual(data.breakdowns, raw.breakdowns);
  assert.equal(data.forecast.rows[0].housing_living, 0);
  assert.equal(data.forecast.rows[1].value, null);
  assert.equal(data.permits.chart.period[1].value, 0);
  assert.deepEqual(data.controls.permitKinds.map(k => k.id), ['housing', 'nonresidential', 'total']);
});

test('permits schema rejects malformed controls, scope, units and nonfinite/missing numeric fields', () => {
  assert.throws(() => readPermitsData(permitsFixture(), { permitKind: 'total', month: 9 }));
  assert.throws(() => readPermitsData(permitsFixture(), { permitKind: 'housing', month: 1 }));
  for (const edit of [r => r.schemaVersion = 2, r => r.version = '', r => r.source.date = '2026-02-30', r => r.generatedAt = '2026-10-19', r => r.source.fileEvidence = 'inferred', r => r.controls.permitKinds.reverse(), r => r.controls.monthsByKind.housing.push(9), r => r.selection.month = 13, r => r.forecast.unitBasis = 'verified', r => r.forecast.unit = 'млн м²', r => r.forecast.rows[0].value = Infinity, r => delete r.forecast.rows[0].other, r => r.forecast.rows.push(r.forecast.rows[0]), r => r.permits.region = 'РФ', r => r.permits.chart.period.pop(), r => r.permits.chart.x[0] = '2012', r => r.permits.rows[0]['За год, тыс. м²'] = true, r => r.breakdowns[0].rows[0].living = '5', r => r.breakdowns[0].series[0].points[0].y = 2, r => r.breakdowns[0].columns.push(r.breakdowns[0].columns[0])]) {
    const raw = permitsFixture(); edit(raw); assert.throws(() => readPermitsData(raw));
  }
});

test('separate annual forecast category leaves actuals, totals, growth and old hover unchanged', () => {
  const data = readPermitsData(permitsFixture()), before = structuredClone(data), old = permitChartOption(data.permits, 'light'), option = permitsMainOption(data, 'light');
  assert.deepEqual(data, before);
  assert.deepEqual(option.xAxis.data, ['2011', '2026', '2026 · прогноз']);
  assert.equal(option.xAxis.data.includes('2027 · прогноз'), false);
  assert.deepEqual(option.series[0].data, [...old.series[0].data, null]);
  assert.deepEqual(option.series[1].data, [...old.series[1].data, null]);
  assert.deepEqual(option.series[2].data, [null, null, 9876.543210123]);
  assert.equal(option.series[2].stack, undefined);
  assert.equal(option.series[2].itemStyle.color, 'transparent');
  assert.equal(option.series[2].itemStyle.borderType, 'dashed');
  const point = { name: '2026', seriesName: 'Факт', value: 0, dataIndex: 1, seriesIndex: 0 };
  assert.equal(option.tooltip.formatter(point), old.tooltip.formatter(point));
  const tooltip = option.tooltip.formatter({ ...point, name: '<2026> · прогноз', value: 9876.543210123, dataIndex: 2, seriesIndex: 2 });
  assert.ok(tooltip.includes('assumed_thousand_m2') && tooltip.includes('нет явного заголовка') && tooltip.includes('&lt;2026&gt;'));
  assert.ok(forecastBasisNote.includes('не пересчитывается'));
  data.selection.month = 1;
  assert.equal(permitsMainOption(data, 'dark').series[2].data.at(-1), 9876.543210123);
  data.forecast.rows[0].value = 0;
  assert.equal(permitsMainOption(data, 'light').series[2].data.at(-1), 0);
  data.forecast.rows[0].value = null;
  assert.equal(permitsMainOption(data, 'light').series.length, 2);
});

test('permit CSV retains all actual fields and all forecast raw fields including empty years', () => {
  const data = readPermitsData(permitsFixture()), rows = permitsMainRows(data);
  assert.equal(rows.length, 1 + 2 * 5 + 2 * forecastFields.length);
  assert.ok(rows.some(row => row[0] === 'Факт' && row[2] === 'Исходное значение периода' && row[3] === null));
  assert.ok(rows.some(row => row[0] === 'Прогноз' && row[1] === 2027 && row[2] === 'value' && row[3] === null));
  assert.ok(rows.some(row => row[2] === 'housing_living' && row[3] === 0 && row[5] === 'assumed_thousand_m2'));
});

test('breakdown stacks exclude aggregate series even if a provider includes them; source rows remain complete', () => {
  const raw = breakdowns(), expected = [['living', 'mop_embedded'], ['offices', 'industry', 'social', 'trade', 'hotels', 'other'], ['native_a', 'native_b', 'unknown'], ['living', 'mop_embedded', 'nonresidential']];
  raw.forEach((b, i) => {
    const before = structuredClone(b), chart = breakdownChart(b), option = breakdownOption(b, 'light');
    assert.deepEqual(chart.series.map(s => s.id), expected[i]);
    assert.equal(option.series.length, expected[i].length);
    assert.ok(option.series.every(s => s.stack === b.id));
    assert.deepEqual(b, before);
    assert.ok(b.columns.length > chart.series.length);
    assert.equal(option.tooltip.trigger, 'item');
    assert.ok(option.tooltip.formatter({ seriesIndex: 0, name: '2011', seriesName: '<test>', value: 1 }).includes('тыс. м²'));
  });
});

test('DOM.RF parser keeps region, source units, independent dates and only observed periods', () => {
  for (const region of ['msk', 'rf']) {
    const raw = domrfFixture(region), data = readDomRfData(raw, region);
    assert.deepEqual(data.construction, raw.construction); assert.deepEqual(data.sales, raw.sales);
    assert.equal(data.construction.metrics.living, 15000.123456789); assert.equal(data.construction.metrics.total, 0);
    assert.equal(data.sales.metrics.price_per_m2_rub, 441306.789); assert.equal(data.sales.metrics.funds_million_rub, 3250869.123);
    assert.equal(data.construction.period.id, '2026-10'); assert.equal(data.sales.period.id, '2026-08'); assert.equal(data.sales.period.reportDate, '2026-09-11');
    assert.equal(data.construction.history.length, 1); assert.equal(data.sales.history.length, 1);
  }
  assert.throws(() => readDomRfData(domrfFixture(), 'rf'));
});

test('DOM.RF rejects invalid history, dates, coerced numbers and scope without rejecting literal out-of-range percentages', () => {
  for (const edit of [r => r.sales.period.id = '2026-13', r => r.construction.metrics.total = undefined, r => r.sales.metrics.sold_pct = '47', r => r.sales.history[0].metrics.sold_thousand_m2 = NaN, r => r.construction.history[0].reportDate = '2026-02-30', r => r.construction.history.push(r.construction.history[0]), r => r.sales.history[0].period = '2026-00', r => delete r.construction.history[0].sourceFile, r => r.sales.metrics = []]) {
    const raw = domrfFixture(); edit(raw); assert.throws(() => readDomRfData(raw, 'msk'));
  }
  const raw = domrfFixture(); raw.sales.metrics.sold_pct = 120;
  assert.equal(readDomRfData(raw, 'msk').sales.metrics.sold_pct, 120);
  raw.sales.metrics = null; raw.sales.history = []; raw.sales.period = null; raw.construction.history = []; raw.construction.period = null; raw.construction.metrics = { living: null, total: null };
  assert.deepEqual(readDomRfData(raw, 'msk').sales, raw.sales);
});

test('DOM.RF history charts retain one real point, correct scaling, zero/null and full source-unit CSV', () => {
  const raw = domrfFixture(), chart = constructionHistoryChart(raw.construction.history), rows = constructionHistoryRows(raw.construction.history), option = realizationHistoryOption(raw.sales.history, 'light');
  assert.deepEqual(chart.series[0].points, [{ x: '2026-10', y: 15000.123456789 / 1000 }]);
  assert.deepEqual(chart.series[1].points, [{ x: '2026-10', y: 0 }]);
  assert.equal(rows[1][3], 15000.123456789); assert.ok(rows[0][3].includes('тыс. м²'));
  assert.deepEqual(option.xAxis.data, ['2026-08']); assert.equal(option.yAxis.name, 'млн м²');
  assert.deepEqual(option.series.map(s => s.data), [[1234.567890123 / 1000], [0], [null]]);
  assert.ok(option.series.every(s => s.stack === 'realization'));
  assert.ok(option.series[0].label.formatter({ dataIndex: 0 }).includes('12'));
  assert.equal(option.series[1].label.formatter({ dataIndex: 0 }), '');
  assert.equal(option.series[2].label.formatter({ dataIndex: 0 }), '');
  const tip = option.tooltip.formatter({ dataIndex: 0, seriesIndex: 0 });
  assert.ok(tip.includes('Доля источника: 12 %') && tip.includes('тыс. м²') && tip.includes('млн м²') && tip.includes('2026-09-11'));
  const missing = option.tooltip.formatter({ dataIndex: 0, seriesIndex: 2 }); assert.ok(!missing.includes('Доля источника'));
  const csv = realizationHistoryRows(raw.sales.history); assert.equal(csv.length, 2);
  for (const [key, value] of Object.entries(raw.sales.metrics)) assert.equal(csv[1][csv[0].indexOf(key)], value);
});

test('observed range controls select existing periods only and never fabricate gap months', () => {
  const history = [{ period: '2026-10', label: 'October' }, { period: '2026-01', label: 'January' }, { period: '2026-08', label: 'August' }], before = structuredClone(history);
  assert.deepEqual(observedRange(history, '2026-02', null).rows.map(h => h.period), ['2026-01', '2026-08', '2026-10']);
  assert.deepEqual(observedRange(history, '2026-10', '2026-08').rows.map(h => h.period), ['2026-08', '2026-10']);
  assert.deepEqual(history, before);
  assert.deepEqual(observedRange([], null, null), { from: '', to: '', rows: [] });
});

test('donuts use source percentages and never invent a real remainder for invalid percentages', () => {
  for (const pct of [0, 47.234, 100]) {
    const option = realizationDonutOption('<Sold>', pct, 1234.567890123, '#2c9869', 'light');
    assert.equal(option.series[0].data[0].value, pct); assert.equal(option.series[0].data[1].value, 100 - pct);
    assert.ok(option.tooltip.formatter().includes('&lt;Sold&gt;'));
  }
  for (const pct of [null, -1, 120]) assert.deepEqual(realizationDonutOption('Sold', pct, 1000, '#2c9869', 'dark').series[0].data, []);
});

test('readiness percentage axis uses observed extent, allows >100, and retains all three series', () => {
  const chart = { id: 'monthly_percent', title: 'Readiness', kind: 'line', unit: '%', series: [45, 52, 125].map((n, i) => ({ id: String(i), name: String(i), unit: '%', points: [{ x: '2026-01', y: n }, { x: '2026-03', y: n + 1 }] })) }, option = readinessChartOption(chart, 'light');
  assert.equal(option.yAxis.scale, true); assert.equal(option.yAxis.min, undefined); assert.equal(option.yAxis.max, undefined);
  assert.equal(option.series.length, 3); assert.equal(option.series[2].data[1], 126);
  assert.ok(option.series.every(s => s.connectNulls === false));
});

test('canonical request filters and version-pinned exports include only the correct API scope', () => {
  assert.deepEqual(permitRequestFilters(new URLSearchParams('region=rf&permit_kind=bad&month=99')), { permitKind: 'housing' });
  assert.deepEqual(permitRequestFilters(new URLSearchParams('permit_kind=nonresidential&month=12&table=unused')), { permitKind: 'nonresidential', month: 12 });
  assert.equal(constructionRegion(new URLSearchParams('region=rf')), 'rf'); assert.equal(constructionRegion(new URLSearchParams('region=invalid')), 'msk');
  const a = new URL(permitsUrl({ permitKind: 'total', month: 9 }, 'v +1'), 'http://localhost');
  assert.equal(a.pathname, '/api/v1/construction/permits/export'); assert.deepEqual([...a.searchParams], [['permit_kind', 'total'], ['month', '9'], ['required_version', 'v +1']]);
  const b = new URL(domRfUrl('rf', 'v +2'), 'http://localhost');
  assert.equal(b.pathname, '/api/v1/construction/domrf/export'); assert.deepEqual([...b.searchParams], [['region', 'rf'], ['required_version', 'v +2']]);
  assert.throws(() => permitsUrl({ permitKind: 'housing' }, 'v')); assert.throws(() => domRfUrl('msk', ''));
});

test('loaders use actual new APIs with abort signals, reject scope mismatch, 409 and unavailable sources without fallback', async () => {
  const original = globalThis.fetch, controller = new AbortController(), calls = [];
  try {
    globalThis.fetch = async (url, options) => { calls.push([url, options]); return new Response(JSON.stringify(url.includes('/permits') ? permitsFixture() : domrfFixture('rf'))); };
    await loadPermitsData({ permitKind: 'housing', month: 9 }, controller.signal); await loadDomRfData('rf', controller.signal);
    assert.deepEqual(calls.map(c => c[0]), ['/api/v1/construction/permits?permit_kind=housing&month=9', '/api/v1/construction/domrf?region=rf']);
    assert.ok(calls.every(c => c[1].signal === controller.signal && c[1].cache === 'no-store'));
    await assert.rejects(loadDomRfData('msk', controller.signal), /схеме/);
    globalThis.fetch = async () => new Response('mismatch', { status: 409 });
    await assert.rejects(request(domRfUrl('rf', 'old'), controller.signal), e => e instanceof ApiError && e.status === 409);
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: { code: 'source_unavailable', reason: 'Actual source is unavailable' } }), { status: 503 });
    await assert.rejects(loadPermitsData({ permitKind: 'housing' }, controller.signal), /Actual source is unavailable/);
    globalThis.fetch = async () => { throw new Error('offline'); };
    await assert.rejects(loadDomRfData('rf', controller.signal), /серверу данных/);
  } finally { globalThis.fetch = original; }
});

test('permit 404 reconciliation discovers the current default once and preserves selection/query scope', async () => {
  const original = globalThis.fetch, controller = new AbortController(), calls = [], params = new URLSearchParams('permit_kind=housing&month=12&table=kept&note=a%2Bb');
  try {
    const raw = permitsFixture(); raw.version = 'new-generation';
    globalThis.fetch = async (url, options) => { calls.push([url, options]); return calls.length === 1 ? new Response('month unavailable', { status: 404 }) : new Response(JSON.stringify(raw)); };
    const data = await loadPermitsSelection(permitRequestFilters(params), controller.signal);
    assert.equal(data.selection.month, 9); assert.equal(data.version, 'new-generation');
    assert.deepEqual(calls.map(c => c[0]), ['/api/v1/construction/permits?permit_kind=housing&month=12', '/api/v1/construction/permits?permit_kind=housing']);
    assert.ok(calls.every(c => c[1].signal === controller.signal && c[1].cache === 'no-store'));
    assert.equal(params.get('month'), '12');
    params.set('month', String(data.selection.month));
    assert.equal(params.get('table'), 'kept'); assert.equal(params.get('note'), 'a+b');
    assert.equal(permitsUrl(data.selection), '/api/v1/construction/permits?permit_kind=housing&month=9');
  } finally { globalThis.fetch = original; }
});

test('persistent permit 404 stops after one discovery request, and default discovery is never recursively retried', async () => {
  const original = globalThis.fetch, controller = new AbortController(), calls = [];
  try {
    globalThis.fetch = async url => { calls.push(url); return new Response('unavailable', { status: 404 }); };
    await assert.rejects(loadPermitsSelection({ permitKind: 'housing', month: 12 }, controller.signal), e => e instanceof ApiError && e.status === 404);
    assert.equal(calls.length, 2); assert.equal(calls[1], '/api/v1/construction/permits?permit_kind=housing');
    calls.length = 0;
    await assert.rejects(loadPermitsSelection({ permitKind: 'total' }, controller.signal), e => e instanceof ApiError && e.status === 404);
    assert.deepEqual(calls, ['/api/v1/construction/permits?permit_kind=total']);
  } finally { globalThis.fetch = original; }
});

test('permit recovery never masks non-404 HTTP, network, schema or abort failures', async () => {
  const original = globalThis.fetch;
  try {
    for (const fail of [() => new Response('unavailable', { status: 503 }), () => new Response('conflict', { status: 409 }), () => { throw new Error('offline'); }, () => new Response('{}')]) {
      let calls = 0; const controller = new AbortController();
      globalThis.fetch = async () => { calls++; return fail(); };
      await assert.rejects(loadPermitsSelection({ permitKind: 'housing', month: 12 }, controller.signal));
      assert.equal(calls, 1);
    }
    let calls = 0; const controller = new AbortController();
    globalThis.fetch = async () => { calls++; controller.abort(); return new Response('not found', { status: 404 }); };
    await assert.rejects(loadPermitsSelection({ permitKind: 'housing', month: 12 }, controller.signal));
    assert.equal(calls, 1);
  } finally { globalThis.fetch = original; }
});

test('changing permit kind retains January when supported and only clears an unavailable month', () => {
  const controls = permitsFixture().controls;
  controls.monthsByKind.nonresidential = [1, 9];
  for (const kind of ['housing', 'nonresidential', 'total']) assert.deepEqual(permitKindChange(controls, 1, kind), { permit_kind: kind, month: '1' });
  controls.monthsByKind.nonresidential = [9];
  assert.deepEqual(permitKindChange(controls, 1, 'nonresidential'), { permit_kind: 'nonresidential', month: null });
  assert.deepEqual(permitKindChange(controls, 9, 'nonresidential'), { permit_kind: 'nonresidential', month: '9' });
});

test('legacy /sales seeds forecast/table/export scope once, then periods remain independent', () => {
  const params = new URLSearchParams('region=msk&period=2026-01&table=by_class&salesby_classPage=2'), before = params.toString();
  let seed = legacySalesForecastSeed(true, params);
  assert.deepEqual(seed, { region: 'msk', period: '2026-01' });
  assert.equal(domRfForecastSelection(params, seed).period, '2026-01'); assert.equal(params.toString(), before);
  params.set('forecast_period', '2026-01');
  let selection = domRfForecastSelection(params, seed); seed = selection.seed;
  assert.equal(seed, null); assert.equal(selection.period, '2026-01');
  params.set('period', '2026-03');
  assert.equal(domRfForecastSelection(params, seed).period, '2026-01');
  params.set('forecast_period', '2026-05');
  assert.equal(domRfForecastSelection(params, seed).period, '2026-05');
  params.delete('forecast_period');
  assert.equal(domRfForecastSelection(params, seed).period, null);
  assert.equal(params.get('table'), 'by_class'); assert.equal(params.get('salesby_classPage'), '2');
  const normal = new URLSearchParams('region=msk&period=2026-01');
  assert.equal(legacySalesForecastSeed(false, normal), null); assert.equal(domRfForecastSelection(normal, null).period, null);
  normal.set('forecast_period', '2026-05');
  assert.equal(legacySalesForecastSeed(true, normal), null); assert.equal(domRfForecastSelection(normal, null).period, '2026-05');
  selection = domRfForecastSelection(new URLSearchParams('region=rf&period=2026-01'), legacySalesForecastSeed(true, params));
  assert.equal(selection.period, null); assert.equal(selection.seed, null);
  assert.equal(legacySalesForecastSeed(true, new URLSearchParams('period=2026-99')), null);
});

test('page ownership exposes QA sections and closed segment/source disclosures without replacing legacy Sales', () => {
  const permits = readFileSync(new URL('./PermitsPage.tsx', import.meta.url), 'utf8'), domrf = readFileSync(new URL('./DomRfPage.tsx', import.meta.url), 'utf8');
  for (const cls of ['permit-main', 'permit-breakdowns']) assert.ok(permits.includes(cls));
  for (const cls of ['domrf-construction', 'domrf-readiness', 'domrf-realization', 'domrf-forecast', 'domrf-segments']) assert.ok(domrf.includes(cls));
  assert.ok(domrf.includes('monthly_volume') && domrf.includes('monthly_percent') && domrf.includes('forecast_period'));
  assert.ok(!domrf.includes('<details open') && !permits.includes('<details open'));
  assert.ok(!domrf.includes('localStorage') && !permits.includes('localStorage'));
  assert.ok(permits.includes('controller.abort()') && permits.includes('controller.signal.aborted'));
});
