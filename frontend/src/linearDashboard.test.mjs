import assert from 'node:assert/strict';
import test from 'node:test';
import * as echarts from 'echarts/core.js';
import { BarChart } from 'echarts/charts.js';
import { GridComponent, TooltipComponent } from 'echarts/components.js';
import { SVGRenderer } from 'echarts/renderers.js';
import { exportCsv } from './format.ts';
import { linearDashboardUrl, linearHistoryOption, linearMetricIds, linearValue, linearYearRows, loadLinearDashboard, readLinearDashboard } from './linearDashboardData.ts';

echarts.use([BarChart, GridComponent, TooltipComponent, SVGRenderer]);

function fixture() {
  const row = { year: 2026, fact: null, factKind: null, factPeriod: null, factSource: null, plan: null, forecast: null };
  return { schemaVersion: 1, currentYear: 2026, version: 'linear-v1', generatedAt: '2026-10-21T10:00:00Z', sources: ['dsti', 'monitoring', 'forecast'].map(id => ({ id, file: null, date: null, sheet: null })), notes: [], metrics: linearMetricIds.map(id => ({ id, label: id, unit: ['roads', 'metro_tunnels'].includes(id) ? 'км' : 'ед.', fact: null, factPeriod: null, factSource: null, annualPlan: null, forecast: null, target: null, targetKind: null, percent: null, remaining: null, years: [{ ...row }] })) };
}

const staticDepotValues = [0, 0, 0, 1, 0, 1, 0, 2, 1, 0, 0, 0, 1, 1, 2];
function staticDepotFixture() {
  const raw = fixture();
  raw.sources.push({ id: 'static_depots', file: 'электродепо11-25.xlsx', date: null, sheet: 'Лист1' });
  raw.metrics[5].years.unshift(...staticDepotValues.map((fact, index) => ({ year: 2011 + index, fact, factKind: 'annual', factPeriod: `За ${2011 + index} год`, factSource: 'static_depots', plan: null, forecast: null })));
  return raw;
}

function historyMetric(index = 0) {
  const metric = fixture().metrics[index];
  metric.years = Array.from({ length: 25 }, (_, index) => {
    const year = 2011 + index, fact = year <= 2026 && year !== 2013 ? index === 0 ? 0 : 12.34 + index : null;
    return { year, fact, factKind: fact === null ? null : year === 2026 ? 'partial' : 'annual', factPeriod: fact === null ? null : `За ${year} год`, factSource: fact === null ? null : 'monitoring', plan: 900000, forecast: year >= 2026 && year !== 2031 ? year === 2027 ? 0 : 90.65 + index : null };
  });
  return metric;
}

test('exact six metric order, absent values remain null, no fabricated 2011 history', () => {
  const raw = fixture();
  assert.deepEqual(readLinearDashboard(raw), raw);
  assert.deepEqual(raw.metrics.map(row => row.id), ['roads', 'bridges', 'crossings', 'metro_stations', 'metro_tunnels', 'depots']);
  assert.equal(linearValue(null, 'км'), '—');
  assert.equal(linearValue(0, 'ед.'), '0');
  assert.equal(linearYearRows(raw.metrics[0])[1][1], null);
  raw.metrics.pop();
  assert.throws(() => readLinearDashboard(raw));
});

test('optional static source preserves the original three sources without inventing history', () => {
  const raw = fixture(), legacy = structuredClone(raw);
  raw.sources.push(staticDepotFixture().sources[3]);
  const parsed = readLinearDashboard(raw);
  assert.deepEqual(parsed.sources.slice(0, 3), legacy.sources);
  assert.deepEqual(parsed.sources[3], { id: 'static_depots', file: 'электродепо11-25.xlsx', date: null, sheet: 'Лист1' });
  assert.deepEqual(parsed.metrics, legacy.metrics);
  for (const mutation of [value => value.sources.splice(2, 1), value => value.sources.push({ ...value.sources[3] }), value => value.sources[3].id = 'unknown', value => delete value.sources[3].date]) {
    const invalid = structuredClone(raw); mutation(invalid); assert.throws(() => readLinearDashboard(invalid));
  }
});

