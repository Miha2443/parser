import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { ApiError, request } from './data.ts';
import { breakdownChart, breakdownOption, constructionHistoryChart, constructionHistoryRows, constructionRegion, domRfForecastSelection, domRfUrl, forecastBasisNote, forecastFields, legacySalesForecastSeed, loadDomRfData, loadPermitsData, loadPermitsSelection, observedRange, permitKindChange, permitRequestFilters, permitsMainOption, permitsMainRows, permitsUrl, readDomRfData, readPermitsData, readinessChartOption, realizationDonutOption, realizationHistoryOption, realizationHistoryRows } from './constructionPagesData.ts';
import { breakdownReconciliation, permitAnnualBasis, permitBreakdownRows, permitCoverageComplete, permitGrowth, permitRemainder } from './permitPresentation.ts';
import { permitPageFilters } from './constructionPagesData.ts';

const metadata = () => ({ schemaVersion: 1, version: 'unit-test + version', generatedAt: '2026-10-19T08:00:00.123456+03:00', source: { date: '2026-10-16', files: ['selected/source.xlsx'], fileEvidence: 'selected', issues: [{ level: 'warning', message: 'Source warning <not markup>' }] } });
const tooltipText = markup => markup.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
function block(id, fields) {
  const rows = [{ year: 2011, periodComplete: true, annualComplete: true, ...Object.fromEntries(fields.map((field, i) => [field, i === 0 ? 0 : i === 1 ? null : 1234.567890123])) }, { year: 2026, periodComplete: false, annualComplete: false, ...Object.fromEntries(fields.map((field, i) => [field, 1000 + i])) }];
  return { id, title: id, unit: 'тыс. м²', periodLabel: 'Январь–сентябрь', aggregateField: id === 'housing_components' ? 'housing_total' : id === 'area_composition' ? 'total' : 'nonresidential', columns: [{ id: 'year', label: 'Год' }, ...fields.map(id => ({ id, label: id }))], rows, series: fields.map(id => ({ id, name: id === 'mop_embedded' ? 'МОП и встроенное нежильё' : id, points: rows.map(row => ({ x: String(row.year), y: row[id] })) })), notes: ['Independent historical basis, not equal to monthly main report.'] };
}
const breakdowns = () => [block('housing_components', ['living', 'mop_embedded', 'housing_total']), block('nonresidential_historical', ['nonresidential', 'offices', 'industry', 'social', 'trade', 'hotels', 'other']), block('nonresidential_native', ['nonresidential', 'native_a', 'native_b', 'unknown']), block('area_composition', ['living', 'mop_embedded', 'nonresidential', 'total'])];
function permitsFixture() {
  const rows = [{ 'Год': 2011, 'За год, тыс. м²': 1234.567890123, 'С начала года, тыс. м²': null, 'Изменение, %': null }, { 'Год': 2026, 'За год, тыс. м²': null, 'С начала года, тыс. м²': 0, 'Изменение, %': -100 }];
  const blocks = breakdowns(), allMonths = Array.from({ length: 12 }, (_, i) => i + 1);
  const presentation = { rows: [{ year: 2011, selected: null, annualActual: 1234.567890123, annualForecast: null, basis: 'annual_observed', difference: null, remainder: null, growth: null, periodComplete: true, annualComplete: true, observedThrough: '2011-12-31', evidence: ['Confirmed annual'] }, { year: 2026, selected: 0, annualActual: null, annualForecast: 9876.543210123, basis: 'forecast', difference: 9876.543210123, remainder: 9876.543210123, growth: null, periodComplete: false, annualComplete: false, observedThrough: '2026-09-15', evidence: ['Partial September <source>'] }] };
  return { ...metadata(), contractRevision: 2, selection: { permitKind: 'housing', month: 9, structure: { mode: 'period', monthFrom: 1, monthTo: 9, yearFrom: 2011, yearTo: 2026 } }, controls: { permitKinds: [{ id: 'housing', label: 'Жильё' }, { id: 'nonresidential', label: 'Нежильё' }, { id: 'total', label: 'Всего' }], monthsByKind: { housing: [1, 9], nonresidential: [9], total: [1, 9] }, structure: { modes: [{ id: 'period', label: 'Выбранные месяцы' }, { id: 'annual', label: 'Полный год' }], years: Array.from({ length: 16 }, (_, i) => 2011 + i), months: allMonths, coverage: blocks.flatMap(b => [2011, 2026].map(year => ({ blockId: b.id, year, months: year === 2011 ? allMonths : allMonths.slice(0, 9), completeMonths: year === 2011 ? allMonths : allMonths.slice(0, 8), annualComplete: year === 2011, observedThrough: year === 2011 ? '2011-12-31' : '2026-09-15', evidence: ['Coverage evidence'] }))) } }, permits: { region: 'Москва', unit: 'тыс. м²', periodLabel: 'Январь–сентябрь', columns: Object.keys(rows[0]).map(id => ({ id, label: id })), rows, chart: { x: ['2011', '2026'], period: [{ value: null }, { value: 0 }], remainder: [{ value: 1234.567890123 }, { value: null }], totals: [{ value: 1234.567890123 }, { value: 0 }], growth: [{ value: null }, { value: -100 }] }, presentation }, forecast: { unit: 'тыс. м²', unitBasis: 'assumed_thousand_m2', source: { file: 'selected/forecast.xlsx', sheet: 'Прогнозы' }, rows: [2026, 2027].map((year, i) => ({ year, ...Object.fromEntries(forecastFields.map((f, j) => [f, i ? null : j === 0 ? 9876.543210123 : j === 1 ? 0 : null])), cells: { year: 'D2', housing_living: 'D3' } })), notes: ['No explicit unit header.'] }, breakdowns: blocks, notes: ['Main notes'] };
}
function domrfFixture(region = 'msk') {
  const metrics = { total_living_thousand_m2: 10001.123456789, sales_open_thousand_m2: 7500.25, sold_thousand_m2: 1234.567890123, sold_pct: 12, unsold_thousand_m2: 0, unsold_pct: 0, sales_not_open_thousand_m2: null, sales_not_open_pct: null, price_per_m2_rub: 441306.789, funds_million_rub: 3250869.123 };
  return { ...metadata(), region: { id: region, label: region }, construction: { period: { id: '2026-10', label: 'Октябрь 2026', reportDate: '2026-10-01' }, metrics: { living: 15000.123456789, total: 0 }, history: [{ period: '2026-10', label: 'Октябрь 2026', reportDate: '2026-10-01', living: 15000.123456789, total: 0, sourceFile: 'construction.json' }] }, sales: { period: { id: '2026-08', label: 'Август 2026', reportDate: '2026-09-11' }, metrics, history: [{ period: '2026-08', label: 'Август 2026', reportDate: '2026-09-11', metrics: { ...metrics }, sourceFile: 'sales.json' }] }, notes: ['Independent construction/sales reporting periods.'] };
}

