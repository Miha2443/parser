import assert from 'node:assert/strict';
import test from 'node:test';
import fixtures from './annualConstruction.browser.cjs';
import { annualChartOption, annualChartRows, annualFactCaption, annualFilters, annualSummaryRows, constructionFilters, loadMarketCatalog, loadMarketReport, marketParams, marketUrl, permitChartOption, permitChartRows, readAnnualCatalog, readAnnualReport, readConstructionCatalog, readConstructionReport, salesTable, tableRows } from './annualConstructionData.ts';
const annualSelection = { region: 'msk' }, constructionSelection = { region: 'msk', permitKind: 'total', month: 3 };
test('annual regions and construction month defaults remain catalog-defined and region-independent', () => {
  const a = readAnnualCatalog(fixtures.annualCatalog), c = readConstructionCatalog(fixtures.constructionCatalog); assert.deepEqual(a, fixtures.annualCatalog); assert.deepEqual(c, fixtures.constructionCatalog); assert.deepEqual(annualFilters(a, new URLSearchParams('region=missing')), annualSelection); assert.deepEqual(constructionFilters(c, new URLSearchParams()), constructionSelection); assert.deepEqual(constructionFilters(c, new URLSearchParams('region=rf&permit_kind=nonresidential&month=1')), { region: 'rf', permitKind: 'nonresidential', month: 2 }); assert.equal(annualFilters({ ...a, regions: [] }, new URLSearchParams()), null);
});
test('annual seven Moscow and two RF charts preserve all years, source zeros, series and supplied summaries', () => {
  for (const region of ['msk', 'rf']) { const raw = fixtures.annualReport({ region }), a = readAnnualReport(raw, { region }); assert.deepEqual(a, raw); assert.equal(a.charts.length, region === 'msk' ? 7 : 2); assert.equal(a.charts[0].rows.length, 16); assert.equal(a.charts[0].rows.at(-1).year, 2026); assert.equal(a.charts[0].summaries[0].values[0].value, 99.1234); assert.equal(annualSummaryRows(a.charts[0])[1][3], 99.1234); }
  const a = readAnnualReport(fixtures.annualReport({ region: 'rf' }), { region: 'rf' }); assert.equal(a.charts[0].rows.at(-1).s0, 0); assert.ok(a.notes.some(n => n.includes('нет данных'))); const c = a.charts[0]; for (const theme of ['light', 'dark']) { const o = annualChartOption(c, theme); assert.equal(o.tooltip.trigger, 'item'); assert.equal(o.series[0].data.at(-1), 0); assert.equal(o.yAxis.name, 'млн м²'); assert.equal(o.series[0].itemStyle.color, c.series[0].color); }
});
test('construction keeps raw units/nulls, independent periods and Moscow permit scope even for RF', () => {
  const selection = { ...constructionSelection, region: 'rf' }, raw = fixtures.constructionReport(selection), c = readConstructionReport(raw, selection); assert.deepEqual(c.selection, raw.selection); assert.equal(c.metrics[0].value, 15.64321); assert.equal(c.metrics[1].value, null); assert.equal(c.metrics[3].value, 0); assert.equal(c.sales.sales_not_open_pct, null); assert.equal(c.permits.region, 'Москва'); assert.equal(c.permits.unit, 'тыс. м²'); assert.equal(c.permits.rows[0]['С начала года, тыс. м²'], null); assert.equal(c.permits.chart.period[0].value, null); assert.equal(c.permits.chart.totals.at(-1).value, null); assert.equal(permitChartRows(c.permits)[1].at(-1), null); assert.equal(salesTable(c.sales).rows.length, Object.keys(c.sales).length); assert.equal(tableRows(c.permits).length, 17); const o = permitChartOption(c.permits, 'dark'); assert.equal(o.series[0].data[0], null); assert.equal(o.tooltip.trigger, 'item'); assert.equal(o.yAxis.name, 'тыс. м²');
});
test('renovation and every single visible annual series keep numeric labels above bars, including mobile', () => {
  const c = readAnnualReport(fixtures.annualReport(), annualSelection).charts[3];
  assert.equal(c.totals, false);
  for (const theme of ['light', 'dark']) {
    const o = annualChartOption(c, theme);
    assert.equal(o.series[0].label.show, true); assert.equal(o.series[0].label.position, 'top'); assert.equal(o.series[0].labelLayout.hideOverlap, false); assert.equal(o.media[0].option.series[0].label.show, true);
    assert.equal(o.series[0].label.formatter({ dataIndex: 0 }), '1,1');
  }
  const multi = readAnnualReport(fixtures.annualReport(), annualSelection).charts[0], visible = multi.series[1], o = annualChartOption(multi, 'light', [visible.name]);
  assert.deepEqual(o.series.map(s => s.label.show), [false, true, false, false]); assert.equal(o.media[0].option.series[1].label.show, true); assert.equal(o.series[1].label.formatter({ dataIndex: 0 }), '0');
  visible.points[0].y = null; assert.equal(annualChartOption(multi, 'light', [visible.name]).series[1].label.formatter({ dataIndex: 0 }), '');
});
test('compatibility permit chart uses the agreed annual-values legend', () => {
  const report = readConstructionReport(fixtures.constructionReport(), constructionSelection);
  assert.equal(permitChartOption(report.permits, 'light').series[1].name, 'Годовые значения');
});

