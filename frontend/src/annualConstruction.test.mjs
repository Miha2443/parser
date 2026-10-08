import assert from 'node:assert/strict';
import test from 'node:test';
import fixtures from './annualConstruction.browser.cjs';
import { annualChartOption, annualFilters, annualSummaryRows, constructionFilters, loadMarketCatalog, loadMarketReport, marketParams, marketUrl, permitChartOption, permitChartRows, readAnnualCatalog, readAnnualReport, readConstructionCatalog, readConstructionReport, salesTable, tableRows } from './annualConstructionData.ts';
const annualSelection = { region: 'msk' }, constructionSelection = { region: 'msk', permitKind: 'total', month: 3 };
test('annual regions and construction month defaults remain catalog-defined and region-independent', () => {
  const a = readAnnualCatalog(fixtures.annualCatalog), c = readConstructionCatalog(fixtures.constructionCatalog); assert.deepEqual(a, fixtures.annualCatalog); assert.deepEqual(c, fixtures.constructionCatalog); assert.deepEqual(annualFilters(a, new URLSearchParams('region=missing')), annualSelection); assert.deepEqual(constructionFilters(c, new URLSearchParams()), constructionSelection); assert.deepEqual(constructionFilters(c, new URLSearchParams('region=rf&permit_kind=nonresidential&month=1')), { region: 'rf', permitKind: 'nonresidential', month: 2 }); assert.equal(annualFilters({ ...a, regions: [] }, new URLSearchParams()), null);
});
test('annual seven Moscow and two RF charts preserve all years, source zeros, series and supplied summaries', () => {
  for (const region of ['msk', 'rf']) { const raw = fixtures.annualReport({ region }), a = readAnnualReport(raw, { region }); assert.deepEqual(a, raw); assert.equal(a.charts.length, region === 'msk' ? 7 : 2); assert.equal(a.charts[0].rows.length, 16); assert.equal(a.charts[0].rows.at(-1).year, 2026); assert.equal(a.charts[0].summaries[0].values[0].value, 99.1234); assert.equal(annualSummaryRows(a.charts[0])[1][3], 99.1234); }
  const a = readAnnualReport(fixtures.annualReport({ region: 'rf' }), { region: 'rf' }); assert.equal(a.charts[0].rows.at(-1).s0, 0); assert.ok(a.notes.some(n => n.includes('нет данных'))); const c = a.charts[0]; for (const theme of ['light', 'dark']) { const o = annualChartOption(c, theme); assert.equal(o.tooltip.trigger, 'item'); assert.equal(o.series[0].data.at(-1), 0); assert.equal(o.yAxis.name, 'млн м²'); assert.equal(o.series[0].itemStyle.color, c.series[0].color); }
});
test('construction keeps raw units/nulls, independent periods and Moscow permit scope even for RF', () => {
  const selection = { ...constructionSelection, region: 'rf' }, raw = fixtures.constructionReport(selection), c = readConstructionReport(raw, selection); assert.deepEqual(c.selection, raw.selection); assert.equal(c.metrics[0].value, 15.64321); assert.equal(c.metrics[1].value, null); assert.equal(c.metrics[3].value, 0); assert.equal(c.sales.sales_not_open_pct, null); assert.equal(c.permits.region, 'Москва'); assert.equal(c.permits.unit, 'тыс. м²'); assert.equal(c.permits.rows[0]['С начала года, тыс. м²'], null); assert.equal(c.permits.chart.period[0].value, 0); assert.equal(c.permits.chart.totals.at(-1).value, null); assert.equal(permitChartRows(c.permits)[1].at(-1), null); assert.equal(salesTable(c.sales).rows.length, Object.keys(c.sales).length); assert.equal(tableRows(c.permits).length, 17); const o = permitChartOption(c.permits, 'dark'); assert.equal(o.series[0].data[0], 0); assert.equal(o.tooltip.trigger, 'item'); assert.equal(o.yAxis.name, 'тыс. м²');
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
  assert.deepEqual(o.series.map(s => s.label.show), [false, true, false, false]); assert.equal(o.media[0].option.series[1].label.show, true); assert.equal(o.series[1].label.formatter({ dataIndex: 0 }), '0,0');
  visible.points[0].y = null; assert.equal(annualChartOption(multi, 'light', [visible.name]).series[1].label.formatter({ dataIndex: 0 }), '');
});
test('annual stack label follows highest visible series and sums only visible segments without changing source data', () => {
  const c = readAnnualReport(fixtures.annualReport(), annualSelection).charts[0], before = structuredClone(c), visible = c.series.slice(0, 2).map(s => s.name), o = annualChartOption(c, 'dark', visible);
  assert.deepEqual(o.series.map(s => s.label.show), [false, true, false, false]); assert.equal(o.series[1].label.formatter({ dataIndex: 5 }), '3,7'); assert.ok(o.media[0].option.series.every(s => s.label.show === false)); assert.deepEqual(c, before);
  assert.ok(annualChartOption(c, 'dark', []).series.every(s => s.label.show === false));
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