test('static depot annual history retains real zeros, gaps and absent current KPI values', () => {
  const raw = staticDepotFixture(), metric = raw.metrics[5];
  metric.years.unshift({ ...metric.years.at(-1), year: 2010 });
  const parsed = readLinearDashboard(raw), depots = parsed.metrics[5];
  assert.deepEqual(parsed, raw);
  assert.equal(depots.unit, 'ед.');
  assert.deepEqual(depots.years.slice(1, -1).map(row => [row.year, row.fact, row.factKind, row.factPeriod, row.factSource]), staticDepotValues.map((fact, index) => [2011 + index, fact, 'annual', `За ${2011 + index} год`, 'static_depots']));
  for (const key of ['fact', 'factPeriod', 'factSource', 'annualPlan', 'forecast', 'target', 'targetKind', 'percent', 'remaining']) assert.equal(depots[key], null, key);
  assert.deepEqual(parsed.metrics.slice(0, 5), fixture().metrics.slice(0, 5));
  for (const theme of ['light', 'dark']) {
    const option = linearHistoryOption(depots, parsed.currentYear, theme);
    assert.deepEqual(option.xAxis.data, ['2010', ...staticDepotValues.map((_, index) => String(2011 + index)), '2026\nфакт']);
    assert.deepEqual(option.series[0].data, [null, ...staticDepotValues, null]);
    assert.ok(option.series[1].data.every(value => value === null));
    assert.match(option.tooltip.formatter({ name: '2011', value: 0, seriesName: 'Факт', dataIndex: 1 }), /0 ед\.<br\/>За 2011 год/);
  }
});

test('static depot CSV exports every annual count and source while missing values remain empty', async t => {
  const metric = readLinearDashboard(staticDepotFixture()).metrics[5], rows = linearYearRows(metric);
  assert.deepEqual(rows[0], ['Год', 'Факт, ед.', 'Период факта', 'Источник факта', 'Прогноз, ед.']);
  assert.deepEqual(rows.slice(1), [...staticDepotValues.map((fact, index) => [2011 + index, fact, `За ${2011 + index} год`, 'static_depots', null]), [2026, null, null, null, null]]);
  const originalDocument = globalThis.document, link = { click: t.mock.fn() };
  let blob;
  t.mock.method(URL, 'createObjectURL', value => { blob = value; return 'blob:static-depots'; });
  t.mock.method(URL, 'revokeObjectURL', () => {});
  t.mock.method(globalThis, 'setTimeout', callback => { callback(); });
  try {
    globalThis.document = { createElement: tag => { assert.equal(tag, 'a'); return link; } };
    exportCsv(rows, 'linear-depots.csv');
    assert.equal(link.download, 'linear-depots.csv');
    assert.equal(link.href, 'blob:static-depots');
    assert.equal(link.click.mock.callCount(), 1);
    assert.deepEqual((await blob.text()).split('\r\n').slice(1), [...staticDepotValues.map((fact, index) => `"${2011 + index}";"${fact}";"За ${2011 + index} год";"static_depots";""`), '"2026";"";"";"";""']);
  } finally {
    if (originalDocument === undefined) delete globalThis.document;
    else globalThis.document = originalDocument;
  }
});

