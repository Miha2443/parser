import { ApiError, request } from './data.ts';
import { number } from './format.ts';
import type { EChartsCoreOption } from 'echarts/core';
import type { Theme } from './types';

export type EconomicsFamily = 'salary' | 'ipc' | 'accounts';
export type EconomicsCell = string | number | null;
export type EconomicsValue = string | boolean | (string | number)[];
export type EconomicsSelection = Record<string, EconomicsValue>;
export type EconomicsOption = { id: string | number; label: string; unit?: string };
export type EconomicsTable = { id: string; title: string; columns: { id: string; label: string }[]; rows: Record<string, EconomicsCell>[] };
export type EconomicsMetadata = { schemaVersion: 1; version: string; generatedAt: string; source: {
  date: string | null; files: string[]; issues: [string, string][]; fileEvidence: string;
  recordedSources: string[]; recordedSourceEvidence: string; dateEvidence: string;
  downloadSummary: string; provenance: Pick<EconomicsTable, 'columns' | 'rows'>;
} };
export type EconomicsControl = { id: string; type: 'select' | 'multiselect' | 'checkbox'; default: EconomicsValue;
  options?: EconomicsOption[]; periods?: string[]; optionsByRegion?: Record<string, EconomicsOption[]>;
  optionsByRegionMode?: Record<string, Record<string, EconomicsOption[]>> };
export type EconomicsBlock = { id: string; title: string; kind: 'bar' | 'line'; unit?: string; stack?: boolean; regionControl: string; industryControl?: string };
export type EconomicsCatalog = EconomicsMetadata & { family: EconomicsFamily; controls: EconomicsControl[];
  defaults: EconomicsSelection; years: number[]; regions?: EconomicsOption[]; minimumYear?: number; blocks?: EconomicsBlock[];
  quarterOptionsByRegionMode?: Record<string, Record<string, number[]>>;
  structureIndustries?: Record<string, Record<string, EconomicsOption[]>>; indexIndustries?: Record<string, EconomicsOption[]> };
export type EconomicsPoint = { x: string | number; y: number | null };
export type EconomicsChart = EconomicsTable & { kind: 'bar' | 'line'; stack: boolean; unit: string;
  series: { id: string; name: string; unit: string; points: EconomicsPoint[] }[]; totals?: EconomicsPoint[] };
export type EconomicsReport = EconomicsMetadata & { family: EconomicsFamily; selection: EconomicsSelection; charts: EconomicsChart[]; tables: EconomicsTable[] };