function previousPermitsFixture() {
  const raw = permitsFixture();
  delete raw.contractRevision;
  delete raw.selection.structure; delete raw.controls.structure; delete raw.permits.presentation;
  for (const block of raw.breakdowns) {
    delete block.aggregateField;
    for (const row of block.rows) { delete row.periodComplete; delete row.annualComplete; }
  }
  return raw;
}

test('previous permits API is rejected with a deployment mismatch, without inventing current fields', async () => {
  const raw = previousPermitsFixture(), before = structuredClone(raw);
  const mismatch = /frontend новее backend.*завершить обновление backend/;
  assert.throws(() => readPermitsData(raw), mismatch);
  assert.deepEqual(raw, before);
  assert.throws(() => readPermitsData({ ...raw, contractRevision: 1 }), mismatch);
  const original = globalThis.fetch, calls = [], controller = new AbortController();
  try {
    globalThis.fetch = async url => { calls.push(url); return new Response(JSON.stringify(raw)); };
    await assert.rejects(loadPermitsSelection({ permitKind: 'housing', month: 9 }, controller.signal), mismatch);
    assert.deepEqual(calls, ['/api/v1/construction/permits?permit_kind=housing&month=9']);
  } finally { globalThis.fetch = original; }
});

test('malformed permits responses retain the generic schema error, not a deployment diagnosis', () => {
  const generic = { message: 'Ответ API строительства не соответствует схеме версии 1.' };
  for (const value of [null, {}, [], { schemaVersion: 1, selection: {}, controls: {}, permits: {} }]) {
    assert.throws(() => readPermitsData(value), generic);
  }
  for (const edit of [r => delete r.contractRevision, r => r.contractRevision = 1, r => r.contractRevision = 3,
    r => delete r.selection.structure, r => delete r.controls.structure, r => delete r.permits.presentation,
    r => r.selection.structure = null, r => r.permits.presentation = {}, r => r.permits.presentation.rows[0].selected = '1']) {
    const raw = permitsFixture(); edit(raw); assert.throws(() => readPermitsData(raw), generic);
  }
  for (const edit of [r => r.schemaVersion = 2, r => r.contractRevision = 2, r => r.version = '', r => r.selection.month = 13,
    r => r.controls.permitKinds = [], r => r.permits.region = 'РФ', r => r.source.date = '2026-02-30', r => r.forecast = null,
    r => r.permits.chart = {}, r => r.permits.rows[0]['Год'] = true, r => r.forecast.rows[0].value = NaN, r => r.notes = [1]]) {
    const raw = previousPermitsFixture(); edit(raw); assert.throws(() => readPermitsData(raw), generic);
  }
});

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
  assert.equal(data.contractRevision, 2); assert.equal(data.schemaVersion, 1);
  assert.deepEqual(raw, before);
  assert.deepEqual(data.permits.chart, raw.permits.chart);
  assert.deepEqual(data.permits.rows, raw.permits.rows);
  assert.deepEqual(data.permits.presentation, raw.permits.presentation);
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