test('static depot evidence is rejected for other metrics, current KPI, nonannual and missing facts', () => {
  for (let index = 0; index < 5; index++) {
    const raw = staticDepotFixture();
    raw.metrics[index].years.unshift({ ...raw.metrics[5].years[0] });
    assert.throws(() => readLinearDashboard(raw), raw.metrics[index].id);
  }
  for (const [label, mutation] of [
    ['unregistered source', raw => raw.sources.pop()],
    ['unknown fact source', raw => raw.metrics[5].years[0].factSource = 'static_other'],
    ['partial historical fact', raw => raw.metrics[5].years[0].factKind = 'partial'],
    ['missing fact', raw => Object.assign(raw.metrics[5].years[0], { fact: null, factKind: null })],
    ['missing period', raw => raw.metrics[5].years[0].factPeriod = null],
    ['current annual fact', raw => raw.currentYear = 2025],
    ['current partial fact', raw => {
      Object.assign(raw.metrics[5], { fact: 0, factPeriod: 'Неполный год', factSource: 'monitoring' });
      Object.assign(raw.metrics[5].years.at(-1), { fact: 0, factKind: 'partial', factPeriod: 'Неполный год', factSource: 'static_depots' });
    }],
    ['current KPI', raw => {
      Object.assign(raw.metrics[5], { fact: 0, factPeriod: 'Неполный год', factSource: 'static_depots' });
      Object.assign(raw.metrics[5].years.at(-1), { fact: 0, factKind: 'partial', factPeriod: 'Неполный год', factSource: 'monitoring' });
    }],
    ['future annual fact', raw => raw.metrics[5].years.push({ ...raw.metrics[5].years[0], year: 2027 })],
  ]) {
    const raw = staticDepotFixture(); mutation(raw); assert.throws(() => readLinearDashboard(raw), label);
  }
});

test('zero source plan cannot replace a positive forecast target', () => {
  const raw = fixture(), metric = raw.metrics[0];
  Object.assign(metric, { fact: 0, factPeriod: 'На 2026-10-16 · неполный год', factSource: 'monitoring', annualPlan: 0, forecast: 10, target: 10, targetKind: 'forecast', percent: 0, remaining: 10 });
  Object.assign(metric.years[0], { fact: 0, factPeriod: metric.factPeriod, factSource: metric.factSource, factKind: 'partial', plan: 0, forecast: 10 });
  assert.equal(readLinearDashboard(raw).metrics[0].percent, 0);
  metric.target = 0;
  assert.throws(() => readLinearDashboard(raw));
});

test('zero fact against positive forecast is valid zero percent', () => {
  const raw = fixture(), metric = raw.metrics[3];
  Object.assign(metric, { fact: 0, factPeriod: 'Неполный год', factSource: 'monitoring', forecast: 3, target: 3, targetKind: 'forecast', percent: 0, remaining: 3 });
  Object.assign(metric.years[0], { fact: 0, factPeriod: metric.factPeriod, factSource: metric.factSource, factKind: 'partial', forecast: 3 });
  assert.equal(readLinearDashboard(raw).metrics[3].percent, 0);
});

test('a source plan never fills an absent forecast and zero forecasts remain valid', () => {
  const raw = fixture(), metric = raw.metrics[0];
  Object.assign(metric, { fact: 4, factPeriod: 'Неполный год', factSource: 'monitoring', annualPlan: 100 });
  Object.assign(metric.years[0], { fact: 4, factPeriod: metric.factPeriod, factSource: metric.factSource, factKind: 'partial', plan: 100 });
  assert.equal(readLinearDashboard(raw).metrics[0].target, null);
  Object.assign(metric, { forecast: 0, target: 0, targetKind: 'forecast', remaining: 0 });
  metric.years[0].forecast = 0;
  assert.equal(readLinearDashboard(raw).metrics[0].percent, null);
  assert.ok(linearYearRows(metric)[0].every(label => !String(label).includes('план')));
});

test('invalid, nonfinite, missing and future facts are rejected', () => {
  for (const mutation of [raw => raw.metrics[0].fact = undefined, raw => raw.metrics[0].forecast = NaN, raw => raw.metrics[0].unit = 'м²', raw => raw.metrics.reverse(), raw => raw.sources.pop(), raw => raw.metrics[0].years[0].factKind = 'annual', raw => raw.metrics[0].years.push({ year: 2030, fact: 5, factKind: 'annual', factPeriod: 'future', factSource: 'monitoring', plan: null, forecast: 3 })]) {
    const raw = fixture(); mutation(raw); assert.throws(() => readLinearDashboard(raw));
  }
});