export const economicsKeys: Record<EconomicsFamily, string[]> = {
  salary: ['period', 'region', 'views', 'ytd', 'months', 'quarters'],
  ipc: ['period', 'regions', 'index_base', 'months', 'quarters'],
  accounts: ['block1_regions', 'block2_regions', 'block3_regions', 'block4_regions', 'structure_region', 'index_region', 'structure_mode', 'structure_industries', 'index_industries', 'show_total'],
};
const invalid = () => new Error('Ответ экономики API не соответствует схеме версии 1.');
type Obj = Record<string, unknown>;
function obj(value: unknown): Obj { if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid(); return value as Obj; }
function str(value: unknown): string { if (typeof value !== 'string') throw invalid(); return value; }
function id(value: unknown): string { const s = str(value); if (!s) throw invalid(); return s; }
function numeric(value: unknown): number { if (typeof value !== 'number' || !Number.isFinite(value)) throw invalid(); return value; }
function bool(value: unknown): boolean { if (typeof value !== 'boolean') throw invalid(); return value; }
function list<T>(value: unknown, read: (value: unknown) => T): T[] { if (!Array.isArray(value)) throw invalid(); return value.map(read); }
function unique<T extends { id: string | number }>(values: T[]): T[] { if (new Set(values.map(v => v.id)).size !== values.length) throw invalid(); return values; }
function dictionary<T>(value: unknown, read: (value: unknown) => T): Record<string, T> { return Object.fromEntries(Object.entries(obj(value)).map(([key, item]) => [key, read(item)])); }
function cell(value: unknown): EconomicsCell { return value === null ? null : typeof value === 'string' ? value : numeric(value); }
function option(value: unknown): EconomicsOption { const v = obj(value); return { id: typeof v.id === 'number' ? numeric(v.id) : id(v.id), label: str(v.label), ...(v.unit === undefined ? {} : { unit: str(v.unit) }) }; }
function options(value: unknown) { return unique(list(value, option)); }
function selection(value: unknown, family: EconomicsFamily): EconomicsSelection {
  const v = obj(value), result: EconomicsSelection = {};
  for (const key of economicsKeys[family]) {
    if (['ytd', 'show_total'].includes(key)) result[key] = bool(v[key]);
    else if (['period', 'region', 'index_base', 'structure_region', 'index_region', 'structure_mode'].includes(key)) result[key] = id(v[key]);
    else result[key] = list<string | number>(v[key], value => key === 'months' || key === 'quarters' ? numeric(value) : id(value));
  }
  if (family !== 'accounts' && !['year', 'quarter', 'month'].includes(String(result.period))) throw invalid();
  if (family === 'ipc' && !['month_to_month', 'ytd_to_yago'].includes(String(result.index_base))) throw invalid();
  if (family === 'accounts' && !['value', 'share'].includes(String(result.structure_mode))) throw invalid();
  for (const key of ['months', 'quarters']) if (result[key] && (result[key] as number[]).some(n => !Number.isInteger(n) || n < 1 || n > (key === 'months' ? 12 : 4))) throw invalid();
  return result;
}
function rowsTable(value: unknown): Pick<EconomicsTable, 'columns' | 'rows'> {
  const v = obj(value), columns = unique(list(v.columns, value => { const c = obj(value); return { id: id(c.id), label: str(c.label) }; }));
  const rows = list(v.rows, value => { const row = dictionary(value, cell); if (columns.some(c => !Object.hasOwn(row, c.id))) throw invalid(); return row; });
  return { columns, rows };
}
function table(value: unknown): EconomicsTable { const v = obj(value); return { id: id(v.id), title: str(v.title), ...rowsTable(v) }; }
function metadata(v: Obj): EconomicsMetadata {
  if (v.schemaVersion !== 1 || !Number.isFinite(Date.parse(str(v.generatedAt)))) throw invalid();
  const source = obj(v.source);
  return { schemaVersion: 1, version: id(v.version), generatedAt: str(v.generatedAt), source: {
    date: source.date === null ? null : str(source.date), files: list(source.files, str),
    issues: list(source.issues, value => { if (!Array.isArray(value) || value.length !== 2) throw invalid(); return [str(value[0]), str(value[1])]; }),
    fileEvidence: str(source.fileEvidence), recordedSources: list(source.recordedSources, str),
    recordedSourceEvidence: str(source.recordedSourceEvidence), dateEvidence: str(source.dateEvidence),
    downloadSummary: str(source.downloadSummary), provenance: rowsTable(source.provenance),
  } };
}
export function readEconomicsCatalog(value: unknown, family: EconomicsFamily): EconomicsCatalog {
  const v = obj(value); if (v.family !== family) throw invalid();
  const controls = unique(list(v.controls, value => {
    const c = obj(value); if (!['select', 'multiselect', 'checkbox'].includes(str(c.type))) throw invalid();
    const defaultValue = c.type === 'checkbox' ? bool(c.default) : c.type === 'multiselect' ? list(c.default, value => typeof value === 'number' ? numeric(value) : id(value)) : id(c.default);
    return { id: id(c.id), type: c.type as EconomicsControl['type'], default: defaultValue,
      ...(c.options === undefined ? {} : { options: options(c.options) }), ...(c.periods === undefined ? {} : { periods: list(c.periods, str) }),
      ...(c.optionsByRegion === undefined ? {} : { optionsByRegion: dictionary(c.optionsByRegion, options) }),
      ...(c.optionsByRegionMode === undefined ? {} : { optionsByRegionMode: dictionary(c.optionsByRegionMode, value => dictionary(value, options)) }),
    };
  }));
  if (economicsKeys[family].some(key => !controls.some(c => c.id === key))) throw invalid();
  const result: EconomicsCatalog = { ...metadata(v), family, controls, defaults: selection(v.defaults, family), years: list(v.years, numeric) };
  if (family === 'accounts') {
    result.minimumYear = numeric(v.minimumYear);
    result.structureIndustries = dictionary(v.structureIndustries, value => dictionary(value, options));
    result.indexIndustries = dictionary(v.indexIndustries, options);
    result.blocks = unique(list(v.blocks, value => {
      const b = obj(value); if (b.kind !== 'bar' && b.kind !== 'line') throw invalid();
      return { id: id(b.id), title: str(b.title), kind: b.kind, regionControl: id(b.regionControl),
        ...(b.unit === undefined ? {} : { unit: str(b.unit) }), ...(b.stack === undefined ? {} : { stack: bool(b.stack) }),
        ...(b.industryControl === undefined ? {} : { industryControl: id(b.industryControl) }) } as EconomicsBlock;
    }));
    if (result.blocks.length !== 6) throw invalid();
  } else {
    result.regions = options(v.regions);
    if (family === 'salary') result.quarterOptionsByRegionMode = dictionary(v.quarterOptionsByRegionMode, value => dictionary(value, value => list(value, numeric)));
  }
  return result;
}
export function readEconomicsReport(value: unknown, family: EconomicsFamily, expected?: EconomicsSelection): EconomicsReport {
  const v = obj(value); if (v.family !== family) throw invalid();
  const selected = selection(v.selection, family);
  if (expected && economicsKeys[family].some(key => JSON.stringify(expected[key]) !== JSON.stringify(selected[key]))) throw invalid();
  const point = (value: unknown): EconomicsPoint => { const p = obj(value); return { x: typeof p.x === 'number' ? numeric(p.x) : str(p.x), y: p.y === null ? null : numeric(p.y) }; };
  const charts = unique(list(v.charts, value => {
    const c = obj(value); if (c.kind !== 'bar' && c.kind !== 'line') throw invalid();
    return { ...table(c), kind: c.kind, stack: bool(c.stack), unit: str(c.unit),
      series: unique(list(c.series, value => { const s = obj(value); return { id: id(s.id), name: str(s.name), unit: str(s.unit), points: list(s.points, point) }; })),
      ...(c.totals === undefined ? {} : { totals: list(c.totals, point) }) } as EconomicsChart;
  }));
  return { ...metadata(v), family, selection: selected, charts, tables: unique(list(v.tables, table)) };
}

