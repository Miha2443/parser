import { chartNumberFormatter, number } from './format.ts';
import type { EChartsCoreOption } from 'echarts/core';
import type { Cell } from './commissioningData';
import type { PermitBreakdown, PermitCoverage, PermitPresentationRow, PermitsData } from './constructionPagesData';
import type { SalesChart } from './salesData';
import type { Theme } from './types';

const escape = (value: string) => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const palette = ['#318dca', '#db9a36', '#359a79', '#ba6a87', '#7f83bd', '#657f89'];
export const permitPeriodName = 'Выбранный период';
export const permitRemainderName = 'Годовые значения';
export const permitForecastName = 'Остаток до годового прогноза';
export type PermitChartView = { width?: number; height?: number; hidden?: string[]; includeGrowth?: boolean; presentation?: boolean; fontScale?: number };
type PlotSeries = { id: string; name: string; color: string; data: (number | null)[]; hoverData?: (number | null)[]; forecast?: boolean };
type Hover = { dataIndex: number; seriesIndex?: number };
// Predict post-adapter label space without scaling fonts or grids a second time.
const presentationScale = (view: PermitChartView) => view.presentation && Number.isFinite(view.fontScale) ? Math.max(1, view.fontScale!) : 1;

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
export type PermitGrowthPoint = { year: number; value: number | null; reason: string };
export function permitGrowthPoints(rows: { year: number; value: number | null; complete: boolean }[]): PermitGrowthPoint[] {
  return rows.map(row => {
    const previous = rows.find(other => other.year === row.year - 1);
    const reason = !row.complete || row.value === null ? 'Нет полного факта за выбранный период' : !previous?.complete || previous.value === null ? 'Нет полного факта за аналогичный период прошлого года' : previous.value === 0 ? 'Значение прошлого года равно нулю' : '';
    const growth = reason || !previous ? null : (row.value! / previous.value! - 1) * 100;
    return { year: row.year, value: growth !== null && Number.isFinite(growth) ? growth : null, reason: reason || (growth !== null && !Number.isFinite(growth) ? 'Темп роста не является конечным числом' : '') };
  });
}
export function permitMainGrowth(data: PermitsData) {
  return permitGrowthPoints(data.permits.presentation.rows.map(row => ({ year: row.year, value: row.selected, complete: row.periodComplete })));
}
export function permitBreakdownGrowth(data: PermitBreakdown) {
  const annual = data.periodLabel === 'За год';
  return permitGrowthPoints(data.rows.map(row => ({ year: row.year, value: typeof row[data.aggregateField] === 'number' ? row[data.aggregateField] as number : null, complete: (annual ? row.annualComplete : row.periodComplete) === true })));
}
function growthBand(points: PermitGrowthPoint[], theme: Theme, view: PermitChartView = {}) {
  const width = Math.max(160, view.width ?? 900), muted = theme === 'light' ? '#737b87' : '#a3aab5';
  const grid = { left: width < 380 ? 42 : 54, right: 12, top: 39, bottom: 16 };
  const scale = presentationScale(view), band = (width - grid.left - grid.right) / Math.max(1, points.length);
  const fitBand = Math.max(1, width - Math.round(grid.left * scale) - Math.round(grid.right * scale)) / Math.max(1, points.length), dense = fitBand < 44 * scale;
  const values = points.flatMap(point => point.value === null ? [] : [point.value]);
  const measure = chartNumberFormatter(values);
  const low = Math.min(0, ...values), high = Math.max(0, ...values), span = Math.max(1, high - low);
  const text = (value: number) => `${value > 0 ? '+' : ''}${measure(value)} %`;
  const missing = points.flatMap((point, index) => point.value === null && values.length ? [{ id: `permit-growth-missing-${point.year}`, type: 'text', x: grid.left + band * (index + .5), y: (view.height ?? 120) - 8, rotation: dense ? Math.PI / 2 : 0, silent: true,
    style: { text: 'н/д', fill: muted, font: '9px Segoe UI', align: dense ? 'left' : 'center', verticalAlign: 'middle' } }] : []);
  return {
    animation: false, grid, aria: { enabled: true, label: { description: `Изменение к прошлому году. ${points.map(point => `${point.year}: ${point.value === null ? `нет данных, ${point.reason}` : text(point.value)}`).join('; ')}.` } },
    backgroundColor: theme === 'light' ? '#ffffff' : '#202225',
    graphic: view.hidden?.includes('К прошлому году, %') ? [] : values.length ? missing : [{ id: 'permit-growth-empty', type: 'text', x: grid.left + (width - grid.left - grid.right) / 2, y: grid.top + ((view.height ?? 120) - grid.top - grid.bottom) / 2, silent: true, style: { text: 'Нет полного сравнимого периода', fill: muted, font: '11px Segoe UI', align: 'center' } }],
    tooltip: { trigger: 'item', confine: true, formatter: (point: { dataIndex: number }) => {
      const value = points[point.dataIndex];
      return value ? `<strong>${value.year}</strong><br/>К прошлому году: ${value.value === null ? `н/д · ${escape(value.reason)}` : text(value.value)}` : '';
    } },
    xAxis: { type: 'category', boundaryGap: true, data: points.map(point => String(point.year)), show: false },
    yAxis: { type: 'value', name: '%', min: low - span * .2, max: high + span * .35, splitNumber: 2,
      nameTextStyle: { color: muted, align: 'left', fontSize: 10 }, axisLabel: { color: muted, fontSize: 9, formatter: (value: number) => measure(Math.abs(value) < 1e-8 ? 0 : value) },
      splitLine: { lineStyle: { color: theme === 'light' ? '#edf0f3' : '#363a40', type: 'dashed' } } },
    series: [{ type: 'line', id: 'permit-yoy', name: 'К прошлому году, %', data: points.map(point => point.value), connectNulls: false, smooth: .25, smoothMonotone: 'x',
      symbol: 'circle', symbolSize: 4, lineStyle: { width: 1.5, color: '#359a79' }, itemStyle: { color: '#359a79' },
      label: { show: true, position: 'top', rotate: dense ? 90 : 0, distance: 5, color: theme === 'light' ? '#252a33' : '#edf0f3', fontSize: 10,
        formatter: (point: { value: number | null }) => point.value === null ? '' : text(point.value) },
      labelLayout: { hideOverlap: true }, emphasis: { scale: 1.5 } }],
  };
}
export function permitGrowthOption(points: PermitGrowthPoint[], theme: Theme, view: PermitChartView = {}): EChartsCoreOption {
  return growthBand(points, theme, view);
}
export function permitCoverageComplete(coverage: PermitCoverage | undefined, mode: 'annual' | 'period', from: number, to: number) {
  if (from < 1 || to > 12 || from > to) return false;
  return !!coverage && (mode === 'annual' ? coverage.annualComplete : Array.from({ length: to - from + 1 }, (_, i) => from + i).every(m => coverage.completeMonths.includes(m)));
}
const monthShort = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
export function permitActualPeriod(row: { actualMonth?: number | null; observedThrough?: string | null; periodComplete?: boolean }, requestedMonth: number) {
  const observedMonth = row.actualMonth ?? (row.observedThrough ? Number(row.observedThrough.slice(5, 7)) : requestedMonth);
  const end = row.periodComplete ? requestedMonth : Math.min(requestedMonth, observedMonth || requestedMonth);
  return end === 1 ? 'янв' : `янв–${monthShort[end - 1]}`;
}
function barOption(years: number[], series: PlotSeries[], totals: (number | null)[], theme: Theme, view: PermitChartView, periodLabel: string, unit: string, actualPeriods?: string[], partialPeriods?: (string | null)[], growth?: PermitGrowthPoint[]): EChartsCoreOption {
  const width = Math.max(160, view.width ?? 900), height = view.height ?? (growth ? 510 : 390), hidden = new Set(view.hidden ?? []);
  const growthHeight = Math.min(120, Math.max(80, height * .3)), growthOption = growth ? growthBand(growth, theme, { ...view, height: growthHeight }) : null;
  const grid = { left: width < 380 ? 42 : 54, right: 12, top: growth ? growthHeight + 50 : 70, bottom: partialPeriods?.some(Boolean) ? 60 : 38, containLabel: false };
  const measure = chartNumberFormatter([...series.flatMap(s => s.data), ...totals]);
  const plotWidth = Math.max(1, width - grid.left - grid.right), plotHeight = height - grid.top - grid.bottom;
  const visibleHeights = years.map((_, i) => series.reduce((sum, s) => sum + (hidden.has(s.name) ? 0 : Math.max(0, s.data[i] ?? 0)), 0));
  const fullHeights = years.map((_, i) => series.reduce((sum, s) => sum + Math.max(0, s.data[i] ?? 0), 0));
  const minimum = Math.min(0, ...years.map((_, i) => series.reduce((sum, s) => sum + Math.min(0, s.data[i] ?? 0), 0)));
  const maximum = Math.max(1, ...fullHeights, ...totals.map(v => v ?? 0)) * 1.08;
  const axisMinimum = minimum * 1.08;
  const pixels = plotHeight / (maximum - axisMinimum);
  const scale = presentationScale(view), band = plotWidth / Math.max(1, years.length);
  const fitBand = Math.max(1, width - Math.round(grid.left * scale) - Math.round(grid.right * scale)) / Math.max(1, years.length);
  const barWidth = view.presentation ? Math.max(1, Math.min(height * .22, fitBand * .7)) : Math.min(52, band * .72);
  const labelPixels = view.presentation ? Math.max(0, height - Math.round(grid.top * (growth ? Math.sqrt(scale) : scale)) - Math.round(grid.bottom * scale)) / (maximum - axisMinimum) : pixels;
  const muted = theme === 'light' ? '#737b87' : '#a3aab5', text = theme === 'light' ? '#252a33' : '#edf0f3';
  // A source total is meaningful only while every component is visible.
  const graphic = totals.flatMap((total, i) => {
    if (total === null || series.some(s => hidden.has(s.name) && s.data[i] !== null && s.data[i] !== 0)) return [];
    const label = measure(total), compact = fitBand < (label.length * 6.2 + 4) * scale;
    return [{ id: `permit-total-${years[i]}`, type: 'group', x: grid.left + band * (i + .5), y: grid.top + (maximum - visibleHeights[i]) * pixels - 8,
      rotation: compact ? Math.PI / 2 : 0, silent: true, z: 20,
      children: [{ type: 'text', style: { text: label, fill: text, font: '600 10px Segoe UI', align: compact ? 'left' : 'center', verticalAlign: compact ? 'middle' : 'bottom' } }],
    }];
  });
  const xAxis = { type: 'category', gridIndex: growthOption ? 1 : 0, boundaryGap: true, data: years.map(String), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: muted, fontSize: 10, lineHeight: 16, interval: 0, rotate: band < 30 ? 90 : 0, hideOverlap: false, formatter: (value: string, index: number) => partialPeriods?.[index] ? `${value}\n${partialPeriods[index]}` : value } };
  const yAxis = { type: 'value', gridIndex: growthOption ? 1 : 0, name: 'тыс. м²', min: axisMinimum, max: maximum, splitNumber: 4, nameTextStyle: { color: muted, align: 'left' }, axisLabel: { color: muted, fontSize: 10, formatter: (v: number) => number(v, 0) }, splitLine: { lineStyle: { color: theme === 'light' ? '#edf0f3' : '#363a40', type: 'dashed' } } };
  return {
    animation: false,
    backgroundColor: theme === 'light' ? '#ffffff' : '#202225',
    aria: growthOption?.aria ?? { enabled: true },
    legend: { show: false, selected: Object.fromEntries([...series, ...(growthOption?.series ?? [])].map(s => [s.name, !hidden.has(s.name)])) },
    grid: growthOption ? [{ ...growthOption.grid, height: growthHeight - growthOption.grid.top - growthOption.grid.bottom, bottom: undefined }, grid] : grid,
    graphic: [...graphic, ...(growthOption ? [{ id: 'permit-growth-title', type: 'text', x: grid.left, y: 4, silent: true, style: { text: `К прошлому году, % · ${periodLabel}`, fill: muted, font: '600 11px Segoe UI', width: plotWidth, overflow: 'truncate' } }, ...growthOption.graphic] : [])],
    tooltip: { trigger: 'item', confine: true, className: 'construction-pages-tooltip', extraCssText: `max-width:${Math.max(100, Math.min(290, width - 32))}px;font-size:11px;`, formatter: (params: Hover | Hover[]) => {
      const point = Array.isArray(params) ? params[0] : params;
      if (point && growthOption && point.seriesIndex === series.length) return growthOption.tooltip.formatter(point);
      const source = point && series[point.seriesIndex ?? 0], year = point && years[point.dataIndex];
      return source && year !== undefined ? `<strong>${year} · ${escape(source.forecast ? 'прогноз на год' : source.id === 'remainder' ? 'за год' : actualPeriods?.[point.dataIndex] ?? periodLabel)}</strong><br/>${escape(source.name)}: ${measure(source.hoverData?.[point.dataIndex] ?? source.data[point.dataIndex])} ${escape(unit)}` : '';
    } },
    xAxis: growthOption ? [{ ...growthOption.xAxis, gridIndex: 0 }, xAxis] : xAxis,
    yAxis: growthOption ? [{ ...growthOption.yAxis, gridIndex: 0 }, yAxis] : yAxis,
    series: [...series.map(s => ({ type: 'bar', id: s.id, name: s.name, stack: 'permit-area', xAxisIndex: growthOption ? 1 : 0, yAxisIndex: growthOption ? 1 : 0, barWidth, data: s.data,
      itemStyle: { color: s.color, ...(s.forecast ? { borderType: 'dashed', borderWidth: 1.5, borderColor: theme === 'light' ? '#657d92' : '#bdc9d5', decal: { symbol: 'rect', dashArrayX: [1, 0], dashArrayY: [3, 5], rotation: -Math.PI / 4, color: theme === 'light' ? 'rgba(73,100,126,0.25)' : 'rgba(220,230,240,0.25)' } } : {}) }, emphasis: { focus: 'series' }, labelLayout: { hideOverlap: true },
      label: { show: true, position: 'inside', color: s.id === 'remainder' || s.forecast ? '#252a33' : '#ffffff', fontSize: 10, formatter: (p: { value: number | null }) => {
        if (p.value === null || p.value === 0 || Math.abs(p.value) * labelPixels < 17 * scale) return '';
        const value = [measure(p.value), number(p.value, 0).replace(/\s/g, '')].find(label => barWidth >= (label.length * 6 + 6) * scale);
        if (!value) return '';
        return barWidth > (s.name.length * 6 + 8) * scale && Math.abs(p.value) * labelPixels >= 34 * scale ? `${s.name}\n${value}` : value;
      } },
    })), ...(growthOption ? growthOption.series.map(s => ({ ...s, xAxisIndex: 0, yAxisIndex: 0 })) : [])],
  };
}

