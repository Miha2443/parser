import assert from 'node:assert/strict';
import test from 'node:test';
import { demoPresentationOption, demoPresentationScale, layoutDemoLineLabels } from './demoPresentation.ts';

test('presentation tiers depend on both dimensions, not just a wide short window', () => {
  assert.equal(demoPresentationScale(390, 650), 1);
  assert.equal(demoPresentationScale(1920, 250), 1);
  assert.equal(demoPresentationScale(1824, 280), 1.5);
  assert.equal(demoPresentationScale(1200, 300), 1.25);
  assert.equal(demoPresentationScale(600, 500), 1);
  assert.equal(demoPresentationScale(592, 197), 1.25);
  assert.equal(demoPresentationScale(1200, 650), 1.5);
  assert.equal(demoPresentationScale(1850, 780), 2);
  assert.equal(demoPresentationScale(1824, 534), 2);
  assert.equal(demoPresentationScale(3750, 1740), 3.2);
});

test('compact permit breakdowns keep a readable year under every bar without changing categories', () => {
  const years = Array.from({ length: 16 }, (_, i) => String(2011 + i));
  years[15] += '\nянв–окт';
  const option = { grid: { left: 40, right: 12, top: 35, bottom: 52 },
    xAxis: { type: 'category', data: years, axisLabel: { interval: 0, fontSize: 11 } },
    yAxis: { type: 'value' }, series: [{ type: 'bar', data: years.map((_, i) => i + 1) }] };
  const shown = demoPresentationOption(option, 520, 270, [], 'permits/housing_components');
  assert.equal(shown.xAxis.axisLabel.rotate, 90);
  assert.equal(shown.xAxis.axisLabel.interval, 0);
  assert.equal(shown.xAxis.axisLabel.formatter(years[15]), '2026');
  assert.deepEqual(shown.xAxis.data, years);
  assert.equal(option.xAxis.axisLabel.rotate, undefined);
  assert.equal(demoPresentationOption(option, 1200, 650, [], 'permits/main').xAxis.axisLabel.rotate, undefined);
});

test('demo disables initial and update animation without changing the provider option', () => {
  for (const animation of [undefined, true, false]) {
    const option = Object.freeze({ animation, animationDuration: 0, animationDurationUpdate: 300 });
    for (const [width, height] of [[1920, 800], [3840, 1740], [350, 300]]) {
      const result = demoPresentationOption(option, width, height);
      assert.equal(result.animation, false);
      assert.equal(result.animationDuration, 0);
      assert.equal(result.animationDurationUpdate, 300);
      assert.equal(option.animation, animation);
    }
  }
});

test('presentation preserves measurements, missing points, styles and non-label callbacks without mutating the option', () => {
  const formatter = point => point.value === null ? '' : `value ${point.value}`;
  const layout = () => ({ hideOverlap: true });
  const option = {
    grid: { left: 48, right: 14, top: 36, bottom: 52 },
    xAxis: { type: 'category', data: ['2024', '2025', '2026'], axisLabel: { fontSize: 11, interval: 0, lineHeight: 16 } },
    yAxis: [{ type: 'value', name: 'area', max: 100, axisLabel: { formatter } }, { type: 'value', name: '%', max: 50 }],
    tooltip: { formatter },
    series: [
      { type: 'bar', name: 'actual', stack: 'area', data: [10.12, null, 30.56], barMaxWidth: 51, labelLayout: layout, label: { show: true, fontSize: 11, formatter, rich: { up: { fontSize: 9, lineHeight: 20 } } } },
      { type: 'bar', name: 'forecast', stack: 'area', data: [null, 20, 35], itemStyle: { borderType: 'dashed', decal: { rotation: -.5 } } },
      { type: 'line', name: 'growth', yAxisIndex: 1, data: [null, 20, -5], connectNulls: false, lineStyle: { type: 'dashed', width: 2 }, symbolSize: 5 },
    ],
  };
  const original = JSON.stringify(option), result = demoPresentationOption(option, 3750, 1740);
  assert.equal(JSON.stringify(option), original);
  assert.deepEqual(result.series.map(item => item.data), option.series.map(item => item.data));
  assert.notEqual(result.series[0].label.formatter, formatter);
  assert.notEqual(result.series[0].labelLayout, layout);
  assert.equal(option.series[0].label.formatter, formatter);
  assert.equal(option.series[0].labelLayout, layout);
  assert.equal(result.yAxis[0].axisLabel.formatter, formatter);
  assert.equal(result.tooltip.formatter, formatter);
  assert.equal(result.yAxis[0].max, 100); assert.equal(result.yAxis[1].max, 50);
  assert.equal(result.series[2].yAxisIndex, 1); assert.equal(result.series[2].connectNulls, false);
  assert.deepEqual(result.series[1].itemStyle, option.series[1].itemStyle);
  assert.equal(result.series[0].label.fontSize, 35);
  assert.equal(result.series[0].label.rich.up.fontSize, 29);
  assert.equal(result.xAxis.axisLabel.interval, 0);
});