export function economicsOptions(catalog: EconomicsCatalog, key: string, selected: EconomicsSelection): EconomicsOption[] {
  if (key === 'structure_industries') return catalog.structureIndustries?.[String(selected.structure_region)]?.[String(selected.structure_mode)] ?? [];
  if (key === 'index_industries') return catalog.indexIndustries?.[String(selected.index_region)] ?? [];
  const control = catalog.controls.find(c => c.id === key);
  if (key === 'quarters' && catalog.family === 'salary') {
    const available = catalog.quarterOptionsByRegionMode?.[String(selected.region)]?.[selected.ytd ? 'ytd' : 'direct'] ?? [];
    return control?.options?.filter(o => available.includes(Number(o.id))) ?? [];
  }
  return control?.options ?? [];
}
function parameterList(params: URLSearchParams, key: string): (string | number)[] | undefined {
  if (!params.has(key)) return undefined;
  const values = params.getAll(key);
  if (values.length === 1 && values[0] === '') return [];
  if (values.length === 1 && values[0].startsWith('[')) {
    try { const parsed: unknown = JSON.parse(values[0]); if (Array.isArray(parsed) && parsed.every(v => typeof v === 'string' || typeof v === 'number')) return parsed; } catch { /* Invalid URL lists reconcile against the catalog. */ }
    return undefined;
  }
  return values;
}
export function economicsFilters(catalog: EconomicsCatalog, params: URLSearchParams): EconomicsSelection {
  const result = structuredClone(catalog.defaults);
  for (const control of catalog.controls) {
    const key = control.id;
    if (control.type === 'multiselect') continue;
    const raw = params.get(key);
    if (control.type === 'checkbox') result[key] = raw === 'true' ? true : raw === 'false' ? false : result[key];
    else if (control.options?.some(o => String(o.id) === raw)) result[key] = raw!;
  }
  for (const control of catalog.controls.filter(c => c.type === 'multiselect')) {
    const key = control.id, available = economicsOptions(catalog, key, result).map(o => o.id), supplied = parameterList(params, key);
    let fallback = (control.default as (string | number)[]).filter(v => available.includes(v));
    if (key.endsWith('_industries')) fallback = available.includes('Строительство') ? ['Строительство'] : available.slice(0, 1);
    if (key === 'quarters') fallback = available;
    if (supplied === undefined) result[key] = fallback;
    else if (!supplied.length) result[key] = [];
    else {
      const valid = [...new Set(supplied.map(v => key === 'months' || key === 'quarters' ? Number(v) : v).filter(v => available.includes(v)))];
      result[key] = valid.length ? valid : fallback;
    }
  }
  return result;
}
export function economicsQueryValue(value: EconomicsValue): string { return Array.isArray(value) ? JSON.stringify(value) : String(value); }
export function economicsRepairs(catalog: EconomicsCatalog, params: URLSearchParams, selected: EconomicsSelection): Record<string, string> {
  return Object.fromEntries(economicsKeys[catalog.family].filter(key => params.has(key) &&
    (params.getAll(key).length !== 1 || params.get(key) !== economicsQueryValue(selected[key])))
    .map(key => [key, economicsQueryValue(selected[key])]));
}
export function economicsFilterKey(family: EconomicsFamily, params: URLSearchParams): string {
  return JSON.stringify([family, ...economicsKeys[family].map(key => [key, params.getAll(key)])]);
}
export function economicsUrl(family: EconomicsFamily, selected?: EconomicsSelection, version?: string): string {
  if (!selected) return `/api/v1/economics/${family}/catalog`;
  const params = new URLSearchParams();
  for (const key of economicsKeys[family]) {
    const value = selected[key];
    if (Array.isArray(value)) { if (!value.length) params.append(key, ''); else value.forEach(item => params.append(key, String(item))); }
    else params.set(key, String(value));
  }
  if (version !== undefined) params.set('required_version', version);
  return `/api/v1/economics/${family}/${version === undefined ? 'report' : 'export'}?${params}`;
}
export async function loadEconomicsCatalog(family: EconomicsFamily, signal: AbortSignal) { return readEconomicsCatalog(await (await request(economicsUrl(family), signal)).json(), family); }
export async function loadEconomicsReport(family: EconomicsFamily, selected: EconomicsSelection, signal: AbortSignal) {
  return readEconomicsReport(await (await request(economicsUrl(family, selected), signal)).json(), family, selected);
}
export async function loadEconomicsPage(family: EconomicsFamily, params: URLSearchParams, signal: AbortSignal,
  cached?: EconomicsCatalog, onCatalog?: (catalog: EconomicsCatalog, selected: EconomicsSelection) => void) {
  let catalog = cached ?? await loadEconomicsCatalog(family, signal);
  for (let attempt = 0; attempt < 2; attempt++) {
    const selected = economicsFilters(catalog, params); onCatalog?.(catalog, selected);
    try {
      const data = await loadEconomicsReport(family, selected, signal);
      if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
      if (data.version === catalog.version) return { catalog, selection: selected, data };
      if (attempt === 1) throw new Error('Каталог и отчёт экономики имеют разные версии. Повторите загрузку.');
    } catch (error) {
      if (signal.aborted || !(error instanceof ApiError && error.status === 404 && attempt === 0)) throw error;
    }
    catalog = await loadEconomicsCatalog(family, signal);
  }
  throw invalid();
}
export function economicsTableRows(table: EconomicsTable): EconomicsCell[][] { return [table.columns.map(c => c.label), ...table.rows.map(row => table.columns.map(c => row[c.id]))]; }
export function economicsChartRows(chart: EconomicsChart): EconomicsCell[][] {
  return [['Период / год', 'Показатель', 'Единица', 'Значение'], ...chart.series.flatMap(s => s.points.map(p => [p.x, s.name, s.unit, p.y]))];
}
export function economicsTablePage(table: EconomicsTable, query: string, page: string | null, size: string | null) {
  const pageSize = [20, 50, 100].includes(Number(size)) ? Number(size) : 50, search = query.toLocaleLowerCase('ru');
  const filtered = table.rows.filter(row => table.columns.some(c => row[c.id] !== null && String(row[c.id]).toLocaleLowerCase('ru').includes(search)));
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize)), current = Math.min(pages, Math.max(1, Number.isSafeInteger(Number(page)) ? Number(page) : 1));
  return { pageSize, pages, current, total: filtered.length, shown: filtered.slice((current - 1) * pageSize, current * pageSize) };
}
const escape = (value: string) => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
function seriesColors(series: EconomicsChart['series'], theme: Theme) {
  const palette = theme === 'light' ? ['#187e50', '#9a6a0d', '#187d7c', '#8054b4', '#c45721', '#39753a', '#b14484', '#50709f', '#737c26', '#4d6c72', '#985f72', '#676373']
    : ['#2c9869', '#d6a23f', '#329d9c', '#b38cdf', '#ea8954', '#80b760', '#e37cae', '#83a5d6', '#bcc65e', '#72b2bc', '#d9a0ae', '#ada3bd'];
  const colors = new Map<string, string>(), used = new Set<number>();
  for (const name of [...new Set(series.map(s => s.name))].sort()) {
    if (name.includes('Остальные')) colors.set(name, theme === 'light' ? '#8993a1' : '#9ca5b1');
    else if (name.includes('Моск') || name === 'Строительство') colors.set(name, theme === 'light' ? '#b9404c' : '#cf5d64');
    else if (name.includes('Росси') || name === 'Всего' || name.includes('Всего по')) colors.set(name, theme === 'light' ? '#2473af' : '#398dcc');
    else {
      let hash = 0; for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
      let index = hash % palette.length;
      while (used.has(index) && used.size < palette.length) index = (index + 1) % palette.length;
      colors.set(name, used.size < palette.length ? palette[index] : `hsl(${(hash + colors.size * 137) % 360}, 55%, ${theme === 'light' ? 40 : 65}%)`);
      used.add(index);
    }
  }
  return colors;
}
export function economicsChartOption(chart: EconomicsChart, theme: Theme): EChartsCoreOption {
  const colors = seriesColors(chart.series, theme);
  const slots: { x: string | number; occurrence: number }[] = [];
  const indexed = chart.series.map(series => {
    const seen = new Map<string | number, number>();
    return series.points.map(point => { const occurrence = seen.get(point.x) ?? 0; seen.set(point.x, occurrence + 1);
      if (!slots.some(s => s.x === point.x && s.occurrence === occurrence)) slots.push({ x: point.x, occurrence });
      return { ...point, occurrence }; });
  });
  const orders = new Map<string | number, number>();
  for (const row of chart.rows) if (typeof row.year === 'number') {
    const x = row['период'] ?? row.period ?? row.year;
    const order = row.year * 100 + (typeof row.month === 'number' ? row.month : typeof row.quarter === 'number' ? row.quarter * 3 : 0);
    if (x !== null && !orders.has(x)) orders.set(x, order);
  }
  const order = (x: string | number) => orders.get(x) ?? (typeof x === 'number' ? x * 100 : /^\d{4}$/.test(x) ? Number(x) * 100 : 0);
  slots.sort((a, b) => order(a.x) - order(b.x) || a.occurrence - b.occurrence);
  const muted = theme === 'light' ? '#606975' : '#b2bac5', line = theme === 'light' ? '#e4e8ed' : '#363a40';
  return {
    grid: { left: 6, right: 16, top: 36, bottom: 12, containLabel: true },
    tooltip: { trigger: 'item', confine: true, className: 'economics-tooltip', formatter: (p: { seriesIndex: number; seriesName: string; name: string; value: number | null; dataIndex: number }) => {
      const total = chart.totals?.find(t => t.x === slots[p.dataIndex]?.x);
      return `<strong>${escape(p.name)} · ${escape(p.seriesName)}</strong><br/>${number(p.value, 2)} ${escape(chart.series[p.seriesIndex].unit)}${total ? `<br/>Всего: ${number(total.y, 2)} ${escape(chart.unit)}` : ''}`;
    } },
    xAxis: { type: 'category', data: slots.map(p => String(p.x)), boundaryGap: chart.kind === 'bar', axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: muted, fontSize: 11, hideOverlap: true, interval: 'auto', alignMinLabel: 'left', alignMaxLabel: 'right' } },
    yAxis: { type: 'value', name: chart.unit, nameTextStyle: { color: muted, align: 'left' }, axisLabel: { color: muted, fontSize: 11, formatter: (n: number) => number(n, Math.abs(n) < 10 ? 1 : 0) }, splitLine: { lineStyle: { color: line, type: 'dashed' } } },
    series: chart.series.map((series, i) => ({ name: series.name, type: chart.kind, ...(chart.stack ? { stack: 'structure' } : {}),
      data: slots.map(slot => indexed[i].find(p => p.x === slot.x && p.occurrence === slot.occurrence)?.y ?? null),
      itemStyle: { color: colors.get(series.name) }, lineStyle: { color: colors.get(series.name), width: 2, type: series.id === 'total' ? 'dashed' : 'solid' },
      symbolSize: 7, showSymbol: true, showAllSymbol: true, connectNulls: false, barMaxWidth: 40, emphasis: { focus: 'series' } })),
  };
}