// includeGrowth returns a single-canvas option; legacy callers can retain bars only.
export function permitsMainOption(data: PermitsData, theme: Theme, view: PermitChartView = {}): EChartsCoreOption {
  const rows = data.permits.presentation.rows;
  const series: PlotSeries[] = [{ id: 'selected', name: permitPeriodName, color: palette[0], data: rows.map(r => r.selected) }];
  if (rows.some(r => permitAnnualBasis(r)?.kind === 'annual_observed' && permitRemainder(r) !== null)) series.push({ id: 'remainder', name: permitRemainderName, color: theme === 'light' ? '#b9bfc7' : '#a1a8b2', data: rows.map(r => permitAnnualBasis(r)?.kind === 'annual_observed' ? permitRemainder(r) : null), hoverData: rows.map(r => permitAnnualBasis(r)?.kind === 'annual_observed' ? r.annualActual : null) });
  if (rows.some(r => r.annualForecast !== null)) series.push({ id: 'forecast', name: permitForecastName, forecast: true, color: theme === 'light' ? '#e4eaf0' : '#8a9cab', data: rows.map(r => r.annualForecast === null ? null : r.selected === null ? r.annualForecast : permitRemainder(r)) });
  const periods = rows.map(r => r.actualMonth == null ? data.permits.periodLabel : permitActualPeriod(r, data.selection.month));
  return barOption(rows.map(r => r.year), series, rows.map(r => r.selected === null ? r.annualForecast : r.selected + (permitRemainder(r) ?? 0)), theme, view, data.permits.periodLabel, data.permits.unit, periods, rows.map(r => r.selected !== null && !r.periodComplete ? permitActualPeriod(r, data.selection.month) : null), view.includeGrowth ? permitMainGrowth(data) : undefined);
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
  const series = data.series.filter(s => s.id !== data.aggregateField && !(s.id === 'housing_living' && data.series.some(other => other.id === 'living')) && (ids[data.id] ? ids[data.id].includes(s.id) : !['total', 'housing_total', 'nonresidential'].includes(s.id)));
  return { id: data.id, title: data.title, kind: 'bar', unit: data.unit, series: series.map(s => ({ ...s, unit: data.unit })) };
}
export function breakdownReconciliation(data: PermitBreakdown, index: number) {
  const total = data.rows[index]?.[data.aggregateField], values = breakdownChart(data).series.map(s => s.points[index]?.y ?? null);
  const sum = values.length && values.every(v => v !== null) ? values.reduce<number>((sum, value) => sum + value!, 0) : null;
  return { total: typeof total === 'number' ? total : null, sum, difference: typeof total === 'number' && sum !== null ? total - sum : null };
}
export function breakdownOption(data: PermitBreakdown, theme: Theme, view: PermitChartView = {}): EChartsCoreOption {
  const chart = breakdownChart(data);
  const periods = data.rows.map(r => typeof r.actualMonth === 'number' ? permitActualPeriod({ actualMonth: r.actualMonth, periodComplete: false }, 12) : data.periodLabel);
  return barOption(data.rows.map(r => r.year), chart.series.map((s, i) => ({ id: s.id, name: s.name, color: palette[i % palette.length], data: s.points.map(p => p.y) })), data.rows.map((_, i) => breakdownReconciliation(data, i).total), theme, view, data.periodLabel, data.unit, periods, data.rows.map((r, i) => r.periodComplete === false && typeof r.actualMonth === 'number' ? periods[i] : null), view.includeGrowth ? permitBreakdownGrowth(data) : undefined);
}
export function permitBreakdownRows(data: PermitBreakdown, version: string): Cell[][] {
  return [[...data.columns.map(c => `${c.label}${c.id === 'year' || c.id === 'growth' ? '' : ', тыс. м²'}${c.id === data.aggregateField ? ' (итог источника)' : ''}`), 'Период полный', 'Год полный', 'Версия источника'],
    ...data.rows.map(row => [...data.columns.map(c => typeof row[c.id] === 'number' ? row[c.id] as number : null), row.periodComplete === undefined ? null : row.periodComplete ? 'Да' : 'Нет', row.annualComplete === undefined ? null : row.annualComplete ? 'Да' : 'Нет', version]),
  ];
}