const cartesian = (series, count = series[0].data.length) => ({
  grid: { left: 48, right: 16, top: 40, bottom: 48 },
  xAxis: { type: 'category', data: Array.from({ length: count }, (_, i) => String(i)), boundaryGap: true },
  yAxis: { type: 'value', axisLabel: { color: '#374151' } }, series,
});
const labels = series => series.data.map((_, dataIndex) => series.label.formatter({ dataIndex }));
const extremaPoints = series => (series.markPoint?.data ?? []).filter(p => p.name?.startsWith('demo-extremum-'));

test('one outside value labels each sign of a stack, including a zero but never an all-null category', () => {
  const option = cartesian([
    { type: 'bar', stack: 'market', data: [10.12, null, -3.12, 0, 8, null] },
    { type: 'bar', stack: 'market', data: [20.22, null, -2.12, null, -4, 2.54] },
  ]);
  const result = demoPresentationOption(option, 1200, 650);
  assert.deepEqual(labels(result.series[0]), ['', '', '', '0', '8', '']);
  assert.deepEqual(labels(result.series[1]), ['30,3', '', '-5,2', '', '-4', '2,5']);
  assert.ok(result.series.every(s => s.label.position === 'outside' && s.label.rotate === 0));
  assert.deepEqual(result.series.map(s => s.data), option.series.map(s => s.data));
});

test('trailing zero components never replace a nonzero stack anchor or add baseline labels', () => {
  const columns = [[0, .9, 0], [41.8, 9.1, 0], [0, 0, 0], [0, 56.8, 0], [0, -.9, 0], [0, 0, null]];
  const option = cartesian([0, 1, 2].map(i => ({ type: 'bar', stack: 'programs', data: columns.map(parts => parts[i]) })));
  const result = demoPresentationOption(option, 1200, 650);
  assert.deepEqual(result.series.map(labels), [
    ['', '', '0', '', '', '0'],
    ['0,9', '50,9', '', '56,8', '-0,9', ''],
    ['', '', '', '', '', ''],
  ]);
  assert.deepEqual(result.series.map(s => s.data), option.series.map(s => s.data));
  const layout = result.series[0].labelLayout({ dataIndex: 2, text: '0', rect: { x: 100, y: 400, width: 20, height: 0 },
    labelRect: { x: 106, y: 406, width: 8, height: 11 }, align: 'center', verticalAlign: 'top' });
  assert.ok(layout.y + 11 <= 400, 'The all-zero total belongs above the zero-height stack');
});

test('positive and negative stack anchors stay on the last nonzero component of their own sign', () => {
  const option = cartesian([10, -4, 0, -2, 0].map(value => ({ type: 'bar', stack: 'signed', data: [value] })));
  const result = demoPresentationOption(option, 600, 500);
  assert.deepEqual(result.series.map(labels), [['10'], [''], [''], ['-6'], ['']]);
  const hidden = demoPresentationOption(cartesian([
    { type: 'bar', stack: 's', name: 'a', data: [0, 0] },
    { type: 'bar', stack: 's', name: 'b', data: [.9, 56.8] },
    { type: 'bar', stack: 's', name: 'c', data: [0, 0] },
  ]), 600, 500, ['b']);
  assert.deepEqual(hidden.series.map(labels), [['0', '0'], ['', ''], ['', '']]);
});

test('stack strategy all uses a cumulative endpoint, not a double-counted total', () => {
  const result = demoPresentationOption(cartesian([
    { type: 'bar', stack: 'net', stackStrategy: 'all', data: [10] },
    { type: 'bar', stack: 'net', stackStrategy: 'all', data: [-3] },
    { type: 'bar', stack: 'net', stackStrategy: 'all', data: [2] },
  ]), 600, 500);
  assert.deepEqual(result.series.map(labels), [[''], [''], ['9']]);
});

test('grouped, overlaid and different-axis bars are not summed with one another or with growth lines', () => {
  const option = cartesian([
    { type: 'bar', stack: 'same', data: [10.4, 20.4] },
    { type: 'bar', stack: 'same', yAxisIndex: 1, data: [30.4, 40.4] },
    { type: 'bar', data: [50.4, 60.4], barGap: '-100%' },
    { type: 'bar', data: [70.4, 80.4], barGap: '-100%' },
    { type: 'line', yAxisIndex: 1, data: [3.4, -4.4] },
  ]);
  option.yAxis = [option.yAxis, { type: 'value', name: '%' }];
  const result = demoPresentationOption(option, 1200, 650);
  assert.deepEqual(result.series.slice(0, 4).map(labels), [['10,4', '20,4'], ['30,4', '40,4'], ['50,4', '60,4'], ['70,4', '80,4']]);
  assert.equal(result.series[4].yAxisIndex, 1);
  assert.equal(result.series[4].label.show, false);
  assert.ok(extremaPoints(result.series[4]).every(p => p.value === option.series[4].data[p.coord[0]]));
});

