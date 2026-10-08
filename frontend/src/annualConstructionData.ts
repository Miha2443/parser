import { request } from './data.ts';
import { number } from './format.ts';
import { readCommissioningMetadata, readReportTable, tableRows } from './commissioningData.ts';
import type { Cell, CommissioningMetadata, ReportTable, ValuePoint } from './commissioningData';
import type { EChartsCoreOption } from 'echarts/core';
import type { Theme } from './types';

type Choice = { id: string; label: string };
export type AnnualCatalog = CommissioningMetadata & { regions: Choice[] };
export type AnnualSelection = { region: string };
export type AnnualChart = ReportTable & { unit: string; totals: boolean; series: { id: string; name: string; color: string; points: { x: string; y: number | null }[] }[]; summaries: { from: number; to: number; values: { id: string; label: string; value: number | null }[] }[] };
export type AnnualReport = CommissioningMetadata & { region: Choice; charts: AnnualChart[]; notes: string[] };
export type ConstructionCatalog = AnnualCatalog & { permitKinds: Choice[]; monthsByKind: Record<string, number[]> };
export type ConstructionSelection = { region: string; permitKind: string; month: number };
export type PermitChart = { x: string[]; period: ValuePoint[]; remainder: ValuePoint[]; totals: ValuePoint[]; growth: ValuePoint[] };
export type ConstructionReport = CommissioningMetadata & { selection: ConstructionSelection; constructionPeriod: string; salesReadinessPeriod: { year: number; month: number } | null; metrics: { id: string; label: string; value: number | null; unit: string; digits: number }[]; sales: Record<string, Cell> | null; permits: ReportTable & { region: string; unit: string; periodLabel: string; chart: PermitChart | null }; sourceDetails: { file: string | null; date: string | null }; notes: string[] };
export type MarketCatalog = AnnualCatalog | ConstructionCatalog;
export type MarketSelection = AnnualSelection | ConstructionSelection;
export type MarketReport = AnnualReport | ConstructionReport;
export type MarketKind = 'annual' | 'construction';
type Obj = Record<string, unknown>;
const invalid = () => new Error('Ответ API не соответствует схеме отчёта версии 1.');
const obj = (v: unknown): Obj => { if (!v || typeof v !== 'object' || Array.isArray(v)) throw invalid(); return v as Obj; };
const str = (v: unknown): string => { if (typeof v !== 'string') throw invalid(); return v; };
const num = (v: unknown): number | null => { if (v === null) return null; if (typeof v !== 'number' || !Number.isFinite(v)) throw invalid(); return v; };
const int = (v: unknown, min = 1, max = 9999): number => { const n = num(v); if (n === null || !Number.isInteger(n) || n < min || n > max) throw invalid(); return n; };
const list = <T,>(v: unknown, read: (v: unknown) => T): T[] => { if (!Array.isArray(v)) throw invalid(); return v.map(read); };
const distinct = <T,>(v: T[], key: (v: T) => string | number): T[] => { if (new Set(v.map(key)).size !== v.length) throw invalid(); return v; };
const choices = (v: unknown) => distinct(list(v, value => { const c = obj(value), id = str(c.id); if (!id) throw invalid(); return { id, label: str(c.label) }; }), c => c.id);
const nullable = (v: unknown) => v === null ? null : str(v);
const cells = (v: unknown) => Object.fromEntries(Object.entries(obj(v)).map(([key, value]) => [key, typeof value === 'string' ? value : num(value)]));
export function readAnnualCatalog(value: unknown): AnnualCatalog { const v = obj(value); return { ...readCommissioningMetadata(v), regions: choices(v.regions) }; }
export function readConstructionCatalog(value: unknown): ConstructionCatalog {
  const v = obj(value), permitKinds = choices(v.permitKinds), monthsByKind = Object.fromEntries(Object.entries(obj(v.monthsByKind)).map(([key, value]) => [key, distinct(list(value, v => int(v, 1, 12)), v => v)]));
  if (permitKinds.some(k => !monthsByKind[k.id])) throw invalid();
  return { ...readAnnualCatalog(v), permitKinds, monthsByKind };
}
export function readAnnualReport(value: unknown, selection: AnnualSelection): AnnualReport {
  const v = obj(value), region = choices([v.region])[0]; if (region.id !== selection.region) throw invalid();
  const charts = distinct(list(v.charts, value => {
    const c = obj(value); if (typeof c.totals !== 'boolean') throw invalid();
    const table = readReportTable(c), series = distinct(list(c.series, value => { const s = obj(value); return { id: str(s.id), name: str(s.name), color: str(s.color), points: list(s.points, value => { const p = obj(value); return { x: str(p.x), y: num(p.y) }; }) }; }), s => s.id);
    if (series.some(s => !table.columns.some(c => c.id === s.id) || s.points.length !== table.rows.length || s.points.some((p, i) => p.x !== String(table.rows[i].year) || p.y !== table.rows[i][s.id]))) throw invalid();
    const summaries = list(c.summaries, value => { const s = obj(value), from = int(s.from), to = int(s.to); if (to < from) throw invalid(); return { from, to, values: distinct(list(s.values, value => { const r = obj(value); return { id: str(r.id), label: str(r.label), value: num(r.value) }; }), r => r.id) }; });
    return { ...table, unit: str(c.unit), totals: c.totals, series, summaries };
  }), c => c.id);
  return { ...readCommissioningMetadata(v), region, charts, notes: list(v.notes, str) };
}
export function readConstructionReport(value: unknown, expected: ConstructionSelection): ConstructionReport {
  const v = obj(value), s = obj(v.selection), selection = { region: str(s.region), permitKind: str(s.permitKind), month: int(s.month, 1, 12) };
  if (Object.entries(expected).some(([key, value]) => selection[key as keyof ConstructionSelection] !== value)) throw invalid();
  const p = obj(v.permits), table = readReportTable({ ...p, id: 'permits', title: 'Разрешения на строительство · Москва' });
  let chart: PermitChart | null = null;
  if (p.chart !== null) { const c = obj(p.chart), x = list(c.x, str); const points = (v: unknown) => { const values = list(v, v => ({ value: num(obj(v).value) })); if (values.length !== x.length) throw invalid(); return values; }; chart = { x, period: points(c.period), remainder: points(c.remainder), totals: points(c.totals), growth: points(c.growth) }; if (x.length !== table.rows.length || x.some((x, i) => x !== String(table.rows[i]['Год']))) throw invalid(); }
  const period = v.salesReadinessPeriod === null ? null : obj(v.salesReadinessPeriod), detail = obj(v.sourceDetails);
  const metrics = distinct(list(v.metrics, value => { const m = obj(value); return { id: str(m.id), label: str(m.label), value: num(m.value), unit: str(m.unit), digits: int(m.digits, 0, 20) }; }), m => m.id);
  const sales = v.sales === null ? null : cells(v.sales); if (sales?.region_key !== undefined && sales.region_key !== selection.region) throw invalid();
  if (p.region !== 'Москва') throw invalid();
  return { ...readCommissioningMetadata(v), selection, constructionPeriod: str(v.constructionPeriod), salesReadinessPeriod: period && { year: int(period.year), month: int(period.month, 1, 12) }, metrics, sales, permits: { ...table, region: str(p.region), unit: str(p.unit), periodLabel: str(p.periodLabel), chart }, sourceDetails: { file: nullable(detail.file), date: nullable(detail.date) }, notes: list(v.notes, str) };
}
export function annualFilters(c: AnnualCatalog | null, p: URLSearchParams): AnnualSelection | null { const region = c?.regions.find(r => r.id === p.get('region')) ?? c?.regions[0]; return region ? { region: region.id } : null; }
export function constructionFilters(c: ConstructionCatalog | null, p: URLSearchParams): ConstructionSelection | null {
  const base = annualFilters(c, p), kind = c?.permitKinds.find(k => k.id === p.get('permit_kind')) ?? c?.permitKinds[0]; if (!base || !kind || !c) return null;
  const months = c.monthsByKind[kind.id]; if (!months.length) return null;
  return { ...base, permitKind: kind.id, month: months.includes(Number(p.get('month'))) ? Number(p.get('month')) : months.at(-1)! };
}
export function marketParams(selection: MarketSelection): Record<string, string> { return 'permitKind' in selection ? { region: selection.region, permit_kind: selection.permitKind, month: String(selection.month) } : { region: selection.region }; }
export const marketRoot = (kind: MarketKind) => kind === 'annual' ? '/api/v1/commissioning/annual' : '/api/v1/construction';
export function marketUrl(kind: MarketKind, selection: MarketSelection, version?: string) { const p = new URLSearchParams(marketParams(selection)); if (version !== undefined) p.set('required_version', version); return `${marketRoot(kind)}${version === undefined ? '' : '/export'}?${p}`; }
export async function loadMarketCatalog(kind: MarketKind, signal: AbortSignal) { const v: unknown = await (await request(`${marketRoot(kind)}/catalog`, signal)).json(); return kind === 'annual' ? readAnnualCatalog(v) : readConstructionCatalog(v); }
export async function loadMarketReport(kind: MarketKind, selection: MarketSelection, signal: AbortSignal) { const v: unknown = await (await request(marketUrl(kind, selection), signal)).json(); return kind === 'annual' ? readAnnualReport(v, selection) : readConstructionReport(v, selection as ConstructionSelection); }
const escape = (v: string) => v.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const tones = (theme: Theme) => ({ muted: theme === 'light' ? '#606975' : '#b2bac5', line: theme === 'light' ? '#e4e8ed' : '#363a40' });
export function annualChartOption(c: AnnualChart, theme: Theme): EChartsCoreOption {
  const t = tones(theme);
  return { grid: { left: 6, right: 14, top: 36, bottom: 12, containLabel: true }, tooltip: { trigger: 'item', confine: true, className: 'commissioning-tooltip', formatter: (p: { name: string; seriesName: string; value: number | null }) => `<strong>${escape(p.name)} · ${escape(p.seriesName)}</strong><br/>${number(p.value, 1)} ${escape(c.unit)}` }, xAxis: { type: 'category', data: c.rows.map(r => String(r.year)), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: t.muted, hideOverlap: true, alignMinLabel: 'left', alignMaxLabel: 'right' } }, yAxis: { type: 'value', name: c.unit, nameTextStyle: { color: t.muted }, axisLabel: { color: t.muted, formatter: (v: number) => number(v, 1) }, splitLine: { lineStyle: { color: t.line, type: 'dashed' } } }, series: c.series.map((s, i) => ({ type: 'bar', stack: c.id, name: s.name, data: s.points.map(p => p.y), itemStyle: { color: s.color }, barMaxWidth: 50, emphasis: { focus: 'series' }, labelLayout: { hideOverlap: true }, label: { show: c.totals && i === c.series.length - 1, position: 'top', color: t.muted, fontSize: 10, formatter: (p: { dataIndex: number }) => { const values = c.series.map(s => s.points[p.dataIndex].y); return values.some(v => v === null) ? '' : number(values.reduce<number>((sum, v) => sum + (v ?? 0), 0), 1); } } })), media: [{ query: { maxWidth: 600 }, option: { series: c.series.map(() => ({ label: { show: false } })) } }] };
}
export function permitChartOption(p: ConstructionReport['permits'], theme: Theme): EChartsCoreOption {
  const c = p.chart! , t = tones(theme);
  return { grid: { left: 6, right: 14, top: 36, bottom: 12, containLabel: true }, tooltip: { trigger: 'item', confine: true, className: 'commissioning-tooltip', formatter: (v: { name: string; seriesName: string; value: number | null; dataIndex: number; seriesIndex: number }) => `<strong>${escape(v.name)} · ${escape(v.seriesName)}</strong><br/>${number(v.value)} ${escape(p.unit)}${v.seriesIndex === 0 && p.rows[v.dataIndex]['С начала года, тыс. м²'] === null ? '<br/>Исходное значение периода: —' : ''}<br/>Итог года: ${number(c.totals[v.dataIndex].value)} ${escape(p.unit)}<br/>Изменение: ${number(c.growth[v.dataIndex].value)} %` }, xAxis: { type: 'category', data: c.x, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: t.muted, hideOverlap: true, alignMinLabel: 'left', alignMaxLabel: 'right' } }, yAxis: { type: 'value', name: p.unit, nameTextStyle: { color: t.muted }, axisLabel: { color: t.muted, formatter: (v: number) => number(v) }, splitLine: { lineStyle: { color: t.line, type: 'dashed' } } }, series: [{ name: p.periodLabel, values: c.period, color: theme === 'light' ? '#b9404c' : '#cf5d64' }, { name: 'Остаток до итога года', values: c.remainder, color: theme === 'light' ? '#6f8091' : '#96a7b8' }].map((s, i) => ({ type: 'bar', stack: 'permits', name: s.name, data: s.values.map(v => v.value), itemStyle: { color: s.color }, barMaxWidth: 48, emphasis: { focus: 'series' }, labelLayout: { hideOverlap: true }, label: { show: true, position: i === 0 ? 'inside' : 'top', color: i === 0 ? '#ffffff' : t.muted, fontSize: 10, formatter: (v: { dataIndex: number; value: number }) => i === 0 ? p.rows[v.dataIndex]['С начала года, тыс. м²'] === null ? '' : `${number(v.value)}${c.growth[v.dataIndex].value === null ? '' : `\n${number(c.growth[v.dataIndex].value)}%`}` : c.totals[v.dataIndex].value === null ? '' : number(c.totals[v.dataIndex].value) } })), media: [{ query: { maxWidth: 600 }, option: { series: [{ label: { show: false } }, { label: { show: false } }] } }] };
}
export const permitChartRows = (p: ConstructionReport['permits']): Cell[][] => !p.chart ? [] : [['Год', `За период, ${p.unit}`, `Остаток, ${p.unit}`, `Итог, ${p.unit}`, 'Изменение, %', `Исходное значение периода, ${p.unit}`], ...p.chart.x.map((x, i) => [x, p.chart!.period[i].value, p.chart!.remainder[i].value, p.chart!.totals[i].value, p.chart!.growth[i].value, p.rows[i]['С начала года, тыс. м²']])];
export function salesTable(sales: Record<string, Cell>): ReportTable { return { id: 'sales', title: 'Реализация квартир · исходные значения', columns: [{ id: 'field', label: 'Показатель' }, { id: 'value', label: 'Исходное значение' }], rows: Object.entries(sales).map(([field, value]) => ({ field, value })) }; }
export const annualSummaryRows = (c: AnnualChart): Cell[][] => [['С', 'По', 'Показатель', c.unit], ...c.summaries.flatMap(s => s.values.map(v => [s.from, s.to, v.label, v.value]))];
export { tableRows };
