import type { EChartsCoreOption } from 'echarts/core';

type Settings = Record<string, unknown>;
const record = (value: unknown): Settings => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Settings : {};
const list = (value: unknown): Settings[] => (Array.isArray(value) ? value : value ? [value] : []).map(record);
const numeric = (value: unknown, fallback: number) => typeof value === 'number' && Number.isFinite(value) ? value : fallback;

// Discrete display tiers keep TV text readable without oversized labels on short windows.
export function demoPresentationScale(width: number, height: number): number {
  if (width >= 3000 && height >= 1100) return 3.2;
  if (width >= 2200 && height >= 850) return 2.6;
  if (width >= 1500 && height >= 560) return 2;
  if (width >= 1000 && height >= 400) return 1.5;
  if (width >= 700 && height >= 300) return 1.25;
  return 1;
}

function copy(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(copy);
  if (value !== null && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, copy(item)]));
  return value;
}
function scaleText(value: unknown, scale: number) {
  if (Array.isArray(value)) { value.forEach(item => scaleText(item, scale)); return; }
  if (value === null || typeof value !== 'object') return;
  for (const [key, item] of Object.entries(value)) {
    if (key === 'data') continue;
    if (['fontSize', 'lineHeight'].includes(key) && typeof item === 'number') (value as Settings)[key] = Math.round(item * scale);
    else if (key === 'font' && typeof item === 'string') (value as Settings)[key] = item.replace(/(\d+(?:\.\d+)?)px/, (_, size) => `${Math.round(Number(size) * scale)}px`);
    else scaleText(item, scale);
  }
}
type Plot = { left: number; top: number; width: number; height: number };
function plot(grid: Settings, width: number, height: number): Plot {
  const position = (value: unknown, dimension: number, fallback: number) => typeof value === 'string' && value.endsWith('%') ? Number.parseFloat(value) * dimension / 100 : numeric(value, fallback);
  const left = position(grid.left, width, 0), top = position(grid.top, height, 0);
  return { left, top, width: position(grid.width, width, width - left - position(grid.right, width, 0)), height: position(grid.height, height, height - top - position(grid.bottom, height, 0)) };
}
function remapGraphics(graphics: unknown, before: Plot[], after: Plot[]) {
  for (const node of list(graphics)) {
    // Total labels use the bar grid; explicit missing-growth labels use the top grid.
    const index = /^(?:missing-|permit-growth-)/.test(String(node.id ?? '')) ? 0 : before.length - 1;
    const source = before[index], target = after[index];
    if (source && target && typeof node.x === 'number') node.x = target.left + (node.x - source.left) / Math.max(1, source.width) * target.width;
    if (source && target && typeof node.y === 'number') node.y = source.height > 0 ? target.top + (node.y - source.top) / source.height * target.height : target.top;
  }
}
const fontSize = (style: Settings, fallback: number) => typeof style.font === 'string' ? Number(style.font.match(/(\d+(?:\.\d+)?)px/)?.[1] ?? fallback) : numeric(style.fontSize, fallback);
function ringLabels(option: Settings, width: number, height: number, scale: number) {
  if (!list(option.series).some(series => series.type === 'pie')) return;
  const labels = list(option.graphic).filter(node => node.type === 'text' && node.left === 'center');
  const main = labels.find(node => /\b600\b/.test(String(record(node.style).font))), unit = labels.find(node => node !== main);
  if (!main || !unit) return;
  const mainStyle = record(main.style), unitStyle = record(unit.style), dimension = Math.min(width, height);
  const size = Math.round(Math.min(42 * scale, dimension * .13, dimension * .6 / Math.max(1, String(mainStyle.text).length * .62)));
  const unitSize = fontSize(unitStyle, 12 * scale), gap = Math.max(8, 6 * scale);
  const top = Math.round((height - size * 1.25 - unitSize * 1.3 - gap) / 2);
  mainStyle.font = String(mainStyle.font).replace(/\d+(?:\.\d+)?px/, `${size}px`);
  main.top = top;
  unit.top = Math.round(top + size * 1.25 + gap);
}
function nativeTotals(option: Settings, scale: number, hidden: readonly string[]) {
  const series = list(option.series), axes = list(option.xAxis), graphics = list(option.graphic);
  const totals = graphics.filter(node => /^(?:permit|mortgage)-total-/.test(String(node.id)));
  if (!totals.length) return;
  const selected = record(record(option.legend).selected);
  const bars = series.filter(item => item.type === 'bar' && selected[String(item.name)] !== false && !hidden.includes(String(item.name))), anchor = bars.at(-1);
  option.graphic = graphics.filter(node => !totals.includes(node));
  if (!anchor) return;
  const axis = axes[numeric(anchor.xAxisIndex, 0)], categories = Array.isArray(axis?.data) ? axis.data : [];
  const mark = record(anchor.markPoint), points = Array.isArray(mark.data) ? [...mark.data] : [];
  for (const node of totals) {
    const year = String(node.id).split('-').at(-1), index = categories.findIndex(value => String(value).split('\n')[0] === year);
    if (index < 0) continue;
    const style = record(list(node.children)[0]?.style), values = bars.map(item => Array.isArray(item.data) ? item.data[index] : null);
    const value = values.reduce<number>((sum, item) => sum + (typeof item === 'number' ? Math.max(0, item) : 0), 0);
    points.push({ coord: [index, value], value, label: { show: true, position: 'top', distance: Math.round(8 * Math.sqrt(scale)), color: style.fill,
      fontSize: fontSize(style, 11 * scale), fontWeight: 600, rotate: numeric(node.rotation, 0) * 180 / Math.PI,
      align: style.align ?? 'center', verticalAlign: style.verticalAlign ?? 'bottom', formatter: () => String(style.text ?? '') } });
  }
  // Native coordinates stay attached to bars even in a short or resized canvas.
  anchor.markPoint = { ...mark, silent: true, symbolSize: 0, data: points };
}