test('different grids keep independent stack totals and axes', () => {
  const option = cartesian([
    { type: 'bar', stack: 'same', data: [10] },
    { type: 'bar', stack: 'same', data: [20] },
    { type: 'bar', stack: 'same', xAxisIndex: 1, yAxisIndex: 1, data: [100] },
  ]);
  option.grid = [{ left: 50, right: 20, top: 30, height: 120 }, { left: 50, right: 20, top: 210, bottom: 40 }];
  option.xAxis = [option.xAxis, { ...option.xAxis, gridIndex: 1 }];
  option.yAxis = [option.yAxis, { ...option.yAxis, gridIndex: 1 }];
  const result = demoPresentationOption(option, 600, 500);
  assert.deepEqual(result.series.map(labels), [[''], ['30'], ['100']]);
  assert.equal(result.series[2].xAxisIndex, 1); assert.equal(result.series[2].yAxisIndex, 1);
});

test('horizontal/object/encoded tuple measurements retain categories, nulls and their original values', () => {
  const result = demoPresentationOption({ grid: { left: 50, right: 50, top: 30, bottom: 30 },
    yAxis: { type: 'category', data: ['a', 'b', 'c'] }, xAxis: { type: 'value' },
    series: [
      { type: 'bar', stack: 's', dimensions: ['category', 'measurement'], encode: { x: 'measurement', y: 'category' }, data: [['c', 3.25], ['a', 1.25], ['b', null]] },
      { type: 'bar', stack: 's', data: [{ value: 2.25, itemStyle: { color: '#123456' } }, { value: null }, { value: 4.25 }] },
    ],
  }, 600, 500);
  // Formatter indices refer to source data, even when tuples are not in axis order.
  assert.deepEqual(labels(result.series[0]), ['', '', '']);
  assert.deepEqual(labels(result.series[1]), ['3,5', '', '7,5']);
});

test('unsorted tuple labels use their source dataIndex, and invalid measurements never become zero', () => {
  const option = cartesian([{ type: 'bar', data: [['2', 3.25], ['0', 1.25], ['1', null], ['3', ''], ['4', NaN], ['5', 0]] }]);
  const result = demoPresentationOption(option, 1200, 300);
  assert.deepEqual(labels(result.series[0]), ['3,3', '1,3', '', '', '', '0']);
  assert.deepEqual(result.series[0].data, option.series[0].data);
});

test('precision is shared by visible measurements and stack totals, and hidden series do not set it', () => {
  const option = cartesian([
    { type: 'bar', name: 'large', data: [1000.49, null] },
    { type: 'bar', name: 'small', data: [12.34, 56.78] },
    { type: 'line', name: 'hidden-line', data: [9000, 8000] },
  ]);
  option.legend = [{ selected: { large: false } }, { selected: { 'hidden-line': false } }];
  const hidden = demoPresentationOption(option, 600, 500);
  assert.equal(hidden.series[0].label.show, false);
  assert.deepEqual(labels(hidden.series[1]), ['12,3', '56,8']);
  assert.deepEqual(extremaPoints(hidden.series[2]), []);
  const visible = demoPresentationOption({ ...option, legend: {} }, 600, 500, ['hidden-line']);
  assert.deepEqual(labels(visible.series[1]), ['12', '57']);
  const threshold = demoPresentationOption(cartesian([
    { type: 'bar', stack: 's', data: [600.4, 1.24] }, { type: 'bar', stack: 's', data: [400.4, 2.24] },
  ]), 600, 500);
  assert.equal(labels(threshold.series[1])[0].replace(/\s/g, ''), '1001');
  assert.equal(labels(threshold.series[1])[1], '3');
});

test('hidden series neither contribute stack sums nor make visible bars narrower', () => {
  const option = cartesian([
    { type: 'bar', name: 'actual', stack: 's', data: [10.24] },
    { type: 'bar', name: 'forecast', stack: 's', data: [30.24] },
    { type: 'bar', name: 'other', data: [1000] },
  ]);
  const result = demoPresentationOption(option, 600, 500, ['forecast', 'other']);
  assert.deepEqual(labels(result.series[0]), ['10,2']);
  assert.equal(result.series[0].barWidth, demoPresentationOption(cartesian([option.series[0]]), 600, 500).series[0].barWidth);
});

test('provider markPoint totals do not duplicate outside sums; unrelated markers survive', () => {
  const option = cartesian([
    { type: 'bar', stack: 's', name: 'a', data: [10, 20] },
    { type: 'bar', stack: 's', name: 'b', data: [30, 40], markPoint: { symbolSize: 0, data: [
      { coord: [0, 40], value: 40 }, { coord: [1, 60], value: 60 }, { coord: [0, 99], value: 99, symbolSize: 12 },
    ] } },
  ]);
  const original = JSON.stringify(option);
  const result = demoPresentationOption(option, 600, 500, ['a']);
  assert.deepEqual(labels(result.series[1]), ['30', '40']);
  assert.deepEqual(result.series[1].markPoint.data, [{ coord: [0, 99], value: 99, symbolSize: 12 }]);
  assert.equal(JSON.stringify(option), original);
});

test('category-named totals are deduplicated but explicitly visible event symbols are retained', () => {
  const option = cartesian([{ type: 'bar', data: [10], markPoint: { symbolSize: 0, data: [
    { coord: ['0', 10], value: 10 }, { coord: [0, 10], value: 10, symbolSize: 12, name: 'event' },
  ] } }]);
  const result = demoPresentationOption(option, 600, 500);
  assert.deepEqual(result.series[0].markPoint.data, [{ coord: [0, 10], value: 10, symbolSize: 12, name: 'event' }]);
  assert.deepEqual(labels(result.series[0]), ['10']);
});

