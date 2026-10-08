import { request } from './data.ts';
import { number } from './format.ts';
import type { EChartsCoreOption } from 'echarts/core';
import type { Theme } from './types';

export type CommissioningKind = 'operational' | 'linear';
export type Cell = string | number | null;
export type CommissioningMetadata = { schemaVersion: 1; version: string; generatedAt: string; source: { date: string | null; files: string[]; issues: [string, string][]; fileEvidence: 'candidates' | 'selected'; dateEvidence?: string } };
export type ReportTable = { id: string; title: string; columns: { id: string; label: string }[]; rows: Record<string, Cell>[] };
export type OperationalSelection = { month: number; year: number; quarter: number; cumulative: boolean; excludeMkd: boolean };
export type LinearSelection = { year: number; quarter: number; cumulative: boolean; indicator: string };
export type OperationalCatalog = CommissioningMetadata & { months: { id: number; label: string }[]; defaultMonth: number; currentYear: number; years: number[] };
export type LinearCatalog = CommissioningMetadata & { years: { id: string; label: string; quarters: number[]; defaultQuarter: number }[]; indicators: { id: string; label: string; unit: string }[] };
export type ValuePoint = { value: number | null };
export type OperationalTable = ReportTable & { chart: { x: string[]; period: ValuePoint[]; remainder: ValuePoint[]; totals: ValuePoint[]; growth: ValuePoint[] } };
export type StructureRow = { id: string; label: string; value: number | null };
export type OperationalReport = CommissioningMetadata & { selection: OperationalSelection; currentYear: number; periodLabel: string; region: string; tables: OperationalTable[]; tree: Record<string, number | null>; treeGrowth?: Record<string, number | null>; treeRows: StructureRow[]; sourceDetails: { file: string | null; date: string | null }; notes: string[] };
export type LinearRow = { code: string; indicator: string; unit: string; year: number; quarter: number; plan: number | null; fact: number | null; percent: number | null };
export type LinearReport = CommissioningMetadata & { selection: LinearSelection; summary: LinearRow[]; trend: LinearRow[]; allPeriods: LinearRow[] };
export type CommissioningCatalog = OperationalCatalog | LinearCatalog;
export type CommissioningReport = OperationalReport | LinearReport;
export type CommissioningSelection = OperationalSelection | LinearSelection;

