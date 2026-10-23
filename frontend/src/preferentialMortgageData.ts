import type { EChartsCoreOption } from 'echarts/core';
import { chartNumberFormatter } from './format.ts';
import type { Theme } from './types';

export const preferentialSourceUrl = 'https://дом.рф/programmy-gosudarstvennoj-podderzhki/operational-reporting/';
export const programs = [
  { id: 'preferential', label: 'Льготная ипотека', color: '#dc6068' },
  { id: 'family', label: 'Семейная ипотека', color: '#8baeb7' },
  { id: 'it', label: 'ИТ-ипотека', color: '#285d70' },
] as const;
export type Program = typeof programs[number]['id'];
export type Region = 'msk' | 'rf';
export type FlowMetric = 'volumeMln' | 'count';
export type FlowValues = { volumeMln: number | null; count: number | null };
export type Characteristic = 'averageAmountMln' | 'ratePct' | 'termMonths' | 'ownFundsPct' | 'areaM2' | 'priceThousandM2';
export const characteristics: { id: Characteristic; label: string; unit: string }[] = [
  { id: 'averageAmountMln', label: 'Средняя сумма кредита', unit: 'млн руб.' },
  { id: 'ratePct', label: 'Средневзвешенная ставка', unit: '%' },
  { id: 'termMonths', label: 'Средний срок кредита', unit: 'мес.' },
  { id: 'ownFundsPct', label: 'Собственные средства заёмщика', unit: '%' },
  { id: 'areaM2', label: 'Средняя площадь', unit: 'м²' },
  { id: 'priceThousandM2', label: 'Средняя стоимость 1 м²', unit: 'тыс. руб.' },
];
export type FlowYear = { year: number; throughMonth: number; programs: Record<Program, FlowValues>; total: FlowValues };
type CharacteristicMonths = Record<Characteristic, number | null>;
export type CharacteristicYear = { year: number; throughMonth: number; throughMonths: CharacteristicMonths; programThroughMonths: Record<Program, CharacteristicMonths>; programs: Record<Program, Record<Characteristic, number | null>> };
type Metadata = { schemaVersion: 1; version: string; generatedAt: string; source: { url: string; date: string | null; files: string[]; collectedAt: string } };
export type PreferentialCatalog = Metadata & { regions: { id: Region; label: string; periods: string[]; characteristicPeriods?: string[] }[]; defaultRegion: Region; months: { id: number; label: string }[] };
export type PreferentialSelection = { region: Region; month: number; characteristicMonth: number };
export type PreferentialReport = Metadata & { selection: PreferentialSelection; regionLabel: string; latestPeriod: string; latestPeriodPartial: boolean | null; annual: FlowYear[]; cumulative: FlowYear[]; characteristicsAnnual: CharacteristicYear[]; characteristicsCumulative: CharacteristicYear[]; notes: string[] };
type Row = Record<string, unknown>;
const fail = (): never => { throw new Error('Ответ API льготной ипотеки не соответствует схеме версии 1.'); };
const object = (value: unknown): Row => value && typeof value === 'object' && !Array.isArray(value) ? value as Row : fail();
const text = (value: unknown): string => typeof value === 'string' && value.trim() ? value : fail();
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : fail();
const unique = <T,>(values: T[]) => new Set(values).size === values.length ? values : fail();
const finite = (value: unknown): number | null => value === null ? null : typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : fail();
const integer = (value: unknown, min: number, max: number): number => typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max ? value : fail();
const regionId = (value: unknown): Region => value === 'msk' || value === 'rf' ? value : fail();
const period = (value: unknown): string => typeof value === 'string' && /^[12]\d{3}-(0[1-9]|1[0-2])$/.test(value) ? value : fail();
const timestamp = (value: unknown): string => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value)) ? value : fail();
function metadata(value: unknown): Metadata {
  const raw = object(value), source = object(raw.source), url = new URL(text(source.url)), expected = new URL(preferentialSourceUrl);
  if (raw.schemaVersion !== 1 || url.protocol !== 'https:' || url.hostname !== expected.hostname || url.pathname.replace(/\/$/, '') !== expected.pathname.replace(/\/$/, '') || url.search || url.hash || url.username || url.password) fail();
  const sourceDate = source.date === null ? null : text(source.date);
  if (sourceDate !== null && (!/^\d{4}-\d{2}-\d{2}$/.test(sourceDate) || !Number.isFinite(Date.parse(sourceDate)))) fail();
  return { schemaVersion: 1, version: text(raw.version), generatedAt: timestamp(raw.generatedAt), source: { url: source.url as string, date: sourceDate, files: list(source.files).map(text), collectedAt: timestamp(source.collectedAt) } };
}
export function readPreferentialCatalog(value: unknown): PreferentialCatalog {
  const raw = object(value), meta = metadata(raw);
  const regions = list(raw.regions).map(value => { const row = object(value); return { id: regionId(row.id), label: text(row.label), periods: unique(list(row.periods).map(period)).sort(), ...(row.characteristicPeriods === undefined ? {} : { characteristicPeriods: unique(list(row.characteristicPeriods).map(period)).sort() }) }; });
  unique(regions.map(row => row.id));
  const defaultRegion = regionId(raw.defaultRegion);
  if (!regions.length || regions.some(row => !row.periods.length) || !regions.some(row => row.id === defaultRegion)) fail();
  const months = list(raw.months).map(value => { const row = object(value); return { id: integer(row.id, 1, 12), label: text(row.label) }; }).sort((a, b) => a.id - b.id);
  if (months.length !== 12) fail(); unique(months.map(row => row.id));
  return { ...meta, regions, defaultRegion, months };
}
function flowValues(value: unknown): FlowValues {
  const raw = object(value); return { volumeMln: finite(raw.volumeMln), count: finite(raw.count) };
}
function flowYears(value: unknown): FlowYear[] {
  const rows = list(value).map(value => { const row = object(value), values = object(row.programs); return { year: integer(row.year, 1900, 2199), throughMonth: integer(row.throughMonth, 1, 12), programs: Object.fromEntries(programs.map(p => [p.id, flowValues(values[p.id])])) as Record<Program, FlowValues>, total: flowValues(row.total) }; });
  unique(rows.map(row => row.year));
  for (const row of rows) for (const metric of ['volumeMln', 'count'] as const) {
    const values = programs.map(program => row.programs[program.id][metric]);
    const total = values.every(value => value !== null) ? values.reduce<number>((sum, value) => sum + value!, 0) : null;
    if (row.total[metric] !== null && (total === null || Math.abs(row.total[metric]! - total) > Math.max(1, total) * 1e-8)) fail();
  }
  return rows.sort((a, b) => a.year - b.year);
}
function characteristicYears(value: unknown): CharacteristicYear[] {
  const months = (value: unknown): CharacteristicMonths => { const row = object(value); return Object.fromEntries(characteristics.map(metric => [metric.id, row[metric.id] === null ? null : integer(row[metric.id], 1, 12)])) as CharacteristicMonths; };
  const rows = list(value).map(value => { const row = object(value), values = object(row.programs), horizons = object(row.programThroughMonths); return { year: integer(row.year, 1900, 2199), throughMonth: integer(row.throughMonth, 1, 12), throughMonths: months(row.throughMonths), programThroughMonths: Object.fromEntries(programs.map(program => [program.id, months(horizons[program.id])])) as Record<Program, CharacteristicMonths>, programs: Object.fromEntries(programs.map(program => { const fields = object(values[program.id]); return [program.id, Object.fromEntries(characteristics.map(metric => [metric.id, finite(fields[metric.id])]))]; })) as CharacteristicYear['programs'] }; });
  unique(rows.map(row => row.year)); return rows.sort((a, b) => a.year - b.year);
}
export function readPreferentialReport(value: unknown, expected: PreferentialSelection): PreferentialReport {
  const raw = object(value), meta = metadata(raw), selection = object(raw.selection);
  if (selection.region !== expected.region || selection.month !== expected.month || selection.characteristicMonth !== expected.characteristicMonth || raw.latestPeriodPartial !== null && typeof raw.latestPeriodPartial !== 'boolean') fail();
  return { ...meta, selection: { ...expected }, regionLabel: text(raw.regionLabel), latestPeriod: period(raw.latestPeriod), latestPeriodPartial: raw.latestPeriodPartial as boolean | null, annual: flowYears(raw.annual), cumulative: flowYears(raw.cumulative), characteristicsAnnual: characteristicYears(raw.characteristicsAnnual), characteristicsCumulative: characteristicYears(raw.characteristicsCumulative), notes: list(raw.notes).map(text) };
}
export function preferentialSelection(catalog: PreferentialCatalog | null, params: URLSearchParams): PreferentialSelection | null {
  if (!catalog) return null;
  const region = catalog.regions.find(row => row.id === params.get('region')) ?? catalog.regions.find(row => row.id === catalog.defaultRegion)!;
  const latest = Number(region.periods.at(-1)!.slice(5)), charLatest = Number((region.characteristicPeriods?.at(-1) ?? region.periods.at(-1))!.slice(5));
  const month = Number(params.get('month')), characteristicMonth = Number(params.get('characteristicMonth'));
  return { region: region.id, month: Number.isInteger(month) && month >= 1 && month <= 12 ? month : latest, characteristicMonth: Number.isInteger(characteristicMonth) && characteristicMonth >= 1 && characteristicMonth <= 12 ? characteristicMonth : charLatest };
}
export function preferentialUrl(selection: PreferentialSelection, version?: string): string {
  const params = new URLSearchParams({ region: selection.region, month: String(selection.month), characteristicMonth: String(selection.characteristicMonth) });
  if (version) params.set('required_version', version);
  return `/api/v1/preferential-mortgage${version ? '/export' : ''}?${params}`;
}
const shortMonths = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
export const cumulativeLabel = (month: number) => month === 1 ? 'янв' : `янв–${shortMonths[month - 1]}`;
const escape = (value: string) => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const tone = (theme: Theme) => ({ text: theme === 'light' ? '#293942' : '#f0f2f5', muted: theme === 'light' ? '#77838c' : '#abb3bc', line: theme === 'light' ? '#e9edef' : '#394047' });
export function flowChartOption(rows: FlowYear[], metric: FlowMetric, theme: Theme, width = 600, hidden: Program[] = [], cumulative = false): EChartsCoreOption {
  const t = tone(theme), divisor = 1000, unit = metric === 'volumeMln' ? 'млрд руб.' : 'тыс. шт.', shown = programs.filter(program => !hidden.includes(program.id));
  const totals = rows.map(row => { const values = shown.map(program => row.programs[program.id][metric]); return values.length && values.every(value => value !== null) ? values.reduce<number>((sum, value) => sum + value!, 0) / divisor : null; });
  const format = chartNumberFormatter([...rows.flatMap(row => shown.map(program => row.programs[program.id][metric] === null ? null : row.programs[program.id][metric]! / divisor)), ...totals]);
  const knownSums = rows.map(row => shown.reduce((sum, program) => sum + (row.programs[program.id][metric] ?? 0) / divisor, 0));
  const maximum = Math.max(1, ...knownSums, ...totals.map(value => value ?? 0)), slot = (width - 68) / Math.max(1, rows.length), small = slot < 38;
  return {
    animation: false, grid: { left: 48, right: 14, top: 36, bottom: 52 },
    tooltip: { trigger: 'item', confine: true, className: 'pm-tooltip', formatter: (point: { dataIndex: number; seriesIndex: number; value: number | null }) => {
      const row = rows[point.dataIndex], program = shown[point.seriesIndex]; if (!row || !program) return '';
      const value = row.programs[program.id][metric], total = totals[point.dataIndex];
      return `<strong>${row.year}${row.throughMonth < 12 || cumulative ? ` · ${cumulativeLabel(row.throughMonth)}` : ''} · ${escape(program.label)}</strong><br/>${format(value === null ? null : value / divisor)} ${unit}${value !== null && total ? `<br/>Доля: ${chartNumberFormatter([])(value / divisor / total * 100)}%` : ''}`;
    } },
    xAxis: { type: 'category', data: rows.map(row => String(row.year)), axisTick: { show: false }, axisLine: { lineStyle: { color: t.line } }, axisLabel: { interval: 0, hideOverlap: false, color: t.muted, fontSize: small ? 9 : 11, lineHeight: 16, formatter: (_year: string, index: number) => `${rows[index].year}${cumulative || rows[index].throughMonth < 12 ? `\n${cumulativeLabel(rows[index].throughMonth)}` : ''}` } },
    yAxis: { type: 'value', max: maximum * 1.17, splitNumber: 4, name: unit, nameTextStyle: { color: t.muted, fontSize: 10 }, axisLabel: { color: t.muted, fontSize: 10, formatter: format }, splitLine: { lineStyle: { color: t.line, type: 'dashed' } } },
    series: shown.map((program, index) => ({ name: program.label, type: 'bar', stack: 'programs', barMaxWidth: 51, itemStyle: { color: program.color }, emphasis: { focus: 'series' }, data: rows.map(row => row.programs[program.id][metric] === null ? null : row.programs[program.id][metric]! / divisor),
      label: { show: true, position: 'inside', color: '#fff', textBorderColor: program.id === 'family' ? '#577e89' : program.color, textBorderWidth: 1, fontSize: small ? 9 : 11, formatter: (point: { value: number | null }) => point.value !== null && point.value / maximum * 210 >= 18 && slot >= 29 ? format(point.value) : '' },
      ...(index === shown.length - 1 ? { markPoint: { silent: true, symbolSize: 0, label: { show: true, position: 'top', distance: 7, color: t.text, fontSize: small ? 10 : 12, fontWeight: 600, formatter: (point: { value: number | null }) => format(point.value) }, data: totals.flatMap((value, i) => value === null ? [] : [{ coord: [i, value], value }]) } } : {}),
    })),
  };
}
export function characteristicChartOption(rows: CharacteristicYear[], metric: Characteristic, theme: Theme, width = 900, hidden: Program[] = [], cumulative = false): EChartsCoreOption {
  const t = tone(theme), shown = programs.filter(program => !hidden.includes(program.id)), descriptor = characteristics.find(row => row.id === metric)!;
  const measures = rows.flatMap(row => shown.map(program => row.programs[program.id][metric])), format = chartNumberFormatter(measures), slot = (width - 65) / Math.max(1, rows.length);
  const maximum = Math.max(1, ...measures.map(value => value ?? 0));
  return {
    animation: false, grid: { left: 46, right: 12, top: 24, bottom: 49 },
    tooltip: { trigger: 'item', confine: true, className: 'pm-tooltip', formatter: (point: { dataIndex: number; seriesIndex: number; value: number | null }) => { const row = rows[point.dataIndex], program = shown[point.seriesIndex]; if (!row || !program) return ''; const month = row.programThroughMonths[program.id][metric]; return `<strong>${row.year}${month !== null && (cumulative || month < 12) ? ` · ${cumulativeLabel(month)}` : ''} · ${escape(program.label)}</strong><br/>${descriptor.label}: ${format(point.value)} ${descriptor.unit}`; } },
    xAxis: { type: 'category', data: rows.map(row => String(row.year)), axisTick: { show: false }, axisLine: { lineStyle: { color: t.line } }, axisLabel: { interval: 0, color: t.muted, fontSize: slot < 40 ? 9 : 11, lineHeight: 15, formatter: (_year: string, index: number) => { const month = rows[index].throughMonths[metric]; return `${rows[index].year}${month !== null && (cumulative || month < 12) ? `\n${cumulativeLabel(month)}` : ''}`; } } },
    yAxis: { type: 'value', max: maximum * 1.25, splitNumber: 3, axisLabel: { color: t.muted, fontSize: 10, formatter: format }, splitLine: { lineStyle: { color: t.line, type: 'dashed' } } },
    series: shown.map(program => ({ type: 'bar', name: program.label, barMaxWidth: 29, barGap: '15%', barCategoryGap: '25%', itemStyle: { color: program.color }, data: rows.map(row => row.programs[program.id][metric]), labelLayout: { hideOverlap: true }, label: { show: slot / Math.max(1, shown.length) >= 24, position: 'top', color: program.id === 'family' ? '#638994' : program.color, fontSize: 10, formatter: (point: { value: number | null }) => point.value === null ? '' : format(point.value) }, emphasis: { focus: 'series' } })),
  };
}
export function flowRows(rows: FlowYear[], metric: FlowMetric, partialDate?: string | null): (string | number | null)[][] {
  return [['Год', 'Период', ...programs.map(program => `${program.label}, ${metric === 'volumeMln' ? 'млн руб.' : 'шт.'}`), 'Всего', 'Дата среза неполного месяца'], ...rows.map(row => [row.year, cumulativeLabel(row.throughMonth), ...programs.map(program => row.programs[program.id][metric]), row.total[metric], partialDate && row.year === Number(partialDate.slice(0, 4)) && row.throughMonth === Number(partialDate.slice(5, 7)) ? partialDate : null])];
}
export function characteristicRows(rows: CharacteristicYear[], metric: Characteristic): (string | number | null)[][] {
  const descriptor = characteristics.find(row => row.id === metric)!;
  return [['Год', 'Период', ...programs.map(program => `${program.label}, ${descriptor.unit}`), ...programs.map(program => `${program.label}: период`)], ...rows.map(row => [row.year, row.throughMonths[metric] === null ? null : cumulativeLabel(row.throughMonths[metric]!), ...programs.map(program => row.programs[program.id][metric]), ...programs.map(program => row.programThroughMonths[program.id][metric] === null ? null : cumulativeLabel(row.programThroughMonths[program.id][metric]!))])];
}
