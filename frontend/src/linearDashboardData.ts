import { request } from './data.ts';
import { chartNumberFormatter, number } from './format.ts';
import type { EChartsCoreOption } from 'echarts/core';
import type { Theme } from './types';

export const linearMetricIds = ['roads', 'bridges', 'crossings', 'metro_stations', 'metro_tunnels', 'depots'] as const;
export type LinearMetricId = typeof linearMetricIds[number];
export type LinearYear = { year: number; fact: number | null; factKind: 'annual' | 'partial' | null; factPeriod: string | null; factSource: string | null; plan: number | null; forecast: number | null };
export type LinearMetric = { id: LinearMetricId; label: string; unit: string; fact: number | null; factPeriod: string | null; factSource: string | null; annualPlan: number | null; forecast: number | null; target: number | null; targetKind: 'plan' | 'forecast' | null; percent: number | null; remaining: number | null; years: LinearYear[] };
export type LinearDashboard = { schemaVersion: 1; version: string; generatedAt: string; currentYear: number; metrics: LinearMetric[]; sources: { id: string; file: string | null; date: string | null; sheet: string | null }[]; notes: string[] };
type ObjectValue = Record<string, unknown>;
const object = (v: unknown): ObjectValue => v !== null && typeof v === 'object' && !Array.isArray(v) ? v as ObjectValue : {};
const nullableNumber = (v: unknown) => v === null || typeof v === 'number' && Number.isFinite(v) && v >= 0;
const nullableString = (v: unknown) => v === null || typeof v === 'string';
const year = (v: unknown) => typeof v === 'number' && Number.isInteger(v) && v >= 1900 && v <= 2200;
const invalid = () => new Error('Данные линейных объектов не соответствуют схеме дашборда.');
const evidence = (v: ObjectValue, staticDepotHistory = false) => nullableString(v.factPeriod) && nullableString(v.factSource) && (v.fact === null ? v.factPeriod === null && v.factSource === null : typeof v.factPeriod === 'string' && (['dsti', 'monitoring'].includes(String(v.factSource)) || staticDepotHistory && v.factSource === 'static_depots'));
export function readLinearDashboard(value: unknown): LinearDashboard {
  const raw = object(value);
  if (raw.schemaVersion !== 1 || typeof raw.version !== 'string' || !raw.version || typeof raw.generatedAt !== 'string' || !Number.isFinite(Date.parse(raw.generatedAt)) || !year(raw.currentYear) || !Array.isArray(raw.metrics) || raw.metrics.length !== 6 || !Array.isArray(raw.sources) || !Array.isArray(raw.notes) || raw.notes.some(v => typeof v !== 'string')) throw invalid();
  const sourceIds: string[] = [];
  for (const value of raw.sources) {
    const source = object(value);
    if (typeof source.id !== 'string' || !['dsti', 'monitoring', 'forecast', 'static_depots'].includes(source.id) || sourceIds.includes(source.id) || !['file', 'date', 'sheet'].every(key => nullableString(source[key])) || source.date !== null && !/^\d{4}-\d{2}-\d{2}$/.test(String(source.date))) throw invalid();
    sourceIds.push(source.id);
  }
  if (!['dsti', 'monitoring', 'forecast'].every(id => sourceIds.includes(id))) throw invalid();
  for (const [index, value] of raw.metrics.entries()) {
    const metric = object(value);
    if (metric.id !== linearMetricIds[index] || typeof metric.label !== 'string' || !metric.label || metric.unit !== (['roads', 'metro_tunnels'].includes(String(metric.id)) ? 'км' : 'ед.') || !['fact', 'annualPlan', 'forecast', 'target', 'percent', 'remaining'].every(key => nullableNumber(metric[key])) || ![null, 'plan', 'forecast'].includes(metric.targetKind as null) || !evidence(metric) || !Array.isArray(metric.years)) throw invalid();
    const target = metric.forecast;
    const targetKind = metric.forecast !== null ? 'forecast' : null;
    const percent = metric.fact !== null && typeof target === 'number' && target > 0 ? Number(metric.fact) / target * 100 : null;
    const remaining = metric.fact !== null && typeof target === 'number' ? Math.max(0, target - Number(metric.fact)) : null;
    if (metric.target !== target || metric.targetKind !== targetKind || metric.percent !== percent || metric.remaining !== remaining) throw invalid();
    let previous = 0;
    for (const value of metric.years) {
      const row = object(value);
      const staticDepotHistory = sourceIds.includes('static_depots') && metric.id === 'depots' && Number(row.year) < Number(raw.currentYear) && row.factKind === 'annual';
      if (!year(row.year) || Number(row.year) <= previous || !['fact', 'plan', 'forecast'].every(key => nullableNumber(row[key])) || ![null, 'annual', 'partial'].includes(row.factKind as null) || !evidence(row, staticDepotHistory)) throw invalid();
      if (row.fact === null ? row.factKind !== null : row.factKind !== (Number(row.year) === raw.currentYear ? 'partial' : 'annual')) throw invalid();
      if (Number(row.year) > Number(raw.currentYear) && row.fact !== null || Number(row.year) < Number(raw.currentYear) && row.forecast !== null) throw invalid();
      previous = Number(row.year);
    }
    const current = metric.years.map(object).find(row => row.year === raw.currentYear);
    if (metric.fact !== (current?.fact ?? null) || metric.annualPlan !== (current?.plan ?? null) || metric.forecast !== (current?.forecast ?? null)) throw invalid();
  }
  return raw as LinearDashboard;
}

