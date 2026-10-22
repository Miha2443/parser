import type { EChartsCoreOption } from 'echarts/core';
import { chartNumberFormatter } from './format.ts';
import type { Theme } from './types';

export const mortgageSourceUrl = 'https://наш.дом.рф/аналитика/ипотечное_кредитование';
export type MortgageRegion = 'msk' | 'rf';
export type MortgageSegment = 'all' | 'primary' | 'secondary';
export type MortgageMetric = 'volume' | 'count';
export type MortgageValues = { volumeRub: number | null; count: number | null; ratePct: number | null; termMonths: number | null; averageAmountRub: number | null };
export type MortgageMonth = { period: string; all: MortgageValues; primary: MortgageValues; secondary: MortgageValues };
export type MortgageAnnualValue = { annual: number | null; selected: number | null; actualThroughMonth: number | null; annualYoYPct: number | null; selectedYoYPct: number | null };
export type MortgageAnnual = { year: number; volume: MortgageAnnualValue; count: MortgageAnnualValue };
export type MortgageMetadata = { schemaVersion: 1; version: string; generatedAt: string; source: { url: string; date: string | null; files: string[]; collectedAt: string } };
export type MortgageCatalog = MortgageMetadata & { regions: { id: MortgageRegion; label: string; periods: string[] }[]; defaultRegion: MortgageRegion; months: { id: number; label: string }[] };
export type MortgageSelection = { region: MortgageRegion; month: number };
export type MortgageReport = MortgageMetadata & { selection: MortgageSelection; regionLabel: string; annual: MortgageAnnual[]; monthly: MortgageMonth[]; notes: string[] };
type Cell = string | number | null;
type UnknownRecord = Record<string, unknown>;
type TooltipPoint = { dataIndex: number; seriesIndex: number; seriesName: string; value: number | null; axisValue?: string; name?: string };

