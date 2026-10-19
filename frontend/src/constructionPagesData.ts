import { ApiError, request } from './data.ts';
import { number } from './format.ts';
import { readReportTable } from './commissioningData.ts';
import { salesChartOption } from './salesData.ts';
import type { ConstructionReport, PermitChart } from './annualConstructionData';
import type { Cell } from './commissioningData';
import type { SalesChart } from './salesData';
import type { EChartsCoreOption } from 'echarts/core';
import type { Theme } from './types';

export type PermitKind = 'housing' | 'nonresidential' | 'total';
export type ConstructionRegion = 'msk' | 'rf';
export type ConstructionPagesMetadata = { schemaVersion: 1; version: string; generatedAt: string; source: { date: string | null; files: string[]; issues: [string, string][]; fileEvidence?: 'selected' | 'candidates'; dateEvidence?: string } };
export type PermitStructureSelection = { mode: 'period' | 'annual'; monthFrom: number; monthTo: number; yearFrom: number; yearTo: number };
export type PermitSelection = { permitKind: PermitKind; month: number; structure: PermitStructureSelection };
export type PermitFilters = { permitKind: PermitKind; month?: number; structure?: Partial<PermitStructureSelection> };
export type PermitCoverage = { blockId: string; year: number; months: number[]; completeMonths: number[]; annualComplete: boolean; observedThrough: string | null; evidence: string[] };
export type PermitPresentationRow = { year: number; selected: number | null; annualActual: number | null; annualForecast: number | null; basis: 'forecast' | 'annual_observed' | 'none'; difference: number | null; remainder: number | null; growth: number | null; periodComplete: boolean; annualComplete: boolean; observedThrough: string | null; evidence: string[] };
export const forecastFields = ['value', 'housing_living', 'housing_total', 'mop_embedded', 'nonresidential', 'offices', 'industry', 'social', 'trade', 'hotels', 'other'] as const;
export type PermitForecastRow = { year: number; cells: Record<string, string> } & Record<typeof forecastFields[number], number | null>;
export type PermitBreakdownRow = { year: number; periodComplete?: boolean; annualComplete?: boolean } & Record<string, number | null | boolean | undefined>;
export type PermitBreakdown = { id: string; title: string; unit: 'тыс. м²'; periodLabel: string; aggregateField: string; columns: { id: string; label: string }[]; rows: PermitBreakdownRow[]; series: { id: string; name: string; points: { x: string; y: number | null }[] }[]; notes: string[] };
export type PermitsData = ConstructionPagesMetadata & {
  contractRevision: 2;
  selection: PermitSelection;
  controls: { permitKinds: { id: PermitKind; label: string }[]; monthsByKind: Record<PermitKind, number[]>; structure: { modes: { id: 'period' | 'annual'; label: string }[]; years: number[]; months: number[]; coverage: PermitCoverage[] } };
  permits: ConstructionReport['permits'] & { presentation: { rows: PermitPresentationRow[] } };
  forecast: { unit: 'тыс. м²'; unitBasis: 'assumed_thousand_m2'; source: { file: string | null; sheet: 'Прогнозы' }; rows: PermitForecastRow[]; notes: string[] };
  breakdowns: PermitBreakdown[]; notes: string[];
};
export type DomRfPeriod = { id: string | null; label: string; reportDate: string | null };
export type ConstructionObservation = { period: string; label: string; reportDate: string | null; living: number | null; total: number | null; sourceFile: string };
export type RealizationObservation = { period: string; label: string; reportDate: string | null; metrics: Record<string, number | null>; sourceFile: string };
export type DomRfData = ConstructionPagesMetadata & {
  region: { id: ConstructionRegion; label: string };
  construction: { period: DomRfPeriod | null; metrics: { living: number | null; total: number | null }; history: ConstructionObservation[] };
  sales: { period: DomRfPeriod | null; metrics: Record<string, number | null> | null; history: RealizationObservation[] };
  notes: string[];
};

