import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { permitPageFixture } from './permitPageFixtures.mjs';
import { loadPermitPageSelection, permitLivingOnly, readPermitPageData, selectPermitArea } from './permitPageData.ts';
import { breakdownOption, permitBreakdownGrowth, permitBreakdownRows, permitGrowthOption, permitGrowthPoints, permitMainGrowth, permitsMainOption, permitsMainRows } from './permitPresentation.ts';

test('living-only defaults checked and URL opt-out does not change the base request', () => {
  assert.equal(permitLivingOnly(new URLSearchParams()), true);
  assert.equal(permitLivingOnly(new URLSearchParams('permit_living_only=0')), false);
  assert.equal(permitLivingOnly(new URLSearchParams('permit_living_only=false')), true);
  const data = readPermitPageData(permitPageFixture()), original = structuredClone(data);
  assert.equal(selectPermitArea(data).permits.presentation.rows.at(-1).selected, 120);
  assert.equal(selectPermitArea(data, false).permits.presentation.rows.at(-1).selected, 170);
  assert.deepEqual(data, original);
  const nonres = readPermitPageData(permitPageFixture('nonresidential'));
  assert.equal(selectPermitArea(nonres, false), nonres);
});

test('area contract rejects missing/invalid variants instead of fabricating living histories', () => {
  for (const edit of [data => delete data.areaVariants, data => delete data.areaVariants.living, data => data.areaVariants.full.permits.presentation.rows[0].selected = '100', data => data.areaVariants.full.forecast = null]) {
    const raw = permitPageFixture(); edit(raw); assert.throws(() => readPermitPageData(raw));
  }
  const data = readPermitPageData(permitPageFixture());
  assert.equal(selectPermitArea(data).permits.presentation.rows[0].selected, null);
});

test('growth is same-period fact only and never forecast or annual remainder', () => {
  const data = selectPermitArea(readPermitPageData(permitPageFixture()));
  assert.ok(Math.abs(permitMainGrowth(data).at(-1).value - 20) < 1e-10);
  data.permits.presentation.rows.at(-1).annualForecast = 999999;
  assert.ok(Math.abs(permitMainGrowth(data).at(-1).value - 20) < 1e-10);
  data.permits.presentation.rows.at(-1).periodComplete = false;
  assert.equal(permitMainGrowth(data).at(-1).value, null);
  const chart = data.breakdowns[0];
  chart.periodLabel = 'За год';
  chart.rows.at(-1).annualComplete = false;
  assert.equal(permitBreakdownGrowth(chart).at(-1).value, null);
  chart.rows.at(-1).annualComplete = true;
  chart.rows.at(-1).periodComplete = false;
  assert.notEqual(permitBreakdownGrowth(chart).at(-1).value, null);
});

test('growth reports missing values for null, zero, incomplete and non-adjacent previous observations', () => {
  for (const value of [null, 0]) {
    const points = permitGrowthPoints([{ year: 2024, value, complete: true }, { year: 2025, value: 30, complete: true }, { year: 2027, value: 50, complete: true }]);
    assert.ok(points.every(point => point.value === null && point.reason.length));
  }
  assert.equal(permitGrowthPoints([{ year: 2024, value: 10, complete: false }, { year: 2025, value: 20, complete: true }])[1].value, null);
  assert.equal(permitGrowthPoints([{ year: 2024, value: 10, complete: true }, { year: 2025, value: 0, complete: true }])[1].value, -100);
});

test('legend component hiding suppresses source totals and forecast hatch remains unchanged', () => {
  const data = selectPermitArea(readPermitPageData(permitPageFixture()));
  const option = permitsMainOption(data, 'light');
  assert.ok(option.series.find(series => series.id === 'forecast').itemStyle.decal);
  const hidden = permitsMainOption(data, 'light', { hidden: [option.series[0].name] });
  assert.equal(hidden.graphic.length, 0);
  const block = data.breakdowns[0], before = structuredClone(block.rows), normal = breakdownOption(block, 'light'), hiddenBlock = breakdownOption(block, 'light', { hidden: [block.series[0].name] });
  assert.equal(normal.graphic.length, block.rows.length); assert.equal(hiddenBlock.graphic.length, 0);
  assert.deepEqual(block.rows, before);
});