test('different forecast horizons retain separate hatched forecasts, never stacked with fact', () => {
  for (const horizon of [1, 4, 9]) {
    const raw = fixture(), metric = raw.metrics[0];
    metric.years.unshift({ year: 2023, fact: 12, factKind: 'annual', factPeriod: 'За 2023 год', factSource: 'monitoring', plan: null, forecast: null });
    for (let i = 1; i <= horizon; i++) metric.years.push({ year: 2026 + i, fact: null, factKind: null, factPeriod: null, factSource: null, plan: null, forecast: i === 1 ? 0 : 30 });
    const parsed = readLinearDashboard(raw);
    for (const theme of ['light', 'dark']) {
      const option = linearHistoryOption(parsed.metrics[0], parsed.currentYear, theme);
      assert.equal(option.xAxis.data.at(-1), `${2026 + horizon}\nпрогноз`);
      assert.equal(option.series[0].data.at(-1), null);
      assert.equal(option.series[1].data[2], 0);
      assert.equal(option.series[1].data[0], null);
      assert.equal(option.series[1].itemStyle.borderType, 'dashed');
      assert.ok(option.series[1].itemStyle.decal);
      assert.ok(option.series.every(series => series.stack === undefined));
    }
    assert.equal(linearYearRows(metric).length, metric.years.length + 1);
  }
});

test('each metric chart keeps its own forecast axis instead of the longest shared horizon', () => {
  const raw = fixture();
  for (const [index, horizon] of [[0, 4], [3, 9]]) {
    const metric = raw.metrics[index];
    for (let offset = 1; offset <= horizon; offset++) metric.years.push({ year: 2026 + offset, fact: null, factKind: null, factPeriod: null, factSource: null, plan: null, forecast: 0 });
  }
  const parsed = readLinearDashboard(raw);
  const roads = linearHistoryOption(parsed.metrics[0], parsed.currentYear, 'light');
  const stations = linearHistoryOption(parsed.metrics[3], parsed.currentYear, 'light');
  const depots = linearHistoryOption(parsed.metrics[5], parsed.currentYear, 'light');
  assert.equal(roads.xAxis.data.at(-1), '2030\nпрогноз');
  assert.equal(roads.xAxis.data.includes('2031'), false);
  assert.equal(stations.xAxis.data.at(-1), '2035\nпрогноз');
  assert.equal(stations.series[1].data.at(-1), 0);
  assert.deepEqual(depots.xAxis.data, ['2026\nфакт']);
});

test('fact and forecast have separate centered year slots with aligned hover values', () => {
  const metric = fixture().metrics[0];
  metric.years = [
    { year: 2025, fact: 12, factPeriod: 'За 2025 год', forecast: null },
    { year: 2026, fact: 7, factPeriod: 'На октябрь 2026', forecast: 50 },
    { year: 2027, fact: null, factPeriod: null, forecast: 55 },
  ];
  const option = linearHistoryOption(metric, 2026, 'light');
  assert.deepEqual(option.xAxis.data, ['2025', '2026\nфакт', '2026\nпрогноз', '2027\nпрогноз']);
  assert.deepEqual(option.series[0].data, [12, 7, null, null]);
  assert.deepEqual(option.series[1].data, [null, null, 50, 55]);
  assert.ok(option.series.every(series => series.barGap === '-100%'));
  assert.match(option.tooltip.formatter({ name: '2026', value: 7, seriesName: 'Факт', dataIndex: 1 }), /На октябрь 2026/);
  assert.doesNotMatch(option.tooltip.formatter({ name: '2026', value: 50, seriesName: 'Прогноз', dataIndex: 2 }), /На октябрь/);
});