test('annual forecast shares the actual year stack and ignores misleading legacy totals', () => {
  const data = readPermitsData(permitsFixture()), before = structuredClone(data), option = permitsMainOption(data, 'light');
  assert.deepEqual(data, before);
  assert.deepEqual(option.xAxis.data, ['2011', '2026']);
  assert.equal(option.series.length, 2);
  assert.deepEqual(option.series[0].data, [null, 0]);
  assert.deepEqual(option.series[1].data, [null, 9876.543210123]);
  assert.equal(option.series[0].stack, option.series[1].stack);
  assert.equal(option.series[1].itemStyle.color, '#e4eaf0');
  assert.equal(option.series[1].itemStyle.borderType, 'dashed');
  assert.ok(option.series[1].itemStyle.decal);
  assert.equal(option.tooltip.trigger, 'item');
  const tooltip = option.tooltip.formatter({ dataIndex: 1, seriesIndex: 1 });
  assert.equal(tooltipText(tooltip), '2026 · прогноз на год Остаток до годового прогноза: 9 877 тыс. м²');
  assert.ok(forecastBasisNote.includes('не пересчитывается'));
  data.permits.chart.totals[1].value = 999999999;
  data.forecast.rows[0].value = 999999999;
  data.selection.month = 1;
  assert.equal(permitsMainOption(data, 'dark').series[1].data.at(-1), 9876.543210123);
  data.permits.presentation.rows[1].annualForecast = 0;
  assert.equal(permitsMainOption(data, 'light').series[1].data.at(-1), 0);
  data.permits.presentation.rows[1].annualForecast = null;
  assert.equal(permitsMainOption(data, 'light').series.length, 1);
});

test('December remains selected while latest partial year gets its actual cumulative month below the year', () => {
  const raw = permitsFixture();
  raw.selection.month = 12;
  raw.controls.monthsByKind.housing = Array.from({ length: 12 }, (_, i) => i + 1);
  raw.permits.periodLabel = 'Январь–декабрь';
  Object.assign(raw.permits.presentation.rows[0], { selected: 1234.567890123, actualMonth: 12 });
  Object.assign(raw.permits.presentation.rows[1], { selected: 300.2, actualMonth: 10, observedThrough: '2026-10-19' });
  const data = readPermitsData(raw, { permitKind: 'housing', month: 12 });
  assert.equal(data.selection.month, 12);
  assert.equal(data.permits.presentation.rows[1].actualMonth, 10);
  const option = permitsMainOption(data, 'light');
  assert.equal(option.xAxis.axisLabel.formatter('2011', 0), '2011');
  assert.equal(option.xAxis.axisLabel.formatter('2026', 1), '2026\nянв–окт');
  assert.equal(tooltipText(option.tooltip.formatter({ dataIndex: 1, seriesIndex: 0 })), '2026 · янв–окт Выбранный период: 300 тыс. м²');
  assert.equal(data.permits.presentation.rows[0].selected, 1234.567890123);
  for (const value of [0, 13, 10.5, '10']) {
    const invalid = structuredClone(raw); invalid.permits.presentation.rows[1].actualMonth = value;
    assert.throws(() => readPermitsData(invalid));
    const invalidBlock = structuredClone(raw); invalidBlock.breakdowns[0].rows[1].actualMonth = value;
    assert.throws(() => readPermitsData(invalidBlock));
  }
});

test('permits page discards hidden year and start-month ranges from old links before requesting all years', () => {
  const filters = permitPageFilters(new URLSearchParams('permit_kind=nonresidential&month=12&structure_mode=period&structure_year_from=2022&structure_year_to=2023&structure_month_from=4&structure_month_to=10'));
  assert.deepEqual(filters, { permitKind: 'nonresidential', month: 12, structure: { mode: 'period', monthFrom: 1, monthTo: 10 } });
  assert.doesNotMatch(permitsUrl(filters), /structure_year_|structure_month_from=4/);
});

test('confirmed annual remainders are solid while forecasts are hatched in the same chart', () => {
  const data = readPermitsData(permitsFixture());
  data.permits.presentation.rows[0].selected = 100;
  const option = permitsMainOption(data, 'light');
  assert.equal(option.series.length, 3);
  assert.equal(option.series[1].id, 'remainder');
  assert.equal(option.series[1].itemStyle.borderType, undefined);
  assert.deepEqual(option.series[1].data, [1234.567890123 - 100, null]);
  assert.equal(option.series[2].id, 'forecast');
  assert.equal(option.series[2].itemStyle.borderType, 'dashed');
  assert.deepEqual(option.series[2].data, [null, 9876.543210123]);
});