test('native totals ignore overlays, unrelated axes and growth, and never label an all-null year as zero', () => {
  const option = cartesian([
    { type: 'bar', stack: 's', data: [10, null] }, { type: 'bar', stack: 's', data: [20, null] },
    { type: 'bar', yAxisIndex: 1, data: [500, null] },
    { type: 'line', data: [100, 200] },
  ]);
  option.yAxis = [option.yAxis, { type: 'value' }];
  option.xAxis.data = ['2025', '2026'];
  option.graphic = [2025, 2026].map(year => ({ id: `mortgage-total-${year}`, type: 'group', children: [{ style: { text: '500', font: '600 11px Segoe UI' } }] }));
  const result = demoPresentationOption(option, 600, 500);
  const points = result.series.flatMap(s => s.markPoint?.data ?? []).filter(p => p.name?.startsWith('demo-native-total-'));
  assert.equal(points.length, 1);
  assert.deepEqual(points[0].coord, [0, 500]);
  assert.deepEqual(labels(result.series[1]), ['30', '']);
  assert.deepEqual(labels(result.series[2]), ['', '']);
});

test('dense monthly bars have readable, regularly spaced values in grouped panels and TV canvases', () => {
  const option = cartesian([
    { type: 'bar', stack: 'market', data: Array(132).fill(12.25), label: { show: false } },
    { type: 'bar', stack: 'market', data: Array(132).fill(23.25), label: { show: false } },
  ]);
  for (const [width, height] of [[600, 500], [1200, 300], [3750, 1740]]) {
    const result = demoPresentationOption(option, width, height), series = result.series[1];
    const indices = labels(series).flatMap((label, i) => label ? [i] : []);
    assert.ok(indices.length >= 3 && indices.length < 132, `${width}x${height}: ${indices.length}`);
    assert.equal(indices.at(-1), 131);
    assert.ok(indices.every((index, i) => !i || index - indices[i - 1] === indices[1] - indices[0]));
    assert.ok(labels(series).filter(Boolean).every(value => value === '35,5'));
    assert.ok(labels(result.series[0]).every(value => value === ''));
    if (width === 600) assert.equal(series.label.fontSize, 11);
    if (width === 1200) assert.equal(series.label.fontSize, 14);
  }
});

test('responsive overrides do not restore dense provider labels; layout clamps and hides actual overlaps', () => {
  const option = cartesian([{ type: 'bar', data: [10], label: { show: false, rotate: 90 } }]);
  option.media = [{ query: { maxWidth: 600 }, option: { series: [{ label: { show: false }, labelLayout: { hideOverlap: false } }] } }];
  const result = demoPresentationOption(option, 600, 500);
  assert.equal(result.media[0].option.series[0].label, undefined);
  const layout = result.series[0].labelLayout({ text: '10', labelRect: { x: -10, y: 495, width: 40, height: 20 }, align: 'center', verticalAlign: 'bottom' });
  assert.deepEqual(layout, { hideOverlap: true, x: 24, y: 496 });
  assert.deepEqual(result.series[0].labelLayout({ text: '', labelRect: { x: 0, y: 0, width: 0, height: 20 } }), { hideOverlap: false });
});

test('responsive ring overrides remain intact, and deeply frozen options work without input mutation', () => {
  const freeze = value => { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; };
  const formatter = value => String(value);
  const option = cartesian([{ id: 'bars', type: 'bar', stack: 's', data: [10.12, null] }, { id: 'ring', type: 'pie', data: [{ value: 20.24 }] }]);
  option.tooltip = { formatter };
  option.media = [{ query: { maxWidth: 600 }, option: { series: [{ id: 'ring', label: { show: false, fontSize: 9 } }, { id: 'bars', label: { show: false } }] } }];
  freeze(option);
  const result = demoPresentationOption(option, 600, 500);
  assert.deepEqual(result.media[0].option.series[0].label, option.media[0].option.series[0].label);
  assert.equal(result.media[0].option.series[1].label, undefined);
  assert.deepEqual(result.series.map(s => s.data), option.series.map(s => s.data));
  assert.equal(result.tooltip.formatter, formatter);
  assert.equal(option.media[0].option.series[1].label.show, false);
});

test('real significant peaks retain their identity among the adaptive line captions', () => {
  const values = [100, 100.02, 100, 100.01, 100, 103, 106, 106, 106, 103, 100, 97, 94, 97, 100];
  const option = cartesian([{ type: 'line', name: 'CPI', data: values, showSymbol: false, connectNulls: false, label: { show: false } }]);
  option.xAxis.boundaryGap = false;
  const result = demoPresentationOption(option, 1200, 650), points = extremaPoints(result.series[0]);
  assert.ok(points.some(p => p.coord[0] === 7 && p.value === 106));
  assert.ok(points.some(p => p.coord[0] === 12 && p.value === 94));
  assert.ok(points.every(p => p.value === values[p.coord[0]]));
  assert.ok(points.every(p => p.coord[0] >= 5));
  assert.ok(points.length <= 4);
  assert.equal(result.series[0].showSymbol, false);
  assert.equal(result.series[0].connectNulls, false);
  assert.ok(points.every(p => p.label.formatter() === String(p.value)));
});

