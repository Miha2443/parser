import { useEffect, useRef, useState } from 'react';
import * as echarts from 'echarts/core';
import { BarChart, PieChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, LegendComponent, GraphicComponent, AriaComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { ChevronDown, Download, ImageDown } from 'lucide-react';
import { area, categories, download, exportCsv, number, sumAreas } from './format';
import type { AnnualRow, Areas, CategoryKey, Delay, Theme } from './types';

echarts.use([BarChart, PieChart, GridComponent, TooltipComponent, LegendComponent, GraphicComponent, AriaComponent, CanvasRenderer]);
type ChartOption = echarts.EChartsCoreOption;
export function Chart({ option, theme, label, rows, filename, height = 310 }: { option: ChartOption; theme: Theme; label: string; rows: (string | number | null)[][]; filename: string; height?: number }) {
  const container = useRef<HTMLDivElement>(null);
  const instance = useRef<echarts.ECharts>();
  const [showData, setShowData] = useState(false);
  const [hidden, setHidden] = useState<string[]>([]);
  const series = option.series as { type: string; name?: string; itemStyle?: { color?: string }; data?: { name: string; itemStyle?: { color?: string } }[] }[];
  const legendItems = series?.[0]?.type === 'pie' ? series[0].data?.map(d => ({ name: d.name, color: d.itemStyle?.color })) ?? [] : series?.map(s => ({ name: s.name ?? '', color: s.itemStyle?.color })) ?? [];
  useEffect(() => {
    if (!container.current) return;
    const chart = echarts.init(container.current, undefined, { renderer: 'canvas' });
    instance.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(container.current);
    return () => { observer.disconnect(); chart.dispose(); instance.current = undefined; };
  }, []);
  useEffect(() => {
    instance.current?.setOption({ ...option, legend: { show: false, selected: Object.fromEntries(legendItems.map(item => [item.name, !hidden.includes(item.name)])) }, backgroundColor: theme === 'light' ? '#ffffff' : '#202225', textStyle: { fontFamily: 'Segoe UI, Arial, sans-serif', color: theme === 'light' ? '#454b54' : '#c3c9d1' }, aria: { enabled: true }, animationDuration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 350 }, true);
  }, [option, theme, hidden]);
  return <div className="chart-block">
    <div className="legend chart-legend">{legendItems.map(item => <button key={item.name} className={hidden.includes(item.name) ? 'legend-off' : ''} aria-pressed={!hidden.includes(item.name)} title={`${hidden.includes(item.name) ? 'Показать' : 'Скрыть'}: ${item.name}`} onClick={() => { instance.current?.dispatchAction({ type: 'legendToggleSelect', name: item.name }); setHidden(prev => prev.includes(item.name) ? prev.filter(n => n !== item.name) : [...prev, item.name]); }}><i style={{ background: item.color }} />{item.name}</button>)}</div>
    <div ref={container} className="chart-canvas" style={{ height }} role="img" aria-label={label} />
    <div className="chart-data"><button className="data-toggle" onClick={() => setShowData(!showData)} aria-expanded={showData}><ChevronDown size={13} className={showData ? 'chevron expanded' : 'chevron'} />Данные и скачивание</button></div>
    {showData && <div className="chart-data-panel"><div className="export-actions"><button onClick={() => exportCsv(rows, `${filename}.csv`)}><Download size={15} />CSV</button><button onClick={() => { const chart = instance.current; if (chart) download(chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: theme === 'light' ? '#ffffff' : '#202225' }), `${filename}.png`); }}><ImageDown size={15} />PNG</button></div><div className="table-wrap"><table><thead><tr>{rows[0]?.map((cell, i) => <th key={i}>{cell}</th>)}</tr></thead><tbody>{rows.slice(1).map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{typeof cell === 'number' ? number(cell, 1) : cell ?? '—'}</td>)}</tr>)}</tbody></table></div></div>}
  </div>;
}
const escape = (s: string) => s.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const tones = (theme: Theme) => ({ muted: theme === 'light' ? '#737b87' : '#a3aab5', line: theme === 'light' ? '#edf0f3' : '#363a40', text: theme === 'light' ? '#222831' : '#f0f2f5' });
export function annualOption(rows: AnnualRow[], theme: Theme): ChartOption {
  const t = tones(theme);
  return {
    grid: { left: 8, right: 12, top: 32, bottom: 8, containLabel: true },
    tooltip: { trigger: 'item', confine: true, formatter: (p: { seriesName: string; name: string; value: number; dataIndex: number }) => { const total = sumAreas(rows[p.dataIndex]); return `<strong>${escape(p.name)} · ${escape(p.seriesName)}</strong><br/>${number(p.value, 1)} тыс. м²${total && p.value != null ? `<br/>Доля в году: ${number(p.value * 1000 / total * 100, 1)}%` : ''}`; } },
    xAxis: { type: 'category', data: rows.map(r => String(r.year)), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: t.muted, fontSize: 11, interval: 'auto', hideOverlap: true } },
    yAxis: { type: 'value', name: 'тыс. м²', nameTextStyle: { color: t.muted, align: 'left', padding: [0, 0, 0, 0] }, axisLabel: { color: t.muted, fontSize: 11, formatter: (v: number) => number(v) }, splitLine: { lineStyle: { color: t.line, type: 'dashed' } } },
    series: categories.map(c => ({ type: 'bar', stack: 'area', name: c.label, barMaxWidth: 52, barCategoryGap: '38%', data: rows.map(r => r[c.key] == null ? null : r[c.key]! / 1000), itemStyle: { color: c.color }, emphasis: { focus: 'series' } })),
  };
}
export function donutOption(values: Areas, theme: Theme, labels: Partial<Record<CategoryKey, string>> = {}): ChartOption {
  const total = sumAreas(values);
  const t = tones(theme);
  return {
    tooltip: { trigger: 'item', confine: true, formatter: (p: { name: string; value: number; percent: number }) => `<strong>${escape(p.name)}</strong><br/>${area(p.value)} тыс. м² · ${number(p.percent, 1)}%` },
    graphic: [{ type: 'text', left: 'center', top: '42%', style: { text: area(total), fill: t.text, font: '600 23px Segoe UI' } }, { type: 'text', left: 'center', top: '56%', style: { text: 'тыс. м²', fill: t.muted, font: '12px Segoe UI' } }],
    series: [{ name: 'Площадь', type: 'pie', radius: ['66%', '87%'], center: ['50%', '50%'], avoidLabelOverlap: true, label: { show: false }, emphasis: { scaleSize: 5, label: { show: false } }, itemStyle: { borderWidth: 2, borderColor: theme === 'light' ? '#fff' : '#202225' }, data: categories.filter(c => values[c.key] != null).map(c => ({ name: labels[c.key] ?? c.label, value: values[c.key], itemStyle: { color: c.color } })) }],
  };
}
export function delayOption(rows: Delay[], theme: Theme): ChartOption {
  const t = tones(theme);
  return {
    grid: { left: 8, right: 12, top: 30, bottom: 8, containLabel: true },
    tooltip: { trigger: 'item', confine: true, formatter: (p: { name: string; seriesName: string; value: number }) => `<strong>${escape(p.name)} · ${escape(p.seriesName)}</strong><br/>${number(p.value, 1)} тыс. м²` },
    xAxis: { type: 'category', data: rows.map(r => String(r.year)), axisTick: { show: false }, axisLine: { show: false }, axisLabel: { color: t.muted } },
    yAxis: { type: 'value', name: 'тыс. м²', nameTextStyle: { color: t.muted }, axisLabel: { color: t.muted, formatter: (v: number) => number(v) }, splitLine: { lineStyle: { color: t.line, type: 'dashed' } } },
    series: [{ key: 'completed', name: 'Без переноса', color: '#2c9869' }, { key: 'delayed', name: 'С переносом', color: '#cf5d64' }, { key: 'clarification', name: 'Уточнение', color: '#8993a1' }].map(c => ({ type: 'bar', stack: 'delay', name: c.name, barMaxWidth: 48, itemStyle: { color: c.color }, emphasis: { focus: 'series' }, data: rows.map(r => r[c.key as keyof Delay] == null ? null : r[c.key as keyof Delay]! / 1000) })),
  };
}