const fields = ['volumeRub', 'count', 'ratePct', 'termMonths', 'averageAmountRub'] as const;
export const mortgageSegments = [
  { id: 'all', label: 'В целом', color: '#92a9af' },
  { id: 'primary', label: 'Первичный рынок', color: '#285d70' },
  { id: 'secondary', label: 'Вторичный рынок', color: '#dd6268' },
] as const;
export const mortgageMonthNames = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
const shortMonths = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const record = (value: unknown): UnknownRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
const fail = (): never => { throw new Error('Ответ API ипотеки не соответствует схеме версии 1.'); };
const text = (value: unknown): string => typeof value === 'string' && value.trim() ? value : fail();
const stringList = (value: unknown): string[] => Array.isArray(value) && value.every(item => typeof item === 'string') ? [...value] : fail();
const array = (value: unknown): unknown[] => Array.isArray(value) ? value : fail();
const finite = (value: unknown, signed = false): number | null => value === null ? null : typeof value === 'number' && Number.isFinite(value) && (signed || value >= 0) ? value : fail();
const region = (value: unknown): MortgageRegion => value === 'msk' || value === 'rf' ? value : fail();
const integer = (value: unknown, min: number, max: number): number => typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max ? value : fail();
const period = (value: unknown): string => typeof value === 'string' && /^(19|20|21)\d{2}-(0[1-9]|1[0-2])$/.test(value) ? value : fail();
const iso = (value: unknown): string => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value)) ? value : fail();
function unique<T>(values: T[]): T[] { if (new Set(values).size !== values.length) fail(); return values; }
function sourceUrl(value: unknown): string {
  const url = new URL(text(value)), expected = new URL(mortgageSourceUrl);
  if (url.protocol !== 'https:' || url.hostname !== expected.hostname || decodeURIComponent(url.pathname).replace(/\/$/, '') !== decodeURIComponent(expected.pathname) || url.search || url.hash || url.username || url.password) fail();
  return String(value);
}
function metadata(value: unknown): MortgageMetadata {
  const raw = record(value), source = record(raw.source);
  if (raw.schemaVersion !== 1) fail();
  return { schemaVersion: 1, version: text(raw.version), generatedAt: iso(raw.generatedAt), source: { url: sourceUrl(source.url), date: source.date === null ? null : typeof source.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(source.date) && Number.isFinite(Date.parse(source.date)) ? source.date : fail(), files: stringList(source.files), collectedAt: iso(source.collectedAt) } };
}
export function readMortgageCatalog(value: unknown): MortgageCatalog {
  const raw = record(value), meta = metadata(raw);
  if (!Array.isArray(raw.regions) || !raw.regions.length || !Array.isArray(raw.months)) fail();
  const regions = array(raw.regions).map(value => { const row = record(value); return { id: region(row.id), label: text(row.label), periods: unique(array(row.periods).map(period)).sort() }; });
  unique(regions.map(row => row.id));
  const defaultRegion = region(raw.defaultRegion);
  if (!regions.some(row => row.id === defaultRegion)) fail();
  const months = array(raw.months).map(value => { const row = record(value); return { id: integer(row.id, 1, 12), label: text(row.label) }; }).sort((a, b) => a.id - b.id);
  if (months.length !== 12) fail(); unique(months.map(row => row.id));
  return { ...meta, regions, defaultRegion, months };
}
function readValues(value: unknown): MortgageValues {
  const raw = record(value);
  return Object.fromEntries(fields.map(field => [field, finite(raw[field])])) as MortgageValues;
}
function readAnnualValue(value: unknown, month: number): MortgageAnnualValue {
  const raw = record(value), annual = finite(raw.annual), selected = finite(raw.selected), actualThroughMonth = raw.actualThroughMonth === null ? null : integer(raw.actualThroughMonth, 1, month);
  const annualYoYPct = finite(raw.annualYoYPct, true), selectedYoYPct = finite(raw.selectedYoYPct, true);
  if (annual === null && annualYoYPct !== null || selected === null && selectedYoYPct !== null || selected !== null && actualThroughMonth === null || annual !== null && selected !== null && selected > annual + Math.max(1, annual) * 1e-9) fail();
  return { annual, selected, actualThroughMonth, annualYoYPct, selectedYoYPct };
}
export function readMortgageReport(value: unknown, expected: MortgageSelection): MortgageReport {
  const raw = record(value), meta = metadata(raw), selection = record(raw.selection);
  if (selection.region !== expected.region || selection.month !== expected.month || !Array.isArray(raw.annual) || !Array.isArray(raw.monthly)) fail();
  const annual = array(raw.annual).map(value => { const row = record(value); return { year: integer(row.year, 1900, 2199), volume: readAnnualValue(row.volume, expected.month), count: readAnnualValue(row.count, expected.month) }; }).sort((a, b) => a.year - b.year);
  unique(annual.map(row => row.year));
  const monthly = array(raw.monthly).map(value => { const row = record(value); return { period: period(row.period), all: readValues(row.all), primary: readValues(row.primary), secondary: readValues(row.secondary) }; }).sort((a, b) => a.period.localeCompare(b.period));
  unique(monthly.map(row => row.period));
  return { ...meta, selection: { ...expected }, regionLabel: text(raw.regionLabel), annual, monthly, notes: stringList(raw.notes) };
}
export function mortgageSelection(catalog: MortgageCatalog | null, params: URLSearchParams): MortgageSelection | null {
  if (!catalog) return null;
  const selected = catalog.regions.find(row => row.id === params.get('region'))?.id ?? catalog.defaultRegion;
  const month = Number(params.get('annualMonth'));
  return { region: selected, month: Number.isInteger(month) && month >= 1 && month <= 12 ? month : 12 };
}
export function mortgageUrl(selection: MortgageSelection, requiredVersion?: string): string {
  const params = new URLSearchParams({ region: selection.region, month: String(selection.month) });
  if (requiredVersion) params.set('required_version', requiredVersion);
  return `/api/v1/mortgage${requiredVersion ? '/export' : ''}?${params}`;
}
export function mortgagePeriodLabel(value: string): string {
  const [year, month] = value.split('-');
  return `${mortgageMonthNames[Number(month) - 1]} ${year}`;
}
export function mortgageCumulativeLabel(month: number | null): string { return month === null ? 'Нет данных' : month === 1 ? 'янв' : `янв–${shortMonths[month - 1]}`; }
export function mortgageRange(rows: MortgageMonth[], from: string | null, to: string | null) {
  const periods = rows.map(row => row.period).sort(), start = periods.includes(from ?? '') ? from! : periods[0] ?? '', requestedEnd = periods.includes(to ?? '') ? to! : periods.at(-1) ?? '';
  const end = requestedEnd >= start ? requestedEnd : start;
  return { from: start, to: end, rows: rows.filter(row => row.period >= start && row.period <= end).sort((a, b) => a.period.localeCompare(b.period)) };
}
export function mortgageMetricPeriods(rows: MortgageMonth[]): string[] {
  return rows.filter(row => row.all.termMonths !== null || row.all.averageAmountRub !== null).map(row => row.period).sort();
}
export function mortgageHistory(rows: MortgageMonth[], metric: MortgageMetric | 'rates'): MortgageMonth[] {
  const field = metric === 'volume' ? 'volumeRub' : metric === 'count' ? 'count' : 'ratePct', segments = metric === 'rates' ? mortgageSegments : mortgageSegments.filter(segment => segment.id !== 'all');
  const observed = rows.filter(row => segments.some(segment => row[segment.id][field] !== null)).map(row => row.period).sort();
  return observed.length ? rows.filter(row => row.period >= observed[0] && row.period <= observed.at(-1)!).sort((a, b) => a.period.localeCompare(b.period)) : [];
}
function calendarPeriods(rows: MortgageMonth[]): string[] {
  if (!rows.length) return [];
  const ordered = [...rows].sort((a, b) => a.period.localeCompare(b.period)), result: string[] = [];
  let [year, month] = ordered[0].period.split('-').map(Number);
  const last = ordered.at(-1)!.period;
  while (`${year}-${String(month).padStart(2, '0')}` <= last) { result.push(`${year}-${String(month).padStart(2, '0')}`); month++; if (month === 13) { month = 1; year++; } }
  return result;
}
const escape = (value: string) => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const tones = (theme: Theme) => ({ text: theme === 'light' ? '#283b45' : '#eef2f4', muted: theme === 'light' ? '#78858e' : '#b1bbc2', line: theme === 'light' ? '#e9edf0' : '#38424a', gray: theme === 'light' ? '#d4dfe3' : '#65737d' });
const scale = (metric: MortgageMetric) => metric === 'volume' ? 1e9 : 1000;
export const mortgageMetricUnit = (metric: MortgageMetric) => metric === 'volume' ? 'млрд руб.' : 'тыс. шт.';