test('gaps, flat runs and isolated observations cannot create fictitious line extrema', () => {
  const option = cartesian([{ type: 'line', data: [null, 100, 100, null, 200, null, 300, 300, 300, null], connectNulls: true }]);
  const result = demoPresentationOption(option, 1200, 650);
  assert.deepEqual(extremaPoints(result.series[0]), []);
  assert.deepEqual(result.series[0].data, option.series[0].data);
});

const lineCaptions = option => option.series.flatMap(series => (series.markPoint?.data ?? []).filter(p => /^demo-(extremum|value)-/.test(p.name)));
function resolveCaptionPixels(option, project, width = 1200, height = 650) {
  let updates = 0;
  layoutDemoLineLabels({ getOption: () => option, getWidth: () => width, getHeight: () => height,
    convertToPixel: (finder, value) => Array.isArray(value) ? project(finder, value) : NaN,
    setOption: () => { updates++; } });
  return { captions: lineCaptions(option), updates };
}

test('roomy annual growth shows every observed percent rather than only extrema', () => {
  const values = [null, 25, -20, 1, 4, 77, -7, 204, -79, 62, 15, 18, 4, -2, 3, -10];
  const option = demoPresentationOption(cartesian([{ type: 'line', name: 'Growth, %', data: values,
    label: { formatter: p => `${p.value > 0 ? '+' : ''}${p.value}%` } }]), 1920, 650);
  const { captions } = resolveCaptionPixels(option, (_, [index, value]) => [100 + index * 110, 150 - value / 5], 1920);
  assert.equal(captions.length, 15);
  assert.ok(captions.every(p => p.label.show), 'No density quota may suppress a caption that fits');
  assert.deepEqual(captions.map(p => p.value), values.filter(v => v !== null));
  assert.ok(captions.every(p => p.label.formatter().endsWith('%')));
});

test('flat runs and isolated finite observations have values without fabricated extrema or missing-value zeros', () => {
  const option = demoPresentationOption(cartesian([{ type: 'line', name: 'flat',
    data: [null, 100, 100, null, 200, null, 0, null] }]), 1200, 650);
  const { captions } = resolveCaptionPixels(option, (_, [index]) => [120 + index * 110, 200]);
  assert.equal(captions.length, 4);
  assert.ok(captions.every(p => p.label.show && p.name.startsWith('demo-value-')));
  assert.deepEqual(captions.map(p => p.value), [100, 100, 200, 0]);
});

test('actual pixel collisions try below the point before dropping either caption', () => {
  const option = demoPresentationOption(cartesian([
    { type: 'line', name: 'one', data: [10] }, { type: 'line', name: 'two', data: [10] },
  ]), 600, 400);
  const { captions } = resolveCaptionPixels(option, () => [250, 180], 600, 400);
  assert.equal(captions.filter(p => p.label.show).length, 2);
  assert.deepEqual(captions.map(p => p.label.position).sort(), ['bottom', 'top']);
});

test('captions at the same x position remain visible when their actual y positions leave room', () => {
  const option = demoPresentationOption(cartesian([
    { type: 'line', name: 'one', data: [10] }, { type: 'line', name: 'two', data: [20] },
    { type: 'line', name: 'three', data: [30] },
  ]), 600, 500);
  const { captions } = resolveCaptionPixels(option, (_, [, value]) => [250, value * 10], 600, 500);
  assert.ok(captions.every(p => p.label.show));
});

test('resolved plot edges keep endpoint captions out of the Y-axis tick gutter', () => {
  const option = demoPresentationOption(cartesian([{ type: 'line', data: [98.1, 100] }]), 600, 400);
  option.xAxis.boundaryGap = false;
  layoutDemoLineLabels({ getOption: () => option, getWidth: () => 600, getHeight: () => 400,
    convertToPixel: (_, value) => Array.isArray(value) ? [value[0] ? 540 : 90, 220] : value ? 540 : 90,
    setOption: () => {} });
  const points = lineCaptions(option);
  assert.ok(points.every(p => p.label.show));
  assert.ok(points[0].label.offset[0] > 0, 'The first label must move right of the actual plot edge');
  assert.ok(points[1].label.offset[0] < 0, 'The final label must stay inside the right plot edge');
});

test('a trough near the plot bottom moves its label above instead of crossing the axis', () => {
  const option = demoPresentationOption(cartesian([{ type: 'line', name: '%', data: [2, 1, 2] }]), 600, 400);
  const { captions } = resolveCaptionPixels(option, (_, [index]) => [100 + index * 150, index === 1 ? 345 : 180], 600, 400);
  const trough = captions.find(p => p.coord[0] === 1);
  assert.ok(trough.label.show);
  assert.equal(trough.label.position, 'top');
});

