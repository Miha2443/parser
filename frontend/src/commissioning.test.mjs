import assert from 'node:assert/strict';
import test from 'node:test';
import fixtures from './commissioning.browser.cjs';
import { commissioningParams, commissioningUrl, groupedStructure, linearDigits, linearFilters, linearSummaryOption, linearTable, linearTrendOption, loadCommissioningCatalog, loadCommissioningReport, operationalChartOption, operationalChartRows, operationalFilters, readLinearCatalog, readLinearReport, readOperationalCatalog, readOperationalReport, reportTablePage, structureRows, tableRows } from './commissioningData.ts';

test('operational catalog defaults are server-defined, filters validate available months and years', () => {
  const c = readOperationalCatalog(fixtures.operationalCatalog); assert.deepEqual(c, fixtures.operationalCatalog);
  assert.deepEqual(operationalFilters(c, new URLSearchParams()), fixtures.operationalSelection);
  assert.deepEqual(operationalFilters(c, new URLSearchParams('month=13&year=2000&quarter=8&cumulative=1&exclude_mkd=1')), fixtures.operationalSelection);
  assert.deepEqual(operationalFilters(c, new URLSearchParams('month=4&year=2025&quarter=2&cumulative=true&exclude_mkd=true')), { month: 4, year: 2025, quarter: 2, cumulative: true, excludeMkd: true });
});
test('linear defaults use catalog latest year and latest complete quarter, not latest calendar quarter', () => {
  const c = readLinearCatalog(fixtures.linearCatalog); assert.deepEqual(c, fixtures.linearCatalog);
  assert.deepEqual(linearFilters(c, new URLSearchParams()), fixtures.linearSelection);
  assert.deepEqual(linearFilters(c, new URLSearchParams('year=2025&quarter=4&indicator=missing')), { year: 2025, quarter: 2, cumulative: false, indicator: '1.1' });
  assert.equal(linearFilters(c, new URLSearchParams('quarter=4')).quarter, 4);
});
test('operational reports retain both full sixteen-year tables and record-wrapped chart values without conversion', () => {
  const raw = fixtures.operationalReport(), data = readOperationalReport(raw, fixtures.operationalSelection); assert.deepEqual(data, raw);
  for (const t of data.tables) { assert.equal(t.rows.length, 16); assert.equal(t.chart.x.length, 16); assert.equal(t.chart.x.at(-1), '2026'); }
  const t = data.tables[1]; assert.equal(t.rows[0]['За выбранный период, млн м²'], null); assert.equal(t.chart.period[0].value, 0); assert.equal(t.chart.remainder[0].value, 2.12345); assert.equal(t.chart.growth[0].value, null);
  assert.equal(data.tables[0].rows.at(-1)['За год, млн м²'], null); assert.equal(data.tables[0].chart.remainder.at(-1).value, 0);
  const rows = operationalChartRows(t); assert.deepEqual(rows[1], ['2011', 0, 2.12345, 2.12345, null, null]); assert.equal(tableRows(t).length, 17);
});
test('all fifteen structure metrics stay supplied independent values, with no invented sums', () => {
  const raw = fixtures.operationalReport(), groups = groupedStructure(raw.treeRows), rows = groups.flatMap(g => g.rows);
  assert.equal(rows.length, 15); assert.equal(new Set(rows.map(r => r.id)).size, 15); assert.equal(rows.find(r => r.id === 'total').value, 99.1234);
  assert.equal(rows.find(r => r.id === 'mop').value, 0); assert.equal(rows.find(r => r.id === 'residential_area').value, null); assert.equal(structureRows(raw.treeRows).length, 16);
  assert.deepEqual(groupedStructure([{ id: 'new', label: 'Новый показатель', value: 7 }])[0].rows, [{ id: 'new', label: 'Новый показатель', value: 7 }]);
});
test('linear reports keep all quarter records, unit precision, raw percentages, zeros and missing facts', () => {
  const selection = { ...fixtures.linearSelection, quarter: 4 }, raw = fixtures.linearReport(selection), data = readLinearReport(raw, selection); assert.deepEqual(data, raw);
  assert.equal(data.summary.length, 4); assert.equal(data.trend.length, 4); assert.equal(data.allPeriods.length, 16); assert.equal(data.summary[0].fact, null); assert.equal(data.summary[0].plan, 14.234);
  assert.equal(linearDigits('км'), 1); assert.equal(linearDigits('ед'), 0); assert.equal(data.allPeriods.find(r => r.code === '1.4' && r.quarter === 2).fact, 0);
  const rows = tableRows(linearTable(data.allPeriods)); assert.equal(rows.length, 17); assert.equal(rows.at(-1)[6], null);
});
test('cumulative reports and raw percentages are not re-aggregated in the UI', () => {
  const selection = { ...fixtures.linearSelection, cumulative: true }, raw = fixtures.linearReport(selection); raw.summary[0].percent = 17.456;
  assert.equal(readLinearReport(raw, selection).summary[0].percent, 17.456); assert.equal(readLinearReport(raw, selection).summary[0].plan, raw.summary[0].plan);
  assert.throws(() => readLinearReport(raw, { ...selection, cumulative: false }));
});
test('operational chart options use exact API plotting zeros, totals and growth, with segment hover', () => {
  const table = fixtures.operationalReport().tables[1], option = operationalChartOption(table, 'Январь–август', 'dark');
  assert.deepEqual(option.series[0].data, table.chart.period.map(p => p.value)); assert.deepEqual(option.series[1].data, table.chart.remainder.map(p => p.value)); assert.equal(option.series[0].stack, option.series[1].stack); assert.equal(option.tooltip.trigger, 'item');
  const tip = option.tooltip.formatter({ dataIndex: 0, seriesIndex: 0, seriesName: '<period>', name: '2011', value: 0 }); assert.ok(tip.includes('0,00 млн м²') && tip.includes('Исходное значение периода: —') && tip.includes('&lt;period&gt;'));
  assert.equal(option.series[0].label.formatter({ dataIndex: 0, value: 0 }), ''); assert.equal(option.series[1].label.formatter({ dataIndex: 0 }), '2,12');
  assert.equal(option.media[0].query.maxWidth, 600); assert.equal(option.media[0].option.series[0].label.show, false);
});
test('linear summary and quarter bars retain missing facts as null instead of drawing zero', () => {
  const selection = { ...fixtures.linearSelection, quarter: 4 }, report = fixtures.linearReport(selection), summary = linearSummaryOption(report.summary[0], 'light'), trend = linearTrendOption(report.trend, 'км', 'light');
  assert.deepEqual(summary.series[1].data, [null, null]); assert.deepEqual(summary.series[0].data, [null, 14.234]); assert.equal(trend.series[1].data.at(-1), null); assert.equal(trend.series[0].label.formatter({ value: 14.234 }), '14,2');
  assert.equal(linearSummaryOption(report.summary[1], 'dark').series[0].label.formatter({ value: 7.4 }), '7');
  assert.notEqual(summary.series[0].itemStyle.color, summary.series[1].itemStyle.color);
});
test('both mandatory pinned export URLs preserve selection scope, never table search or pagination', () => {
  for (const [kind, selection, root] of [['operational', fixtures.operationalSelection, '/api/v1/commissioning/operational/export'], ['linear', fixtures.linearSelection, '/api/v1/linear/export']]) {
    const url = new URL(commissioningUrl(kind, selection, 'v +1'), 'http://localhost'); assert.equal(url.pathname, root); assert.deepEqual(Object.fromEntries(url.searchParams), { ...commissioningParams(selection), required_version: 'v +1' });
  }
});
test('complete table search and pagination preserve raw rows, source order and full CSV', () => {
  const table = linearTable(fixtures.linearReport().allPeriods); table.rows = Array.from({ length: 73 }, (_, i) => ({ ...table.rows[i % 16], indicator: `Показатель ${i + 1}` }));
  assert.equal(reportTablePage(table, '', null, null).shown.length, 50); assert.equal(reportTablePage(table, '', '2', '50').shown.length, 23); assert.equal(reportTablePage(table, 'Показатель 73', '999', '7').shown[0].indicator, 'Показатель 73'); assert.equal(reportTablePage(table, 'none', '-4', '20').current, 1); assert.equal(tableRows(table).length, 74);
});
test('schema validation rejects malformed wrapped points, units, selection mismatches and duplicate controls', () => {
  for (const change of [raw => raw.schemaVersion = 2, raw => raw.source.fileEvidence = 'unknown', raw => raw.defaultMonth = 12, raw => raw.years.push(raw.years[0])]) { const raw = structuredClone(fixtures.operationalCatalog); change(raw); assert.throws(() => readOperationalCatalog(raw)); }
  const c = structuredClone(fixtures.linearCatalog); c.years[0].defaultQuarter = 8; assert.throws(() => readLinearCatalog(c));
  for (const change of [raw => raw.selection.month = 1, raw => raw.tables[0].chart.period[0] = 0, raw => raw.tables[0].chart.remainder.pop(), raw => raw.treeRows[0].value = 0, raw => raw.tables[0].rows[0]['За год, млн м²'] = true]) { const raw = fixtures.operationalReport(); change(raw); assert.throws(() => readOperationalReport(raw, fixtures.operationalSelection)); }
  for (const change of [raw => raw.summary[0].fact = Infinity, raw => raw.trend[0].code = 'wrong', raw => raw.allPeriods[0].year = 2000, raw => raw.summary[0].unit = undefined, raw => raw.selection.indicator = '1.4']) { const raw = fixtures.linearReport(); change(raw); assert.throws(() => readLinearReport(raw, fixtures.linearSelection)); }
});
test('candidate evidence, selected report files, notes, missing dates and empty availability remain truthful', () => {
  const op = fixtures.operationalReport(); op.source.date = null; assert.deepEqual(readOperationalReport(op, fixtures.operationalSelection).source, op.source); assert.deepEqual(readOperationalReport(op, fixtures.operationalSelection).sourceDetails, op.sourceDetails);
  assert.equal(readLinearCatalog(fixtures.linearCatalog).source.fileEvidence, 'selected'); assert.equal(operationalFilters(readOperationalCatalog({ ...fixtures.operationalCatalog, months: [], years: [] }), new URLSearchParams()), null); assert.equal(linearFilters(readLinearCatalog({ ...fixtures.linearCatalog, years: [], indicators: [] }), new URLSearchParams()), null);
  const linear = fixtures.linearReport(); linear.summary = linear.trend = linear.allPeriods = []; assert.deepEqual(readLinearReport(linear, fixtures.linearSelection), linear);
});
test('loaders use caller abort signals and no frozen fallback on API or transport failures', async () => {
  const original = globalThis.fetch, controller = new AbortController(), calls = [];
  try {
    globalThis.fetch = async (url, options) => { calls.push([url, options]); return new Response(JSON.stringify(url.endsWith('/catalog') ? url.includes('/linear') ? fixtures.linearCatalog : fixtures.operationalCatalog : url.includes('/linear') ? fixtures.linearReport() : fixtures.operationalReport())); };
    await loadCommissioningCatalog('operational', controller.signal); await loadCommissioningReport('operational', fixtures.operationalSelection, controller.signal); await loadCommissioningCatalog('linear', controller.signal); await loadCommissioningReport('linear', fixtures.linearSelection, controller.signal);
    assert.ok(calls.every(([url, options]) => url.startsWith('/api/v1/') && options.signal === controller.signal && options.cache === 'no-store'));
    globalThis.fetch = async () => new Response('offline', { status: 503 }); await assert.rejects(loadCommissioningCatalog('linear', controller.signal), /HTTP 503/);
    globalThis.fetch = async () => { throw new Error('offline'); }; await assert.rejects(loadCommissioningReport('operational', fixtures.operationalSelection, controller.signal), /серверу данных/);
  } finally { globalThis.fetch = original; }
});