export function mortgageAnnualOption(rows: MortgageAnnual[], metric: MortgageMetric, theme: Theme, width = 700): EChartsCoreOption {
  const t = tones(theme), values = rows.map(row => row[metric]), divisor = scale(metric), unit = mortgageMetricUnit(metric);
  const measures = values.flatMap(row => [row.annual, row.selected].map(value => value === null ? null : value / divisor));
  const format = chartNumberFormatter([...measures, ...values.flatMap(row => [row.annualYoYPct, row.selectedYoYPct])]), pct = format;
  const slot = Math.max(0, width - 68) / Math.max(1, rows.length), showValues = slot >= 38, showGrowth = slot >= 48;
  const maximum = Math.max(1, ...measures.filter((value): value is number => value !== null));
  const annualLabel = (point: { dataIndex: number; value: number | null }) => {
    const row = values[point.dataIndex];
    return row.annual === null ? '' : format(row.annual / divisor);
  };
  return {
    animation: false,
    grid: [{ left: 52, right: 15, top: 22, height: 68 }, { left: 52, right: 15, top: 154, bottom: 51 }],
    tooltip: { trigger: 'item', confine: true, className: 'mortgage-tooltip', formatter: (point: TooltipPoint) => {
      const row = values[point.dataIndex], year = rows[point.dataIndex]?.year;
      if (!row || year === undefined) return '';
      if (point.seriesIndex === 2) return `<strong>${year} · Годовой прирост</strong><br/>${pct(row.annualYoYPct)} %`;
      const title = point.seriesIndex === 1 ? 'Годовое значение' : mortgageCumulativeLabel(row.actualThroughMonth);
      const value = point.seriesIndex === 1 ? row.annual : row.selected, growth = point.seriesIndex === 1 ? row.annualYoYPct : row.selectedYoYPct;
      return `<strong>${year} · ${escape(title)}</strong><br/>${format(value === null ? null : value / divisor)} ${unit}${growth === null ? '' : `<br/>К аналогичному периоду: ${growth > 0 ? '+' : ''}${pct(growth)} %`}`;
    } },
    xAxis: [{ type: 'category', gridIndex: 0, data: rows.map(row => String(row.year)), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { show: false } }, { type: 'category', gridIndex: 1, data: rows.map(row => String(row.year)), axisLine: { lineStyle: { color: t.line } }, axisTick: { show: false }, axisLabel: { color: t.muted, fontSize: 10, hideOverlap: true, interval: 'auto', lineHeight: 16, formatter: (year: string, index: number) => `${year}${values[index].annual === null && values[index].actualThroughMonth !== null ? `\n${mortgageCumulativeLabel(values[index].actualThroughMonth)}` : ''}` } }],
    yAxis: [{ type: 'value', gridIndex: 0, scale: true, min: (value: { min: number; max: number }) => value.min - Math.max(8, (value.max - value.min) * 0.35), max: (value: { min: number; max: number }) => value.max + Math.max(8, (value.max - value.min) * 0.35), axisLabel: { show: false }, splitLine: { show: false } }, { type: 'value', gridIndex: 1, name: unit, nameTextStyle: { color: t.muted, align: 'left', padding: [0, 0, 0, 0], fontSize: 10 }, axisLabel: { color: t.muted, fontSize: 10, formatter: format }, splitLine: { lineStyle: { color: t.line, type: 'dashed' } } }],
    series: [
      { name: 'Выбранный период', type: 'bar', stack: 'annual', xAxisIndex: 1, yAxisIndex: 1, barMaxWidth: 47, data: values.map(row => row.selected === null ? null : row.selected / divisor), itemStyle: { color: '#dc6068' }, emphasis: { focus: 'series' }, label: { show: showValues, position: 'insideTop', color: '#fff', fontSize: 11, distance: 7, formatter: (point: { dataIndex: number; value: number | null }) => { const row = values[point.dataIndex]; if (point.value === null || point.value / maximum * 180 < 22) return ''; const growth = row.selectedYoYPct; return `${format(point.value)}${showGrowth && growth !== null && point.value / maximum * 180 >= 49 ? `\n{${growth >= 0 ? 'up' : 'down'}|${growth > 0 ? '+' : ''}${pct(growth)}%}` : ''}`; }, rich: { up: { color: '#fff', backgroundColor: '#358b75', padding: [2, 3], fontSize: 9, lineHeight: 23 }, down: { color: '#fff', backgroundColor: '#565f68', padding: [2, 3], fontSize: 9, lineHeight: 23 } } } },
      { name: 'Годовые значения', type: 'bar', stack: 'annual', xAxisIndex: 1, yAxisIndex: 1, barMaxWidth: 47, data: values.map(row => row.annual === null || row.selected === null || row.annual === row.selected ? null : (row.annual - row.selected) / divisor), itemStyle: { color: t.gray }, label: { show: showValues, position: 'top', color: t.text, fontSize: 11, formatter: annualLabel }, emphasis: { focus: 'series' } },
      { name: 'Годовой прирост, %', type: 'line', xAxisIndex: 0, yAxisIndex: 0, connectNulls: false, data: values.map(row => row.annualYoYPct), itemStyle: { color: '#3b7787' }, lineStyle: { color: '#3b7787', type: 'dashed', width: 1.5 }, symbol: 'circle', symbolSize: 5, label: { show: slot >= 34, position: 'top', color: '#3b7787', fontSize: 10, formatter: (point: { value: number | null }) => point.value === null ? '' : `${point.value > 0 ? '+' : ''}${pct(point.value)}%` } },
    ],
  };
}
function monthlyAxes(periods: string[], theme: Theme, unit: string, format: (value: number | null | undefined) => string) {
  const t = tones(theme);
  return {
    grid: { left: 12, right: 15, top: 39, bottom: 17, containLabel: true },
    xAxis: { type: 'category', data: periods, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: t.muted, fontSize: 10, hideOverlap: true, interval: 'auto', formatter: (value: string) => `${shortMonths[Number(value.slice(5)) - 1]}\n${value.slice(0, 4)}`, lineHeight: 16 } },
    yAxis: { type: 'value', name: unit, nameTextStyle: { color: t.muted, fontSize: 10 }, axisLabel: { color: t.muted, fontSize: 10, formatter: format }, splitLine: { lineStyle: { color: t.line, type: 'dashed' } } },
  };
}
export function mortgageMonthlyOption(rows: MortgageMonth[], metric: MortgageMetric, theme: Theme): EChartsCoreOption {
  const periods = calendarPeriods(rows), map = new Map(rows.map(row => [row.period, row])), field = metric === 'volume' ? 'volumeRub' : 'count', divisor = scale(metric), unit = mortgageMetricUnit(metric);
  const segments = mortgageSegments.filter(segment => segment.id !== 'all');
  const format = chartNumberFormatter(rows.flatMap(row => [...segments.map(segment => row[segment.id][field] === null ? null : row[segment.id][field]! / divisor), row.primary[field] === null || row.secondary[field] === null ? null : (row.primary[field]! + row.secondary[field]!) / divisor]));
  return {
    ...monthlyAxes(periods, theme, unit, format), animation: false,
    tooltip: { trigger: 'item', confine: true, className: 'mortgage-tooltip', formatter: (point: TooltipPoint) => {
      const selected = periods[point.dataIndex], segment = segments[point.seriesIndex], value = map.get(selected)?.[segment?.id]?.[field] ?? null;
      return `<strong>${escape(mortgagePeriodLabel(selected))}</strong><br/>${segment?.label ?? ''}: ${format(value === null ? null : value / divisor)} ${unit}`;
    } },
    series: segments.map(segment => ({ name: segment.label, type: 'bar', stack: 'market', barMaxWidth: 34, data: periods.map(period => { const value = map.get(period)?.[segment.id][field] ?? null; return value === null ? null : value / divisor; }), itemStyle: { color: segment.color }, label: { show: false }, emphasis: { focus: 'series', label: { show: false } } })),
  };
}
export function mortgageRatesOption(rows: MortgageMonth[], theme: Theme): EChartsCoreOption {
  const periods = calendarPeriods(rows), map = new Map(rows.map(row => [row.period, row]));
  const format = chartNumberFormatter(rows.flatMap(row => mortgageSegments.map(segment => row[segment.id].ratePct))), axes = monthlyAxes(periods, theme, '% годовых', format);
  return {
    ...axes, animation: false,
    yAxis: { ...axes.yAxis, scale: true, min: (value: { min: number; max: number }) => Math.max(0, Math.floor(value.min - Math.max(0.5, (value.max - value.min) * 0.12) || 0)), max: (value: { min: number; max: number }) => Math.ceil(value.max + Math.max(0.5, (value.max - value.min) * 0.12) || 1) },
    tooltip: { trigger: 'axis', confine: true, className: 'mortgage-tooltip', formatter: (input: TooltipPoint | TooltipPoint[]) => {
      const points = Array.isArray(input) ? input : [input], selected = periods[points[0]?.dataIndex], row = map.get(selected);
      if (!selected) return '';
      return `<strong>${escape(mortgagePeriodLabel(selected))}</strong>${mortgageSegments.map(segment => `<br/><span style="color:${segment.color}">●</span> ${segment.label}: ${format(row?.[segment.id].ratePct ?? null)} %`).join('')}`;
    } },
    series: mortgageSegments.map(segment => ({ name: segment.label, type: 'line', connectNulls: false, showSymbol: periods.length <= 42, symbolSize: 4, data: periods.map(period => map.get(period)?.[segment.id].ratePct ?? null), itemStyle: { color: segment.color }, lineStyle: { width: segment.id === 'all' ? 2 : 2.5, color: segment.color, type: segment.id === 'all' ? 'dashed' : 'solid' }, label: { show: false }, emphasis: { focus: 'series' } })),
  };
}
export function mortgageAnnualRows(rows: MortgageAnnual[], metric: MortgageMetric): Cell[][] {
  const unit = metric === 'volume' ? 'руб.' : 'шт.';
  return [['Год', 'Период', `За выбранный период, ${unit}`, `Годовые значения, ${unit}`, 'Прирост выбранного периода, %', 'Годовой прирост, %'], ...rows.map(row => [row.year, mortgageCumulativeLabel(row[metric].actualThroughMonth), row[metric].selected, row[metric].annual, row[metric].selectedYoYPct, row[metric].annualYoYPct])];
}
export function mortgageMonthlyRows(rows: MortgageMonth[], metric: MortgageMetric | 'rates'): Cell[][] {
  const field = metric === 'volume' ? 'volumeRub' : metric === 'count' ? 'count' : 'ratePct', unit = metric === 'volume' ? 'руб.' : metric === 'count' ? 'шт.' : '%';
  const segments = metric === 'rates' ? mortgageSegments : mortgageSegments.filter(segment => segment.id !== 'all');
  return [['Месяц', ...segments.map(segment => `${segment.label}, ${unit}`)], ...rows.map(row => [row.period, ...segments.map(segment => row[segment.id][field])])];
}
