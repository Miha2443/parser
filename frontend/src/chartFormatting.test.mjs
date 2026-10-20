import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { registerHooks } from 'node:module';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import commissioning from './commissioning.browser.cjs';
import construction from './annualConstruction.browser.cjs';

// Load the actual TSX option builders without a browser, emitted files or config changes.
const sourceRoot = new URL('./', import.meta.url).href;
const hooks = registerHooks({
  resolve(specifier, context, nextResolve) {
    if (context.parentURL?.startsWith(sourceRoot) && /^\.\.?\/[^.]+$/.test(specifier)) return nextResolve(`${specifier}.ts`, context);
    if (['echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers'].includes(specifier)) return nextResolve(`${specifier}.js`, context);
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.endsWith('/Chart.tsx')) return { format: 'module', shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX }, fileName: 'Chart.tsx',
    }).outputText };
    return nextLoad(url, context);
  },
});
const { salesChartOption, salesChartRows } = await import('./salesData.ts');
const { annualChartOption, annualSummaryRows, permitChartOption, permitChartRows } = await import('./annualConstructionData.ts');
const { operationalChartOption, operationalChartRows, linearSummaryOption, linearTrendOption, linearTable } = await import('./commissioningData.ts');
const { economicsChartOption, economicsChartRows } = await import('./economicsData.ts');
const { Chart, annualOption, donutOption, delayOption } = await import('./Chart.tsx');
const { distributionOption, roomsOption } = await import('./apartmentCharts.ts');
hooks.deregister();

const themes = ['light', 'dark'];
const missing = '\u2014';
const tip = (option, value, extra = {}) => option.tooltip.formatter({ name: '2026', seriesName: 'Measured', seriesIndex: 0, dataIndex: 0, value, ...extra });
const series = (values, unit = 'area', id = 'measured') => ({ id, name: id, unit, points: values.map((y, i) => ({ x: String(2026 + i), y })) });
const chart = (values, extra = {}) => ({ id: 'observed', title: 'Measurements', kind: 'bar', unit: 'area', stack: false, columns: [], rows: [], series: [series(values)], ...extra });
const areas = (housing, common = null) => ({ housing, common, nonresidentialEmbedded: null, nonresidentialSeparate: null });
const annual = (values, extra = {}) => ({ id: 'annual', title: 'Annual', unit: 'area', totals: true, columns: [], summaries: [],
  rows: [{ year: 2026, ...Object.fromEntries(values.map((y, i) => [`s${i}`, y])) }],
  series: values.map((y, i) => ({ ...series([y], 'area', `s${i}`), color: '#398dcc' })), ...extra });

test('sales use one precision across all series, both axes and axis tooltips', () => {
  for (const theme of themes) for (const large of [1000.01, -1000.01]) {
    const source = chart([], { id: 'forecast', series: [series([large, null, 0]), series([12.34, null, 0], '%', 'ratio')] });
    const before = structuredClone(source), raw = salesChartRows(source), option = salesChartOption(source, theme);
    assert.deepEqual(option.xAxis.data, ['2026', '2028']);
    assert.deepEqual(option.series[0].data, [large, 0]);
    for (const axis of option.yAxis) assert.equal(axis.axisLabel.formatter(12.34), '12');
    assert.match(tip(option, 12.34, { seriesIndex: 1 }), /<br\/>12 %$/);
    assert.match(tip(option, null), new RegExp(`${missing} area$`));
    assert.deepEqual(salesChartRows(source), raw);
    assert.deepEqual(source, before);
  }
  const small = salesChartOption(chart([12.34, 999.94, 0, null], { kind: 'line' }), 'light');
  assert.equal(small.yAxis.axisLabel.formatter(12.34), '12,3');
  assert.equal(small.yAxis.axisLabel.formatter(0), '0');
  assert.match(small.tooltip.formatter([{ name: '2026', seriesName: 'Measured', seriesIndex: 0, value: 12.34 }]), /12,3 area$/);
});