export function demoPresentationOption(option: EChartsCoreOption, width: number, height: number, hidden: readonly string[] = []): EChartsCoreOption {
  const result = copy(option) as Settings, scale = demoPresentationScale(width, height);
  scaleText(result, scale);
  result.textStyle = { ...record(result.textStyle), fontSize: Math.round(12 * scale) };
  const grids = list(result.grid), before = grids.map(grid => plot(grid, width, height));
  for (const grid of grids) {
    for (const key of ['left', 'right', 'top', 'bottom', 'height']) if (typeof grid[key] === 'number') {
      const factor = grids.length > 1 && (key === 'top' || key === 'height') ? Math.sqrt(scale) : scale;
      grid[key] = Math.round(grid[key] * factor);
    }
  }
  const main = grids.at(-1);
  if (main && plot(main, width, height).height < 64) {
    main.bottom = Math.min(numeric(main.bottom, 0), height * .25);
    main.top = Math.max(12, height - numeric(main.bottom, 0) - 64);
    delete main.height;
    for (const upper of grids.slice(0, -1)) {
      upper.top = Math.min(numeric(upper.top, 20), Math.max(12, numeric(main.top, 0) * .3));
      upper.height = Math.max(8, numeric(main.top, 0) - numeric(upper.top, 0) - 24);
    }
  }
  const after = grids.map(grid => plot(grid, width, height));
  remapGraphics(result.graphic, before, after);
  ringLabels(result, width, height, scale);
  nativeTotals(result, scale, hidden);
  for (const axis of [...list(result.xAxis), ...list(result.yAxis)]) {
    axis.axisLabel = { fontSize: Math.round(11 * scale), ...record(axis.axisLabel) };
    axis.nameTextStyle = { fontSize: Math.round(11 * scale), ...record(axis.nameTextStyle) };
    axis.nameGap = numeric(axis.nameGap, 15) * scale;
    if (grids.length > 1 && numeric(axis.gridIndex, 0) === 0 && axis.name === '%') axis.name = '';
    const label = record(axis.axisLabel);
    if (typeof label.margin === 'number') label.margin *= scale;
    if (typeof label.width === 'number') label.width *= scale;
  }
  const series = list(result.series), xAxes = list(result.xAxis), yAxes = list(result.yAxis);
  for (const item of series) {
    if (item.type === 'line') {
      item.lineStyle = { ...record(item.lineStyle), width: numeric(record(item.lineStyle).width, 2) * Math.sqrt(scale) };
      if (typeof item.symbolSize === 'number') item.symbolSize *= Math.sqrt(scale);
    }
    if (item.type !== 'bar') continue;
    const xIndex = numeric(item.xAxisIndex, 0), yIndex = numeric(item.yAxisIndex, 0), horizontal = yAxes[yIndex]?.type === 'category';
    const axis = horizontal ? yAxes[yIndex] : xAxes[xIndex], gridIndex = numeric(axis?.gridIndex, 0), area = after[gridIndex];
    if (!area || !axis || !Array.isArray(axis.data) || !axis.data.length) continue;
    const neighbors = series.filter(other => other.type === 'bar' && (horizontal ? numeric(other.yAxisIndex, 0) === yIndex : numeric(other.xAxisIndex, 0) === xIndex));
    const overlay = neighbors.some(other => other.barGap === '-100%');
    const groups = overlay ? 1 : new Set(neighbors.map((other, index) => other.stack ? `${horizontal ? numeric(other.xAxisIndex, 0) : numeric(other.yAxisIndex, 0)}:${other.stack}` : `series-${index}`)).size;
    const band = (horizontal ? area.height : area.width) / axis.data.length;
    const barWidth = Math.max(1, Math.min(height * .22, band * .7 / (groups + Math.max(0, groups - 1) * .15)));
    item.barWidth = barWidth;
    item.barMaxWidth = barWidth;
    item.barCategoryGap = '30%';
    item.barGap ??= '15%';
  }
  const tooltip = record(result.tooltip);
  if (Object.keys(tooltip).length) {
    tooltip.textStyle = { ...record(tooltip.textStyle), fontSize: Math.round(12 * scale) };
    // Provider builders may specify their ordinary-page tooltip font through inline CSS.
    if (typeof tooltip.extraCssText === 'string') tooltip.extraCssText = tooltip.extraCssText.replace(/font-size:\s*\d+(?:\.\d+)?px;?/g, '');
  }
  return result as EChartsCoreOption;
}
