import assert from 'node:assert/strict';
import test from 'node:test';
import fixture from './sales.browser.cjs';
import { loadSalesCatalog, loadSalesData, readSalesCatalog, readSalesData, salesChartOption, salesChartRows, salesFilters, salesTablePage, salesTableRows, salesUrl } from './salesData.ts';

test('sales catalog defaults to Moscow and latest available regional period without sorting inputs', () => {
  const c = readSalesCatalog(fixture.catalog);
  assert.deepEqual(c, fixture.catalog);
  assert.equal(salesFilters(c, new URLSearchParams()).period.id, '2026-06');
  assert.equal(salesFilters(c, new URLSearchParams({ region: 'rf' })).period.id, '2026-05');
  assert.equal(salesFilters(c, new URLSearchParams({ region: 'rf', period: '2026-06' })).period.id, '2026-05');
  assert.equal(salesFilters(c, new URLSearchParams({ region: 'rf', period: '2026-04' })).period.id, '2026-04');
  assert.equal(salesFilters(readSalesCatalog({ ...c, regions: [c.regions[1]] }), new URLSearchParams()).region.id, 'rf');
  assert.deepEqual(c.regions[0].periods, fixture.periods);
});
test('sales responses preserve every KPI, raw cell, unit, date, issue, row and null', () => {
  const raw = fixture.detail(), data = readSalesData(raw, 'msk', '2026-06');
  assert.deepEqual(data, raw);
  assert.equal(data.metrics[0].value, 132456.789);
  assert.equal(data.metrics[1].value, 0);
  assert.equal(data.metrics[2].value, null);
  assert.equal(data.metrics[3].digits, 2);
  assert.equal(data.tables.length, 6);
  assert.equal(data.tables[0].rows.length, 123);
  assert.deepEqual(data.tables[0].rows[0], { name: 'Сегмент 1', volume: null, sold: '12,3 %', ready: '', ratio: 0, forecast: 'не указано' });
});
test('table pagination searches all columns and full CSV rows ignore active filter', () => {
  const table = fixture.detail().tables[0];
  assert.equal(salesTablePage(table, '', null, null).shown.length, 50);
  assert.equal(salesTablePage(table, '', '3', null).shown.length, 23);
  assert.equal(salesTablePage(table, 'А+Б', '999', '7').shown[0].forecast, '01.07.2026');
  assert.equal(salesTablePage(table, '12,3 %', '-4', '20').total, 1);
  assert.equal(salesTablePage(table, '01.07.2026', 'NaN', '20').total, 1);
  assert.equal(salesTablePage(table, 'does not exist', '20', '100').current, 1);
  const rows = salesTableRows(table);
  assert.equal(rows.length, 124);
  assert.deepEqual(rows[1], ['Сегмент 1', null, '12,3 %', '', 0, 'не указано']);
});
test('candidate date evidence is retained without declaring it a selected source date', () => {
  const raw = fixture.detail(); raw.source.dateEvidence = 'Latest candidate date; not proof of the selected mart or raw workbook';
  assert.deepEqual(readSalesData(raw, 'msk', '2026-06').source, raw.source);
  assert.deepEqual(readSalesCatalog({ ...fixture.catalog, source: raw.source }).source, raw.source);
});
test('forecast separates area and percentage axes and retains complete unit-aware CSV', () => {
  const chart = fixture.detail().charts[0], option = salesChartOption(chart, 'dark');
  assert.equal(Array.isArray(option.yAxis), true);
  assert.deepEqual(option.yAxis.map(axis => axis.name), ['тыс. м²', '%']);
  assert.equal(option.series[0].yAxisIndex, 0);
  assert.equal(option.series[1].yAxisIndex, 1);
  assert.equal(option.series[0].data[0], 1000);
  assert.equal(option.series[1].data[0], 80);
  assert.equal(option.xAxis.data.includes('2028'), false);
  assert.equal(option.tooltip.trigger, 'item');
  const rows = salesChartRows(chart);
  assert.equal(rows.length, 17);
  assert.deepEqual(rows[1], ['2026', 'Объём жил. строительства', 'тыс. м²', 1000]);
  assert.deepEqual(rows[7], ['2028', 'Распроданность', '%', null]);
  const lines = salesChartOption(fixture.detail().charts[2], 'light');
  assert.equal(lines.tooltip.trigger, 'axis');
  for (const s of lines.series) assert.equal(s.connectNulls, false);
  const tooltip = lines.tooltip.formatter([{ name: '2026-01', seriesIndex: 0, seriesName: 'Распроданность', value: 44 }, { name: '2026-01', seriesIndex: 1, seriesName: 'Стройготовность', value: 42 }]);
  assert.ok(tooltip.includes('44,0 %') && tooltip.includes('42,0 %'));
});
test('series colors stay assigned by meaning in each theme and missing series do not shift them', () => {
  for (const theme of ['dark', 'light']) {
    const chart = fixture.detail().charts[0], full = salesChartOption(chart, theme), partial = salesChartOption({ ...chart, series: chart.series.slice(1) }, theme);
    assert.equal(new Set(full.series.map(s => s.itemStyle.color)).size, 4);
    assert.deepEqual(partial.series.map(s => s.itemStyle.color), full.series.slice(1).map(s => s.itemStyle.color));
    assert.ok(full.tooltip.formatter({ name: '<2026>', seriesName: '<script>', seriesIndex: 0, value: 1000 }).includes('&lt;script&gt;'));
  }
});
test('repeated source categories survive alignment without dropping points', () => {
  const chart = fixture.detail().charts[1];
  chart.series[0].points = [{ x: '2026-01', y: 10 }, { x: '2026-01', y: null }, { x: '2026-02', y: 20 }];
  chart.series.push({ id: 'other', name: 'Другая серия', unit: 'м²', points: [{ x: '2026-01', y: 0 }, { x: '2026-03', y: 40 }] });
  const option = salesChartOption(chart, 'dark');
  assert.deepEqual(option.xAxis.data, ['2026-01', '2026-01', '2026-02', '2026-03']);
  assert.deepEqual(option.series[0].data, [10, null, 20, null]);
  assert.deepEqual(option.series[1].data, [0, null, null, 40]);
  assert.equal(salesChartRows(chart).length, 6);
});
test('full Excel uses only region, period and displayed version, encoded correctly', () => {
  const url = new URL(salesUrl('msk', '2026-06', 'v +1'), 'http://localhost');
  assert.equal(url.pathname, '/api/v1/sales/export');
  assert.deepEqual([...url.searchParams.entries()], [['region', 'msk'], ['period', '2026-06'], ['required_version', 'v +1']]);
  assert.equal(new URL(salesUrl('rf', '2026-05'), 'http://localhost').pathname, '/api/v1/sales');
});
test('monthly gaps align chronologically across series while bars retain source column order', () => {
  const chart = fixture.detail().charts[1];
  chart.series[0].points = [{ x: '2026-01', y: 10 }, { x: '2026-03', y: 30 }, { x: '2026-03', y: 31 }];
  chart.series.push({ id: 'other', name: 'Другая серия', unit: '%', points: [{ x: '2026-02', y: 20 }] });
  const line = salesChartOption(chart, 'dark');
  assert.deepEqual(line.xAxis.data, ['2026-01', '2026-02', '2026-03', '2026-03']);
  assert.deepEqual(line.series[0].data, [10, null, 30, 31]);
  assert.deepEqual(line.series[1].data, [null, 20, null, null]);
  const bar = salesChartOption({ ...chart, kind: 'bar' }, 'dark');
  assert.deepEqual(bar.xAxis.data, ['2026-01', '2026-03', '2026-03', '2026-02']);
});
test('invalid schema, periods, nonfinite metrics, raw booleans and scope mismatches reject visibly', () => {
  for (const raw of [{ ...fixture.catalog, schemaVersion: 2 }, { ...fixture.catalog, version: '' }, { ...fixture.catalog, generatedAt: 'bad' }, { ...fixture.catalog, regions: [...fixture.catalog.regions, fixture.catalog.regions[0]] }]) assert.throws(() => readSalesCatalog(raw));
  const badPeriod = structuredClone(fixture.catalog); badPeriod.regions[0].periods[0].id = '2026-13'; assert.throws(() => readSalesCatalog(badPeriod));
  assert.throws(() => readSalesData(fixture.detail(), 'rf', '2026-06'));
  assert.throws(() => readSalesData(fixture.detail(), 'msk', '2026-05'));
  for (const update of [raw => raw.metrics[0].value = Infinity, raw => raw.metrics[0].digits = 21, raw => raw.tables[0].rows[0].volume = true, raw => delete raw.tables[0].rows[0].sold, raw => raw.charts[0].series[0].points[0].y = '5', raw => raw.charts[0].kind = 'pie']) { const raw = fixture.detail(); update(raw); assert.throws(() => readSalesData(raw, 'msk', '2026-06')); }
});
test('empty catalogs, regional periods, metrics, charts, tables and missing dates remain empty', () => {
  assert.equal(salesFilters(readSalesCatalog({ ...fixture.catalog, regions: [] }), new URLSearchParams()).region, undefined);
  const c = structuredClone(fixture.catalog); c.regions[0].periods = []; assert.equal(salesFilters(readSalesCatalog(c), new URLSearchParams()).period, undefined);
  const raw = fixture.detail(); raw.source.date = null; raw.charts = []; raw.metrics = []; raw.tables.forEach(t => { t.columns = []; t.rows = []; });
  assert.deepEqual(readSalesData(raw, 'msk', '2026-06'), raw);
});
test('API loaders use abort signal and never read a frozen fallback on failures', async () => {
  const original = globalThis.fetch, calls = [], controller = new AbortController();
  try {
    globalThis.fetch = async (url, options) => { calls.push([url, options]); return new Response(JSON.stringify(url.endsWith('/catalog') ? fixture.catalog : fixture.detail()), { status: 200 }); };
    await loadSalesCatalog(controller.signal); await loadSalesData('msk', '2026-06', controller.signal);
    assert.deepEqual(calls.map(c => c[0]), ['/api/v1/sales/catalog', '/api/v1/sales?region=msk&period=2026-06']);
    assert.ok(calls.every(c => c[1].signal === controller.signal && c[1].cache === 'no-store'));
    globalThis.fetch = async () => new Response('offline', { status: 503 });
    await assert.rejects(loadSalesCatalog(controller.signal), /HTTP 503/);
    globalThis.fetch = async () => { throw new Error('offline'); };
    await assert.rejects(loadSalesData('msk', '2026-06', controller.signal), /серверу данных/);
  } finally { globalThis.fetch = original; }
});