type Obj = Record<string, unknown>;
const invalid = () => new Error('Ответ API ввода недвижимости не соответствует схеме версии 1.');
function obj(v: unknown): Obj { if (!v || typeof v !== 'object' || Array.isArray(v)) throw invalid(); return v as Obj; }
function str(v: unknown): string { if (typeof v !== 'string') throw invalid(); return v; }
function id(v: unknown): string { const key = str(v); if (!key) throw invalid(); return key; }
function num(v: unknown): number | null { if (v === null) return null; if (typeof v !== 'number' || !Number.isFinite(v)) throw invalid(); return v; }
function int(v: unknown, min: number, max: number): number { const n = num(v); if (n === null || !Number.isInteger(n) || n < min || n > max) throw invalid(); return n; }
function bool(v: unknown): boolean { if (typeof v !== 'boolean') throw invalid(); return v; }
function nullableStr(v: unknown): string | null { return v === null ? null : str(v); }
function list<T>(v: unknown, read: (v: unknown) => T): T[] { if (!Array.isArray(v)) throw invalid(); return v.map(read); }
function distinct<T>(values: T[], key: (v: T) => string | number): T[] { if (new Set(values.map(key)).size !== values.length) throw invalid(); return values; }
function metadata(v: Obj): CommissioningMetadata {
  if (v.schemaVersion !== 1 || !id(v.version) || !Number.isFinite(Date.parse(str(v.generatedAt)))) throw invalid();
  const s = obj(v.source); if (s.fileEvidence !== 'candidates' && s.fileEvidence !== 'selected') throw invalid();
  return { schemaVersion: 1, version: id(v.version), generatedAt: str(v.generatedAt), source: { date: nullableStr(s.date), files: list(s.files, str), fileEvidence: s.fileEvidence, ...(s.dateEvidence === undefined ? {} : { dateEvidence: str(s.dateEvidence) }), issues: list(s.issues, value => { if (!Array.isArray(value) || value.length !== 2) throw invalid(); return [str(value[0]), str(value[1])]; }) } };
}
export const readCommissioningMetadata = (value: unknown) => metadata(obj(value));
export function readOperationalCatalog(value: unknown): OperationalCatalog {
  const v = obj(value), months = distinct(list(v.months, value => { const m = obj(value); return { id: int(m.id, 1, 12), label: str(m.label) }; }), m => m.id);
  const years = distinct(list(v.years, value => int(value, 1, 9999)), y => y), defaultMonth = int(v.defaultMonth, 1, 12), currentYear = int(v.currentYear, 1, 9999);
  if (months.length && !months.some(m => m.id === defaultMonth) || years.length && !years.includes(currentYear)) throw invalid();
  return { ...metadata(v), months, defaultMonth, currentYear, years };
}
export function readLinearCatalog(value: unknown): LinearCatalog {
  const v = obj(value);
  const years = distinct(list(v.years, value => { const y = obj(value), key = id(y.id), quarters = distinct(list(y.quarters, value => int(value, 1, 4)), q => q), defaultQuarter = int(y.defaultQuarter, 1, 4); if (!/^\d{4}$/.test(key) || Number(key) < 1 || quarters.length && !quarters.includes(defaultQuarter)) throw invalid(); return { id: key, label: str(y.label), quarters, defaultQuarter }; }), y => y.id);
  const indicators = distinct(list(v.indicators, value => { const i = obj(value); return { id: id(i.id), label: str(i.label), unit: str(i.unit) }; }), i => i.id);
  return { ...metadata(v), years, indicators };
}
function operationalSelection(value: unknown): OperationalSelection { const v = obj(value); return { month: int(v.month, 1, 12), year: int(v.year, 1, 9999), quarter: int(v.quarter, 1, 4), cumulative: bool(v.cumulative), excludeMkd: bool(v.excludeMkd) }; }
function linearSelection(value: unknown): LinearSelection { const v = obj(value); return { year: int(v.year, 1, 9999), quarter: int(v.quarter, 1, 4), cumulative: bool(v.cumulative), indicator: id(v.indicator) }; }
function matchSelection(actual: CommissioningSelection, expected: CommissioningSelection) { if (Object.entries(expected).some(([key, value]) => actual[key as keyof typeof actual] !== value)) throw invalid(); }
function table(value: unknown): ReportTable {
  const t = obj(value), columns = distinct(list(t.columns, value => { const c = obj(value); return { id: id(c.id), label: str(c.label) }; }), c => c.id);
  const rows = list(t.rows, value => { const v = obj(value), row: Record<string, Cell> = {}; for (const [key, value] of Object.entries(v)) row[key] = typeof value === 'string' ? value : num(value); if (columns.some(c => !Object.hasOwn(row, c.id))) throw invalid(); return row; });
  return { id: id(t.id), title: str(t.title), columns, rows };
}
export const readReportTable = (value: unknown) => table(value);
export function readOperationalReport(value: unknown, expected: OperationalSelection): OperationalReport {
  const v = obj(value), selection = operationalSelection(v.selection); matchSelection(selection, expected);
  const tables = distinct(list(v.tables, value => {
    const t = obj(value), c = obj(t.chart), x = list(c.x, str);
    const readPoints = (value: unknown) => { const points = list(value, value => ({ value: num(obj(value).value) })); if (points.length !== x.length) throw invalid(); return points; };
    return { ...table(t), chart: { x, period: readPoints(c.period), remainder: readPoints(c.remainder), totals: readPoints(c.totals), growth: readPoints(c.growth) } };
  }), t => t.id);
  const tree = Object.fromEntries(Object.entries(obj(v.tree)).map(([key, value]) => [key, num(value)]));
  const treeRows = distinct(list(v.treeRows, value => { const r = obj(value); return { id: id(r.id), label: str(r.label), value: num(r.value) }; }), r => r.id);
  if (treeRows.some(row => !Object.hasOwn(tree, row.id) || tree[row.id] !== row.value)) throw invalid();
  const sourceDetails = obj(v.sourceDetails);
  return { ...metadata(v), selection, currentYear: int(v.currentYear, 1, 9999), periodLabel: str(v.periodLabel), region: str(v.region), tables, tree, ...(v.treeGrowth === undefined ? {} : { treeGrowth: Object.fromEntries(Object.entries(obj(v.treeGrowth)).map(([key, value]) => [key, num(value)])) }), treeRows, sourceDetails: { file: nullableStr(sourceDetails.file), date: nullableStr(sourceDetails.date) }, notes: list(v.notes, str) };
}
function linearRow(value: unknown): LinearRow { const r = obj(value); return { code: id(r.code), indicator: str(r.indicator), unit: str(r.unit), year: int(r.year, 1, 9999), quarter: int(r.quarter, 1, 4), plan: num(r.plan), fact: num(r.fact), percent: num(r.percent) }; }
export function readLinearReport(value: unknown, expected: LinearSelection): LinearReport {
  const v = obj(value), selection = linearSelection(v.selection); matchSelection(selection, expected);
  const summary = list(v.summary, linearRow), trend = list(v.trend, linearRow), allPeriods = list(v.allPeriods, linearRow);
  if (summary.some(r => r.year !== selection.year || r.quarter !== selection.quarter) || trend.some(r => r.year !== selection.year || r.code !== selection.indicator) || allPeriods.some(r => r.year !== selection.year)) throw invalid();
  return { ...metadata(v), selection, summary, trend, allPeriods };
}
export function operationalFilters(c: OperationalCatalog | null, p: URLSearchParams): OperationalSelection | null {
  if (!c?.months.length || !c.years.length) return null;
  return { month: c.months.some(m => m.id === Number(p.get('month'))) ? Number(p.get('month')) : c.defaultMonth, year: c.years.includes(Number(p.get('year'))) ? Number(p.get('year')) : c.currentYear, quarter: [1, 2, 3, 4].includes(Number(p.get('quarter'))) ? Number(p.get('quarter')) : 1, cumulative: p.get('cumulative') === 'true', excludeMkd: p.get('exclude_mkd') === 'true' };
}
export function linearFilters(c: LinearCatalog | null, p: URLSearchParams): LinearSelection | null {
  const year = c?.years.find(y => y.id === p.get('year')) ?? c?.years[0], indicator = c?.indicators.find(i => i.id === p.get('indicator')) ?? c?.indicators[0];
  if (!year?.quarters.length || !indicator) return null;
  return { year: Number(year.id), quarter: year.quarters.includes(Number(p.get('quarter'))) ? Number(p.get('quarter')) : year.defaultQuarter, cumulative: p.get('cumulative') === 'true', indicator: indicator.id };
}
export function commissioningParams(selection: CommissioningSelection): Record<string, string> {
  return 'month' in selection ? { month: String(selection.month), exclude_mkd: String(selection.excludeMkd), year: String(selection.year), quarter: String(selection.quarter), cumulative: String(selection.cumulative) } : { year: String(selection.year), quarter: String(selection.quarter), cumulative: String(selection.cumulative), indicator: selection.indicator };
}
export function commissioningRoot(kind: CommissioningKind) { return kind === 'operational' ? '/api/v1/commissioning/operational' : '/api/v1/linear'; }
export function commissioningUrl(kind: CommissioningKind, selection: CommissioningSelection, version?: string) {
  const params = new URLSearchParams(commissioningParams(selection)); if (version !== undefined) params.set('required_version', version);
  return `${commissioningRoot(kind)}${version === undefined ? '' : '/export'}?${params}`;
}
export async function loadCommissioningCatalog(kind: CommissioningKind, signal: AbortSignal) { const value: unknown = await (await request(`${commissioningRoot(kind)}/catalog`, signal)).json(); return kind === 'operational' ? readOperationalCatalog(value) : readLinearCatalog(value); }
export async function loadCommissioningReport(kind: CommissioningKind, selection: CommissioningSelection, signal: AbortSignal) { const value: unknown = await (await request(commissioningUrl(kind, selection), signal)).json(); return kind === 'operational' ? readOperationalReport(value, selection as OperationalSelection) : readLinearReport(value, selection as LinearSelection); }
export function tableRows(t: ReportTable): Cell[][] { return [t.columns.map(c => c.label), ...t.rows.map(r => t.columns.map(c => r[c.id]))]; }
export function reportTablePage(t: ReportTable, query: string, page: string | null, size: string | null) {
  const pageSize = [20, 50, 100].includes(Number(size)) ? Number(size) : 50, search = query.toLocaleLowerCase('ru');
  const filtered = t.rows.filter(r => t.columns.some(c => r[c.id] !== null && String(r[c.id]).toLocaleLowerCase('ru').includes(search))), pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const current = Math.min(pages, Math.max(1, Number.isSafeInteger(Number(page)) ? Number(page) : 1));
  return { pageSize, pages, current, total: filtered.length, shown: filtered.slice((current - 1) * pageSize, current * pageSize) };
}
export const structureGroups = [
  { title: 'Общие показатели', ids: ['total', 'residential_area', 'nonres_total'] },
  { title: 'Жилые объекты', ids: ['housing_objects', 'mkd_total', 'mkd_residential', 'izhs', 'mop', 'nonres_in_housing'] },
  { title: 'Отдельно стоящие нежилые объекты', ids: ['nonres_objects', 'offices', 'hotels', 'industrial', 'social', 'other'] },
];
export function groupedStructure(rows: StructureRow[]) {
  const known = new Set(structureGroups.flatMap(g => g.ids));
  return [...structureGroups.map(g => ({ title: g.title, rows: g.ids.flatMap(id => rows.filter(r => r.id === id)) })), { title: 'Прочие показатели источника', rows: rows.filter(r => !known.has(r.id)) }].filter(g => g.rows.length);
}
export function structureRows(rows: StructureRow[]): Cell[][] { return [['Показатель', 'млн м²'], ...rows.map(r => [r.label, r.value])]; }
export const linearDigits = (unit: string) => unit === 'км' ? 1 : 0;
export function linearTable(rows: LinearRow[], title = 'Все показатели по кварталам'): ReportTable {
  return { id: 'all-periods', title, columns: [['code', 'Код'], ['indicator', 'Показатель'], ['unit', 'Единица'], ['year', 'Год'], ['quarter', 'Квартал'], ['plan', 'План'], ['fact', 'Факт'], ['percent', 'Выполнение плана, %']].map(([id, label]) => ({ id, label })), rows: rows.map(r => ({ ...r })) };
}
export function operationalChartRows(t: OperationalTable): Cell[][] { return [['Год', 'За период, млн м²', 'Остаток, млн м²', 'Итог / доступный период, млн м²', 'Изменение к аналогичному периоду, %', 'Исходное значение периода, млн м²'], ...t.chart.x.map((x, i) => [x, t.chart.period[i].value, t.chart.remainder[i].value, t.chart.totals[i].value, t.chart.growth[i].value, t.rows.find(r => String(r['Год']) === x)?.['За выбранный период, млн м²'] ?? null])]; }
const escape = (s: string) => s.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const tones = (theme: Theme) => ({ muted: theme === 'light' ? '#606975' : '#b2bac5', line: theme === 'light' ? '#e4e8ed' : '#363a40', plan: theme === 'light' ? '#6f8091' : '#96a7b8', fact: theme === 'light' ? '#b9404c' : '#cf5d64' });
export function operationalChartOption(t: OperationalTable, periodLabel: string, theme: Theme): EChartsCoreOption {
  const colors = tones(theme), c = t.chart;
  return {
    grid: { left: 6, right: 14, top: 40, bottom: 12, containLabel: true },
    tooltip: { trigger: 'item', confine: true, className: 'commissioning-tooltip', formatter: (p: { dataIndex: number; seriesIndex: number; seriesName: string; name: string; value: number | null }) => {
      const raw = t.rows.find(r => String(r['Год']) === p.name)?.['За выбранный период, млн м²'];
      return `<strong>${escape(p.name)} · ${escape(p.seriesName)}</strong><br/>${number(p.value, 2)} млн м²${p.seriesIndex === 0 && raw === null ? '<br/>Исходное значение периода: —' : ''}<br/>Итог / доступный период: ${number(c.totals[p.dataIndex].value, 2)} млн м²<br/>Изменение к аналогичному периоду: ${number(c.growth[p.dataIndex].value, 1)} %`;
    } },
    xAxis: { type: 'category', data: c.x, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: colors.muted, fontSize: 11, interval: 'auto', hideOverlap: true, alignMinLabel: 'left', alignMaxLabel: 'right' } },
    yAxis: { type: 'value', name: 'млн м²', nameTextStyle: { color: colors.muted }, axisLabel: { color: colors.muted, formatter: (v: number) => number(v, 1) }, splitLine: { lineStyle: { color: colors.line, type: 'dashed' } } },
    series: [
      { type: 'bar', name: periodLabel, stack: 'period', data: c.period.map(p => p.value), itemStyle: { color: colors.fact }, barMaxWidth: 48, emphasis: { focus: 'series' }, labelLayout: { hideOverlap: true }, label: { show: true, position: 'inside', color: '#ffffff', fontSize: 10, formatter: (p: { dataIndex: number; value: number }) => { const raw = t.rows.find(r => String(r['Год']) === c.x[p.dataIndex])?.['За выбранный период, млн м²'], growth = c.growth[p.dataIndex].value; return raw == null ? '' : `${number(p.value, 2)}${growth === null ? '' : `\n${growth >= 0 ? '+' : ''}${number(growth)}%`}`; } } },
      { type: 'bar', name: 'Остаток до итога года', stack: 'period', data: c.remainder.map(p => p.value), itemStyle: { color: colors.plan }, barMaxWidth: 48, emphasis: { focus: 'series' }, labelLayout: { hideOverlap: true }, label: { show: true, position: 'top', color: colors.muted, fontSize: 10, formatter: (p: { dataIndex: number }) => c.totals[p.dataIndex].value === null ? '' : number(c.totals[p.dataIndex].value, 2) } },
    ],
    media: [{ query: { maxWidth: 600 }, option: { series: [{ label: { show: false } }, { label: { show: false } }] } }],
  };
}
export function linearSummaryOption(row: LinearRow, theme: Theme): EChartsCoreOption {
  const c = tones(theme), digits = linearDigits(row.unit), maximum = Math.max(row.plan ?? 0, row.fact ?? 0);
  return {
    grid: { left: 6, right: 60, top: 12, bottom: 32, containLabel: true },
    tooltip: { trigger: 'item', confine: true, className: 'commissioning-tooltip', formatter: (p: { seriesName: string; value: number | null }) => `<strong>${escape(row.indicator)} · ${escape(p.seriesName)}</strong><br/>${number(p.value, digits)} ${escape(row.unit)}` },
    xAxis: { type: 'value', name: row.unit, nameLocation: 'middle', nameGap: 25, max: maximum > 0 ? maximum * 1.18 : undefined, axisLabel: { color: c.muted, fontSize: 10, formatter: (v: number) => number(v, digits) }, splitLine: { lineStyle: { color: c.line, type: 'dashed' } } },
    yAxis: { type: 'category', data: ['Факт', 'План'], axisTick: { show: false }, axisLine: { show: false }, axisLabel: { color: c.muted, fontSize: 11 } },
    series: [{ name: 'План', values: [null, row.plan], color: c.plan }, { name: 'Факт', values: [row.fact, null], color: c.fact }].map(s => ({ type: 'bar', name: s.name, data: s.values, barGap: '-100%', barMaxWidth: 20, itemStyle: { color: s.color }, label: { show: true, position: 'right', color: c.muted, fontSize: 11, formatter: (p: { value: number | null }) => p.value === null ? '' : number(p.value, digits) } })),
  };
}
export function linearTrendOption(rows: LinearRow[], unit: string, theme: Theme): EChartsCoreOption {
  const c = tones(theme), digits = linearDigits(unit);
  return {
    grid: { left: 6, right: 14, top: 36, bottom: 12, containLabel: true },
    tooltip: { trigger: 'item', confine: true, className: 'commissioning-tooltip', formatter: (p: { seriesName: string; name: string; value: number | null; dataIndex: number }) => `<strong>${escape(p.name)} · ${escape(p.seriesName)}</strong><br/>${number(p.value, digits)} ${escape(unit)}<br/>Выполнение плана: ${number(rows[p.dataIndex].percent, 1)} %` },
    xAxis: { type: 'category', data: rows.map(r => `${r.quarter} кв.`), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: c.muted, hideOverlap: true, alignMinLabel: 'left', alignMaxLabel: 'right' } },
    yAxis: { type: 'value', name: unit, nameTextStyle: { color: c.muted }, axisLabel: { color: c.muted, formatter: (v: number) => number(v, digits) }, splitLine: { lineStyle: { color: c.line, type: 'dashed' } } },
    series: [{ name: 'План', key: 'plan' as const, color: c.plan }, { name: 'Факт', key: 'fact' as const, color: c.fact }].map(s => ({ type: 'bar', name: s.name, data: rows.map(r => r[s.key]), itemStyle: { color: s.color }, barMaxWidth: 45, emphasis: { focus: 'series' }, labelLayout: { hideOverlap: true }, label: { show: true, position: 'top', color: c.muted, fontSize: 11, formatter: (p: { value: number | null }) => p.value === null ? '' : number(p.value, digits) } })),
  };
}