test('small annual IHS measurements remain nonzero in hover without changing chart or export values', () => {
  const chart = readAnnualReport(fixtures.annualReport(), annualSelection).charts[1];
  chart.series[1].points[0].y = .0258;
  chart.rows[0][chart.series[1].id] = .0258;
  const before = structuredClone(chart);
  for (const theme of ['light', 'dark']) {
    const option = annualChartOption(chart, theme);
    assert.match(option.tooltip.formatter({ name: '2011', seriesName: 'ИЖС', value: .0258 }), /25,8 тыс\. м²/);
    assert.match(option.tooltip.formatter({ name: '2012', seriesName: 'ИЖС', value: .2218 }), /0,2 млн м²/);
    assert.match(option.tooltip.formatter({ name: '2013', seriesName: 'ИЖС', value: 0 }), /0 млн м²/);
    assert.equal(option.series[1].data[0], .0258);
  }
  assert.equal(annualChartRows(chart)[1][2], .0258);
  assert.deepEqual(chart, before);
});
test('annual stack label follows highest visible series and sums only visible segments without changing source data', () => {
  const c = readAnnualReport(fixtures.annualReport(), annualSelection).charts[0], before = structuredClone(c), visible = c.series.slice(0, 2).map(s => s.name), o = annualChartOption(c, 'dark', visible);
  assert.deepEqual(o.series.map(s => s.label.show), [false, true, false, false]); assert.equal(o.series[1].label.formatter({ dataIndex: 5 }), '3,7'); assert.ok(o.media[0].option.series.every(s => s.label.show === false)); assert.deepEqual(c, before);
  assert.ok(annualChartOption(c, 'dark', []).series.every(s => s.label.show === false));
});