test('permit CSV exports explicit presentation, completion, provenance and version, never legacy annual sums', () => {
  const data = readPermitsData(permitsFixture()), rows = permitsMainRows(data);
  assert.equal(rows.length, 3);
  assert.equal(rows[2][1], 0); assert.equal(rows[2][2], null); assert.equal(rows[2][3], 9876.543210123);
  assert.equal(rows[1][2], 1234.567890123); assert.equal(rows[2][5], 9876.543210123); assert.equal(rows[2][6], 9876.543210123);
  assert.equal(rows[2][8], 'Нет'); assert.equal(rows[2][9], 'Нет'); assert.equal(rows[2][10], '2026-09-15');
  assert.equal(rows[2][12], 'assumed_thousand_m2'); assert.equal(rows[2][13], data.version);
  assert.ok(!rows[0].some(cell => cell === 'За год, тыс. м²'));
  assert.ok(rows[0][2].includes('Подтверждённый январь–декабрь'));
  assert.deepEqual(data.permits.rows, permitsFixture().permits.rows);
});

test('breakdown stacks exclude aggregate series even if a provider includes them; source rows remain complete', () => {
  const raw = breakdowns(), expected = [['living', 'mop_embedded'], ['offices', 'industry', 'social', 'trade', 'hotels', 'other'], ['native_a', 'native_b', 'unknown'], ['living', 'mop_embedded', 'nonresidential']];
  raw.forEach((b, i) => {
    const before = structuredClone(b), chart = breakdownChart(b), option = breakdownOption(b, 'light');
    assert.deepEqual(chart.series.map(s => s.id), expected[i]);
    assert.equal(option.series.length, expected[i].length);
    assert.ok(option.series.every(s => s.stack === 'permit-area'));
    assert.ok(!option.series.some(s => s.id === b.aggregateField));
    assert.deepEqual(b, before);
    assert.ok(b.columns.length > chart.series.length);
    assert.equal(option.tooltip.trigger, 'item');
    assert.ok(option.tooltip.formatter({ dataIndex: 0, seriesIndex: 0 }).includes('тыс. м²'));
  });
});

test('main permit hover shows only the selected segment or actual gray remainder, never the annual total', () => {
  const data = readPermitsData(permitsFixture()), row = data.permits.presentation.rows[1];
  row.selected = 1234.567890123; row.annualComplete = true;
  for (const [forecast, actual, expectedRemainder] of [[9876.543210123, 20000, '8 642'], [null, 5432.109876543, '4 198']]) {
    row.annualForecast = forecast; row.annualActual = actual;
    const before = structuredClone(data), remainder = (forecast ?? actual) - row.selected;
    for (const theme of ['light', 'dark']) {
      const option = permitsMainOption(data, theme);
      assert.equal(option.tooltip.trigger, 'item');
      assert.equal(tooltipText(option.tooltip.formatter({ dataIndex: 1, seriesIndex: 0 })), '2026 · Январь–сентябрь Выбранный период: 1 235 тыс. м²');
      assert.equal(tooltipText(option.tooltip.formatter({ dataIndex: 1, seriesIndex: 1 })), forecast === null ? `2026 · Январь–сентябрь Остаток до годового ориентира: ${expectedRemainder} тыс. м²` : `2026 · прогноз на год Остаток до годового прогноза: ${expectedRemainder} тыс. м²`);
      assert.equal(option.series[0].data[1], row.selected); assert.equal(option.series[1].data[1], remainder);
    }
    const csv = permitsMainRows(data);
    assert.equal(csv[2][1], row.selected); assert.equal(csv[2][2], actual); assert.equal(csv[2][3], forecast);
    assert.equal(csv[2][5], remainder); assert.equal(csv[2][6], remainder); assert.equal(permitRemainder(row), remainder);
    assert.deepEqual(data, before);
  }
});

test('every component in every permit breakdown has its own compact rounded hover', () => {
  for (const block of breakdowns()) {
    const components = block.series.filter(s => s.id !== block.aggregateField);
    components.forEach((component, i) => {
      const value = (i + 1) * 10 + 0.123456789;
      component.points[1].y = value; block.rows[1][component.id] = value;
    });
    const before = structuredClone(block), csvBefore = permitBreakdownRows(block, 'full-precision');
    for (const theme of ['light', 'dark']) {
      const option = breakdownOption(block, theme);
      assert.equal(option.tooltip.trigger, 'item');
      components.forEach((component, seriesIndex) => {
        const tooltip = option.tooltip.formatter({ dataIndex: 1, seriesIndex });
        assert.equal(tooltipText(tooltip), `2026 · Январь–сентябрь ${component.name}: ${(seriesIndex + 1) * 10} тыс. м²`, `${block.id}/${component.id}`);
        assert.equal(option.series[seriesIndex].data[1], component.points[1].y);
        assert.equal(csvBefore[2][block.columns.findIndex(c => c.id === component.id)], component.points[1].y);
      });
    }
    assert.deepEqual(permitBreakdownRows(block, 'full-precision'), csvBefore);
    assert.deepEqual(block, before);
  }
});

