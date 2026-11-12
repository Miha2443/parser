import { breakdownOption, type PermitChartView } from './permitPresentation';
import type { PermitBreakdown } from './constructionPagesData';
import type { Theme } from './types';

export function demoPermitOption(chart: PermitBreakdown, theme: Theme, view: PermitChartView) {
  const areaFields = new Set([chart.aggregateField, ...chart.series.map(series => series.id)]);
  const display = { ...chart,
    rows: chart.rows.map(row => Object.fromEntries(Object.entries(row).map(([key, value]) =>
      [key, areaFields.has(key) && typeof value === 'number' ? value / 1000 : value])) as typeof row),
    series: chart.series.map(series => ({ ...series, points: series.points.map(point => ({ ...point, y: point.y === null ? null : point.y / 1000 })) })),
  };
  const option = breakdownOption(display, theme, view);
  const axes = Array.isArray(option.yAxis) ? option.yAxis : [option.yAxis];
  const tooltip = option.tooltip as { formatter: (point: { dataIndex: number; seriesIndex?: number }) => string };
  return { ...option, yAxis: axes.map(axis => ({ ...axis, name: axis?.name === 'тыс. м²' ? 'млн м²' : axis?.name })),
    tooltip: { ...tooltip, formatter: (point: { dataIndex: number; seriesIndex?: number }) => tooltip.formatter(point).replaceAll('тыс. м²', 'млн м²') } };
}
