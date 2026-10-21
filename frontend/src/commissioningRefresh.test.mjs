import assert from 'node:assert/strict';
import test from 'node:test';
import fixtures from './commissioning.browser.cjs';
import { operationalChartOption, operationalChartRows, readOperationalReport } from './commissioningData.ts';
import { operationalScopeParams } from './operationalViewData.ts';

test('operational forecasts extend only supplied years, retain zeros and remain separate from annual actuals', () => {
  const raw = fixtures.operationalReport();
  raw.tables[0].annualForecasts = [{ year: 2026, value: 7.5 }, { year: 2027, value: 7.6 }, { year: 2029, value: 0 }];
  raw.tables[0].chart.actualMonths = raw.tables[0].chart.x.map((_, i) => i === 15 ? 10 : null);
  const data = readOperationalReport(raw, fixtures.operationalSelection), table = data.tables[0], before = structuredClone(table);
  for (const theme of ['light', 'dark']) {
    const option = operationalChartOption(table, data.periodLabel, theme);
    assert.equal(option.series[1].name, 'Годовые значения');
    assert.equal(option.series[1].data[15], null);
    assert.equal(option.series[2].itemStyle.borderType, 'dashed');
    assert.ok(option.series[2].itemStyle.decal);
    assert.deepEqual(option.xAxis.data.slice(-2), ['2027', '2029']);
    assert.equal(option.series[2].data.at(-1), 0);
    assert.equal(option.xAxis.axisLabel.formatter('2026', 15), '2026\nянв–окт');
    assert.ok(option.tooltip.formatter({ dataIndex: 16, seriesIndex: 2, seriesName: 'Годовой прогноз', name: '2027', value: 7.6 }).includes('7,6 млн м²'));
  }
  assert.equal(operationalChartRows(table).at(-1).at(-1), 0);
  assert.deepEqual(table, before);
});

test('monthly structure schema retains incomplete sums and independent month/renovation comparisons', () => {
  const raw = fixtures.operationalReport();
  raw.structure = { year: 2026, requestedMonth: 8, actualMonth: 8, sourceDate: '2026-10-16', rows: [{ id: 'total', label: 'Всего', value: null, knownSum: 10.123, monthChange: 1.23, yearChangePct: null, renovation: 2, renovationKnownSum: 2, renovationMonthChange: 0, renovationYearChangePct: null }] };
  assert.deepEqual(readOperationalReport(raw, fixtures.operationalSelection).structure, raw.structure);
  raw.structure.actualMonth = 9;
  assert.throws(() => readOperationalReport(raw, fixtures.operationalSelection));
  assert.equal(operationalScopeParams(new URLSearchParams('structureMonth=9&housingMonth=5'), 'structure').get('month'), '9');
  assert.equal(operationalScopeParams(new URLSearchParams('structureMonth=9&housingMonth=5'), 'housing').get('month'), '5');
});