test('every supplied annual fact and forecast slot keeps its label, including zeros and years after 2030', () => {
  for (const [index, id] of linearMetricIds.entries()) {
    const metric = historyMetric(index);
    const slots = [...metric.years.filter(row => row.year <= 2026).map(row => [String(row.year), row.fact]), ...metric.years.filter(row => row.year >= 2026 && row.forecast !== null).map(row => [`${row.year}\nпрогноз`, row.forecast])];
    slots.find(slot => slot[0] === '2026')[0] = '2026\nфакт';
    for (const theme of ['light', 'dark']) for (const width of [360, 600, 768, 1000, 1920]) {
      const option = linearHistoryOption(metric, 2026, theme, width);
      assert.deepEqual(option.xAxis.data, slots.map(slot => slot[0]), `${id}: every year slot`);
      assert.equal(option.xAxis.data.at(-1), '2035\nпрогноз');
      for (const series of option.series) {
        assert.equal(series.label.show, true);
        assert.equal(series.label.position, 'top');
        assert.equal(series.labelLayout.hideOverlap, false);
        assert.equal(series.label.fontSize, 9);
        assert.equal(series.barGap, '-100%');
        assert.equal(series.stack, undefined);
        assert.equal(series.data.length, slots.length);
        const expected = slots.map(([name, value]) => name.includes('прогноз') === (series.name === 'Прогноз') ? value : null);
        assert.deepEqual(series.data, expected);
        for (const value of expected) assert.equal(series.label.formatter({ value }), value === null ? '' : new Intl.NumberFormat('ru-RU', { maximumFractionDigits: metric.unit === 'км' ? 1 : 0 }).format(value));
        assert.equal(series.label.formatter({ value: 0 }), '0');
        assert.equal(series.label.formatter({ value: null }), '');
        if (width <= 600) assert.equal(series.label.rotate, 90);
        if (width === 1920) assert.equal(series.label.rotate, 0);
      }
      assert.equal(option.media[0].option.xAxis.axisLabel.rotate, 90);
      assert.equal(option.media[0].option.xAxis.axisLabel.hideOverlap, false);
      assert.equal(option.media[0].option.xAxis.axisLabel.formatter('2026\nпрогноз'), '2026 прогноз');
    }
  }
});

test('labels, tooltips and axis ticks share plotted-value rounding, not unplotted plans or KPI values', () => {
  for (const [unit, largeSlot, expected] of [
    ['км', null, '12,3'], ['ед.', null, '12'], ['км', 'fact', '12'], ['км', 'forecast', '12'], ['км', 'threshold', '12'],
  ]) {
    const metric = historyMetric();
    metric.unit = unit;
    metric.fact = 200000;
    metric.forecast = 200000;
    metric.years[0].fact = largeSlot === 'fact' ? 200000.75 : 999.94;
    metric.years[1].fact = 12.34;
    metric.years.at(-1).forecast = largeSlot === 'forecast' ? 200000.75 : largeSlot === 'threshold' ? 1000 : 55.56;
    const option = linearHistoryOption(metric, 2026, 'light');
    for (const series of option.series) {
      assert.equal(series.label.formatter({ value: 12.34 }), expected);
      assert.equal(series.label.formatter({ value: 0 }), '0');
      assert.equal(series.label.formatter({ value: null }), '');
      const dataIndex = series.data.findIndex(value => value !== null);
      assert.match(option.tooltip.formatter({ name: 'year', seriesName: series.name, dataIndex, value: 12.34 }), new RegExp(`<br/>${expected.replace('.', '\\.')} ${unit}`));
    }
    assert.equal(option.yAxis.axisLabel.formatter(12.34), expected);
    assert.equal(option.series[0].label.formatter({ value: 999.94 }), unit === 'км' && largeSlot === null ? '999,9' : '1\u00a0000');
    if (largeSlot === 'forecast') {
      const series = option.series[1], dataIndex = series.data.length - 1;
      assert.equal(series.label.formatter({ value: series.data[dataIndex] }), '200\u00a0001');
      assert.match(option.tooltip.formatter({ name: '2035', seriesName: series.name, dataIndex, value: series.data[dataIndex] }), /200\u00a0001 км/);
    }
  }
});