test('permit item hovers format zero compactly and null as missing without substituting annual values', () => {
  const data = readPermitsData(permitsFixture());
  data.permits.presentation.rows[1].annualForecast = 0;
  const main = permitsMainOption(data, 'light'), breakdown = breakdownOption(data.breakdowns[0], 'light');
  for (const [option, dataIndex, seriesIndex, expected] of [
    [main, 1, 0, '2026 · Январь–сентябрь Выбранный период: 0 тыс. м²'],
    [main, 1, 1, '2026 · прогноз на год Остаток до годового прогноза: 0 тыс. м²'],
    [main, 0, 0, '2011 · Январь–сентябрь Выбранный период: — тыс. м²'],
    [main, 0, 1, '2011 · прогноз на год Остаток до годового прогноза: — тыс. м²'],
    [breakdown, 0, 0, '2011 · Январь–сентябрь living: 0 тыс. м²'],
    [breakdown, 0, 1, '2011 · Январь–сентябрь МОП и встроенное нежильё: — тыс. м²'],
  ]) assert.equal(tooltipText(option.tooltip.formatter({ dataIndex, seriesIndex })), expected);
});

test('permit hovers default to the first series and array input uses only its first item', () => {
  const data = readPermitsData(permitsFixture());
  for (const option of [permitsMainOption(data, 'light'), breakdownOption(data.breakdowns[0], 'light')]) {
    const first = { dataIndex: 1, seriesIndex: 1 }, second = { dataIndex: 0, seriesIndex: 0 };
    assert.equal(tooltipText(option.tooltip.formatter([first, second])), tooltipText(option.tooltip.formatter(first)));
    assert.equal(tooltipText(option.tooltip.formatter([second, first])), tooltipText(option.tooltip.formatter(second)));
    const selected = tooltipText(option.tooltip.formatter({ dataIndex: 1, seriesIndex: 0 }));
    assert.equal(tooltipText(option.tooltip.formatter({ dataIndex: 1 })), selected);
    assert.equal(tooltipText(option.tooltip.formatter([{ dataIndex: 1 }, first])), selected);
    assert.equal(option.tooltip.formatter([]), '');
  }
});

test('permit hovers escape period, component label and unit as literal text', () => {
  const data = readPermitsData(permitsFixture()), block = data.breakdowns[0];
  data.permits.periodLabel = block.periodLabel = '<period> & "months"';
  data.permits.unit = block.unit = '<unit> & "area"';
  block.series[0].name = '<label> & "living"';
  const main = permitsMainOption(data, 'light'), breakdown = breakdownOption(block, 'light');
  assert.equal(tooltipText(main.tooltip.formatter({ dataIndex: 1, seriesIndex: 0 })), '2026 · &lt;period&gt; &amp; &quot;months&quot; Выбранный период: 0 &lt;unit&gt; &amp; &quot;area&quot;');
  assert.equal(tooltipText(breakdown.tooltip.formatter({ dataIndex: 0, seriesIndex: 0 })), '2011 · &lt;period&gt; &amp; &quot;months&quot; &lt;label&gt; &amp; &quot;living&quot;: 0 &lt;unit&gt; &amp; &quot;area&quot;');
});

test('permit hovers omit provenance, observation dates, growth, annual basis and reconciliation details', () => {
  const data = readPermitsData(permitsFixture()), row = data.permits.presentation.rows[1], block = data.breakdowns[3];
  row.selected = 1234.567890123; row.periodComplete = true; row.growth = 23.4567890123;
  row.evidence = ['selected/permit-detail.xlsx', 'Technical evidence <source>', 'Reconciliation evidence'];
  data.permits.presentation.rows = [{ ...row, year: 2025, selected: 1000 }, row];
  block.rows[1].nonresidential = block.series[2].points[1].y = 1234.567890123;
  const main = permitsMainOption(data, 'light'), breakdown = breakdownOption(block, 'light');
  const tips = [main.tooltip.formatter({ dataIndex: 1, seriesIndex: 0 }), main.tooltip.formatter({ dataIndex: 1, seriesIndex: 1 }), breakdown.tooltip.formatter({ dataIndex: 1, seriesIndex: 2 })];
  assert.equal(tooltipText(tips[0]), '2026 · Январь–сентябрь Выбранный период: 1 235 тыс. м²');
  assert.equal(tooltipText(tips[1]), '2026 · прогноз на год Остаток до годового прогноза: 8 642 тыс. м²');
  assert.equal(tooltipText(tips[2]), '2026 · Январь–сентябрь nonresidential: 1 235 тыс. м²');
  for (const tip of tips) {
    assert.doesNotMatch(tip, /assumed_thousand_m2|\.xlsx|2026-09-15|Technical evidence|Reconciliation evidence|Coverage evidence|Наблюдения|неполный|Полные месяцы|К аналогичному|Изменение|Годовой прогноз|итог источника|Сумма компонентов|Расхождение/);
    assert.doesNotMatch(tip, /[.,]\d{2,}/);
  }
});

