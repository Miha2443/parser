import { number } from './format.ts';
import type { EChartsCoreOption } from 'echarts/core';
import type { Cell } from './commissioningData';
import type { PermitBreakdown, PermitCoverage, PermitPresentationRow, PermitsData } from './constructionPagesData';
import type { SalesChart } from './salesData';
import type { Theme } from './types';

const escape = (value: string) => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const measure = (value: number) => number(value, 1);
const palette = ['#318dca', '#db9a36', '#359a79', '#ba6a87', '#7f83bd', '#657f89'];
export const permitPeriodName = 'Выбранный период';
export const permitRemainderName = 'Остаток до годового ориентира';
export type PermitChartView = { width?: number; height?: number; hidden?: string[] };
type PlotSeries = { id: string; name: string; color: string; data: (number | null)[] };
type Hover = { dataIndex: number; seriesIndex?: number };

export function permitAnnualBasis(row: PermitPresentationRow) {
  if (row.annualForecast !== null) return { value: row.annualForecast, kind: 'forecast' as const, label: 'Годовой прогноз' };
  if (row.annualComplete && row.annualActual !== null) return { value: row.annualActual, kind: 'annual_observed' as const, label: 'Подтверждённый итог января–декабря' };
  return null;
}
export function permitRemainder(row: PermitPresentationRow) {
  const basis = permitAnnualBasis(row);
  return basis === null || row.selected === null ? null : Math.max(0, basis.value - row.selected);
}
export function permitGrowth(rows: PermitPresentationRow[], row: PermitPresentationRow) {
  const previous = rows.find(r => r.year === row.year - 1);
  return row.periodComplete && previous?.periodComplete && row.selected !== null && previous.selected !== null && previous.selected !== 0 ? row.growth : null;
}
export function permitCoverageComplete(coverage: PermitCoverage | undefined, mode: 'annual' | 'period', from: number, to: number) {
  if (from < 1 || to > 12 || from > to) return false;
  return !!coverage && (mode === 'annual' ? coverage.annualComplete : Array.from({ length: to - from + 1 }, (_, i) => from + i).every(m => coverage.completeMonths.includes(m)));
}
function barOption(years: number[], series: PlotSeries[], totals: (number | null)[], theme: Theme, view: PermitChartView, periodLabel: string, unit: string): EChartsCoreOption {
  const width = Math.max(160, view.width ?? 900), height = view.height ?? 390, hidden = new Set(view.hidden ?? []);
  const grid = { left: width < 380 ? 42 : 54, right: 12, top: 90, bottom: 38, containLabel: false };
  const plotWidth = Math.max(1, width - grid.left - grid.right), plotHeight = height - grid.top - grid.bottom;
  const visibleHeights = years.map((_, i) => series.reduce((sum, s) => sum + (hidden.has(s.name) ? 0 : Math.max(0, s.data[i] ?? 0)), 0));
  const fullHeights = years.map((_, i) => series.reduce((sum, s) => sum + Math.max(0, s.data[i] ?? 0), 0));
  const minimum = Math.min(0, ...years.map((_, i) => series.reduce((sum, s) => sum + Math.min(0, s.data[i] ?? 0), 0)));
  const maximum = Math.max(1, ...fullHeights, ...totals.map(v => v ?? 0)) * 1.08;
  const axisMinimum = minimum * 1.08;
  const pixels = plotHeight / (maximum - axisMinimum);
  const band = plotWidth / Math.max(1, years.length), barWidth = Math.min(52, band * .72);
  const muted = theme === 'light' ? '#737b87' : '#a3aab5', text = theme === 'light' ? '#252a33' : '#edf0f3';
  // Graphics are reference labels, not another data series. They survive all legend toggles.
  const graphic = totals.flatMap((total, i) => {
    if (total === null) return [];
    const label = measure(total), compact = band < label.length * 6.2 + 4;
    return [{ id: `total-${years[i]}`, type: 'group', x: grid.left + band * (i + .5), y: grid.top + (maximum - visibleHeights[i]) * pixels - 8,
      rotation: compact ? Math.PI / 2 : 0, silent: true, z: 20,
      children: [{ type: 'text', style: { text: label, fill: text, font: '600 10px Segoe UI', align: compact ? 'left' : 'center', verticalAlign: compact ? 'middle' : 'bottom' } }],
    }];
  });
  return {
    grid, graphic,
    tooltip: { trigger: 'item', confine: true, className: 'construction-pages-tooltip', extraCssText: `max-width:${Math.max(100, Math.min(290, width - 32))}px;font-size:11px;`, formatter: (params: Hover | Hover[]) => {
      const point = Array.isArray(params) ? params[0] : params;
      const source = point && series[point.seriesIndex ?? 0], year = point && years[point.dataIndex];
      return source && year !== undefined ? `<strong>${year} · ${escape(periodLabel)}</strong><br/>${escape(source.name)}: ${number(source.data[point.dataIndex], 1)} ${escape(unit)}` : '';
    } },
    xAxis: { type: 'category', data: years.map(String), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: muted, fontSize: 10, interval: 0, rotate: band < 30 ? 90 : 0, hideOverlap: false } },
    yAxis: { type: 'value', name: 'тыс. м²', min: axisMinimum, max: maximum, splitNumber: 4, nameTextStyle: { color: muted, align: 'left' }, axisLabel: { color: muted, fontSize: 10, formatter: (v: number) => number(v, 0) }, splitLine: { lineStyle: { color: theme === 'light' ? '#edf0f3' : '#363a40', type: 'dashed' } } },
    series: series.map(s => ({ type: 'bar', id: s.id, name: s.name, stack: 'permit-area', barWidth, data: s.data,
      itemStyle: { color: s.color }, emphasis: { focus: 'series' }, labelLayout: { hideOverlap: true },
      label: { show: true, position: 'inside', color: s.id === 'remainder' ? '#252a33' : '#ffffff', fontSize: 10, formatter: (p: { value: number | null }) => {
        if (p.value === null || p.value === 0 || Math.abs(p.value) * pixels < 17) return '';
        const value = [measure(p.value), number(p.value, 0).replace(/\s/g, '')].find(label => barWidth >= label.length * 6 + 6);
        if (!value) return '';
        return barWidth > s.name.length * 6 + 8 && Math.abs(p.value) * pixels >= 34 ? `${s.name}\n${value}` : value;
      } },
    })),
  };
}