export const linearDashboardUrl = (version?: string) => `/api/v1/linear/dashboard${version ? `/export?${new URLSearchParams({ required_version: version })}` : ''}`;
export async function loadLinearDashboard(signal: AbortSignal) { return readLinearDashboard(await (await request(linearDashboardUrl(), signal)).json()); }
export const linearValue = (value: number | null, unit: string) => number(value, unit === 'км' ? 1 : 0);
export function linearYearRows(metric: LinearMetric): (string | number | null)[][] {
  return [['Год', `Факт, ${metric.unit}`, 'Период факта', 'Источник факта', `Прогноз, ${metric.unit}`], ...metric.years.map(row => [row.year, row.fact, row.factPeriod, row.factSource, row.forecast])];
}
const escape = (s: string) => s.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
export function linearHistoryOption(metric: LinearMetric, currentYear: number, theme: Theme, width = 1000): EChartsCoreOption {
  const muted = theme === 'light' ? '#737b87' : '#a3aab5', line = theme === 'light' ? '#edf0f3' : '#363a40';
  const palette: Record<LinearMetricId, string> = { roads: '#367ba4', bridges: '#448a76', crossings: '#a77b32', metro_stations: '#b75160', metro_tunnels: '#6e7e45', depots: '#697c92' };
  const color = palette[metric.id];
  const slots = [...metric.years.filter(row => row.year <= currentYear).map(row => ({ row, forecast: false })),
    ...metric.years.filter(row => row.year >= currentYear && row.forecast !== null).map(row => ({ row, forecast: true }))];
  const values = slots.map(slot => slot.forecast ? slot.row.forecast : slot.row.fact);
  const format = chartNumberFormatter(values, metric.unit === 'км' ? 1 : 0);
  const fontSize = 9, distance = 5;
  // Reserve the full rotated text extent, even when the largest bar reaches the axis ceiling.
  const labelWidth = Math.max(1, ...values.map(value => value === null ? 0 : format(value).length)) * fontSize * 0.7;
  const vertical = width <= 600 || (width - 80) / Math.max(1, slots.length) < labelWidth + 8;
  const label = { show: true, position: 'top', distance, rotate: vertical ? 90 : 0, align: vertical ? 'left' : 'center', verticalAlign: vertical ? 'middle' : 'bottom', color: muted, fontSize, lineHeight: fontSize, formatter: (p: { value: number | null }) => p.value === null ? '' : format(p.value) };
  return {
    grid: { left: 8, right: 12, top: Math.ceil(24 + distance + (vertical ? labelWidth : 12)), bottom: 8, containLabel: true },
    tooltip: { trigger: 'item', confine: true, formatter: (p: { name: string; value: number | null; seriesName: string; dataIndex: number }) => {
      const row = slots[p.dataIndex].row;
      return `<strong>${escape(p.name)} · ${escape(p.seriesName)}</strong><br/>${format(p.value)} ${escape(metric.unit)}${p.seriesName === 'Факт' && row.factPeriod ? `<br/>${escape(row.factPeriod)}` : ''}`;
    } },
    xAxis: { type: 'category', data: slots.map(({ row, forecast }) => forecast ? `${row.year}\nпрогноз` : row.year === currentYear ? `${row.year}\nфакт` : String(row.year)), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: muted, interval: 0, hideOverlap: true, fontSize: 10, lineHeight: 14 } },
    yAxis: { type: 'value', name: metric.unit, min: 0, minInterval: metric.unit === 'км' ? undefined : 1, nameTextStyle: { color: muted, align: 'right' }, axisLabel: { color: muted, formatter: format }, splitLine: { lineStyle: { color: line, type: 'dashed' } } },
    series: [
      { name: 'Факт', type: 'bar', barMaxWidth: 38, barGap: '-100%', label, labelLayout: { hideOverlap: false }, itemStyle: { color }, data: slots.map(slot => slot.forecast ? null : slot.row.fact) },
      { name: 'Прогноз', type: 'bar', barMaxWidth: 38, barGap: '-100%', label: { ...label }, labelLayout: { hideOverlap: false }, itemStyle: { color: theme === 'light' ? '#fafafa' : '#282b30', borderColor: color, borderWidth: 1.5, borderType: 'dashed', decal: { symbol: 'rect', dashArrayX: [1, 0], dashArrayY: [2, 4], rotation: -Math.PI / 4, color: `${color}88` } }, data: slots.map(slot => slot.forecast ? slot.row.forecast : null) },
    ],
    media: [{ query: { maxWidth: 600 }, option: { xAxis: { axisLabel: { rotate: 90, fontSize: 9, hideOverlap: false, formatter: (value: string) => value.replaceAll('\n', ' ') } } } }],
  };
}
