import assert from 'node:assert/strict';
import test from 'node:test';
import { demoPresentationOption, demoPresentationScale } from './demoPresentation.ts';

test('presentation tiers depend on both dimensions, not just a wide short window', () => {
  assert.equal(demoPresentationScale(390, 650), 1);
  assert.equal(demoPresentationScale(1920, 250), 1);
  assert.equal(demoPresentationScale(1200, 650), 1.5);
  assert.equal(demoPresentationScale(1850, 780), 2);
  assert.equal(demoPresentationScale(3750, 1740), 3.2);
});

test('presentation preserves measurements, missing points, styles and callback identity without mutating the option', () => {
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
  assert.equal(result.series[0].label.formatter, formatter);
  assert.equal(result.series[0].labelLayout, layout);
  assert.equal(result.tooltip.formatter, formatter);
  assert.equal(result.yAxis[0].max, 100); assert.equal(result.yAxis[1].max, 50);
  assert.equal(result.series[2].yAxisIndex, 1); assert.equal(result.series[2].connectNulls, false);
  assert.deepEqual(result.series[1].itemStyle, option.series[1].itemStyle);
  assert.equal(result.series[0].label.fontSize, 35);
  assert.equal(result.series[0].label.rich.up.fontSize, 29);
  assert.equal(result.xAxis.axisLabel.interval, 0);
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
