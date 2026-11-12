import { format as echartsFormat, type ECharts, type EChartsCoreOption } from 'echarts/core';
import { chartNumberFormatter } from './format.ts';

type Settings = Record<string, unknown>;
const unlabelledLineCharts = new Set(['domrf/construction', 'sales/monthly_percent', 'ipc/ipc_month', 'salary/salary_month', 'mortgage/rates']);
const record = (value: unknown): Settings => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Settings : {};
const list = (value: unknown): Settings[] => (Array.isArray(value) ? value : value ? [value] : []).map(record);
const numeric = (value: unknown, fallback: number) => typeof value === 'number' && Number.isFinite(value) ? value : fallback;

// Discrete display tiers keep TV text readable without oversized labels on short windows.
export function demoPresentationScale(width: number, height: number): number {
  if (width >= 3000 && height >= 1100) return 3.2;
  if (width >= 2200 && height >= 850) return 2.6;
  if (width >= 1500 && height >= 500) return 2;
  if (width >= 1000 && height >= 400) return 1.5;
  if (width >= 1400 && height >= 270) return 1.5;
  if (width >= 1000 && height >= 270) return 1.25;
  if (width >= 700 && height >= 300) return 1.25;
  if (width >= 480 && width < 1000 && height >= 160 && height < 300) return 1.25;
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
type CartesianSeries = {
  item: Settings; index: number; x: number; y: number; grid: number; horizontal: boolean;
  axis: Settings; valueAxis: Settings; values: (number | null)[]; categories: number[];
};
type BarLabel = { series: CartesianSeries; index: number; value: number };
function visibleSeries(option: Settings, hidden: readonly string[]) {
  const selections = list(option.legend).map(legend => record(legend.selected));
  return (item: Settings) => !hidden.includes(String(item.name)) && selections.every(selected => selected[String(item.name)] !== false);
}
function cartesianSeries(option: Settings): CartesianSeries[] {
  const xs = list(option.xAxis), ys = list(option.yAxis);
  return list(option.series).flatMap((item, index) => {
    if (!['bar', 'line'].includes(String(item.type)) || item.coordinateSystem && item.coordinateSystem !== 'cartesian2d' || !Array.isArray(item.data)) return [];
    const x = numeric(item.xAxisIndex, 0), y = numeric(item.yAxisIndex, 0), horizontal = ys[y]?.type === 'category';
    const axis = (horizontal ? ys[y] : xs[x]), valueAxis = horizontal ? xs[x] : ys[y];
    if (!axis || !valueAxis || !Array.isArray(axis.data) || !axis.data.length || numeric(axis.gridIndex, 0) !== numeric(valueAxis.gridIndex, 0)) return [];
    const categories = axis.data;
    const values: (number | null)[] = categories.map(() => null), sourceCategories: number[] = [], encode = record(item.encode);
    const dimension = (key: string, fallback: number) => {
      const encoded = Array.isArray(encode[key]) ? encode[key][0] : encode[key];
      return typeof encoded === 'string' && Array.isArray(item.dimensions) ? item.dimensions.findIndex(d => d === encoded || record(d).name === encoded) : numeric(encoded, fallback);
    };
    item.data.forEach((point, dataIndex) => {
      const value = point !== null && typeof point === 'object' && !Array.isArray(point) ? record(point).value : point;
      let category = dataIndex, measure = value;
      if (Array.isArray(value)) {
        measure = value[dimension(horizontal ? 'x' : 'y', value.length === 1 || horizontal ? 0 : 1)];
        if (value.length > 1) {
          const key = value[dimension(horizontal ? 'y' : 'x', horizontal ? 1 : 0)];
          const found = categories.findIndex((entry: unknown) => (record(entry).value ?? entry) === key);
          category = found >= 0 ? found : typeof key === 'number' && Number.isInteger(key) ? key : -1;
        }
      }
      sourceCategories.push(category);
      if (category >= 0 && category < values.length) values[category] = typeof measure === 'number' && Number.isFinite(measure) ? measure : null;
    });
    return [{ item, index, x, y, grid: numeric(axis.gridIndex, 0), horizontal, axis, valueAxis, values, categories: sourceCategories }];
  });
}
function barGroups(series: CartesianSeries[]) {
  const groups = new Map<string, CartesianSeries[]>();
  for (const entry of series.filter(s => s.item.type === 'bar')) {
    const key = `${entry.x}:${entry.y}:${entry.item.stack ? `stack:${entry.item.stack}` : `series:${entry.index}`}`;
    const group = groups.get(key) ?? []; group.push(entry); groups.set(key, group);
  }
  return [...groups.values()];
}
function barLabels(group: CartesianSeries[]): BarLabel[] {
  return group[0].values.flatMap((_, index) => {
    if (!group.some(series => series.values[index] !== null && series.values[index] !== 0)) {
      const zero = group.find(series => series.values[index] === 0);
      return zero ? [{ series: zero, index, value: 0 }] : [];
    }
    // Follow ECharts' stack strategies; nulls create no segment, not a zero segment.
    const ends: (BarLabel & { used: boolean })[] = [];
    for (const series of group) {
      const raw = series.values[index];
      // ECharts does not position zero-height segments at the accumulated stack top.
      if (raw === null || raw === 0) continue;
      const strategy = series.item.stackStrategy ?? 'samesign';
      const previous = series.item.stack ? [...ends].reverse().find(end => strategy === 'all' || strategy === 'positive' && end.value > 0 || strategy === 'negative' && end.value < 0 || strategy === 'samesign' && (raw >= 0 && end.value > 0 || raw <= 0 && end.value < 0)) : undefined;
      if (previous) previous.used = true;
      ends.push({ series, index, value: raw + (previous?.value ?? 0), used: false });
    }
    return ends.filter(end => !end.used);
  });
}
function nativeTotals(option: Settings, scale: number, groups: CartesianSeries[][]) {
  const graphics = list(option.graphic);
  const totals = graphics.filter(node => /^(?:permit|mortgage)-total-/.test(String(node.id)));
  if (!totals.length) return;
  option.graphic = graphics.filter(node => !totals.includes(node));
  for (const node of totals) {
    const year = String(node.id).split('-').at(-1);
    const group = [...groups].reverse().find(group => (group[0].axis.data as unknown[]).some(value => String(record(value).value ?? value).split('\n')[0] === year));
    if (!group) continue;
    const index = (group[0].axis.data as unknown[]).findIndex(value => String(record(value).value ?? value).split('\n')[0] === year);
    const end = barLabels(group).filter(end => end.index === index).sort((a, b) => b.value - a.value)[0];
    if (!end) continue;
    const anchor = end.series.item, mark = record(anchor.markPoint), points = Array.isArray(mark.data) ? [...mark.data] : [];
    const style = record(list(node.children)[0]?.style), value = end.value;
    points.push({ name: `demo-native-total-${index}`, coord: end.series.horizontal ? [value, index] : [index, value], value,
      symbolSize: 0, label: { show: true, position: end.series.horizontal ? 'right' : value < 0 ? 'bottom' : 'top', distance: Math.round(8 * Math.sqrt(scale)), color: style.fill,
        fontSize: fontSize(style, 11 * scale), fontWeight: 600, rotate: 0, formatter: () => String(style.text ?? '') } });
    // Native coordinates stay attached to bars even in a short or resized canvas.
    anchor.markPoint = { ...mark, silent: true, symbolSize: 0, data: points };
  }
}

function labelScale(area: Plot, width: number, height: number, scale: number) {
  const floor = width >= 1000 && height >= 270 ? 1.25 : width >= 480 && width < 1000 && height >= 160 && height < 300 ? 1.1 : 1;
  return Math.min(scale, Math.max(floor, demoPresentationScale(width, Math.min(height, area.height * 2))));
}
function sparseIndices(values: Map<number, number>, count: number, space: number, required: number) {
  const step = Math.max(1, Math.ceil(required * count / Math.max(1, space))), selected = new Set<number>();
  let last = Infinity;
  for (const index of [...values.keys()].sort((a, b) => b - a)) if (last - index >= step) { selected.add(index); last = index; }
  return selected;
}
function demoLabels(option: Settings, areas: Plot[], width: number, height: number, scale: number, hidden: readonly string[], showLineValues: boolean) {
  const all = cartesianSeries(option), visible = visibleSeries(option, hidden), shown = all.filter(s => visible(s.item));
  const groups = barGroups(shown), ends = groups.flatMap(barLabels);
  const format = chartNumberFormatter([...shown.flatMap(s => s.values), ...ends.map(end => end.value)]);
  const originalEnds = barGroups(all).flatMap(barLabels);
  nativeTotals(option, scale, groups);
  for (const entry of all) {
    const { item } = entry;
    if (item.type !== 'bar') continue;
    const area = areas[entry.grid];
    if (!area) continue;
    const size = Math.round(11 * labelScale(area, width, height, scale));
    const own = ends.filter(end => end.series === entry), labels = new Map(own.map(end => [end.index, end.value]));
    const maxLength = Math.max(1, ...own.map(end => format(end.value).length));
    const neighbors = groups.filter(group => group[0].horizontal === entry.horizontal && (entry.horizontal ? group[0].y === entry.y : group[0].x === entry.x));
    const overlay = neighbors.some(group => group.some(s => s.item.barGap === '-100%'));
    const slots = overlay ? 1 : neighbors.length;
    const indices = sparseIndices(labels, entry.values.length, entry.horizontal ? area.height : area.width, (entry.horizontal ? size * 1.5 : maxLength * size * .65 + 12) * slots);
    const mark = record(item.markPoint);
    if (Array.isArray(mark.data)) {
      mark.data = mark.data.filter(point => {
        const p = record(point), coord = p.coord;
        if (!Array.isArray(coord) || coord.length !== 2) return true;
        const category = coord[entry.horizontal ? 1 : 0], value = coord[entry.horizontal ? 0 : 1];
        const index = typeof category === 'number' ? category : (entry.axis.data as unknown[]).findIndex(c => (record(c).value ?? c) === category);
        const native = String(p.name).startsWith('demo-native-total-');
        const matches = (end: BarLabel) => end.series.x === entry.x && end.series.y === entry.y && (item.stack ? end.series.item.stack === item.stack : end.series.index === entry.index) && end.index === index && Math.abs(end.value - Number(value)) < 1e-9;
        if (native) {
          if (!indices.has(Number(index))) return false;
          labels.delete(Number(index));
          const label = record(p.label), sourceText = typeof label.formatter === 'function' ? String(label.formatter()) : '';
          // Preserve non-numeric provider captions, but recompute numeric totals after hiding series.
          if (/^[\d\s.,+\u2212-]+$/.test(sourceText)) label.formatter = () => format(Number(value));
          label.fontSize = size;
          return true;
        }
        // Provider totals become the single outside label; retain unrelated event markers.
        return !((p.symbolSize ?? mark.symbolSize) === 0 && !p.type && [...ends, ...originalEnds].some(matches));
      });
    }
    item.label = { ...record(item.label), show: visible(item), position: 'outside', rotate: 0, align: undefined, verticalAlign: undefined,
      distance: Math.round(6 * Math.sqrt(scale)), fontSize: size, fontWeight: 600, textBorderWidth: 0,
      color: record(entry.valueAxis.axisLabel).color ?? record(option.textStyle).color ?? '#77838c',
      formatter: (point: { dataIndex: number }) => {
        const index = entry.categories[point.dataIndex];
        return visible(item) && indices.has(index) && labels.has(index) ? format(labels.get(index)) : '';
      } };
    item.labelLayout = (point: { text?: string; dataIndex?: number; rect?: { y: number }; labelRect: { x: number; y: number; width: number; height: number }; align?: string; verticalAlign?: string }) => {
      // Empty component labels must not reserve space and hide the visible total.
      if (!point.text) return { hideOverlap: false };
      const rect = point.labelRect;
      const zero = !entry.horizontal && point.dataIndex !== undefined && entry.values[entry.categories[point.dataIndex]] === 0;
      const top = zero && point.rect ? point.rect.y - numeric(record(item.label).distance, 6) - rect.height : rect.y;
      return { hideOverlap: true,
        x: Math.max(4, Math.min(rect.x, width - rect.width - 4)) + (point.align === 'center' ? rect.width / 2 : point.align === 'right' ? rect.width : 0),
        y: Math.max(4, Math.min(top, height - rect.height - 4)) + (point.verticalAlign === 'middle' ? rect.height / 2 : point.verticalAlign === 'bottom' ? rect.height : 0) };
    };
  }
  if (showLineValues) lineLabels(shown.filter(s => s.item.type === 'line'), areas, width, height, scale, format);
  for (const entry of all) {
    if (entry.item.type === 'line' && (!showLineValues || !visible(entry.item))) {
      entry.item.label = { ...record(entry.item.label), show: false };
      if (!showLineValues && entry.item.markPoint) {
        const mark = record(entry.item.markPoint);
        mark.label = { ...record(mark.label), show: false };
        mark.data = list(mark.data).filter(point => !generatedCaption(point)).map(point => ({ ...point, label: { ...record(point.label), show: false } }));
      }
    }
    entry.item.emphasis = { ...record(entry.item.emphasis), label: { ...record(record(entry.item.emphasis).label), show: false } };
  }
  // Responsive provider overrides must not bring back component labels in small demo panels.
  for (const media of list(option.media)) list(record(media.option).series).forEach((override, index) => {
    const entry = all.find(s => override.id !== undefined ? s.item.id === override.id : override.name !== undefined ? s.item.name === override.name : s.index === index);
    if (entry) { delete override.label; delete override.labelLayout; }
  });
}

function extrema(values: (number | null)[]) {
  const candidates: { index: number; value: number; low: boolean; score: number }[] = [];
  const observed = values.filter((v): v is number => v !== null), range = Math.max(...observed) - Math.min(...observed);
  if (!(range > 0)) return candidates;
  let start = 0;
  while (start < values.length) {
    if (values[start] === null) { start++; continue; }
    let end = start;
    while (end + 1 < values.length && values[end + 1] !== null) end++;
    const turns: { index: number; value: number }[] = [];
    for (let i = start; i <= end; i++) {
      let last = i;
      while (last < end && values[last + 1] === values[i]) last++;
      turns.push({ index: Math.floor((i + last) / 2), value: values[i]! }); i = last;
    }
    const minimum = Math.min(...turns.map(point => point.value)), maximum = Math.max(...turns.map(point => point.value));
    for (let i = 0; i < turns.length; i++) {
      const point = turns[i], before = turns[i - 1], after = turns[i + 1];
      const low = (!before || point.value < before.value) && (!after || point.value < after.value);
      const high = (!before || point.value > before.value) && (!after || point.value > after.value);
      if (turns.length < 2 || !low && !high) continue;
      if ((!before || !after) && point.value !== minimum && point.value !== maximum) continue;
      let left = point.value, right = point.value;
      for (let j = i - 1; j >= 0; j--) { left = low ? Math.max(left, turns[j].value) : Math.min(left, turns[j].value); if (low ? turns[j].value < point.value : turns[j].value > point.value) break; }
      for (let j = i + 1; j < turns.length; j++) { right = low ? Math.max(right, turns[j].value) : Math.min(right, turns[j].value); if (low ? turns[j].value < point.value : turns[j].value > point.value) break; }
      const prominence = !before ? Math.abs(point.value - right) : !after ? Math.abs(point.value - left) : Math.min(Math.abs(point.value - left), Math.abs(point.value - right));
      if (prominence >= range * .12) candidates.push({ ...point, low, score: prominence / range });
    }
    start = end + 1;
  }
  return candidates;
}
type LineCaption = { entry: CartesianSeries; mark: Settings; index: number; value: number; text: string; size: number; score: number; low: boolean };
type CaptionRect = { x: number; y: number; width: number; height: number; grid: number };
const generatedCaption = (point: Settings) => /^demo-(?:extremum|value)-/.test(String(point.name));
function placeLineCaptions(captions: LineCaption[], areas: Plot[], width: number, height: number,
  project: (caption: LineCaption) => [number, number] | undefined) {
  const placed: CaptionRect[] = [];
  const anchors = captions.map(caption => ({ caption, point: project(caption) }));
  anchors.sort((a, b) => b.caption.score - a.caption.score || a.caption.index - b.caption.index || a.caption.entry.index - b.caption.entry.index);
  for (const { caption, point } of anchors) {
    const { entry, mark, size, text } = caption, label = record(mark.label), area = areas[entry.grid];
    label.show = false;
    if (!area || !point?.every(Number.isFinite)) continue;
    const [x, y] = point, padding = Math.max(2, size * .18);
    const measured = echartsFormat.getTextRect(text, `600 ${size}px Segoe UI, Arial, sans-serif`);
    const w = measured.width + 2, h = Math.max(measured.height, size * 1.2);
    const left = Math.max(4, area.left), right = Math.min(width - 4, area.left + area.width);
    if (w > right - left) continue;
    const center = Math.max(left + w / 2, Math.min(right - w / 2, x));
    const gap = numeric(label.distance, 6), top = Math.max(4, area.top - size * 1.6), bottom = Math.min(height - 4, area.top + area.height - 2);
    for (const below of caption.low ? [true, false] : [false, true]) {
      const rect = { x: center - w / 2, y: below ? y + gap : y - gap - h, width: w, height: h, grid: entry.grid };
      if (rect.x < 2 || rect.x + w > width - 2 || rect.y < top || rect.y + h > bottom) continue;
      if (placed.some(other => other.grid === rect.grid && rect.x < other.x + other.width + padding && rect.x + w + padding > other.x && rect.y < other.y + other.height + padding && rect.y + h + padding > other.y)) continue;
      // A nearby point is also an obstacle: do not place a caption across another series.
      if (anchors.some(other => other.caption.entry.grid === entry.grid && other.point &&
        other.point[0] > rect.x - 2 && other.point[0] < rect.x + w + 2 && other.point[1] > rect.y - 2 && other.point[1] < rect.y + h + 2)) continue;
      label.show = true;
      label.position = below ? 'bottom' : 'top';
      label.align = 'center';
      label.offset = [center - x, 0];
      placed.push(rect);
      break;
    }
  }
}
function provisionalLineProjection(series: CartesianSeries[], areas: Plot[]) {
  return (caption: LineCaption): [number, number] | undefined => {
    const { entry, index, value } = caption, area = areas[entry.grid];
    if (!area) return;
    const values = series.filter(other => other.y === entry.y).flatMap(other => other.values).filter((v): v is number => v !== null);
    const minimum = Math.min(...values), maximum = Math.max(...values), axis = entry.valueAxis;
    const bound = (setting: unknown, fallback: number) => typeof setting === 'function' ? numeric(setting({ min: minimum, max: maximum }), fallback) :
      setting === 'dataMin' ? minimum : setting === 'dataMax' ? maximum : numeric(setting, fallback);
    let low = bound(axis.min, axis.scale ? minimum : Math.min(0, minimum)), high = bound(axis.max, axis.scale ? maximum : Math.max(0, maximum));
    if (low === high) { low -= Math.abs(low) * .1 || 1; high += Math.abs(high) * .1 || 1; }
    const band = entry.axis.boundaryGap !== false, count = entry.values.length;
    const fraction = band ? (index + .5) / count : index / Math.max(1, count - 1);
    const y = (value - low) / (high - low);
    return [area.left + (entry.axis.inverse ? 1 - fraction : fraction) * area.width, area.top + (axis.inverse ? y : 1 - y) * area.height];
  };
}
function lineLabels(series: CartesianSeries[], areas: Plot[], width: number, height: number, scale: number, format: ReturnType<typeof chartNumberFormatter>) {
  const candidates: LineCaption[] = series.flatMap(entry => {
    entry.item.label = { ...record(entry.item.label), show: false };
    // A stacked/step line's displayed turning geometry is not its raw measurement.
    if (entry.item.stack || entry.item.step || entry.horizontal || !areas[entry.grid]) return [];
    const area = areas[entry.grid], size = Math.round(11 * labelScale(area, width, height, scale));
    const turns = new Map(extrema(entry.values).map(point => [point.index, point]));
    const mark = record(entry.item.markPoint), data = list(mark.data).filter(point => !generatedCaption(point));
    const points = entry.values.flatMap((value, index) => {
      if (value === null) return [];
      const turn = turns.get(index), low = turn?.low ?? false;
      const provider = record(entry.item.label).formatter;
      const sourceIndex = entry.categories.indexOf(index);
      const caption = typeof provider === 'function' ? String(provider({ value, dataIndex: sourceIndex, seriesIndex: entry.index,
        seriesName: entry.item.name, name: (entry.axis.data as unknown[])[index] }) ?? '') : typeof provider === 'string' ? provider : '';
      const percent = /%/.test(`${caption} ${entry.valueAxis.name ?? ''} ${entry.item.name ?? ''}`);
      const text = `${value > 0 && caption.includes('+') ? '+' : ''}${format(value)}${percent ? '%' : ''}`;
      const score = turn ? 2 + turn.score : index === entry.values.length - 1 ? 1 : index === 0 ? .5 : 0;
      const point = { name: `demo-${turn ? 'extremum' : 'value'}-${entry.index}-${index}`, coord: [index, value], value, symbolSize: 0,
        demoPriority: score, demoBelow: low,
        label: { show: true, formatter: () => text, position: low ? 'bottom' : 'top', distance: Math.round(6 * Math.sqrt(scale)),
          fontFamily: 'Segoe UI, Arial, sans-serif', fontSize: size, fontWeight: 600, lineHeight: Math.ceil(size * 1.2), padding: 0,
          color: record(entry.item.lineStyle).color ?? record(entry.item.itemStyle).color ?? record(entry.valueAxis.axisLabel).color } };
      data.push(point);
      return [{ index, value, low, score, entry, mark: point, size, text }];
    });
    entry.item.markPoint = { ...mark, silent: true, data };
    return points;
  });
  placeLineCaptions(candidates, areas, width, height, provisionalLineProjection(series, areas));
}

// Reflow after ECharts resolves nice scales and containLabel, rather than guessing point pixels.
export function layoutDemoLineLabels(chart: ECharts) {
  const option = chart.getOption() as Settings, all = cartesianSeries(option), shown = visibleSeries(option, []);
  const width = chart.getWidth(), height = chart.getHeight(), areas = list(option.grid).map(grid => plot(grid, width, height));
  for (const entry of all) {
    if (entry.horizontal || entry.values.length < 2 || !areas[entry.grid]) continue;
    const first = chart.convertToPixel({ xAxisIndex: entry.x }, 0), last = chart.convertToPixel({ xAxisIndex: entry.x }, entry.values.length - 1);
    if (typeof first !== 'number' || typeof last !== 'number' || !Number.isFinite(first) || !Number.isFinite(last)) continue;
    const band = entry.axis.boundaryGap !== false ? Math.abs(last - first) / (entry.values.length - 1) / 2 : 0;
    areas[entry.grid] = { ...areas[entry.grid], left: Math.min(first, last) - band, width: Math.abs(last - first) + band * 2 };
  }
  const candidates = all.filter(entry => shown(entry.item)).flatMap(entry => list(record(entry.item.markPoint).data).flatMap(mark => {
    if (!generatedCaption(mark) || !Array.isArray(mark.coord)) return [];
    const label = record(mark.label), formatter = label.formatter;
    const text = typeof formatter === 'function' ? String(formatter()) : String(formatter ?? mark.value);
    return [{ entry, mark, index: Number(mark.coord[0]), value: Number(mark.coord[1]), size: numeric(label.fontSize, 11), text,
      score: numeric(mark.demoPriority, 0), low: Boolean(mark.demoBelow) }];
  }));
  if (!candidates.length) return;
  placeLineCaptions(candidates, areas, width, height, caption => {
    const point = chart.convertToPixel({ xAxisIndex: caption.entry.x, yAxisIndex: caption.entry.y }, [caption.index, caption.value]);
    return Array.isArray(point) ? point as [number, number] : undefined;
  });
  chart.setOption({ series: list(option.series).map(item => item.type === 'line' ? { markPoint: item.markPoint } : {}) }, { silent: true });
}

export function demoPresentationOption(option: EChartsCoreOption, width: number, height: number, hidden: readonly string[] = [], chartId = ''): EChartsCoreOption {
  const result = copy(option) as Settings, scale = demoPresentationScale(width, height);
  // Native labels must settle immediately when the viewer changes canvas size or screen.
  result.animation = false;
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
  for (const axis of [...list(result.xAxis), ...list(result.yAxis)]) {
    axis.axisLabel = { fontSize: Math.round(11 * scale), ...record(axis.axisLabel) };
    axis.nameTextStyle = { fontSize: Math.round(11 * scale), ...record(axis.nameTextStyle) };
    axis.nameGap = numeric(axis.nameGap, 15) * scale;
    const label = record(axis.axisLabel);
    if (typeof label.margin === 'number') label.margin *= scale;
    if (typeof label.width === 'number') label.width *= scale;
  }
  const series = list(result.series), xAxes = list(result.xAxis), yAxes = list(result.yAxis);
  if (chartId.startsWith('permits/') && width < 700 && height < 380) for (const axis of xAxes) {
    const label = record(axis.axisLabel);
    if (axis.type !== 'category' || !Array.isArray(axis.data) || axis.data.length < 13 || label.show === false) continue;
    axis.axisLabel = { ...label, interval: 0, hideOverlap: false, rotate: 90, margin: 6,
      fontSize: Math.max(10, Math.min(12, numeric(label.fontSize, 10))),
      formatter: (value: unknown) => String(record(value).value ?? value).split('\n')[0] };
  }
  for (const item of series) {
    if (item.type === 'line') {
      item.lineStyle = { ...record(item.lineStyle), width: numeric(record(item.lineStyle).width, 2) * Math.sqrt(scale) };
      if (typeof item.symbolSize === 'number') item.symbolSize *= Math.sqrt(scale);
    }
    if (item.type !== 'bar') continue;
    const xIndex = numeric(item.xAxisIndex, 0), yIndex = numeric(item.yAxisIndex, 0), horizontal = yAxes[yIndex]?.type === 'category';
    const axis = horizontal ? yAxes[yIndex] : xAxes[xIndex], gridIndex = numeric(axis?.gridIndex, 0), area = after[gridIndex];
    if (!area || !axis || !Array.isArray(axis.data) || !axis.data.length) continue;
    const neighbors = series.filter(other => other.type === 'bar' && visibleSeries(result, hidden)(other) && (horizontal ? numeric(other.yAxisIndex, 0) === yIndex : numeric(other.xAxisIndex, 0) === xIndex));
    const overlay = neighbors.some(other => other.barGap === '-100%');
    const groups = overlay ? 1 : new Set(neighbors.map((other, index) => other.stack ? `${horizontal ? numeric(other.xAxisIndex, 0) : numeric(other.yAxisIndex, 0)}:${other.stack}` : `series-${index}`)).size;
    const band = (horizontal ? area.height : area.width) / axis.data.length;
    const barWidth = Math.max(1, Math.min(height * .22, band * .7 / (groups + Math.max(0, groups - 1) * .15)));
    item.barWidth = barWidth;
    item.barMaxWidth = barWidth;
    item.barCategoryGap = '30%';
    item.barGap ??= '15%';
  }
  demoLabels(result, after, width, height, scale, hidden, !unlabelledLineCharts.has(chartId));
  // Keep the unit available while building captions, then avoid the duplicate upper-grid caption.
  for (const axis of [...xAxes, ...yAxes]) if (grids.length > 1 && numeric(axis.gridIndex, 0) === 0 && axis.name === '%') axis.name = '';
  const tooltip = record(result.tooltip);
  if (Object.keys(tooltip).length) {
    tooltip.textStyle = { ...record(tooltip.textStyle), fontSize: Math.round(12 * scale) };
    // Provider builders may specify their ordinary-page tooltip font through inline CSS.
    if (typeof tooltip.extraCssText === 'string') tooltip.extraCssText = tooltip.extraCssText.replace(/font-size:\s*\d+(?:\.\d+)?px;?/g, '');
  }
  return result as EChartsCoreOption;
}