test('only forecast volume gets dashed borders and hatching, including its custom legend', () => {
  for (const theme of themes) {
    const source = chart([], { id: 'forecast', series: [series([1000.01]), series([12.34], '%', 'ratio'), series([34.56], '%', 'readiness')] });
    const option = salesChartOption(source, theme);
    assert.equal(option.series[0].itemStyle.borderType, 'dashed');
    assert.ok(option.series[0].itemStyle.decal);
    assert.equal(option.series[0].lineStyle.type, 'dashed');
    for (const s of option.series.slice(1)) {
      assert.equal(s.itemStyle.borderType, undefined);
      assert.equal(s.itemStyle.decal, undefined);
      assert.equal(s.lineStyle.type, 'solid');
    }
    const markup = renderToStaticMarkup(createElement(Chart, { option, theme, label: 'Forecast', rows: [], filename: 'forecast' }));
    assert.equal((markup.match(/border:1px dashed/g) ?? []).length, 1);
    assert.equal((markup.match(/repeating-linear-gradient/g) ?? []).length, 1);
    const observed = salesChartOption({ ...source, id: 'monthly_volume' }, theme);
    for (const s of observed.series) {
      assert.equal(s.itemStyle.borderType, undefined);
      assert.equal(s.itemStyle.decal, undefined);
      assert.equal(s.lineStyle.type, 'solid');
    }
  }
});

test('annual stacked labels include displayed totals and only visible measurements', () => {
  for (const theme of themes) {
    const source = annual([600.24, 500.34]), before = structuredClone(source), option = annualChartOption(source, theme);
    assert.equal(option.series[1].label.formatter({ dataIndex: 0 }), '1\u00a0101');
    assert.equal(option.yAxis.axisLabel.formatter(12.34), '12');
    assert.match(tip(option, 600.24), /<br\/>600 area$/);
    const single = annualChartOption(source, theme, ['s0']);
    assert.equal(single.series[0].label.formatter({ dataIndex: 0 }), '600,2');
    assert.equal(single.yAxis.axisLabel.formatter(12.34), '12,3');
    assert.equal(single.media[0].option.series[0].label.show, true);
    assert.deepEqual(source, before);
    const noTotals = annualChartOption({ ...source, totals: false }, theme);
    assert.equal(noTotals.yAxis.axisLabel.formatter(12.34), '12,3');
    const hiddenLarge = annualChartOption(annual([12.34, 2000.01]), theme, ['s0']);
    assert.equal(hiddenLarge.yAxis.axisLabel.formatter(12.34), '12,3');
  }
});

test('annual control totals retain source precision checks and missing coverage', () => {
  const source = annual([600, 399.96], { controlTotal: 'TOTAL', categoryProvenance: [{ year: 2026, TOTAL_source_precision_million: .1 }] });
  source.rows[0].TOTAL = 1000.01;
  const before = structuredClone(source), option = annualChartOption(source, 'light');
  assert.equal(option.series[0].label.formatter({ dataIndex: 0 }), '');
  assert.equal(option.series[1].label.formatter({ dataIndex: 0 }), '1\u00a0000');
  assert.equal(option.yAxis.axisLabel.formatter(12.34), '12');
  assert.equal(option.series.length, 2);
  assert.deepEqual(source, before);
  source.rows[0].TOTAL = 2000;
  source.series[1].points[0].y = null;
  const incomplete = annualChartOption(source, 'dark');
  assert.equal(incomplete.series[0].label.formatter({ dataIndex: 0 }), '');
  assert.equal(incomplete.yAxis.axisLabel.formatter(12.34), '12,3');
  source.summaries = [{ from: 2011, to: 2026, values: [{ id: 's0', label: 'Summary', value: 9999.123 }] }];
  assert.equal(annualChartOption(source, 'dark').yAxis.axisLabel.formatter(12.34), '12,3');
  assert.equal(annualSummaryRows(source)[1][3], 9999.123);
});