type Obj = Record<string, unknown>;
const invalid = () => new Error('Ответ API строительства не соответствует схеме версии 1.');
const deploymentMismatch = () => new Error('Версия frontend новее backend. Нужно завершить обновление backend, затем обновить страницу.');
function obj(v: unknown): Obj { if (!v || typeof v !== 'object' || Array.isArray(v)) throw invalid(); return v as Obj; }
function str(v: unknown): string { if (typeof v !== 'string') throw invalid(); return v; }
function id(v: unknown): string { const s = str(v); if (!s.trim()) throw invalid(); return s; }
function num(v: unknown): number | null { if (v === null) return null; if (typeof v !== 'number' || !Number.isFinite(v)) throw invalid(); return v; }
function int(v: unknown, min = 1, max = 9999): number { const n = num(v); if (n === null || !Number.isInteger(n) || n < min || n > max) throw invalid(); return n; }
function list<T>(v: unknown, read: (v: unknown) => T): T[] { if (!Array.isArray(v)) throw invalid(); return v.map(read); }
function distinct<T>(values: T[], key: (v: T) => string | number): T[] { if (new Set(values.map(key)).size !== values.length) throw invalid(); return values; }
function sourceDate(v: unknown): string | null {
  if (v === null) return null;
  const s = id(v), calendar = /^(\d{4}-\d{2}-\d{2})(?:$|T)/.exec(s);
  if (!calendar || !Number.isFinite(Date.parse(s)) || new Date(`${calendar[1]}T00:00:00Z`).toISOString().slice(0, 10) !== calendar[1]) throw invalid();
  return s;
}
function periodId(v: unknown): string { const s = id(v); if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(s)) throw invalid(); return s; }
function numericRecord(v: unknown) { return Object.fromEntries(Object.entries(obj(v)).map(([key, value]) => [key, num(value)])); }
function metadata(v: Obj): ConstructionPagesMetadata {
  if (v.schemaVersion !== 1) throw invalid();
  const s = obj(v.source), generatedAt = sourceDate(v.generatedAt);
  if (!generatedAt || !generatedAt.includes('T')) throw invalid();
  if (s.fileEvidence !== undefined && s.fileEvidence !== 'selected' && s.fileEvidence !== 'candidates') throw invalid();
  return { schemaVersion: 1, version: id(v.version), generatedAt, source: { date: sourceDate(s.date), files: list(s.files, str),
    ...(s.fileEvidence === undefined ? {} : { fileEvidence: s.fileEvidence }), ...(s.dateEvidence === undefined ? {} : { dateEvidence: str(s.dateEvidence) }),
    issues: list(s.issues, value => {
      if (Array.isArray(value)) { if (value.length !== 2) throw invalid(); return [str(value[0]), str(value[1])]; }
      const issue = obj(value); return [str(issue.level), str(issue.message)];
    }) } };
}
export function permitKind(v: unknown): PermitKind { if (v !== 'housing' && v !== 'nonresidential' && v !== 'total') throw invalid(); return v; }
export function constructionRegion(params: URLSearchParams): ConstructionRegion { return params.get('region') === 'rf' ? 'rf' : 'msk'; }
export const permitStructureParams = { mode: 'structure_mode', monthFrom: 'structure_month_from', monthTo: 'structure_month_to', yearFrom: 'structure_year_from', yearTo: 'structure_year_to' } as const;
export function permitRequestFilters(params: URLSearchParams): PermitFilters {
  const kind = params.get('permit_kind'), month = params.get('month');
  const structure: Partial<PermitStructureSelection> = {};
  const mode = params.get(permitStructureParams.mode);
  if (mode === 'period' || mode === 'annual') structure.mode = mode;
  for (const key of ['monthFrom', 'monthTo', 'yearFrom', 'yearTo'] as const) {
    const value = params.get(permitStructureParams[key]);
    if ((key.startsWith('month') ? /^(?:[1-9]|1[0-2])$/ : /^[1-9]\d{0,3}$/).test(value ?? '')) structure[key] = Number(value);
  }
  return { permitKind: kind === 'nonresidential' || kind === 'total' ? kind : 'housing', ...(/^(?:[1-9]|1[0-2])$/.test(month ?? '') ? { month: Number(month) } : {}), ...(Object.keys(structure).length ? { structure } : {}) };
}
export function permitKindChange(controls: PermitsData['controls'], month: number, nextKind: PermitKind): Record<string, string | null> {
  return { permit_kind: nextKind, month: controls.monthsByKind[nextKind].includes(month) ? String(month) : null };
}
export function legacySalesForecastSeed(legacySales: boolean, params: URLSearchParams): { region: ConstructionRegion; period: string } | null {
  const period = params.get('period');
  return legacySales && !params.has('forecast_period') && period && /^\d{4}-(0[1-9]|1[0-2])$/.test(period) ? { region: constructionRegion(params), period } : null;
}
export function domRfForecastSelection(params: URLSearchParams, seed: ReturnType<typeof legacySalesForecastSeed>) {
  const pending = seed?.region === constructionRegion(params) && !params.has('forecast_period') ? seed : null;
  return { period: params.get('forecast_period') || pending?.period || null, seed: pending };
}
export function permitsUrl(selection: PermitFilters, version?: string) {
  const p = new URLSearchParams({ permit_kind: selection.permitKind });
  if (selection.month !== undefined) p.set('month', String(selection.month));
  for (const key of Object.keys(permitStructureParams) as (keyof PermitStructureSelection)[]) {
    if (selection.structure?.[key] !== undefined) p.set(permitStructureParams[key], String(selection.structure[key]));
  }
  if (version !== undefined) { if (!version || selection.month === undefined) throw invalid(); p.set('required_version', version); }
  return `/api/v1/construction/permits${version === undefined ? '' : '/export'}?${p}`;
}
export function domRfUrl(region: ConstructionRegion, version?: string) {
  const p = new URLSearchParams({ region });
  if (version !== undefined) { if (!version) throw invalid(); p.set('required_version', version); }
  return `/api/v1/construction/domrf${version === undefined ? '' : '/export'}?${p}`;
}
function readPermitTable(p: Obj) {
  if (p.region !== 'Москва' || p.unit !== 'тыс. м²') throw invalid();
  const table = readReportTable({ ...p, id: p.id ?? 'permits', title: p.title ?? 'Разрешения на строительство' });
  let chart: PermitChart | null = null;
  if (p.chart !== null) {
    const raw = obj(p.chart), x = list(raw.x, str);
    const points = (v: unknown) => { const a = list(v, v => ({ value: num(obj(v).value) })); if (a.length !== x.length) throw invalid(); return a; };
    chart = { x, period: points(raw.period), remainder: points(raw.remainder), totals: points(raw.totals), growth: points(raw.growth) };
    if (x.length !== table.rows.length || x.some((x, i) => x !== String(table.rows[i]['Год']))) throw invalid();
  }
  return { ...table, region: p.region, unit: p.unit, periodLabel: str(p.periodLabel), chart };
}
function readPermitForecast(value: unknown): PermitsData['forecast'] {
  const f = obj(value), fs = obj(f.source);
  if (f.unit !== 'тыс. м²' || f.unitBasis !== 'assumed_thousand_m2' || fs.sheet !== 'Прогнозы') throw invalid();
  const rows = distinct(list(f.rows, value => {
    const row = obj(value), fields = Object.fromEntries(forecastFields.map(key => [key, num(row[key])])) as Record<typeof forecastFields[number], number | null>;
    return { year: int(row.year), ...fields, cells: Object.fromEntries(Object.entries(obj(row.cells)).map(([k, v]) => [k, str(v)])) };
  }), row => row.year);
  return { unit: f.unit, unitBasis: f.unitBasis, source: { file: fs.file === null ? null : str(fs.file), sheet: fs.sheet }, rows, notes: list(f.notes, str) };
}
function previousPermitsApi(v: Obj): boolean {
  try {
    const s = obj(v.selection), c = obj(v.controls), p = obj(v.permits);
    if (v.schemaVersion !== 1 || v.contractRevision !== undefined && v.contractRevision !== 1
      || Object.hasOwn(s, 'structure') || Object.hasOwn(c, 'structure') || Object.hasOwn(p, 'presentation')) return false;
    // Recognize the previous endpoint, not arbitrary missing fields or an older schema version.
    metadata(v);
    const kind = permitKind(s.permitKind), month = int(s.month, 1, 12);
    const kinds = list(c.permitKinds, value => { const k = obj(value); str(k.label); return permitKind(k.id); });
    if (kinds.join() !== 'housing,nonresidential,total') return false;
    const months = obj(c.monthsByKind);
    for (const k of kinds) distinct(list(months[k], value => int(value, 1, 12)), n => n);
    if (!list(months[kind], value => int(value, 1, 12)).includes(month)) return false;
    readPermitTable(p);
    readPermitForecast(v.forecast);
    list(v.notes, str);
    return list(v.breakdowns, value => { const b = obj(value); return !Object.hasOwn(b, 'aggregateField')
      && b.unit === 'тыс. м²' && Array.isArray(b.rows) && Array.isArray(b.columns) && Array.isArray(b.series); }).every(Boolean);
  } catch { return false; }
}
export function readPermitsData(value: unknown, expected?: PermitFilters): PermitsData {
  const bool = (v: unknown) => { if (typeof v !== 'boolean') throw invalid(); return v; };
  const mode = (v: unknown): PermitStructureSelection['mode'] => { if (v !== 'period' && v !== 'annual') throw invalid(); return v; };
  const monthList = (v: unknown) => distinct(list(v, v => int(v, 1, 12)), n => n);
  const v = obj(value);
  if (previousPermitsApi(v)) throw deploymentMismatch();
  if (v.contractRevision !== 2) throw invalid();
  const s = obj(v.selection), ss = obj(s.structure);
  const structure = { mode: mode(ss.mode), monthFrom: int(ss.monthFrom, 1, 12), monthTo: int(ss.monthTo, 1, 12), yearFrom: int(ss.yearFrom), yearTo: int(ss.yearTo) };
  if (structure.monthFrom > structure.monthTo || structure.yearFrom > structure.yearTo) throw invalid();
  const selection = { permitKind: permitKind(s.permitKind), month: int(s.month, 1, 12), structure };
  if (expected && (expected.permitKind !== selection.permitKind || expected.month !== undefined && expected.month !== selection.month)) throw invalid();
  if (expected?.structure && (Object.keys(expected.structure) as (keyof PermitStructureSelection)[]).some(key => expected.structure![key] !== structure[key])) throw invalid();
  const c = obj(v.controls), kinds = list(c.permitKinds, value => { const k = obj(value); return { id: permitKind(k.id), label: str(k.label) }; });
  if (kinds.map(k => k.id).join() !== 'housing,nonresidential,total') throw invalid();
  const rawMonths = obj(c.monthsByKind), monthsByKind = Object.fromEntries(kinds.map(k => [k.id, distinct(list(rawMonths[k.id], v => int(v, 1, 12)), n => n)])) as Record<PermitKind, number[]>;
  if (monthsByKind[selection.permitKind].length && !monthsByKind[selection.permitKind].includes(selection.month)) throw invalid();
  const sc = obj(c.structure), structureControls = {
    modes: distinct(list(sc.modes, value => { const m = obj(value); return { id: mode(m.id), label: str(m.label) }; }), m => m.id),
    years: distinct(list(sc.years, v => int(v)), n => n), months: monthList(sc.months),
    coverage: distinct(list(sc.coverage, value => {
      const row = obj(value), months = monthList(row.months), completeMonths = monthList(row.completeMonths), annualComplete = bool(row.annualComplete);
      if (completeMonths.some(m => !months.includes(m)) || annualComplete && completeMonths.length !== 12) throw invalid();
      return { blockId: id(row.blockId), year: int(row.year), months, completeMonths, annualComplete, observedThrough: sourceDate(row.observedThrough), evidence: list(row.evidence, str) };
    }), row => `${row.blockId}:${row.year}`),
  };
  if (!structureControls.modes.some(m => m.id === structure.mode) || structureControls.years.length && (!structureControls.years.includes(structure.yearFrom) || !structureControls.years.includes(structure.yearTo))) throw invalid();
  const p = obj(v.permits), table = readPermitTable(p);
  const presentation = { rows: distinct(list(obj(p.presentation).rows, (value): PermitPresentationRow => {
    const row = obj(value), basis = row.basis;
    if (basis !== 'forecast' && basis !== 'annual_observed' && basis !== 'none') throw invalid();
    const annualActual = num(row.annualActual), annualForecast = num(row.annualForecast), annualComplete = bool(row.annualComplete);
    if (annualActual !== null && !annualComplete) throw invalid();
    if (basis !== (annualForecast !== null ? 'forecast' : annualActual !== null && annualComplete ? 'annual_observed' : 'none')) throw invalid();
    return { year: int(row.year), selected: num(row.selected), annualActual, annualForecast, basis, difference: num(row.difference), remainder: num(row.remainder), growth: num(row.growth), periodComplete: bool(row.periodComplete), annualComplete, observedThrough: sourceDate(row.observedThrough), evidence: list(row.evidence, str) };
  }), row => row.year) };
  const forecast = readPermitForecast(v.forecast);
  const breakdowns = distinct(list(v.breakdowns, value => {
    const b = obj(value); if (b.unit !== 'тыс. м²') throw invalid();
    const columns = distinct(list(b.columns, value => { const col = obj(value); return { id: id(col.id), label: str(col.label) }; }), c => c.id);
    const aggregateField = id(b.aggregateField);
    if (!columns.some(c => c.id === aggregateField)) throw invalid();
    const rows = distinct(list(b.rows, value => {
      const raw = obj(value), row = Object.fromEntries(Object.entries(raw).map(([key, value]) => [key, key === 'periodComplete' || key === 'annualComplete' ? bool(value) : num(value)])) as PermitBreakdownRow;
      int(row.year); if (columns.some(c => !Object.hasOwn(row, c.id) || typeof row[c.id] === 'boolean')) throw invalid(); return row;
    }), row => row.year);
    const series = distinct(list(b.series, value => {
      const s = obj(value), key = id(s.id), points = list(s.points, value => { const p = obj(value); return { x: str(p.x), y: num(p.y) }; });
      if (!columns.some(c => c.id === key) || points.length !== rows.length || points.some((p, i) => p.x !== String(rows[i].year) || p.y !== rows[i][key])) throw invalid();
      return { id: key, name: str(s.name), points };
    }), s => s.id);
    return { id: id(b.id), title: str(b.title), unit: 'тыс. м²' as const, periodLabel: str(b.periodLabel), aggregateField, columns, rows, series, notes: list(b.notes, str) };
  }), b => b.id);
  return { ...metadata(v), contractRevision: 2, selection, controls: { permitKinds: kinds, monthsByKind, structure: structureControls }, permits: { ...table, presentation }, forecast, breakdowns, notes: list(v.notes, str) };
}
function readPeriod(value: unknown): DomRfPeriod | null { if (value === null) return null; const p = obj(value); return { id: p.id === null ? null : periodId(p.id), label: str(p.label), reportDate: sourceDate(p.reportDate) }; }
export function readDomRfData(value: unknown, expected: ConstructionRegion): DomRfData {
  const v = obj(value), r = obj(v.region), c = obj(v.construction), cm = obj(c.metrics), s = obj(v.sales);
  if (r.id !== expected) throw invalid();
  const constructionHistory = distinct(list(c.history, value => { const h = obj(value); return { period: periodId(h.period), label: str(h.label), reportDate: sourceDate(h.reportDate), living: num(h.living), total: num(h.total), sourceFile: str(h.sourceFile) }; }), h => h.period);
  const salesHistory = distinct(list(s.history, value => { const h = obj(value); return { period: periodId(h.period), label: str(h.label), reportDate: sourceDate(h.reportDate), metrics: numericRecord(h.metrics), sourceFile: str(h.sourceFile) }; }), h => h.period);
  return { ...metadata(v), region: { id: expected, label: str(r.label) }, construction: { period: readPeriod(c.period), metrics: { living: num(cm.living), total: num(cm.total) }, history: constructionHistory }, sales: { period: readPeriod(s.period), metrics: s.metrics === null ? null : numericRecord(s.metrics), history: salesHistory }, notes: list(v.notes, str) };
}
export async function loadPermitsData(selection: PermitFilters, signal: AbortSignal) { return readPermitsData(await (await request(permitsUrl(selection), signal)).json(), selection); }
export function reconcilePermitFilters(selection: PermitFilters, discovery: PermitsData): PermitSelection {
  const defaults = discovery.selection, controls = discovery.controls.structure, requested = selection.structure;
  const structure = { ...defaults.structure };
  if (requested) {
    if (controls.modes.some(m => m.id === requested.mode)) structure.mode = requested.mode!;
    for (const key of ['monthFrom', 'monthTo', 'yearFrom', 'yearTo'] as const) {
      const values = key.startsWith('month') ? controls.months : controls.years;
      if (requested[key] !== undefined && values.includes(requested[key]!)) structure[key] = requested[key]!;
    }
    if (structure.monthFrom > structure.monthTo) { structure.monthFrom = defaults.structure.monthFrom; structure.monthTo = defaults.structure.monthTo; }
    if (structure.yearFrom > structure.yearTo) { structure.yearFrom = defaults.structure.yearFrom; structure.yearTo = defaults.structure.yearTo; }
  }
  return { permitKind: selection.permitKind, month: selection.month !== undefined && discovery.controls.monthsByKind[selection.permitKind].includes(selection.month) ? selection.month : defaults.month, structure };
}
export async function loadPermitsSelection(selection: PermitFilters, signal: AbortSignal): Promise<PermitsData> {
  try { return await loadPermitsData(selection, signal); }
  catch (error) {
    if (signal.aborted || !(error instanceof ApiError) || error.status !== 404 || selection.month === undefined && !selection.structure) throw error;
    // Discover once, then fetch the compatible view directly, never recursively.
    const discovery = await loadPermitsData({ permitKind: selection.permitKind }, signal);
    if (signal.aborted) throw error;
    const canonical = reconcilePermitFilters(selection, discovery);
    return permitsUrl(canonical) === permitsUrl(discovery.selection) ? discovery : loadPermitsData(canonical, signal);
  }
}
export async function loadDomRfData(region: ConstructionRegion, signal: AbortSignal) { return readDomRfData(await (await request(domRfUrl(region), signal)).json(), region); }