test('permit remainder needs a known annual basis AND selected observation, prefers forecast, and never clips overplan', () => {
  const data = readPermitsData(permitsFixture()), row = data.permits.presentation.rows[1];
  row.selected = 120; row.annualForecast = 100;
  row.annualActual = 200; row.annualComplete = true;
  assert.equal(permitAnnualBasis(row).kind, 'forecast'); assert.equal(permitRemainder(row), 0);
  let option = permitsMainOption(data, 'light');
  assert.equal(option.series[0].data[1], 120); assert.equal(option.series[1].data[1], 0);
  assert.equal(tooltipText(option.tooltip.formatter({ dataIndex: 1, seriesIndex: 0 })), '2026 · Январь–сентябрь Выбранный период: 120 тыс. м²');
  assert.equal(tooltipText(option.tooltip.formatter({ dataIndex: 1, seriesIndex: 1 })), '2026 · прогноз на год Остаток до годового прогноза: 0 тыс. м²');
  assert.equal(permitsMainRows(data)[2][5], -20);
  row.annualForecast = null;
  assert.equal(permitRemainder(row), 80); assert.equal(permitAnnualBasis(row).kind, 'annual_observed');
  assert.equal(tooltipText(permitsMainOption(data, 'light').tooltip.formatter({ dataIndex: 1, seriesIndex: 1 })), '2026 · Январь–сентябрь Остаток до годового ориентира: 80 тыс. м²');
  row.annualComplete = false;
  assert.equal(permitRemainder(row), null);
  option = permitsMainOption(data, 'light');
  assert.equal(option.series.length, 1); assert.equal(option.series[0].data[1], 120);
  assert.equal(tooltipText(option.tooltip.formatter({ dataIndex: 1, seriesIndex: 0 })), '2026 · Январь–сентябрь Выбранный период: 120 тыс. м²');
  row.selected = null; row.annualForecast = 100;
  assert.equal(permitRemainder(row), null); assert.equal(permitsMainOption(data, 'light').graphic.length, 1);
  const raw = permitsFixture(); raw.permits.presentation.rows[1].annualActual = 100;
  assert.throws(() => readPermitsData(raw));
});

test('permit presentation adds forecast-only years only if supplied by presentation and never interprets them as observed selected', () => {
  const data = readPermitsData(permitsFixture()), row = data.permits.presentation.rows[1];
  data.permits.presentation.rows = [{ ...row, year: 2026, selected: null }];
  const option = permitsMainOption(data, 'light');
  assert.deepEqual(option.xAxis.data, ['2026']); assert.deepEqual(option.series[0].data, [null]);
  assert.equal(option.series.length, 2);
  assert.equal(option.series[1].data[0], 9876.543210123);
  assert.equal(option.series[1].itemStyle.borderType, 'dashed');
  assert.equal(tooltipText(option.tooltip.formatter({ dataIndex: 0, seriesIndex: 0 })), '2026 · Январь–сентябрь Выбранный период: — тыс. м²');
  const csv = permitsMainRows(data);
  assert.equal(csv.length, 2); assert.equal(csv[1][3], 9876.543210123);
});

test('permit growth is shown only for complete same-interval adjacent years with a nonzero previous value', () => {
  const row = permitsFixture().permits.presentation.rows[1], previous = { ...row, year: 2025, selected: 100, periodComplete: true };
  row.selected = 150; row.periodComplete = true; row.growth = 50;
  assert.equal(permitGrowth([previous, row], row), 50);
  previous.periodComplete = false; assert.equal(permitGrowth([previous, row], row), null);
  previous.periodComplete = true; previous.year = 2024; assert.equal(permitGrowth([previous, row], row), null);
  previous.year = 2025; previous.selected = 0; assert.equal(permitGrowth([previous, row], row), null);
  previous.selected = 100; row.periodComplete = false; assert.equal(permitGrowth([previous, row], row), null);
});

