import type { EChartsCoreOption } from 'echarts/core';
import { number, percent } from './format';
import { roomColors, roomTypes } from './apartmentData';
import type { ApartmentOverview, Room } from './apartmentData';
import type { Theme } from './types';

const escape = (text: string) => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
export function distributionOption(rows: ApartmentOverview['distribution'], theme: Theme): EChartsCoreOption {
  const muted = theme === 'light' ? '#737b87' : '#a3aab5';
  return {
    grid: { left: 8, right: 12, top: 28, bottom: 8, containLabel: true },
    tooltip: { trigger: 'item', confine: true, appendTo: 'body', className: 'apartment-tooltip', extraCssText: 'max-width:calc(100vw - 32px);white-space:normal;overflow-wrap:anywhere;', formatter: (p: { name: string; value: number | null }) => `<strong>${escape(p.name)} м²</strong><br/>Доля квартир: ${percent(p.value)}` },
    xAxis: { type: 'category', data: rows.map(r => r.range), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: muted, fontSize: 10, interval: 0, rotate: 45, width: 65, overflow: 'break' } },
    yAxis: { type: 'value', name: '%', axisLabel: { color: muted, formatter: (n: number) => number(n) }, splitLine: { lineStyle: { color: theme === 'light' ? '#edf0f3' : '#363a40', type: 'dashed' } } },
    series: [{ type: 'bar', name: 'Доля квартир', barMaxWidth: 48, itemStyle: { color: roomColors[0] }, data: rows.map(r => r.sharePercent), label: { show: true, position: 'top', color: muted, fontSize: 10, formatter: (p: { value: number | null }) => p.value === null ? '' : percent(p.value) } }],
  };
}
export function roomsOption(rows: Room[], theme: Theme): EChartsCoreOption {
  return {
    tooltip: { trigger: 'item', confine: true, appendTo: 'body', className: 'apartment-tooltip', extraCssText: 'max-width:calc(100vw - 32px);white-space:normal;overflow-wrap:anywhere;', formatter: (p: { name: string; value: number | null }) => `<strong>${escape(p.name)}</strong><br/>Доля источника: ${percent(p.value)}` },
    series: [{ type: 'pie', name: 'Комнатность', radius: ['62%', '86%'], center: ['50%', '50%'], label: { show: false }, emphasis: { scaleSize: 5, label: { show: false } }, itemStyle: { borderWidth: 2, borderColor: theme === 'light' ? '#fff' : '#202225' }, data: rows.filter(r => r.sharePercent !== null && r.sharePercent >= 0).map(r => ({ name: r.type, value: r.sharePercent, itemStyle: { color: roomColors[roomTypes.indexOf(r.type)] } })) }],
  };
}