export function permitsMainOption(data: PermitsData, theme: Theme, view: PermitChartView = {}): EChartsCoreOption {
  const rows = data.permits.presentation.rows;
  const series: PlotSeries[] = [{ id: 'selected', name: permitPeriodName, color: palette[0], data: rows.map(r => r.selected) }];
  if (rows.some(r => permitRemainder(r) !== null)) series.push({ id: 'remainder', name: permitRemainderName, color: theme === 'light' ? '#b9bfc7' : '#a1a8b2', data: rows.map(permitRemainder) });
  return barOption(rows.map(r => r.year), series, rows.map(r => r.selected === null ? null : r.selected + (permitRemainder(r) ?? 0)), theme, view, data.permits.periodLabel, data.permits.unit);
}

export function permitsMainRows(data: PermitsData): Cell[][] {
  return [['Год', `Факт: ${data.permits.periodLabel}, тыс. м²`, 'Подтверждённый январь–декабрь, тыс. м²', 'Годовой прогноз, тыс. м²', 'Основание годового ориентира', 'Ориентир минус выбранный период, тыс. м²', 'Неотрицательный остаток, тыс. м²', 'Изменение к аналогичному полному периоду прошлого года, %', 'Период полный', 'Январь–декабрь подтверждён', 'Наблюдения по', 'Основание источника', 'Единица прогноза', 'Версия источника'],
    ...data.permits.presentation.rows.map(r => {
      const basis = permitAnnualBasis(r);
      return [r.year, r.selected, r.annualActual, r.annualForecast, basis?.label ?? 'Нет годового основания', basis === null || r.selected === null ? null : basis.value - r.selected, permitRemainder(r), permitGrowth(data.permits.presentation.rows, r), r.periodComplete ? 'Да' : 'Нет', r.annualComplete ? 'Да' : 'Нет', r.observedThrough, r.evidence.join('; '), basis?.kind === 'forecast' ? data.forecast.unitBasis : '', data.version];
    }),
  ];
}

export function breakdownChart(data: PermitBreakdown): SalesChart {
  const ids: Record<string, string[]> = { housing_components: ['living', 'housing_living', 'mop_embedded'], nonresidential_historical: ['offices', 'industry', 'social', 'trade', 'hotels', 'other'], area_composition: ['living', 'housing_living', 'mop_embedded', 'nonresidential'] };
  const series = data.series.filter(s => s.id !== data.aggregateField && (ids[data.id] ? ids[data.id].includes(s.id) : !['total', 'housing_total', 'nonresidential'].includes(s.id)));
  return { id: data.id, title: data.title, kind: 'bar', unit: data.unit, series: series.map(s => ({ ...s, unit: data.unit })) };
}
export function breakdownReconciliation(data: PermitBreakdown, index: number) {
  const total = data.rows[index]?.[data.aggregateField], values = breakdownChart(data).series.map(s => s.points[index]?.y ?? null);
  const sum = values.length && values.every(v => v !== null) ? values.reduce<number>((sum, value) => sum + value!, 0) : null;
  return { total: typeof total === 'number' ? total : null, sum, difference: typeof total === 'number' && sum !== null ? total - sum : null };
}
export function breakdownOption(data: PermitBreakdown, theme: Theme, view: PermitChartView = {}): EChartsCoreOption {
  const chart = breakdownChart(data);
  return barOption(data.rows.map(r => r.year), chart.series.map((s, i) => ({ id: s.id, name: s.name, color: palette[i % palette.length], data: s.points.map(p => p.y) })), data.rows.map((_, i) => breakdownReconciliation(data, i).total), theme, view, data.periodLabel, data.unit);
}
export function permitBreakdownRows(data: PermitBreakdown, version: string): Cell[][] {
  return [[...data.columns.map(c => `${c.label}${c.id === 'year' ? '' : ', тыс. м²'}${c.id === data.aggregateField ? ' (итог источника)' : ''}`), 'Период полный', 'Год полный', 'Версия источника'],
    ...data.rows.map(row => [...data.columns.map(c => typeof row[c.id] === 'number' ? row[c.id] as number : null), row.periodComplete === undefined ? null : row.periodComplete ? 'Да' : 'Нет', row.annualComplete === undefined ? null : row.annualComplete ? 'Да' : 'Нет', version]),
  ];
}