export const exactNumber = (n: number | null | undefined) => n == null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: 20 });
export const forecastBasisNote = 'Единица прогноза принята как тыс. м² (assumed_thousand_m2): на листе «Прогнозы» нет явного заголовка единицы. Прогноз годовой и не пересчитывается по выбранному месяцу.';
export const escapeHtml = (s: string) => s.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
export { permitsMainOption, permitsMainRows, breakdownChart, breakdownOption } from './permitPresentation.ts';
export function observedRange<T extends { period: string }>(history: T[], from: string | null, to: string | null) {
  const sorted = [...history].sort((a, b) => a.period.localeCompare(b.period));
  const start = sorted.some(h => h.period === from) ? from! : sorted[0]?.period ?? '';
  const end = sorted.some(h => h.period === to) ? to! : sorted.at(-1)?.period ?? '';
  return { from: start <= end ? start : end, to: start <= end ? end : start, rows: sorted.filter(h => h.period >= (start <= end ? start : end) && h.period <= (start <= end ? end : start)) };
}
export function constructionHistoryChart(history: ConstructionObservation[]): SalesChart {
  return { id: 'domrf-construction', title: 'Динамика текущего строительства по ДДУ', kind: 'line', unit: 'млн м²', series: [{ id: 'living', name: 'Жилая площадь' }, { id: 'total', name: 'Общая площадь' }].map(s => ({ ...s, unit: 'млн м²', points: history.map(h => ({ x: h.period, y: h[s.id as 'living' | 'total'] === null ? null : h[s.id as 'living' | 'total']! / 1000 })) })) };
}
export function constructionHistoryRows(history: ConstructionObservation[]): Cell[][] { return [['Период', 'Подпись периода', 'Дата отчёта', 'Жилая площадь, тыс. м²', 'Общая площадь, тыс. м²', 'Файл'], ...history.map(h => [h.period, h.label, h.reportDate, h.living, h.total, h.sourceFile])]; }
export const realizationStatuses = [{ id: 'sold', label: 'Проданная площадь', color: '#2c9869' }, { id: 'unsold', label: 'Непроданная площадь', color: '#398dcc' }, { id: 'sales_not_open', label: 'Продажи не открыты', color: '#8993a1' }] as const;
export function realizationHistoryOption(history: RealizationObservation[], theme: Theme): EChartsCoreOption {
  const chart: SalesChart = { id: 'domrf-realization', title: 'Реализация квартир по месяцам', kind: 'bar', unit: 'млн м²', series: realizationStatuses.map(s => ({ id: s.id, name: s.label, unit: 'млн м²', points: history.map(h => ({ x: h.period, y: h.metrics[`${s.id}_thousand_m2`] == null ? null : h.metrics[`${s.id}_thousand_m2`]! / 1000 })) })) };
  const option = salesChartOption(chart, theme);
  return { ...option, series: (option.series as object[]).map((s, i) => ({ ...s, stack: 'realization', itemStyle: { color: realizationStatuses[i].color }, labelLayout: { hideOverlap: true }, label: { show: true, position: 'inside', color: '#ffffff', fontSize: 11, formatter: (p: { dataIndex: number }) => {
    const h = history[p.dataIndex], status = realizationStatuses[i], area = h.metrics[`${status.id}_thousand_m2`], pct = h.metrics[`${status.id}_pct`];
    return area == null || area === 0 ? '' : `${number(area / 1000, 2)}${pct == null ? '' : `\n${number(pct, 1)} %`}`;
  } } })), tooltip: { trigger: 'item', confine: true, className: 'construction-pages-tooltip', formatter: (p: { dataIndex: number; seriesIndex: number }) => {
    const h = history[p.dataIndex], s = realizationStatuses[p.seriesIndex], area = h.metrics[`${s.id}_thousand_m2`], pct = h.metrics[`${s.id}_pct`];
    return `<strong>${escapeHtml(h.label)} · ${escapeHtml(s.label)}</strong><br/>${exactNumber(area == null ? null : area / 1000)} млн м²<br/>Исходная площадь: ${exactNumber(area)} тыс. м²${pct == null ? '' : `<br/>Доля источника: ${exactNumber(pct)} %`}${h.reportDate ? `<br/>Дата отчёта: ${escapeHtml(h.reportDate)}` : ''}`;
  } } };
}
export function realizationHistoryRows(history: RealizationObservation[]): Cell[][] {
  const fields = [...new Set(history.flatMap(h => Object.keys(h.metrics)))];
  return [['Период', 'Подпись периода', 'Дата отчёта', 'Файл', ...fields], ...history.map(h => [h.period, h.label, h.reportDate, h.sourceFile, ...fields.map(f => h.metrics[f] ?? null)])];
}
export function realizationDonutOption(label: string, pct: number | null, area: number | null, color: string, theme: Theme): EChartsCoreOption {
  const valid = pct !== null && pct >= 0 && pct <= 100;
  return { tooltip: { trigger: 'item', confine: true, formatter: () => `<strong>${escapeHtml(label)}</strong><br/>Доля источника: ${exactNumber(pct)} %<br/>${exactNumber(area)} тыс. м²` }, graphic: [{ type: 'text', left: 'center', top: '43%', style: { text: pct === null ? '—' : `${number(pct, 1)} %`, font: '600 21px Segoe UI', fill: theme === 'light' ? '#252a33' : '#edf0f3' } }], series: [{ type: 'pie', name: label, radius: ['65%', '85%'], label: { show: false }, data: valid ? [{ name: label, value: pct, itemStyle: { color } }, { name: 'Остальная доля', value: 100 - pct!, itemStyle: { color: theme === 'light' ? '#e5e8ed' : '#35393f' }, tooltip: { formatter: () => 'Дополнение до 100 % для отображения доли, не отдельный показатель источника.' } }] : [] }] };
}
export function readinessChartOption(chart: SalesChart, theme: Theme): EChartsCoreOption {
  const option = salesChartOption(chart, theme);
  return chart.id === 'monthly_percent' ? { ...option, yAxis: { ...(option.yAxis as object), scale: true } } : option;
}