test('only genuine lack of two-dimensional room suppresses line captions', () => {
  const option = demoPresentationOption(cartesian(Array.from({ length: 12 }, (_, i) => ({
    type: 'line', name: String(i), data: [10],
  }))), 600, 400);
  const { captions } = resolveCaptionPixels(option, () => [250, 180], 600, 400);
  assert.equal(captions.length, 12);
  assert.equal(captions.filter(p => p.label.show).length, 2);
});

test('charts without generated line captions require no second layout update', () => {
  const option = demoPresentationOption(cartesian([{ type: 'bar', data: [10] }]), 600, 400);
  assert.equal(resolveCaptionPixels(option, () => { throw new Error('No line projection expected'); }).updates, 0);
});

test('the five quiet monthly charts retain hover, axes and measurements without any persistent line values', () => {
  const tooltip = point => `value: ${point.value}`;
  const source = cartesian([{ type: 'line', name: 'monthly', data: [10, null, 30], label: { show: true },
    markPoint: { data: [{ type: 'max', name: 'maximum', label: { show: true } }] } }]);
  source.tooltip = { trigger: 'axis', formatter: tooltip };
  const before = JSON.stringify(source);
  for (const id of ['domrf/construction', 'sales/monthly_percent', 'ipc/ipc_month', 'salary/salary_month', 'mortgage/rates']) {
    for (const [width, height] of [[390, 350], [1920, 900], [3840, 1740]]) {
      const option = demoPresentationOption(source, width, height, [], id);
      assert.equal(option.series[0].label.show, false, id);
      assert.equal(option.series[0].emphasis.label.show, false, id);
      assert.equal(option.series[0].markPoint.label.show, false, id);
      assert.ok(option.series[0].markPoint.data.every(p => p.label.show === false), id);
      assert.equal(lineCaptions(option).length, 0, id);
      assert.equal(resolveCaptionPixels(option, () => [100, 100]).updates, 0, id);
      assert.deepEqual(option.series[0].data, source.series[0].data);
      assert.deepEqual(option.xAxis.data, source.xAxis.data);
      assert.equal(option.tooltip.formatter, tooltip);
    }
  }
  assert.equal(JSON.stringify(source), before);
});

test('quiet monthly charts do not remove annual bars or the adaptive permit growth captions', () => {
  for (const id of ['ipc/ipc_year', 'salary/salary_year', 'linear/roads', 'preferential/ratePct/annual']) {
    const option = demoPresentationOption(cartesian([{ type: 'bar', data: [10, 20, 30] }]), 1200, 650, [], id);
    assert.deepEqual(labels(option.series[0]), ['10', '20', '30'], id);
  }
  const option = demoPresentationOption(cartesian([{ type: 'line', name: 'growth, %', data: [1, 2, 3] }]), 1200, 650, [], 'permits/main');
  assert.equal(lineCaptions(option).filter(p => p.label.show).length, 3);
});

test('switching to a quiet chart removes previously generated demo annotations', () => {
  const first = demoPresentationOption(cartesian([{ type: 'line', data: [10, 20, 30] }]), 1200, 650);
  assert.equal(lineCaptions(first).length, 3);
  const quiet = demoPresentationOption(first, 1200, 650, [], 'mortgage/rates');
  assert.equal(lineCaptions(quiet).length, 0);
  assert.equal(quiet.series[0].label.show, false);
});

test('sparse provider growth labels retain percent/sign semantics at real plateau extrema', () => {
  const values = [25, 25, -20, 25, 25, -20, 25, 25, -20, 25, 25, -20, 25, 25, -20, 25, 25, -20];
  const option = cartesian([{ type: 'line', name: 'Growth, %', data: values, label: { show: true, formatter: p => `${p.value > 0 ? '+' : ''}${p.value}%` } }]);
  option.yAxis.name = '%';
  const result = demoPresentationOption(option, 900, 300), points = extremaPoints(result.series[0]);
  assert.ok(points.length >= 2 && points.length < values.length);
  assert.ok(points.some(p => p.label.formatter() === '+25%'));
  assert.ok(points.some(p => p.label.formatter() === '-20%'));
  assert.ok(points.every(p => p.value === values[p.coord[0]]));
  const withLargeHidden = demoPresentationOption({ ...option, series: [...option.series, { type: 'line', name: 'hidden', data: values.map(() => 1000) }] }, 900, 300, ['hidden']);
  assert.ok(extremaPoints(withLargeHidden.series[0]).every(p => p.label.formatter().endsWith('%')));
});

test('the upper-grid percent axis remains semantic even when its duplicate caption is suppressed', () => {
  const option = cartesian([{ type: 'line', name: 'growth', data: [25, -20, 25] }]);
  option.grid = [{ left: 50, right: 20, top: 30, height: 100 }, { left: 50, right: 20, top: 210, bottom: 40 }];
  option.yAxis.name = '%';
  const result = demoPresentationOption(option, 600, 500);
  assert.equal(result.yAxis.name, '');
  assert.ok(extremaPoints(result.series[0]).every(p => p.label.formatter().endsWith('%')));
  assert.ok(extremaPoints(result.series[0]).length >= 2);
});