test('permit structure coverage supports arbitrary inclusive month ranges, independent of main month and annual mode', () => {
  const raw = permitsFixture(), coverage = raw.controls.structure.coverage.find(c => c.year === 2026);
  assert.equal(permitCoverageComplete(coverage, 'period', 3, 5), true);
  assert.equal(permitCoverageComplete(coverage, 'period', 8, 9), false);
  assert.equal(permitCoverageComplete(coverage, 'annual', 3, 5), false);
  assert.equal(permitCoverageComplete(undefined, 'period', 3, 5), false);
  assert.equal(permitCoverageComplete(coverage, 'period', 5, 3), false);
  raw.selection.month = 1;
  raw.selection.structure = { mode: 'period', monthFrom: 3, monthTo: 5, yearFrom: 2020, yearTo: 2022 };
  const data = readPermitsData(raw, raw.selection);
  assert.equal(data.selection.month, 1); assert.deepEqual(data.selection.structure, raw.selection.structure);
  assert.equal(data.breakdowns[0].rows[1].periodComplete, false);
  const csv = permitBreakdownRows(data.breakdowns[0], data.version);
  assert.ok(csv[0].some(cell => cell.includes('итог источника'))); assert.equal(csv[2].at(-3), 'Нет'); assert.equal(csv[2].at(-1), data.version);
  const defaults = permitsFixture(); defaults.selection.month = 1; defaults.selection.structure.monthTo = 10;
  assert.deepEqual(permitRequestFilters(new URLSearchParams('month=1')), { permitKind: 'housing', month: 1 });
  assert.equal(readPermitsData(defaults, { permitKind: 'housing', month: 1 }).selection.structure.monthTo, 10);
});

test('permit structure source aggregate stays a label without fake balancing or reconciliation in item hover', () => {
  const block = breakdowns()[3], option = breakdownOption(block, 'light');
  const totals = breakdownReconciliation(block, 1);
  assert.equal(totals.total, 1003); assert.equal(totals.sum, 3003); assert.equal(totals.difference, -2000);
  assert.equal(option.graphic[1].children[0].style.text.replace(/\s/g, ''), '1003');
  assert.equal(tooltipText(option.tooltip.formatter({ dataIndex: 1, seriesIndex: 2 })), '2026 · Январь–сентябрь nonresidential: 1 002 тыс. м²');
  assert.ok(!option.series.some(s => /balance|total/.test(s.id)));
});

test('permit labels stay on responsive plot, omit inside values that cannot fit, and survive every series hidden', () => {
  const data = readPermitsData(permitsFixture());
  data.permits.presentation.rows = Array.from({ length: 16 }, (_, i) => ({ ...data.permits.presentation.rows[1], year: 2011 + i, selected: 1000 + i }));
  for (const width of [160, 240, 320, 375, 768, 1280, 1920]) {
    const option = permitsMainOption(data, 'light', { width, height: 390 });
    assert.equal(option.xAxis.axisLabel.interval, 0); assert.equal(option.dataZoom, undefined);
    assert.equal(option.graphic.length, 16); assert.equal(option.series.length, 2);
    assert.ok(option.graphic.every(g => g.x > 0 && g.x < width && g.y > 0 && g.y < 390));
    const hidden = permitsMainOption(data, 'light', { width, height: 390, hidden: option.series.map(s => s.name) });
    assert.equal(hidden.graphic.length, 16); assert.equal(hidden.yAxis.max, option.yAxis.max);
    assert.equal(hidden.series.length, 2); assert.equal(hidden.graphic[0].children[0].style.text, option.graphic[0].children[0].style.text);
    assert.equal(option.series[0].label.formatter({ value: 0 }), '');
    if (width <= 375) assert.equal(option.series[0].label.formatter({ value: 1000 }), '');
    if (width === 768) assert.equal(option.series[0].label.formatter({ value: 1000.2 }), '1000');
  }
});

