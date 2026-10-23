import assert from 'node:assert/strict';
import test from 'node:test';
import { linearDashboardUrl, linearHistoryOption, linearMetricIds, linearValue, linearYearRows, loadLinearDashboard, readLinearDashboard } from './linearDashboardData.ts';

function fixture() {
  const row = { year: 2026, fact: null, factKind: null, factPeriod: null, factSource: null, plan: null, forecast: null };
  return { schemaVersion: 1, currentYear: 2026, version: 'linear-v1', generatedAt: '2026-10-21T10:00:00Z', sources: ['dsti', 'monitoring', 'forecast'].map(id => ({ id, file: null, date: null, sheet: null })), notes: [], metrics: linearMetricIds.map(id => ({ id, label: id, unit: ['roads', 'metro_tunnels'].includes(id) ? 'км' : 'ед.', fact: null, factPeriod: null, factSource: null, annualPlan: null, forecast: null, target: null, targetKind: null, percent: null, remaining: null, years: [{ ...row }] })) };
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