test('growth, actual and forecast lines retain their own axes, values and existing event annotations', () => {
  const option = cartesian([
    { type: 'line', name: 'actual', data: [10.24, 13.24, 10.24, 7.24, 10.24], lineStyle: { color: '#123456' } },
    { type: 'line', name: 'forecast', data: [null, null, null, 7.24, 11.24], lineStyle: { type: 'dashed' }, markPoint: { data: [{ type: 'max', name: 'event' }] } },
    { type: 'line', name: 'growth', xAxisIndex: 1, yAxisIndex: 1, data: [null, 50.24, -30.24, 20.24, -10.24], label: { show: true, formatter: () => 'old dense caption' } },
  ]);
  option.grid = [{ left: 50, right: 15, top: 30, height: 100 }, { left: 50, right: 15, top: 190, bottom: 50 }];
  option.xAxis = [{ ...option.xAxis, gridIndex: 1 }, { ...option.xAxis, gridIndex: 0 }];
  option.yAxis = [{ type: 'value', gridIndex: 1, max: 20 }, { type: 'value', gridIndex: 0, name: '%', min: -100, max: 100 }];
  const result = demoPresentationOption(option, 1200, 650, ['forecast']);
  for (const i of [0, 2]) {
    const points = extremaPoints(result.series[i]);
    assert.ok(points.length >= 2);
    assert.ok(points.every(p => p.value === option.series[i].data[p.coord[0]]));
  }
  assert.deepEqual(result.series[1].markPoint.data, [{ type: 'max', name: 'event' }]);
  assert.equal(result.series[2].xAxisIndex, 1); assert.equal(result.series[2].yAxisIndex, 1);
  assert.equal(result.yAxis[1].min, -100); assert.equal(result.yAxis[1].max, 100);
});

test('stacked and step lines are not annotated at misleading raw coordinates', () => {
  const result = demoPresentationOption(cartesian([
    { type: 'line', stack: 's', data: [1, 5, 1] }, { type: 'line', step: 'middle', data: [1, 5, 1] },
  ]), 600, 500);
  assert.ok(result.series.every(s => extremaPoints(s).length === 0));
});

test('yearly bars fill the available category width but dense monthly bars never exceed their slots', () => {
  const option = (count, grouped = false, overlay = false) => ({
    grid: { left: 48, right: 14, top: 36, bottom: 52 },
    xAxis: { type: 'category', data: Array.from({ length: count }, (_, i) => String(i)), axisLabel: { interval: 'auto', hideOverlap: true } },
    yAxis: { type: 'value' },
    series: Array.from({ length: 3 }, (_, i) => ({ type: 'bar', name: String(i), ...(grouped ? {} : { stack: 'same' }), ...(overlay ? { barGap: '-100%' } : {}), barMaxWidth: 51, data: Array(count).fill(i) })),
  });
  const yearly = demoPresentationOption(option(9), 3750, 1740);
  assert.ok(yearly.series[0].barWidth > 250);
  assert.equal(yearly.series[0].barWidth, yearly.series[2].barWidth);
  const monthly = demoPresentationOption(option(132), 1850, 780);
  assert.ok(monthly.series[0].barWidth < 10);
  assert.equal(monthly.xAxis.axisLabel.interval, 'auto');
  const grouped = demoPresentationOption(option(9, true), 3750, 1740);
  assert.ok(grouped.series[0].barWidth < yearly.series[0].barWidth / 3);
  const overlay = demoPresentationOption(option(9, true, true), 3750, 1740);
  assert.equal(overlay.series[0].barWidth, yearly.series[0].barWidth);
  assert.equal(overlay.series[0].barGap, '-100%');
});

test('totals use native coordinates on the bar grid rather than fixed canvas positions', () => {
  const width = 1850, height = 780;
  const grid = { left: 52, right: 15, top: 154, bottom: 51 };
  const option = {
    grid: [{ left: 52, right: 15, top: 22, height: 68 }, grid],
    xAxis: [{ type: 'category', gridIndex: 0, data: ['2025'] }, { type: 'category', gridIndex: 1, data: ['2025'] }],
    yAxis: [{ gridIndex: 0 }, { gridIndex: 1 }],
    graphic: [{ id: 'mortgage-total-2025', type: 'group', x: 52 + (width - 52 - 15) * .5, y: grid.top + (height - grid.top - grid.bottom) * .4, children: [{ type: 'text', style: { text: 'value', font: '600 11px Segoe UI' } }] }],
    series: [{ type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: [10] }],
  };
  const result = demoPresentationOption(option, width, height), transformed = result.grid[1];
  assert.deepEqual(result.graphic, []);
  const point = result.series[0].markPoint.data[0];
  assert.deepEqual(point.coord, [0, 10]);
  assert.equal(point.label.formatter(), 'value');
  assert.equal(point.label.fontSize, 22);
  assert.equal(result.series[0].yAxisIndex, 1);
  assert.ok(height - transformed.top - transformed.bottom > 0);
  assert.equal(result.grid[0].height, Math.round(68 * Math.sqrt(2)));
});