test('operational and permit charts format totals, growth and labels consistently without new forecast semantics', () => {
  for (const theme of themes) for (const mode of ['small', 'total', 'growth', 'segment']) {
    const operational = commissioning.operationalReport().tables[0];
    const permits = construction.constructionReport().permits;
    for (const source of [operational, permits]) {
      const c = source.chart, size = c.x.length;
      c.period = Array.from({ length: size }, () => ({ value: 12.34 }));
      c.remainder = Array.from({ length: size }, () => ({ value: 23.45 }));
      c.totals = Array.from({ length: size }, () => ({ value: 35.79 }));
      c.growth = Array.from({ length: size }, () => ({ value: -4.56 }));
      if (mode !== 'small') c[mode === 'total' ? 'totals' : mode === 'growth' ? 'growth' : 'period'][1].value = -1000.01;
      const before = structuredClone(source), isOperational = source === operational;
      const raw = isOperational ? operationalChartRows(source) : permitChartRows(source);
      const option = isOperational ? operationalChartOption(source, 'Period', theme) : permitChartOption(source, theme);
      const fractional = mode === 'small', index = isOperational ? 0 : 2;
      assert.equal(option.yAxis.axisLabel.formatter(12.34), fractional ? '12,3' : '12');
      assert.equal(option.series[0].label.formatter({ dataIndex: index, value: 12.34 }), `${fractional ? '12,3' : '12'}\n${fractional ? '-4,6' : '-5'}%`);
      assert.equal(option.series[1].label.formatter({ dataIndex: index }), fractional ? '35,8' : '36');
      const text = tip(option, 12.34, { name: c.x[index], dataIndex: index });
      assert.ok(text.includes(`<br/>${fractional ? '12,3' : '12'} `));
      assert.ok(text.includes(`${fractional ? '-4,6' : '-5'} %`));
      for (const s of option.series) {
        assert.equal(s.itemStyle.borderType, undefined);
        assert.equal(s.itemStyle.decal, undefined);
      }
      assert.deepEqual(isOperational ? operationalChartRows(source) : permitChartRows(source), raw);
      assert.deepEqual(source, before);
    }
  }
});

test('linear summary and trend share precision across plans, facts and displayed percentages', () => {
  for (const theme of themes) for (const large of [null, 1000.01, -1000.01]) {
    const row = { code: 'road', indicator: 'Road', unit: 'km', year: 2026, quarter: 1, plan: 12.34, fact: large ?? 23.45, percent: 45.67 };
    const before = structuredClone(row), raw = linearTable([row]);
    const summary = linearSummaryOption(row, theme), trend = linearTrendOption([row], row.unit, theme);
    const value = large === null ? '12,3' : '12';
    for (const option of [summary, trend]) {
      assert.equal(option.series[0].label.formatter({ value: row.plan }), value);
      assert.equal(option.series[1].label.formatter({ value: null }), '');
      assert.ok(tip(option, row.plan).includes(`<br/>${value} km`));
    }
    assert.equal(summary.xAxis.axisLabel.formatter(row.plan), value);
    assert.equal(trend.yAxis.axisLabel.formatter(row.plan), value);
    assert.ok(tip(trend, row.plan).includes(`${large === null ? '45,7' : '46'} %`));
    assert.deepEqual(linearTable([row]), raw);
    assert.deepEqual(row, before);
  }
  const row = { code: 'count', indicator: 'Count', unit: 'count', year: 2026, quarter: 1, plan: 12.34, fact: null, percent: 1000.01 };
  assert.equal(linearTrendOption([row], row.unit, 'light').series[0].label.formatter({ value: row.plan }), '12');
  assert.equal(linearSummaryOption(row, 'light').series[0].label.formatter({ value: row.plan }), '12,3');
});

test('economics includes displayed reference totals, but not years or unused totals', () => {
  for (const theme of themes) for (const kind of ['bar', 'line']) {
    const source = chart([12.34, 0, null], { kind, rows: [{ year: 2026 }], totals: [{ x: '2026', y: 1000.01 }] });
    const before = structuredClone(source), raw = economicsChartRows(source), option = economicsChartOption(source, theme);
    assert.equal(option.yAxis.axisLabel.formatter(12.34), '12');
    assert.match(tip(option, 12.34), /<br\/>12 area<br\/>[^:]+: 1\u00a0000 area$/);
    assert.deepEqual(economicsChartRows(source), raw);
    assert.deepEqual(source, before);
    const unused = economicsChartOption({ ...source, totals: [{ x: '2099', y: 9999 }] }, theme);
    assert.equal(unused.yAxis.axisLabel.formatter(12.34), '12,3');
    const negative = economicsChartOption(chart([12.34, -1000.01]), theme);
    assert.equal(negative.yAxis.axisLabel.formatter(12.34), '12');
  }
});

