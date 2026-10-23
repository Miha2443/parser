import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { characteristicChartOption, characteristicRows, characteristics, cumulativeLabel, flowChartOption, flowRows, preferentialSelection, preferentialSourceUrl, preferentialUrl, programs, readPreferentialCatalog, readPreferentialReport } from './preferentialMortgageData.ts';

const metadata = () => ({ schemaVersion: 1, version: 'generation', generatedAt: '2026-10-23T10:00:00Z', source: { url: preferentialSourceUrl, date: '2026-10-11', files: ['data/raw/realty/domrf/preferential_mortgage.json'], collectedAt: '2026-10-23T09:00:00Z' } });
const selection = { region: 'msk', month: 10, characteristicMonth: 7 };
const catalog = () => ({ ...metadata(), defaultRegion: 'msk', regions: [{ id: 'msk', label: 'Москва', periods: ['2026-01', '2026-10'], characteristicPeriods: ['2026-01', '2026-07'] }, { id: 'rf', label: 'Российская Федерация', periods: ['2025-12', '2026-09'] }], months: Array.from({ length: 12 }, (_, i) => ({ id: i + 1, label: `По месяц ${i + 1}` })) });
const flow = (year, throughMonth = 12, volume = [10000, 20000, 3000]) => ({ year, throughMonth, programs: Object.fromEntries(programs.map((p, i) => [p.id, { volumeMln: volume[i], count: volume[i] === null ? null : volume[i] / 2 }])), total: { volumeMln: volume.every(v => v !== null) ? volume.reduce((a, b) => a + b, 0) : null, count: volume.every(v => v !== null) ? volume.reduce((a, b) => a + b, 0) / 2 : null } });
const characteristic = (year, throughMonth = 12) => ({ year, throughMonth, throughMonths: Object.fromEntries(characteristics.map(m => [m.id, throughMonth])), programThroughMonths: Object.fromEntries(programs.map(p => [p.id, Object.fromEntries(characteristics.map(m => [m.id, throughMonth]))])), programs: Object.fromEntries(programs.map((p, i) => [p.id, Object.fromEntries(characteristics.map(m => [m.id, i + 5.4]))])) });
const report = () => ({ ...metadata(), selection, regionLabel: 'Москва', latestPeriod: '2026-10', latestPeriodPartial: true, annual: [flow(2025), flow(2026, 10)], cumulative: [flow(2025, 10), flow(2026, 10)], characteristicsAnnual: [characteristic(2025), characteristic(2026, 7)], characteristicsCumulative: [characteristic(2025, 7), characteristic(2026, 7)], notes: ['Средние за год взяты из отчёта.'] });