test('history options, formatters and resizing never mutate source data or export values', () => {
  const metric = historyMetric(), original = structuredClone(metric), rows = linearYearRows(metric);
  for (const row of metric.years) Object.freeze(row);
  Object.freeze(metric.years);
  Object.freeze(metric);
  for (const theme of ['light', 'dark']) for (const width of [360, 768, 1920, 360]) {
    const option = linearHistoryOption(metric, 2026, theme, width);
    for (const series of option.series) series.data.forEach((value, dataIndex) => {
      series.label.formatter({ value });
      option.tooltip.formatter({ name: option.xAxis.data[dataIndex], value, seriesName: series.name, dataIndex });
    });
    assert.deepEqual(metric, original);
    assert.deepEqual(linearYearRows(metric), rows);
  }
});

test('rendered labels stay above centered bars, inside the canvas and separate with 200k at the axis ceiling', () => {
  const metric = historyMetric();
  for (const row of metric.years) {
    if (row.fact !== null) row.fact = 200000;
    if (row.forecast !== null && row.forecast !== 0) row.forecast = 200000;
  }
  for (const width of [312, 360, 600, 768, 1000, 1920]) {
    const chart = echarts.init(null, undefined, { renderer: 'svg', ssr: true, width, height: 290 });
    try {
      const option = linearHistoryOption(metric, 2026, 'light', width);
      chart.setOption({ ...option, animation: false, textStyle: { fontFamily: 'Segoe UI, Arial, sans-serif' } });
      const bounds = [];
      option.series.forEach((series, seriesIndex) => {
        const data = chart.getModel().getSeriesByIndex(seriesIndex).getData();
        series.data.forEach((value, dataIndex) => {
          if (value === null) return;
          const bar = data.getItemGraphicEl(dataIndex), label = bar.getTextContent(), layout = data.getItemLayout(dataIndex);
          assert.ok(label && !label.ignore, `${width}: visible ${series.name} ${dataIndex}`);
          assert.equal(label.style.text, series.label.formatter({ value }));
          const rect = label.getBoundingRect().clone();
          rect.applyTransform(label.getComputedTransform());
          const center = chart.convertToPixel({ xAxisIndex: 0 }, dataIndex);
          assert.ok(Math.abs(layout.x + layout.width / 2 - center) <= 1, `${width}: centered bar`);
          assert.ok(Math.abs(rect.x + rect.width / 2 - center) <= 1, `${width}: centered label`);
          assert.ok(rect.x >= 0 && rect.y >= 0 && rect.x + rect.width <= width && rect.y + rect.height <= 290, `${width}: label within canvas ${JSON.stringify(rect)}`);
          assert.ok(rect.y + rect.height <= layout.y + Math.min(0, layout.height), `${width}: label above bar`);
          bounds.push(rect);
        });
      });
      bounds.sort((a, b) => a.x - b.x);
      for (let index = 1; index < bounds.length; index++) assert.equal(bounds[index].intersect(bounds[index - 1]), false, `${width}: adjacent labels overlap`);
      const unit = chart.getZr().storage.getDisplayList(true).find(element => element.style?.text === metric.unit);
      assert.ok(unit);
      const unitBounds = unit.getBoundingRect().clone();
      unitBounds.applyTransform(unit.getComputedTransform());
      for (const rect of bounds) assert.equal(rect.intersect(unitBounds), false, `${width}: label overlaps unit caption`);
      assert.equal(bounds.length, option.series.flatMap(series => series.data).filter(value => value !== null).length);
    } finally { chart.dispose(); }
  }
});

test('version-pinned export URL and abortable fresh API fetch', async () => {
  assert.equal(linearDashboardUrl(), '/api/v1/linear/dashboard');
  assert.equal(new URL(linearDashboardUrl('a & b'), 'http://localhost').searchParams.get('required_version'), 'a & b');
  const original = globalThis.fetch, controller = new AbortController();
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(url, '/api/v1/linear/dashboard'); assert.equal(options.cache, 'no-store'); assert.equal(options.signal, controller.signal);
      return new Response(JSON.stringify(fixture()));
    };
    assert.equal((await loadLinearDashboard(controller.signal)).metrics.length, 6);
    globalThis.fetch = async () => new Response('offline', { status: 503 });
    await assert.rejects(loadLinearDashboard(controller.signal), /503/);
  } finally { globalThis.fetch = original; }
});
