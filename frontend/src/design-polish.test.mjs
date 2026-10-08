import test from 'node:test';
import assert from 'node:assert/strict';
import { operationalScopeParams } from './operationalViewData.ts';
import { salesChartOption, salesChartRows } from './salesData.ts';
import { economicsChartOption } from './economicsData.ts';

test('each operational visualization keeps only its own filters and supports legacy links', () => {
  const params = new URLSearchParams('housingMonth=12&nonresMonth=3&nonresExcludeMkd=true&year=2021&quarter=2&cumulative=true');
  assert.equal(operationalScopeParams(params, 'housing').toString(), 'month=12');
  assert.equal(operationalScopeParams(params, 'nonres').toString(), 'month=3&exclude_mkd=true');
  assert.equal(operationalScopeParams(params, 'structure').toString(), 'year=2021&quarter=2&cumulative=true');
  assert.equal(operationalScopeParams(new URLSearchParams('month=6&exclude_mkd=true'), 'nonres').toString(), 'month=6&exclude_mkd=true');
});
test('forecast omits only completely missing years, sorts years naturally and retains source CSV', () => {
  const chart = { id: 'forecast', kind: 'bar', unit: '', series: [
    { id: 'volume', name: 'Volume', unit: 'тыс. м²', points: [{ x: '2026', y: 1000 }, { x: '2031+', y: 200 }, { x: '2020', y: null }, { x: '2025+', y: null }] },
    { id: 'sold', name: 'Sold', unit: '%', points: [{ x: '2026', y: 70 }, { x: '2020', y: null }, { x: '2028', y: 0 }, { x: '2031+', y: 65 }] },
  ] };
  const option = salesChartOption(chart, 'dark');
  assert.deepEqual(option.xAxis.data, ['2026', '2028', '2031+']);
  assert.deepEqual(option.series[0].data, [1000, null, 200]);
  assert.deepEqual(option.series[1].data, [70, 0, 65]);
  assert.equal(option.series[1].yAxisIndex, 1);
  assert.equal(salesChartRows(chart).length, 9);
});
test('non-stacked percentage indices use padded observed range, stacked shares keep zero baseline', () => {
  const chart = { id: 'index', unit: '%', kind: 'line', rows: [{ year: 2020 }, { year: 2021 }], series: [{ id: 'a', name: 'A', unit: '%', points: [{ x: 2020, y: 100.2 }, { x: 2021, y: 101.7 }] }] };
  const option = economicsChartOption(chart, 'dark');
  assert.ok(option.yAxis.min > 99 && option.yAxis.max < 103);
  assert.deepEqual(option.series[0].data, [100.2, 101.7]);
  assert.equal(economicsChartOption({ ...chart, stack: true, kind: 'bar' }, 'light').yAxis.min, undefined);
  assert.equal(economicsChartOption({ ...chart, series: [] }, 'light').yAxis.min, undefined);
});
