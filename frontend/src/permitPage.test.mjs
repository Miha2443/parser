import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { permitPageFixture } from './permitPageFixtures.mjs';
import { loadPermitPageSelection, permitLivingOnly, readPermitPageData, selectPermitArea } from './permitPageData.ts';
import { permitPageFilters, permitsUrl } from './constructionPagesData.ts';
import { breakdownOption, permitBreakdownGrowth, permitBreakdownRows, permitGrowthOption, permitGrowthPoints, permitMainGrowth, permitsMainOption, permitsMainRows } from './permitPresentation.ts';
import { mortgageAnnualOption, mortgageMonthlyOption, mortgageRatesOption } from './mortgageData.ts';

test('living area defaults selected and full housing area preserves filters and export identity', () => {
  assert.equal(permitLivingOnly(new URLSearchParams()), true);
  assert.equal(permitLivingOnly(new URLSearchParams('permit_living_only=0')), false);
  assert.equal(permitLivingOnly(new URLSearchParams('permit_living_only=false')), true);
  for (const kind of ['housing', 'total']) {
    const params = new URLSearchParams(`permit_kind=${kind}&month=5&structure_mode=annual&structure_month_to=12`);
    const filters = permitPageFilters(params), requestUrl = permitsUrl(filters);
    params.set('permit_living_only', '0');
    assert.deepEqual(permitPageFilters(params), filters);
    assert.equal(permitsUrl(permitPageFilters(params)), requestUrl);
    const data = readPermitPageData(permitPageFixture(kind)), original = structuredClone(data);
    const living = selectPermitArea(data), full = selectPermitArea(data, permitLivingOnly(params));
    assert.equal(living.permits, data.areaVariants.living.permits);
    assert.equal(living.forecast, data.areaVariants.living.forecast);
    assert.equal(full.permits, data.areaVariants.full.permits);
    assert.equal(full.forecast, data.areaVariants.full.forecast);
    assert.equal(living.permits.presentation.rows.at(-1).selected, 120);
    assert.equal(full.permits.presentation.rows.at(-1).selected, 170);
    assert.equal(full.permits.presentation.rows.at(-1).selected - living.permits.presentation.rows.at(-1).selected, data.breakdowns[0].rows.at(-1).mop_embedded);
    assert.equal(full.selection, data.selection);
    assert.equal(full.breakdowns, data.breakdowns);
    assert.equal(permitsUrl(full.selection, full.version), permitsUrl(living.selection, living.version));
    assert.deepEqual(data, original);
  }
  const nonres = readPermitPageData(permitPageFixture('nonresidential'));
  assert.equal(selectPermitArea(nonres), nonres);
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

test('area selector has two ordered pressed buttons with unchanged URL semantics and a nonresidential guard', () => {
  const page = readFileSync(new URL('./PermitsPage.tsx', import.meta.url), 'utf8');
  const selector = page.match(/\{data\.selection\.permitKind !== 'nonresidential' && <div className="permit-area-toggle"[\s\S]*?<\/div>\}/)?.[0];
  assert.ok(selector);
  assert.match(selector, /role="group" aria-label="Площадь разрешений"/);
  assert.deepEqual([...selector.matchAll(/<button type="button" aria-pressed=\{([^}]+)\} onClick=\{\(\) => change\(\{ permit_living_only: (null|'0') \}\)\}>([^<]+)<\/button>/g)].map(match => match.slice(1)), [
    ['livingOnly', 'null', 'Жилая площадь'],
    ['!livingOnly', "'0'", 'Общая площадь в жилье'],
  ]);
  assert.doesNotMatch(page, /type="checkbox"|Только жилая площадь/);
});

test('all permit charts use one accessible combined canvas and the shared DPR resize helper', () => {
  const page = readFileSync(new URL('./PermitsPage.tsx', import.meta.url), 'utf8');
  assert.ok(page.includes('createResponsiveChart(container.current'));
  assert.ok(!page.includes('echarts.init(') && !page.includes('new ResizeObserver'));
  assert.ok(page.includes('growth={permitMainGrowth(data)}') && page.includes('growth={permitBreakdownGrowth(b)}'));
  assert.ok(page.includes('permit-growth-accessible') && page.includes('н/д: ${point.reason}'));
  assert.ok(page.includes('includeGrowth: true'));
  assert.ok(!page.includes('growthContainer') && !page.includes('growthInstance') && !page.includes('permit-growth-canvas'));
  assert.equal((page.match(/createResponsiveChart\(/g) ?? []).length, 1);
  assert.ok(page.includes('download(instance.current.getDataURL('));
  assert.ok(!page.includes("document.createElement('canvas')"));
});

test('combined growth band uses an independent percent grid aligned to bar years at every size', () => {
  const data = selectPermitArea(readPermitPageData(permitPageFixture())), points = permitMainGrowth(data), before = structuredClone(points);
  for (const theme of ['light', 'dark']) for (const width of [320, 375, 768, 1440]) for (const height of [350, 510, 900]) {
    for (const [option, expected] of [[permitsMainOption(data, theme, { width, height, includeGrowth: true }), points], [breakdownOption(data.breakdowns[0], theme, { width, height, includeGrowth: true }), permitBreakdownGrowth(data.breakdowns[0])]]) {
      const line = option.series.at(-1), bars = option.series.slice(0, -1);
      assert.equal(line.type, 'line'); assert.equal(option.yAxis[0].name, '%'); assert.equal(option.yAxis[1].name, 'тыс. м²');
      assert.deepEqual(option.xAxis[0].data, option.xAxis[1].data);
      assert.equal(option.grid[0].left, option.grid[1].left); assert.equal(option.grid[0].right, option.grid[1].right);
      assert.ok(option.grid[0].top + option.grid[0].height < option.grid[1].top);
      assert.equal(option.xAxis[0].boundaryGap, true); assert.equal(option.xAxis[1].boundaryGap, true);
      assert.equal(line.xAxisIndex, 0); assert.equal(line.yAxisIndex, 0); assert.equal(line.connectNulls, false);
      assert.equal(line.smooth, .25); assert.equal(line.smoothMonotone, 'x');
      assert.ok(bars.every(series => series.type === 'bar' && series.xAxisIndex === 1 && series.yAxisIndex === 1 && !series.smooth));
      assert.equal(line.label.rotate, (width - option.grid[0].left - option.grid[0].right) / points.length < 44 ? 90 : 0);
      assert.deepEqual(line.data, expected.map(point => point.value));
      const totals = option.graphic.filter(item => item.id.startsWith('permit-total-'));
      assert.ok(totals.every(item => item.y > option.grid[1].top && item.y < height - option.grid[1].bottom));
      assert.ok(option.graphic.some(item => item.id === 'permit-growth-title'));
      assert.ok(option.graphic.every(item => item.id.startsWith('permit-total-') || item.id.startsWith('permit-growth-')));
      const tip = option.tooltip.formatter({ dataIndex: line.data.length - 1, seriesIndex: option.series.length - 1 });
      assert.match(tip, /К прошлому году:/); assert.doesNotMatch(tip, /м²/);
    }
  }
  assert.deepEqual(points, before);
});

test('combined legends independently hide growth and bar components without changing source totals or forecast', () => {
  const data = selectPermitArea(readPermitPageData(permitPageFixture())), before = structuredClone(data);
  const option = permitsMainOption(data, 'light', { includeGrowth: true });
  const totals = chart => chart.graphic.filter(item => item.id.startsWith('permit-total-'));
  const growthHidden = permitsMainOption(data, 'light', { includeGrowth: true, hidden: ['К прошлому году, %'] });
  assert.deepEqual(totals(growthHidden), totals(option));
  assert.equal(growthHidden.legend.selected['К прошлому году, %'], false);
  assert.ok(!growthHidden.graphic.some(item => item.id.startsWith('permit-growth-missing-')));
  const barHidden = permitsMainOption(data, 'light', { includeGrowth: true, hidden: [option.series[0].name] });
  assert.equal(totals(barHidden).length, 0);
  assert.deepEqual(barHidden.series.at(-1).data, option.series.at(-1).data);
  assert.deepEqual(barHidden.series.find(series => series.id === 'forecast').itemStyle, option.series.find(series => series.id === 'forecast').itemStyle);
  assert.deepEqual(barHidden.yAxis.map(axis => [axis.min, axis.max]), option.yAxis.map(axis => [axis.min, axis.max]));
  assert.deepEqual(data, before);
});

test('combined growth never bridges missing, nonconsecutive, zero-base or incomplete periods', () => {
  const data = selectPermitArea(readPermitPageData(permitPageFixture()));
  const template = data.permits.presentation.rows.at(-1);
  data.permits.presentation.rows = [
    { ...template, year: 2019, selected: 100 }, { ...template, year: 2020, selected: 150 },
    { ...template, year: 2022, selected: 180 }, { ...template, year: 2023, selected: 0 },
    { ...template, year: 2024, selected: 200 }, { ...template, year: 2025, selected: null },
    { ...template, year: 2026, selected: 300, periodComplete: false },
  ];
  const option = permitsMainOption(data, 'light', { includeGrowth: true });
  assert.deepEqual(option.series.at(-1).data, [null, 50, null, -100, null, null, null]);
  assert.equal(option.series.at(-1).connectNulls, false);
  const tip = option.tooltip.formatter({ dataIndex: 6, seriesIndex: option.series.length - 1 });
  assert.match(tip, /н\/д.*Нет полного факта/);
  data.permits.presentation.rows.forEach(row => { row.periodComplete = false; });
  const empty = permitsMainOption(data, 'light', { includeGrowth: true });
  assert.ok(empty.graphic.some(item => item.id === 'permit-growth-empty'));
  assert.ok(empty.series.at(-1).data.every(value => value === null));
});

test('presentation labels use uncapped final bar width and scaled fit checks without pre-scaling fonts or grids', () => {
  const data = selectPermitArea(readPermitPageData(permitPageFixture())), template = data.permits.presentation.rows.at(-1);
  data.permits.presentation.rows = Array.from({ length: 8 }, (_, index) => ({ ...template, year: 2019 + index, selected: 12345678.9 + index, annualForecast: null }));
  const view = { width: 3840, height: 1800, includeGrowth: true };
  const normal = permitsMainOption(data, 'light', view);
  const option = permitsMainOption(data, 'light', { ...view, presentation: true, fontScale: 3.2 });
  assert.equal(normal.series[0].label.formatter({ value: 12345678.9 }), '');
  assert.notEqual(option.series[0].label.formatter({ value: 12345678.9 }), '');
  const grid = option.grid[1], expectedWidth = (view.width - Math.round(grid.left * 3.2) - Math.round(grid.right * 3.2)) / 8 * .7;
  assert.equal(option.series[0].barWidth, expectedWidth); assert.ok(expectedWidth > 52);
  assert.deepEqual(option.grid, normal.grid);
  assert.equal(option.series[0].label.fontSize, 10);
  assert.equal(option.series.at(-1).label.fontSize, 10);
  assert.deepEqual(option.graphic.filter(item => item.id.startsWith('permit-total-')).map(item => [item.x, item.y]), normal.graphic.filter(item => item.id.startsWith('permit-total-')).map(item => [item.x, item.y]));
  const narrow = permitsMainOption(data, 'light', { ...view, width: 900, presentation: true, fontScale: 3.2 });
  assert.equal(narrow.series[0].label.formatter({ value: 12345678.9 }), '');
  assert.equal(narrow.series.at(-1).label.rotate, 90);
  const block = breakdownOption(data.breakdowns[0], 'light', { ...view, presentation: true, fontScale: 3.2 });
  assert.ok(block.series.filter(series => series.type === 'bar').every(series => series.barWidth > 52));
  assert.equal(block.series[0].label.formatter({ value: .00001 }), '');
});

test('mortgage annual growth alone is smoothed and totals use the supplied canvas height', () => {
  const value = { annual: 200e9, selected: 100e9, actualThroughMonth: 5, annualYoYPct: 10, selectedYoYPct: 15 };
  const rows = [{ year: 2024, volume: { ...value, annualYoYPct: null }, count: value }, { year: 2025, volume: value, count: value }], before = structuredClone(rows);
  const normal = mortgageAnnualOption(rows, 'volume', 'light', 900);
  assert.deepEqual(normal.graphic, mortgageAnnualOption(rows, 'volume', 'light', 900, 400).graphic);
  for (const height of [400, 720, 1080]) {
    const option = mortgageAnnualOption(rows, 'volume', 'light', 900, height), grid = option.grid[1];
    const pixels = (height - grid.top - grid.bottom) / option.yAxis[1].max;
    assert.equal(option.graphic[1].y, height - grid.bottom - 200 * pixels - 7);
    assert.equal(option.graphic[1].id, 'mortgage-total-2025');
    assert.deepEqual(option.series[2].data, [null, 10]);
    assert.equal(option.series[2].connectNulls, false);
    assert.equal(option.series[2].smooth, .25); assert.equal(option.series[2].smoothMonotone, 'x');
    assert.ok(option.series.slice(0, 2).every(series => series.smooth === undefined));
  }
  const month = { period: '2026-01', all: { ratePct: 10 }, primary: { volumeRub: 10, count: 2, ratePct: 6 }, secondary: { volumeRub: 20, count: 4, ratePct: 15 } };
  for (const option of [mortgageMonthlyOption([month], 'volume', 'light'), mortgageRatesOption([month], 'light')]) assert.ok(option.series.every(series => !series.smooth));
  assert.deepEqual(rows, before);
});

test('growth band applies one precision across labels, ticks and hover', () => {
  const option = permitGrowthOption([{ year: 2024, value: 1020.2, reason: '' }, { year: 2025, value: 300.2, reason: '' }], 'light');
  assert.equal(option.series[0].label.formatter({ value: 300.2 }), '+300 %');
  assert.equal(option.yAxis.axisLabel.formatter(300.2), '300');
  assert.match(option.tooltip.formatter({ dataIndex: 1 }), /\+300 %/);
});
