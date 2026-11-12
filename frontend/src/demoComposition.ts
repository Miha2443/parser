import type { EChartsCoreOption } from 'echarts/core';
import { chartLegendItems, type ChartLegendItem } from './Chart';
import type { DemoChart, DemoSummary } from './demoData';
import type { Theme } from './types';

const programColors: Record<string, string> = {
  'Льготная ипотека': '#bd6778', 'Семейная ипотека': '#5a9296', 'ИТ-ипотека': '#344f68',
};
const darkProgramColors: Record<string, string> = {
  'Льготная ипотека': '#db8da0', 'Семейная ипотека': '#73a9ae', 'ИТ-ипотека': '#8baecc',
};

export function demoPalette(option: EChartsCoreOption, chartId: string, theme: Theme = 'light'): EChartsCoreOption {
  if (!chartId.startsWith('preferential/') || !Array.isArray(option.series)) return option;
  return { ...option, series: option.series.map(series => {
    const color = (theme === 'dark' ? darkProgramColors : programColors)[String(series.name)];
    return color ? { ...series, itemStyle: { ...series.itemStyle, color } } : series;
  }) };
}

export function commonDemoLegend(charts: DemoChart[], theme: Theme): ChartLegendItem[] {
  if (charts.length < 2) return [];
  const legends = charts.map(chart => chartLegendItems(demoPalette(chart.option(theme, 600, [], 400), chart.id, theme)));
  const first = legends[0];
  if (!first.length || first.some(item => !item.name || typeof item.color !== 'string' || !item.color.trim()) || new Set(first.map(item => item.name)).size !== first.length) return [];
  return legends.slice(1).every(items => items.length === first.length && first.every(item => items.some(other =>
    other.name === item.name && other.color === item.color && other.borderType === item.borderType))) ? first : [];
}

export function demoSummaryText(summary: DemoSummary): string {
  return summary.value === null ? '—' : new Intl.NumberFormat('ru-RU', {
    maximumFractionDigits: summary.digits ?? 1,
  }).format(summary.value);
}
