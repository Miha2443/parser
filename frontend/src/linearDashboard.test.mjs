import assert from 'node:assert/strict';
import test from 'node:test';
import { exportCsv } from './format.ts';
import { linearDashboardUrl, linearHistoryOption, linearMetricIds, linearValue, linearYearRows, loadLinearDashboard, readLinearDashboard } from './linearDashboardData.ts';

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