test('economics index coordinate ticks stay distinct when measurements round to integers', () => {
  for (const theme of themes) for (const base of [100, 1000]) {
    const source = chart([], { unit: '%', kind: 'line', series: [series([base + .11, base + .22], '%')] });
    const option = economicsChartOption(source, theme), axis = option.yAxis;
    assert.equal(axis.scale, true);
    const labels = [base + .1, base + .15, base + .2].map(value => axis.axisLabel.formatter(value));
    assert.equal(new Set(labels).size, 3);
    assert.match(tip(option, base + .11), base === 100 ? /100,1 %$/ : /1\u00a0000 %$/);
  }
});

test('profile annual and delay formatting uses converted measurements rather than raw areas or years', () => {
  for (const theme of themes) for (const large of [999940, 1000010, -1000010]) {
    const annualRows = [{ year: 2026, ...areas(large, 12340) }];
    const delayRows = [{ year: 2026, completed: large, delayed: 12340, clarification: null }];
    const before = structuredClone([annualRows, delayRows]);
    const a = annualOption(annualRows, theme), d = delayOption(delayRows, theme);
    const expected = Math.abs(large / 1000) >= 1000 ? '12' : '12,3';
    for (const option of [a, d]) {
      assert.equal(option.yAxis.axisLabel.formatter(12.34), expected);
      assert.ok(tip(option, 12.34).includes(`<br/>${expected} `));
      assert.equal(option.series[1].data[0], 12.34);
    }
    const share = 12.34 * 1000 / (large + 12340) * 100;
    const digits = expected === '12' ? 0 : 1;
    const shareText = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: digits }).format(share);
    assert.ok(tip(a, 12.34).endsWith(`${shareText}%`));
    assert.deepEqual([annualRows, delayRows], before);
  }
});

test('donut threshold includes its displayed converted total and formats shares identically', () => {
  for (const theme of themes) {
    const source = areas(600240, 500340), before = structuredClone(source), option = donutOption(source, theme);
    assert.equal(option.graphic[0].style.text, '1\u00a0101');
    assert.ok(option.tooltip.formatter({ name: '<area>', value: 600240, percent: 54.54 }).endsWith('600 \u0442\u044b\u0441. \u043c\u00b2 \u00b7 55%'));
    assert.deepEqual(option.series[0].data.map(p => p.value), [600240, 500340]);
    assert.deepEqual(source, before);
    const small = donutOption(areas(12340, 23450), theme);
    assert.equal(small.graphic[0].style.text, '35,8');
    assert.match(small.tooltip.formatter({ name: 'Area', value: 12340, percent: 34.48 }), /12,3 .*34,5%$/);
    assert.equal(donutOption(areas(null), theme).graphic[0].style.text, missing);
    assert.equal(donutOption(areas(0), theme).graphic[0].style.text, '0');
  }
});

test('apartment distributions share one formatter and room charts ignore unplotted negative shares', () => {
  for (const theme of themes) for (const large of [null, 1000.01, -1000.01]) {
    const source = [{ range: '2000+', sharePercent: 12.34 }, { range: '10-20', sharePercent: large }, { range: '20-30', sharePercent: 0 }];
    const before = structuredClone(source), option = distributionOption(source, theme), expected = large === null ? '12,3' : '12';
    assert.equal(option.yAxis.axisLabel.formatter(12.34), expected);
    assert.equal(option.series[0].label.formatter({ value: 12.34 }), `${expected}%`);
    assert.equal(option.series[0].label.formatter({ value: null }), '');
    assert.ok(tip(option, 12.34).endsWith(`${expected}%`));
    assert.ok(tip(option, null).endsWith(missing));
    assert.deepEqual(source, before);
  }
  const rows = [{ type: 'one', sharePercent: 12.34 }, { type: 'two', sharePercent: -1000.01 }, { type: 'three', sharePercent: null }];
  for (const theme of themes) {
    assert.ok(tip(roomsOption(rows, theme), 12.34).endsWith('12,3%'));
    const option = roomsOption([...rows, { type: 'four', sharePercent: 1000.01 }], theme);
    assert.ok(tip(option, 12.34).endsWith('12%'));
    assert.deepEqual(option.series[0].data.map(p => p.value), [12.34, 1000.01]);
  }
});