test('preferential catalog is restricted to intended source and valid unique observations', () => {
  assert.equal(readPreferentialCatalog(catalog()).regions.length, 2);
  for (const mutate of [r => { r.schemaVersion = 2; }, r => { r.source.url = 'https://example.com/'; }, r => { r.source.url += '?other'; }, r => { r.source.url = preferentialSourceUrl.replace('https:', 'http:'); }, r => { r.source.collectedAt = 'yesterday'; }, r => { r.regions[0].periods.push('2026-13'); }, r => { r.regions[0].periods.push('2026-10'); }, r => { r.regions[0].id = 'mo'; }, r => { r.months.pop(); }, r => { r.months[0].id = 2; }]) { const raw = catalog(); mutate(raw); assert.throws(() => readPreferentialCatalog(raw)); }
});
test('selection defaults independently to latest source issue month and characteristic month', () => {
  assert.deepEqual(preferentialSelection(readPreferentialCatalog(catalog()), new URLSearchParams()), selection);
  assert.deepEqual(preferentialSelection(readPreferentialCatalog(catalog()), new URLSearchParams('region=rf&month=12&characteristicMonth=2')), { region: 'rf', month: 12, characteristicMonth: 2 });
  assert.equal(preferentialSelection(null, new URLSearchParams()), null);
  assert.deepEqual(preferentialSelection(readPreferentialCatalog(catalog()), new URLSearchParams('month=0&characteristicMonth=13')), selection);
});
test('report validates filters, preserves zero and missing cells and detaches source', () => {
  const raw = report(); raw.annual[0] = flow(2025, 12, [0, 20, null]);
  const result = readPreferentialReport(raw, selection);
  assert.equal(result.annual[0].programs.preferential.volumeMln, 0);
  assert.equal(result.annual[0].programs.it.volumeMln, null);
  result.annual[0].programs.family.volumeMln = 35;
  assert.equal(raw.annual[0].programs.family.volumeMln, 20);
  assert.throws(() => readPreferentialReport(raw, { ...selection, month: 8 }));
  for (const mutate of [r => { r.annual.push(r.annual[0]); }, r => { r.annual[0].total.count = 1; }, r => { r.cumulative[0].programs.it.volumeMln = -2; }, r => { delete r.characteristicsAnnual[0].programs.family.ratePct; }, r => { r.characteristicsAnnual[0].programs.family.ratePct = Infinity; }, r => { r.annual[0].throughMonth = 13; }]) { const value = report(); mutate(value); assert.throws(() => readPreferentialReport(value, selection)); }
});
test('version-pinned export includes both independent month filters', () => {
  const url = new URL(preferentialUrl(selection, 'version-2'), 'http://localhost');
  assert.equal(url.pathname, '/api/v1/preferential-mortgage/export');
  assert.equal(url.searchParams.get('month'), '10');
  assert.equal(url.searchParams.get('characteristicMonth'), '7');
  assert.equal(url.searchParams.get('required_version'), 'version-2');
});
test('unknown month completeness is preserved, never converted to a complete month', () => {
  const value = report(); value.latestPeriodPartial = null;
  assert.equal(readPreferentialReport(value, selection).latestPeriodPartial, null);
  delete value.latestPeriodPartial; assert.throws(() => readPreferentialReport(value, selection));
});
test('flow bars show every year and explicitly label the current partial period', () => {
  const rows = [flow(2018), flow(2026, 10)], option = flowChartOption(rows, 'volumeMln', 'light');
  assert.deepEqual(option.xAxis.data, ['2018', '2026']);
  assert.equal(option.xAxis.axisLabel.interval, 0);
  assert.equal(option.xAxis.axisLabel.formatter('', 0), '2018');
  assert.equal(option.xAxis.axisLabel.formatter('', 1), '2026\nянв–окт');
  assert.equal(option.series.length, 3);
  assert.equal(option.series[0].stack, 'programs');
  assert.equal(option.series[0].label.formatter({ value: 10 }), '10');
  assert.equal(option.series[0].label.formatter({ value: 0.01 }), '');
  assert.equal(option.series[2].markPoint.data[0].value, 33);
  assert.match(option.tooltip.formatter({ dataIndex: 1, seriesIndex: 1 }), /Семейная ипотека/);
});
test('program visibility recomputes totals, shares and thresholds', () => {
  const rows = [flow(2026, 10)], option = flowChartOption(rows, 'count', 'light', 700, ['preferential', 'it'], true);
  assert.equal(option.series.length, 1);
  assert.equal(option.series[0].markPoint.data[0].value, 10);
  assert.match(option.tooltip.formatter({ dataIndex: 0, seriesIndex: 0 }), /100%/);
  assert.equal(flowChartOption(rows, 'count', 'light', 700, programs.map(p => p.id)).series.length, 0);
});
test('unknown components never clip the known segments or invent a total', () => {
  const option = flowChartOption([flow(2026, 10, [0, 200000, null])], 'volumeMln', 'light');
  assert.ok(option.yAxis.max > 200);
  assert.equal(option.series[2].markPoint.data.length, 0);
  assert.doesNotMatch(option.tooltip.formatter({ dataIndex: 0, seriesIndex: 1 }), /Доля/);
});
test('a thousand in a displayed chart suppresses decimals for the entire chart', () => {
  const rows = [flow(2025, 12, [1020200, 300200, 399200])], option = flowChartOption(rows, 'volumeMln', 'light');
  assert.equal(option.series[0].label.formatter({ value: 300.2 }), '300');
  assert.equal(option.yAxis.axisLabel.formatter(399.2), '399');
});
test('characteristic chart preserves unknowns with calendar labels and explicit units', () => {
  const rows = [characteristic(2018), characteristic(2026, 7)]; rows[0].programs.it.ratePct = null;
  const option = characteristicChartOption(rows, 'ratePct', 'light', 900, [], true);
  assert.equal(option.series[2].data[0], null);
  assert.equal(option.xAxis.axisLabel.formatter('', 1), '2026\nянв–июл');
  assert.match(option.tooltip.formatter({ dataIndex: 1, seriesIndex: 0, value: 5.4 }), /5,4 %/);
  assert.equal(characteristicChartOption(Array.from({ length: 9 }, (_, i) => characteristic(2018 + i)), 'ratePct', 'light', 320).series[0].label.show, false);
  assert.equal(flowRows([flow(2018)], 'volumeMln')[1][0], 2018);
  assert.equal(characteristicRows(rows, 'ratePct')[1][4], null);
  assert.equal(cumulativeLabel(1), 'янв');
});
test('characteristics use metric-specific rather than operational coverage', () => {
  const row = characteristic(2026, 10); row.throughMonths.ratePct = 7; row.programThroughMonths.family.ratePct = 7;
  const option = characteristicChartOption([row], 'ratePct', 'light');
  assert.equal(option.xAxis.axisLabel.formatter('', 0), '2026\nянв–июл');
  assert.match(option.tooltip.formatter({ dataIndex: 0, seriesIndex: 1, value: 6.4 }), /янв–июл/);
  assert.equal(characteristicRows([row], 'ratePct')[1][1], 'янв–июл');
  assert.equal(characteristicRows([row], 'ratePct')[1][6], 'янв–июл');
  assert.equal(flowRows([flow(2026, 10)], 'count', '2026-10-11')[1].at(-1), '2026-10-11');
});
test('page and both navigation surfaces expose the new usable report', () => {
  const component = readFileSync(new URL('./PreferentialMortgage.tsx', import.meta.url), 'utf8');
  assert.match(component, /rule="preferentialMortgage"/);
  assert.match(component, /Накопленный период выдачи/);
  assert.match(component, /Накопленный период характеристик/);
  assert.match(component, /Источник и расчёты/);
  for (const file of ['App.tsx', 'Sidebar.tsx', 'ServicePages.tsx']) assert.match(readFileSync(new URL(`./${file}`, import.meta.url), 'utf8'), /\/economics\/preferential-mortgage/);
});