test('growth captions and missing-point marks remain in the upper grid', () => {
  const option = {
    grid: [{ left: 54, right: 12, top: 39, height: 65 }, { left: 54, right: 12, top: 170, bottom: 60 }],
    graphic: [
      { id: 'permit-growth-title', type: 'text', x: 54, y: 4, style: { text: 'growth', font: '600 11px Segoe UI' } },
      { id: 'permit-growth-missing-2011', type: 'text', x: 120, y: 112, style: { text: 'n/a', font: '9px Segoe UI' } },
    ],
  };
  const result = demoPresentationOption(option, 1850, 780);
  assert.ok(result.graphic[0].y < result.grid[0].top);
  assert.ok(result.graphic[1].y < result.grid[1].top);
  assert.ok(780 - result.grid[1].top - result.grid[1].bottom > 400);
});

test('ring totals and units form a large centered pair without changing values', () => {
  const option = { graphic: [
    { type: 'text', left: 'center', top: '42%', style: { text: '4 158', font: '600 23px Segoe UI' } },
    { type: 'text', left: 'center', top: '56%', style: { text: 'area', font: '12px Segoe UI' } },
  ], series: [{ type: 'pie', radius: ['66%', '87%'], data: [{ name: 'actual', value: 4158 }] }] };
  const result = demoPresentationOption(option, 3750, 1740);
  assert.equal(result.graphic[0].style.font, '600 134px Segoe UI');
  assert.equal(result.graphic[1].style.font, '38px Segoe UI');
  assert.equal(result.graphic[0].style.text, '4 158');
  assert.ok(result.graphic[0].top < result.graphic[1].top);
  assert.deepEqual(result.series[0], option.series[0]);
});

test('bars on different value axes still share the same category slot', () => {
  const result = demoPresentationOption({ grid: { left: 12, right: 12, top: 36, bottom: 12 }, xAxis: { type: 'category', data: ['2026'] }, yAxis: [{ type: 'value' }, { type: 'value' }], series: [
    { type: 'bar', yAxisIndex: 0, data: [12] }, { type: 'bar', yAxisIndex: 1, data: [25] },
  ] }, 1000, 500);
  assert.equal(result.series[0].barWidth, result.series[1].barWidth);
  assert.ok(result.series[0].barWidth * 2.15 < 1000 - 36);
});

test('short canvases keep positive plots and never remap mortgage totals beyond the canvas', () => {
  const result = demoPresentationOption({ grid: [{ left: 52, right: 15, top: 22, height: 68 }, { left: 52, right: 15, top: 154, bottom: 51 }],
    xAxis: [{ type: 'category', data: ['2025'] }, { type: 'category', gridIndex: 1, data: ['2025'] }], yAxis: [{ name: '%' }, { gridIndex: 1 }],
    graphic: [{ id: 'mortgage-total-2025', type: 'group', x: 400, y: 108, children: [{ type: 'text', style: { text: '100', font: '600 11px Segoe UI' } }] }],
    series: [{ type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: [100] }],
  }, 1000, 180);
  assert.ok(180 - result.grid[1].top - result.grid[1].bottom >= 64);
  assert.ok(result.grid[0].top + result.grid[0].height < result.grid[1].top);
  assert.equal(result.yAxis[0].name, '');
  assert.deepEqual(result.graphic, []);
  assert.deepEqual(result.series[0].markPoint.data[0].coord, [0, 100]);
});

test('hiding a forecast series never hides an unrelated actual-year total', () => {
  const option = { grid: { left: 52, right: 12, top: 70, bottom: 38 }, xAxis: { type: 'category', data: ['2025'] }, yAxis: { type: 'value' },
    legend: { selected: { Forecast: false } },
    graphic: [{ id: 'permit-total-2025', type: 'group', x: 200, y: 100, children: [{ type: 'text', style: { text: '200', font: '600 10px Segoe UI' } }] }],
    series: [{ type: 'bar', name: 'Actual', stack: 'area', data: [200] }, { type: 'bar', name: 'Forecast', stack: 'area', data: [null] }],
  };
  const result = demoPresentationOption(option, 1920, 780);
  assert.equal(result.series[0].markPoint.data[0].label.formatter(), '200');
  assert.equal(result.series[1].markPoint, undefined);
  const allHidden = demoPresentationOption(option, 1920, 780, ['Actual']);
  assert.deepEqual(allHidden.graphic, []);
  assert.ok(allHidden.series.every(series => !series.markPoint));
});

test('ring totals and units retain a full line of separation in short windows', () => {
  for (const [width, height] of [[1000, 400], [1920, 560], [390, 320]]) {
    const result = demoPresentationOption({ graphic: [
      { type: 'text', left: 'center', top: '42%', style: { text: '123 456', font: '600 23px Segoe UI' } },
      { type: 'text', left: 'center', top: '56%', style: { text: 'unit', font: '12px Segoe UI' } },
    ], series: [{ type: 'pie', data: [{ value: 123456 }] }] }, width, height);
    const [main, unit] = result.graphic, size = Number(main.style.font.match(/(\d+)px/)[1]);
    assert.ok(main.top + size * 1.25 + 7 <= unit.top, `${width}x${height}`);
    assert.equal(main.style.text, '123 456');
  }
});