test('annual controls label the last measured segment per year and never add TOTAL to the stack', () => {
  const raw = fixtures.annualReport(), chart = raw.charts[1];
  chart.columns = [{ id: 'year', label: 'Год' }, ...['МКД', 'ИЖС', 'Остальное', 'TOTAL'].map(id => ({ id, label: id }))];
  chart.rows = [{ year: 2011, МКД: null, ИЖС: null, Остальное: 3, TOTAL: 3 },
    { year: 2025, МКД: 6.7925, ИЖС: .6015, Остальное: null, TOTAL: 7.3939 },
    { year: 2026, МКД: 4.0554834, ИЖС: .214205, Остальное: null, TOTAL: 4.2696884 }];
  chart.series = ['МКД', 'ИЖС', 'Остальное'].map(id => ({ id, name: id, color: '#abcdef', points: chart.rows.map(row => ({ x: String(row.year), y: row[id] })) }));
  chart.controlTotal = 'TOTAL';
  chart.rawCategoryValues = [{ year: 2011, МКД: null, ИЖС: .5, TOTAL: 3 }];
  chart.categoryProvenance = [{ year: 2025, МКД_source_file: 'base.xls', ИЖС_source_file: 'dynamic.xls',
    МКД_source_precision_million: .0001, ИЖС_source_precision_million: .0001, TOTAL_source_precision_million: .0001 },
  { year: 2026, МКД_source_rows: [2, 3], ИЖС_source_rows: [4], TOTAL_source_precision_million: 1e-6 }];
  const parsed = readAnnualReport(raw, annualSelection).charts[1], before = structuredClone(parsed), o = annualChartOption(parsed, 'light');
  assert.equal(o.series.length, 3); assert.equal(o.series.some(s => s.name === 'TOTAL'), false);
  assert.equal(o.series[0].data[0], null); assert.equal(o.series[1].data[0], null);
  assert.equal(o.series[2].label.formatter({ dataIndex: 0 }), '3');
  assert.equal(o.series[2].label.formatter({ dataIndex: 1 }), '');
  assert.equal(o.series[1].label.formatter({ dataIndex: 1 }), '7,4');
  assert.equal(o.series[1].label.formatter({ dataIndex: 2 }), '4,3');
  const ihsOnly = annualChartOption(parsed, 'light', ['ИЖС']);
  assert.equal(ihsOnly.series[1].label.formatter({ dataIndex: 1 }), '0,6');
  assert.equal(ihsOnly.series[1].label.formatter({ dataIndex: 0 }), '');
  assert.deepEqual(parsed, before); assert.deepEqual(parsed.categoryProvenance, chart.categoryProvenance);
  assert.deepEqual(parsed.rawCategoryValues, chart.rawCategoryValues); assert.equal(o.series[1].data[0], null);
  assert.equal(tableRows(parsed)[1][1], null); assert.equal(tableRows(parsed)[2].at(-1), 7.3939);
  // A known control with incomplete visible coverage is not a measured stack.
  parsed.rows[1].TOTAL = 8;
  assert.equal(annualChartOption(parsed, 'light').series[1].label.formatter({ dataIndex: 1 }), '');
  for (const mutate of [c => c.rows[0].TOTAL = false, c => c.controlTotal = 'missing',
    c => c.series.push({ id: 'TOTAL', name: 'TOTAL', color: '#fff', points: c.rows.map(row => ({ x: String(row.year), y: row.TOTAL })) }),
    c => c.categoryProvenance[0].МКД_source_file = { guessed: true }, c => c.categoryProvenance[1].МКД_source_rows = [0]]) {
    const bad = structuredClone(raw); mutate(bad.charts[1]); assert.throws(() => readAnnualReport(bad, annualSelection));
  }
});
test('annual forecasts occupy separate hatched categories after the dated fact without altering facts or summaries', () => {
  const raw = fixtures.annualForecastReport(), before = structuredClone(raw), parsed = readAnnualReport(raw, annualSelection), c = parsed.charts[0];
  assert.deepEqual(parsed, raw); assert.equal(annualFactCaption(c), 'янв–сен');
  for (const theme of ['light', 'dark']) {
    const o = annualChartOption(c, theme), forecast = o.series.at(-1), count = c.rows.length;
    assert.deepEqual(o.xAxis.data.slice(-4), ['2026\nянв–сен', '2026\nпрогноз', '2027\nпрогноз', '2030\nпрогноз']);
    assert.equal(forecast.name, 'Прогноз'); assert.equal(forecast.itemStyle.borderType, 'dashed'); assert.ok(forecast.itemStyle.decal);
    assert.deepEqual(forecast.data, [...Array(count).fill(null), 14, 0, 17]);
    for (const [index, series] of o.series.slice(0, -1).entries()) {
      assert.deepEqual(series.data.slice(0, count), c.series[index].points.map(point => point.y));
      assert.deepEqual(series.data.slice(count), [null, null, null]);
    }
    assert.match(o.tooltip.formatter({ name: '2026\nпрогноз', seriesName: 'Прогноз', value: 14 }), /Общая площадь жилья/);
    assert.equal(annualChartOption(c, theme, c.series.map(s => s.name)).series.at(-1).label.show, false);
    const only = annualChartOption(c, theme, ['Прогноз']); assert.ok(only.series.slice(0, -1).every(s => !s.label.show)); assert.equal(only.series.at(-1).label.show, true);
  }
  const rows = annualChartRows(c);
  assert.equal(rows[0].at(-1), 'Прогноз, млн м²'); assert.equal(rows[c.rows.length][0], '2026 · факт · янв–сен');
  assert.deepEqual(rows.at(-2), ['2027 · прогноз', ...c.columns.slice(1).map(() => null), 0]);
  assert.deepEqual(annualSummaryRows(c), annualSummaryRows(before.charts[0])); assert.deepEqual(raw, before);
});
test('annual forecast schema rejects duplicate/historical/fabricated/RF values and keeps missing forecasts absent', () => {
  for (const mutate of [v => v.charts[0].forecast.points.push({ year: 2026, value: 1 }),
    v => v.charts[0].forecast.points[0].year = 2025, v => v.charts[0].forecast.points[0].value = null,
    v => v.charts[0].forecast.points[0].value = -1, v => v.charts[0].forecast.points[0].value = NaN,
    v => v.charts[0].forecast.points.reverse(), v => v.charts[0].factPeriod.month = 13,
    v => v.charts[0].factPeriod.year = 2027, v => v.charts[0].forecast.sourceFields = [],
    v => delete v.charts[0].factPeriod]) {
    const value = fixtures.annualForecastReport(); mutate(value); assert.throws(() => readAnnualReport(value, annualSelection));
  }
  const rf = fixtures.annualReport({ region: 'rf' });
  rf.charts[0].factPeriod = { year: 2026, month: null };
  const c = readAnnualReport(rf, { region: 'rf' }).charts[0];
  assert.equal(c.forecast, undefined); assert.equal(annualChartOption(c, 'light').series.length, c.series.length);
  rf.forecastSource = fixtures.annualForecastReport().forecastSource; assert.throws(() => readAnnualReport(rf, { region: 'rf' }));
  const missing = fixtures.annualReport(); missing.charts[0].factPeriod = { year: 2026, month: null };
  assert.equal(annualChartOption(readAnnualReport(missing, annualSelection).charts[0], 'light').xAxis.data.at(-1), '2026\nпериод не указан');
});
test('pinned exports contain every selection filter and never table search/page', () => {
  assert.deepEqual(marketParams(constructionSelection), { region: 'msk', permit_kind: 'total', month: '3' }); assert.equal(new URL(marketUrl('annual', annualSelection, 'v +'), 'http://local').searchParams.get('required_version'), 'v +'); const u = new URL(marketUrl('construction', constructionSelection, 'v2'), 'http://local'); assert.equal(u.pathname, '/api/v1/construction/export'); assert.deepEqual([...u.searchParams.keys()], ['region', 'permit_kind', 'month', 'required_version']);
});
test('malformed schema, wrapped points, annual alignment, row cells and mismatched scopes reject visibly', () => {
  for (const alter of [v => v.schemaVersion = 2, v => v.region.id = 'rf', v => v.charts[0].series[0].points.pop(), v => v.charts[0].rows[0].s0 = true, v => v.charts[0].series[0].points[0].x = '2099']) { const a = fixtures.annualReport(); alter(a); assert.throws(() => readAnnualReport(a, annualSelection)); }
  for (const alter of [v => v.selection.month = 1, v => v.sales.region_key = 'rf', v => v.permits.region = 'РФ', v => v.permits.chart.period = [0], v => v.metrics[0].value = NaN, v => v.permits.rows[0]['С начала года, тыс. м²'] = false]) { const c = fixtures.constructionReport(); alter(c); assert.throws(() => readConstructionReport(c, constructionSelection)); }
});
test('empty records and missing provenance stay empty, not invented values', () => {
  const a = fixtures.annualReport(); a.charts = []; a.source.date = null; assert.equal(readAnnualReport(a, annualSelection).charts.length, 0); const c = fixtures.constructionReport(); c.sales = null; c.permits.chart = null; c.permits.rows = []; c.permits.columns = []; c.sourceDetails.date = null; const read = readConstructionReport(c, constructionSelection); assert.equal(read.sales, null); assert.equal(read.sourceDetails.date, null); assert.equal(read.permits.rows.length, 0); assert.equal(read.source.fileEvidence, 'candidates'); assert.equal(read.source.dateEvidence, fixtures.annualCatalog.source.dateEvidence);
});
test('annual and construction API loaders carry abort signals and never use frozen fallback', async () => {
  const original = globalThis.fetch, calls = [], signal = new AbortController().signal;
  try { globalThis.fetch = async (url, options) => { calls.push({ url, options }); return new Response(JSON.stringify(String(url).endsWith('/catalog') ? fixtures.annualCatalog : fixtures.annualReport()), { status: 200 }); }; await loadMarketCatalog('annual', signal); await loadMarketReport('annual', annualSelection, signal); assert.equal(calls[0].options.signal, signal); assert.equal(calls[1].options.signal, signal); globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'source unavailable' }), { status: 503 }); await assert.rejects(() => loadMarketCatalog('construction', signal), /HTTP 503/); assert.equal(calls.some(c => c.url.includes('snapshot')), false); } finally { globalThis.fetch = original; }
});
