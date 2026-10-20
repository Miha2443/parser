import type { EChartsCoreOption } from 'echarts/core';
import { chartNumberFormatter } from './format.ts';
import { escapeHtml, readinessChartOption, realizationDonutOption, realizationHistoryOption, realizationStatuses } from './constructionPagesData.ts';
import type { RealizationObservation } from './constructionPagesData';
import type { SalesChart } from './salesData';
import type { Theme } from './types';

export const domrfPriceNote = 'Объём привлечённых средств (млн руб.) / проданная площадь (млн м²) = руб./м². Показана готовая цена ДОМ.РФ по проектным декларациям, без пересчёта.';

export function domrfLineOption(chart: SalesChart, theme: Theme): EChartsCoreOption {
  const option = readinessChartOption(chart, theme);
  const format = chartNumberFormatter(chart.series.flatMap(series => series.points.map(point => point.y)));
  const formatAxis = (axis: object) => ({ ...axis, axisLabel: { ...(axis as { axisLabel?: object }).axisLabel, formatter: format } });
  return {
    ...option,
    yAxis: Array.isArray(option.yAxis) ? option.yAxis.map(formatAxis) : formatAxis(option.yAxis as object),
    tooltip: {
      trigger: chart.kind === 'line' ? 'axis' : 'item', confine: true, className: 'construction-pages-tooltip',
      formatter: (input: { name: string; seriesName: string; seriesIndex: number; value: number | null } | { name: string; seriesName: string; seriesIndex: number; value: number | null }[]) => {
        const points = Array.isArray(input) ? input : [input];
        return points.map(point => `<strong>${escapeHtml(point.name)} · ${escapeHtml(point.seriesName)}</strong><br/>${format(point.value)} ${escapeHtml(chart.series[point.seriesIndex]?.unit ?? chart.unit)}`).join('<br/>');
      },
    },
  };
}

export function domrfDonutOption(label: string, pct: number | null, area: number | null, color: string, theme: Theme): EChartsCoreOption {
  const option = realizationDonutOption(label, pct, area, color, theme);
  const millions = area == null ? null : area / 1000;
  const format = chartNumberFormatter([millions, pct]);
  return {
    ...option,
    graphic: [
      { type: 'text', left: 'center', top: '39%', style: { text: format(millions), font: '600 30px Segoe UI', fill: theme === 'light' ? '#252a33' : '#edf0f3' } },
      { type: 'text', left: 'center', top: '57%', style: { text: 'млн м²', font: '13px Segoe UI', fill: theme === 'light' ? '#737b87' : '#a3aab5' } },
    ],
    tooltip: { trigger: 'item', confine: true, className: 'construction-pages-tooltip', formatter: () => `<strong>${escapeHtml(label)}</strong><br/>${format(millions)} млн м² · ${format(pct)} %` },
  };
}

export function domrfRealizationHistoryOption(history: RealizationObservation[], theme: Theme): EChartsCoreOption {
  const option = realizationHistoryOption(history, theme);
  const areas = history.flatMap(row => realizationStatuses.map(status => row.metrics[`${status.id}_thousand_m2`] == null ? null : row.metrics[`${status.id}_thousand_m2`]! / 1000));
  const format = chartNumberFormatter([...areas, ...history.flatMap(row => realizationStatuses.map(status => row.metrics[`${status.id}_pct`]))]);
  return {
    ...option,
    series: (option.series as object[]).map(series => ({ ...series, label: { show: false }, emphasis: { focus: 'series', label: { show: false } } })),
    yAxis: { ...(option.yAxis as object), axisLabel: { ...(option.yAxis as { axisLabel?: object }).axisLabel, formatter: format } },
    tooltip: {
      trigger: 'item', confine: true, className: 'construction-pages-tooltip',
      formatter: (point: { dataIndex: number; seriesIndex: number }) => {
        const row = history[point.dataIndex], status = realizationStatuses[point.seriesIndex];
        const area = row.metrics[`${status.id}_thousand_m2`], pct = row.metrics[`${status.id}_pct`];
        return `<strong>${escapeHtml(row.label)} · ${escapeHtml(status.label)}</strong><br/>${format(area == null ? null : area / 1000)} млн м²${pct == null ? '' : ` · ${format(pct)} %`}`;
      },
    },
  };
}