test('downloads retain original approved annual control and growth unit is percent not area', () => {
  const source = readPermitPageData(permitPageFixture());
  source.permits.presentation.rows[0].annualActual = 2972;
  const selected = selectPermitArea(source), csv = permitsMainRows(selected, source.permits);
  assert.equal(csv[1].at(-1), 2972);
  assert.match(csv[0].at(-1), /legacy/);
  const rows = permitBreakdownRows(selected.breakdowns[0], 'version');
  const growthHeader = rows[0].find(value => value.startsWith('growth'));
  assert.equal(growthHeader, 'growth');
});

test('permit loader validates raw variants and preserves abort signal and one discovery recovery', async () => {
  const original = globalThis.fetch, controller = new AbortController(), calls = [];
  try {
    globalThis.fetch = async (url, options) => { calls.push([url, options.signal]); return calls.length === 1 ? new Response('', { status: 404 }) : new Response(JSON.stringify(permitPageFixture())); };
    const data = await loadPermitPageSelection({ permitKind: 'housing', month: 1 }, controller.signal);
    assert.equal(data.selection.month, 5);
    assert.equal(calls.length, 2); assert.ok(calls.every(call => call[1] === controller.signal));
  } finally { globalThis.fetch = original; }
});

test('all permit charts have accessible growth strips and share parent DPR resize helper', () => {
  const page = readFileSync(new URL('./PermitsPage.tsx', import.meta.url), 'utf8');
  assert.ok(page.includes('createResponsiveChart(container.current'));
  assert.ok(!page.includes('echarts.init(') && !page.includes('new ResizeObserver'));
  assert.ok(page.includes('growth={permitMainGrowth(data)}') && page.includes('growth={permitBreakdownGrowth(b)}'));
  assert.ok(page.includes('type="checkbox" checked={livingOnly}'));
  assert.ok(page.includes('permit-growth-accessible') && page.includes('н/д: ${point.reason}'));
  assert.ok(page.includes('permitGrowthOption(growth, theme'));
});

test('growth band is a separate percent line aligned to bar years, rotating labels at dense widths', () => {
  const data = selectPermitArea(readPermitPageData(permitPageFixture())), points = permitMainGrowth(data), before = structuredClone(points);
  for (const width of [320, 1440]) {
    const growth = permitGrowthOption(points, 'light', { width, height: 120 }), bars = permitsMainOption(data, 'light', { width });
    assert.equal(growth.series[0].type, 'line'); assert.equal(growth.yAxis.name, '%');
    assert.deepEqual(growth.xAxis.data, bars.xAxis.data);
    assert.equal(growth.grid.left, bars.grid.left); assert.equal(growth.grid.right, bars.grid.right);
    assert.equal(growth.xAxis.boundaryGap, true); assert.equal(growth.series[0].connectNulls, false);
    assert.equal(growth.series[0].label.rotate, width === 320 ? 90 : 0);
    assert.equal(growth.series[0].data[0], null); assert.equal(growth.series[0].data.at(-1), points.at(-1).value);
  }
  assert.deepEqual(points, before);
});

test('growth band applies one precision across labels, ticks and hover', () => {
  const option = permitGrowthOption([{ year: 2024, value: 1020.2, reason: '' }, { year: 2025, value: 300.2, reason: '' }], 'light');
  assert.equal(option.series[0].label.formatter({ value: 300.2 }), '+300 %');
  assert.equal(option.yAxis.axisLabel.formatter(300.2), '300');
  assert.match(option.tooltip.formatter({ dataIndex: 1 }), /\+300 %/);
});
