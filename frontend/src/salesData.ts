import { request } from './data.ts';
import { number } from './format.ts';
import type { EChartsCoreOption } from 'echarts/core';
import type { Theme } from './types';

export type SalesCell = string | number | null;
export type SalesMetadata = { schemaVersion: 1; version: string; generatedAt: string; source: { date: string | null; files: string[]; issues: [string, string][]; dateEvidence?: string } };
export type SalesPeriod = { id: string; label: string };
export type SalesRegion = { id: string; label: string; periods: SalesPeriod[] };
export type SalesCatalog = SalesMetadata & { regions: SalesRegion[] };
export type SalesMetric = { id: string; label: string; unit: string; value: number | null; digits: number };
export type SalesChart = { id: string; title: string; kind: 'bar' | 'line'; unit: string; series: { id: string; name: string; unit: string; points: { x: string; y: number | null }[] }[] };
export type SalesTable = { id: string; title: string; columns: { id: string; label: string }[]; rows: Record<string, SalesCell>[] };
export type SalesData = SalesMetadata & { region: { id: string; label: string }; period: SalesPeriod; metrics: SalesMetric[]; charts: SalesChart[]; tables: SalesTable[] };

type Obj = Record<string, unknown>;
const invalid = () => new Error('Ответ распроданности API не соответствует схеме версии 1.');
function obj(v: unknown): Obj { if (!v || typeof v !== 'object' || Array.isArray(v)) throw invalid(); return v as Obj; }
function str(v: unknown): string { if (typeof v !== 'string') throw invalid(); return v; }
function id(v: unknown): string { const s = str(v); if (!s) throw invalid(); return s; }
function num(v: unknown): number | null { if (v === null) return null; if (typeof v !== 'number' || !Number.isFinite(v)) throw invalid(); return v; }
function list<T>(v: unknown, read: (v: unknown) => T): T[] { if (!Array.isArray(v)) throw invalid(); return v.map(read); }
function unique<T extends { id: string }>(values: T[]): T[] { if (new Set(values.map(v => v.id)).size !== values.length) throw invalid(); return values; }
function metadata(v: Obj): SalesMetadata {
  if (v.schemaVersion !== 1 || !id(v.version) || !Number.isFinite(Date.parse(str(v.generatedAt)))) throw invalid();
  const source = obj(v.source);
  return { schemaVersion: 1, version: id(v.version), generatedAt: str(v.generatedAt), source: { date: source.date === null ? null : str(source.date), ...(source.dateEvidence === undefined ? {} : { dateEvidence: str(source.dateEvidence) }), files: list(source.files, str), issues: list(source.issues, value => { if (!Array.isArray(value) || value.length !== 2) throw invalid(); return [str(value[0]), str(value[1])]; }) } };
}
function period(value: unknown): SalesPeriod {
  const v = obj(value), key = id(v.id);
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(key)) throw invalid();
  return { id: key, label: str(v.label) };
}
export function readSalesCatalog(value: unknown): SalesCatalog {
  const v = obj(value);
  return { ...metadata(v), regions: unique(list(v.regions, value => { const r = obj(value); return { id: id(r.id), label: str(r.label), periods: unique(list(r.periods, period)) }; })) };
}
export function readSalesData(value: unknown, region: string, selectedPeriod: string): SalesData {
  const v = obj(value), r = obj(v.region), p = period(v.period);
  if (id(r.id) !== region || p.id !== selectedPeriod) throw invalid();
  const metrics = unique(list(v.metrics, value => {
    const m = obj(value), digits = num(m.digits);
    if (digits === null || !Number.isInteger(digits) || digits < 0 || digits > 20) throw invalid();
    return { id: id(m.id), label: str(m.label), unit: str(m.unit), value: num(m.value), digits };
  }));
  const charts = unique(list(v.charts, value => {
    const c = obj(value); if (c.kind !== 'bar' && c.kind !== 'line') throw invalid();
    return { id: id(c.id), title: str(c.title), kind: c.kind, unit: str(c.unit), series: unique(list(c.series, value => { const s = obj(value); return { id: id(s.id), name: str(s.name), unit: str(s.unit), points: list(s.points, value => { const p = obj(value); return { x: str(p.x), y: num(p.y) }; }) }; })) } as SalesChart;
  }));
  const tables = unique(list(v.tables, value => {
    const t = obj(value), columns = unique(list(t.columns, value => { const c = obj(value); return { id: id(c.id), label: str(c.label) }; }));
    const rows = list(t.rows, value => {
      const row = obj(value), cells: Record<string, SalesCell> = {};
      for (const [key, cell] of Object.entries(row)) cells[key] = typeof cell === 'string' ? cell : num(cell);
      if (columns.some(c => !Object.hasOwn(cells, c.id))) throw invalid();
      return cells;
    });
    return { id: id(t.id), title: str(t.title), columns, rows };
  }));
  return { ...metadata(v), region: { id: region, label: str(r.label) }, period: p, metrics, charts, tables };
}
export function salesFilters(catalog: SalesCatalog | null, params: URLSearchParams) {
  const region = catalog?.regions.find(r => r.id === params.get('region')) ?? catalog?.regions.find(r => r.id === 'msk') ?? catalog?.regions[0];
  const periods = [...region?.periods ?? []].sort((a, b) => a.id.localeCompare(b.id));
  return { region, periods, period: periods.find(p => p.id === params.get('period')) ?? periods.at(-1) };
}
export function salesUrl(region: string, period: string, version?: string) {
  const params = new URLSearchParams({ region, period });
  if (version !== undefined) params.set('required_version', version);
  return `/api/v1/sales${version === undefined ? '' : '/export'}?${params}`;
}
export async function loadSalesCatalog(signal: AbortSignal) { return readSalesCatalog(await (await request('/api/v1/sales/catalog', signal)).json()); }
export async function loadSalesData(region: string, period: string, signal: AbortSignal) { return readSalesData(await (await request(salesUrl(region, period), signal)).json(), region, period); }
export function salesTableRows(table: SalesTable): SalesCell[][] { return [table.columns.map(c => c.label), ...table.rows.map(row => table.columns.map(c => row[c.id]))]; }
export function salesChartRows(chart: SalesChart): SalesCell[][] { return [['Период / год', 'Показатель', 'Единица', 'Значение'], ...chart.series.flatMap(s => s.points.map(p => [p.x, s.name, s.unit, p.y]))]; }
export function salesTablePage(table: SalesTable, query: string, page: string | null, size: string | null) {
  const pageSize = [20, 50, 100].includes(Number(size)) ? Number(size) : 50;
  const search = query.toLocaleLowerCase('ru');
  const filtered = table.rows.filter(row => table.columns.some(c => row[c.id] !== null && String(row[c.id]).toLocaleLowerCase('ru').includes(search)));
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const current = Math.min(pages, Math.max(1, Number.isSafeInteger(Number(page)) ? Number(page) : 1));
  return { pageSize, pages, current, total: filtered.length, shown: filtered.slice((current - 1) * pageSize, current * pageSize) };
}
export const salesColors = ['#398dcc', '#cf5d64', '#2c9869', '#b38cdf', '#d6a23f', '#329d9c'];
function color(name: string, index: number, theme: Theme) {
  const key = name.toLocaleLowerCase('ru');
  const slot = /об[ъь]?е?м|объем|объём/.test(key) ? 0 : key.includes('отношен') ? 3 : key.includes('распрод') ? 1 : key.includes('готов') ? 2 : index % salesColors.length;
  return theme === 'light' ? ['#2473af', '#b9404c', '#187e50', '#8054b4', '#9a6a0d', '#187d7c'][slot] : salesColors[slot];
}
const escape = (s: string) => s.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
type TooltipPoint = { seriesIndex: number; seriesName: string; name: string; value: number | null };
export function salesChartOption(chart: SalesChart, theme: Theme): EChartsCoreOption {
  // Retain duplicate occurrences and forecast order; monthly gaps align by date.
  const slots: { x: string; occurrence: number }[] = [];
  const indexed = chart.series.map(s => {
    const seen = new Map<string, number>();
    return s.points.map(p => { const occurrence = seen.get(p.x) ?? 0; seen.set(p.x, occurrence + 1); if (!slots.some(slot => slot.x === p.x && slot.occurrence === occurrence)) slots.push({ x: p.x, occurrence }); return { ...p, occurrence }; });
  });
  if (chart.kind === 'line' && slots.every(p => /^\d{4}-(0[1-9]|1[0-2])$/.test(p.x))) slots.sort((a, b) => a.x.localeCompare(b.x) || a.occurrence - b.occurrence);
  const muted = theme === 'light' ? '#606975' : '#b2bac5', line = theme === 'light' ? '#e4e8ed' : '#363a40';
  return {
    grid: { left: 6, right: 12, top: 36, bottom: 12, containLabel: true },
    tooltip: { trigger: chart.kind === 'line' ? 'axis' : 'item', confine: true, className: 'sales-tooltip', formatter: (value: TooltipPoint | TooltipPoint[]) => {
      const points = Array.isArray(value) ? value : [value];
      return points.map(p => `<strong>${escape(p.name)} · ${escape(p.seriesName)}</strong><br/>${number(p.value, 1)} ${escape(chart.series[p.seriesIndex].unit)}`).join('<br/>');
    } },
    xAxis: { type: 'category', data: slots.map(p => p.x), boundaryGap: chart.kind === 'bar', axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: muted, fontSize: 11, hideOverlap: true, interval: 'auto', alignMinLabel: 'left', alignMaxLabel: 'right' } },
    yAxis: { type: 'value', name: chart.unit, nameTextStyle: { color: muted }, axisLabel: { color: muted, fontSize: 11, formatter: (v: number) => number(v) }, splitLine: { lineStyle: { color: line, type: 'dashed' } } },
    series: chart.series.map((s, i) => ({ name: s.name, type: chart.kind, data: slots.map(slot => indexed[i].find(p => p.x === slot.x && p.occurrence === slot.occurrence)?.y ?? null), itemStyle: { color: color(s.name, i, theme) }, lineStyle: { color: color(s.name, i, theme), width: 2 }, symbolSize: 7, showSymbol: true, connectNulls: false, barMaxWidth: 40, emphasis: { focus: 'series' } })),
  };
}