test('permit parser rejects invalid structure ranges, coverage, presentation flags, basis and aggregate fields', () => {
  for (const edit of [r => r.selection.structure.monthFrom = 10, r => r.selection.structure.yearFrom = 2027, r => r.controls.structure.coverage[0].completeMonths.push(12), r => r.controls.structure.coverage[0].annualComplete = 'true', r => r.controls.structure.coverage[0].observedThrough = '2026-10', r => r.permits.presentation.rows[1].basis = 'annual_observed', r => r.permits.presentation.rows[1].selected = undefined, r => r.permits.presentation.rows[1].growth = Infinity, r => r.permits.presentation.rows[1].annualComplete = 0, r => r.breakdowns[0].aggregateField = 'missing', r => r.breakdowns[0].rows[0].periodComplete = 1]) {
    const raw = permitsFixture(); edit(raw); assert.throws(() => readPermitsData(raw));
  }
  const raw = permitsFixture();
  assert.throws(() => readPermitsData(raw, { permitKind: 'housing', month: 9, structure: { monthFrom: 3 } }));
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
    assert.equal(new URL(permitsUrl(data.selection), 'http://localhost').searchParams.get('structure_month_to'), '9');
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

test('permit structure scope appears in requests, canonical cache keys and pinned exports independently of main month', () => {
  const query = new URLSearchParams('permit_kind=total&month=1&structure_mode=annual&structure_month_from=3&structure_month_to=5&structure_year_from=2020&structure_year_to=2022&note=keep');
  const filters = permitRequestFilters(query), expected = { permitKind: 'total', month: 1, structure: { mode: 'annual', monthFrom: 3, monthTo: 5, yearFrom: 2020, yearTo: 2022 } };
  assert.deepEqual(filters, expected);
  const url = new URL(permitsUrl(filters, 'v + x'), 'http://localhost');
  assert.equal(url.searchParams.get('month'), '1'); assert.equal(url.searchParams.get('structure_month_from'), '3');
  assert.equal(url.searchParams.get('structure_month_to'), '5'); assert.equal(url.searchParams.get('structure_year_from'), '2020');
  assert.equal(url.searchParams.get('structure_year_to'), '2022'); assert.equal(url.searchParams.get('structure_mode'), 'annual');
  assert.equal(url.searchParams.get('required_version'), 'v + x'); assert.equal(url.searchParams.get('note'), null);
  assert.notEqual(permitsUrl(filters), permitsUrl({ ...filters, structure: { ...filters.structure, monthFrom: 4 } }));
  assert.deepEqual(permitKindChange(permitsFixture().controls, 1, 'total'), { permit_kind: 'total', month: '1' });
  assert.equal(query.get('note'), 'keep');
});

test('unavailable structure year without main month gets exactly one unfiltered discovery and one compatible-view request', async () => {
  const original = globalThis.fetch, controller = new AbortController(), calls = [];
  const params = new URLSearchParams('permit_kind=housing&structure_mode=annual&structure_month_from=3&structure_month_to=5&structure_year_from=1900&structure_year_to=2022&note=a%2Bb');
  try {
    globalThis.fetch = async (url, options) => {
      calls.push([url, options]);
      if (calls.length === 1) return new Response('unavailable year', { status: 404 });
      const raw = permitsFixture();
      if (calls.length === 3) raw.selection.structure = { mode: 'annual', monthFrom: 3, monthTo: 5, yearFrom: 2011, yearTo: 2022 };
      return new Response(JSON.stringify(raw));
    };
    const data = await loadPermitsSelection(permitRequestFilters(params), controller.signal);
    assert.equal(calls.length, 3); assert.equal(calls[1][0], '/api/v1/construction/permits?permit_kind=housing');
    assert.deepEqual(data.selection.structure, { mode: 'annual', monthFrom: 3, monthTo: 5, yearFrom: 2011, yearTo: 2022 });
    assert.equal(data.selection.month, 9); assert.equal(params.get('note'), 'a+b');
    assert.ok(calls.every(call => call[1].signal === controller.signal));
    assert.equal(calls[2][0], permitsUrl(data.selection));
  } finally { globalThis.fetch = original; }
});

test('structure 404 recovery retains supported main January and valid structural filters, but stops after a failed final view', async () => {
  const original = globalThis.fetch, calls = [], controller = new AbortController();
  try {
    globalThis.fetch = async url => {
      calls.push(url);
      if (calls.length === 1) return new Response('unavailable year', { status: 404 });
      const raw = permitsFixture();
      if (calls.length === 3) { raw.selection.month = 1; raw.selection.structure = { mode: 'period', monthFrom: 3, monthTo: 5, yearFrom: 2020, yearTo: 2026 }; }
      return new Response(JSON.stringify(raw));
    };
    const filters = { permitKind: 'housing', month: 1, structure: { mode: 'period', monthFrom: 3, monthTo: 5, yearFrom: 2020, yearTo: 2099 } };
    const data = await loadPermitsSelection(filters, controller.signal);
    assert.equal(calls.length, 3); assert.equal(data.selection.month, 1); assert.equal(data.selection.structure.monthFrom, 3); assert.equal(data.selection.structure.yearFrom, 2020);
    calls.length = 0;
    globalThis.fetch = async url => { calls.push(url); return calls.length === 2 ? new Response(JSON.stringify(permitsFixture())) : new Response('still unavailable', { status: 404 }); };
    await assert.rejects(loadPermitsSelection(filters, controller.signal), error => error instanceof ApiError && error.status === 404);
    assert.equal(calls.length, 3);
    calls.length = 0;
    globalThis.fetch = async url => { calls.push(url); return new Response('missing', { status: 404 }); };
    await assert.rejects(loadPermitsSelection({ permitKind: 'housing', structure: { yearFrom: 1900 } }, controller.signal), error => error.status === 404);
    assert.deepEqual(calls, ['/api/v1/construction/permits?permit_kind=housing&structure_year_from=1900', '/api/v1/construction/permits?permit_kind=housing']);
  } finally { globalThis.fetch = original; }
});

test('permit recovery never masks non-404 HTTP, network, schema or abort failures', async () => {
  const original = globalThis.fetch;
  try {
    for (const fail of [() => new Response('unavailable', { status: 503 }), () => new Response('conflict', { status: 409 }), () => new Response('reversed interval', { status: 422 }), () => { throw new Error('offline'); }, () => new Response('{}')]) {
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
