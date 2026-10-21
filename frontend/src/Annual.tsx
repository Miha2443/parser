import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight, Database, Download, FileSpreadsheet, RefreshCw } from 'lucide-react';
import { Chart } from './Chart';
import { getInstanceByDom } from 'echarts/core';
import { ApiError, request } from './data';
import { date, exportCsv, number } from './format';
import { CommissioningEmpty, CommissioningEvidence, CommissioningState } from './Operational';
import type { CommissioningChange } from './Operational';
import { annualChartOption, annualChartRows, annualFactCaption, annualSummaryRows, marketParams, marketUrl } from './annualConstructionData';
import type { AnnualChart, MarketKind, MarketSelection } from './annualConstructionData';
import { useAnnualConstructionData } from './useAnnualConstructionData';
import type { Theme } from './types';
import './annual.css';

function AnnualChartView({ chart, theme, filename }: { chart: AnnualChart; theme: Theme; filename: string }) {
  const root = useRef<HTMLDivElement>(null), [visible, setVisible] = useState<string[]>();
  useEffect(() => {
    const canvas = root.current?.querySelector<HTMLDivElement>('.chart-canvas'), instance = canvas && getInstanceByDom(canvas);
    if (!instance) return;
    const onLegend = (event: unknown) => { const { selected } = event as { selected: Record<string, boolean> }; setVisible([...chart.series.map(s => s.name), ...(chart.forecast ? ['Прогноз'] : [])].filter(name => selected[name] !== false)); };
    instance.on('legendselectchanged', onLegend);
    return () => { instance.off('legendselectchanged', onLegend); };
  }, [chart]);
  return <div ref={root} className="annual-chart-scroll" role="region" aria-label={chart.title}><div className="annual-chart-surface"><Chart theme={theme} label={chart.title} height={360} option={annualChartOption(chart, theme, visible)} rows={annualChartRows(chart)} filename={filename} legendHints={chart.forecast ? { 'Прогноз': chart.forecast.metric } : {}} /></div></div>;
}

export function MarketExport({ kind, selection, version }: { kind: MarketKind; selection: MarketSelection; version: string }) {
  const active = useRef<AbortController>(), scope = JSON.stringify([kind, selection, version]), [busy, setBusy] = useState(false), [error, setError] = useState('');
  useEffect(() => { setBusy(false); setError(''); return () => active.current?.abort(); }, [scope]);
  async function download() {
    active.current?.abort(); const c = new AbortController(); active.current = c; setBusy(true); setError('');
    try { const blob = await (await request(marketUrl(kind, selection, version), c.signal)).blob(); if (c.signal.aborted) return; const url = URL.createObjectURL(blob), a = document.createElement('a'); a.href = url; a.download = `${kind}-${selection.region}.xlsx`; document.body.append(a); a.click(); a.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000); }
    catch (e) { if (!c.signal.aborted) setError(e instanceof ApiError && e.status === 409 ? 'Данные изменились. Перезагрузите страницу, затем скачайте Excel.' : `${e instanceof Error ? e.message : 'Не удалось скачать Excel.'} Повторите скачивание.`); }
    finally { if (!c.signal.aborted) setBusy(false); }
  }
  const caption = kind === 'annual' ? 'Скачать годовой ввод Excel' : 'Скачать текущее строительство Excel';
  return <div className="profile-export commissioning-export"><button className="icon-button" title={caption} aria-label={caption} disabled={busy} onClick={download}>{busy ? <RefreshCw size={17} className="loading-icon" /> : <FileSpreadsheet size={17} />}</button>{error && <span role="alert" className="warning-text">{error}</span>}</div>;
}
export default function Annual({ theme, query, change }: { theme: Theme; query: string; change: CommissioningChange }) {
  const params = useMemo(() => new URLSearchParams(query), [query]), { catalog, selection, data, error, retry } = useAnnualConstructionData('annual', params), metadata = data ?? catalog;
  const periodChart = data?.charts.find(chart => chart.factPeriod), forecast = data?.forecastSource;
  const subtitle = [data?.region.label ?? catalog?.regions.find(region => region.id === selection?.region)?.label, 'млн м²',
    ...(periodChart?.factPeriod ? [data?.region.id === 'rf' ? `годовой итог ${periodChart.factPeriod.year} отсутствует` : `факт: ${annualFactCaption(periodChart)} ${periodChart.factPeriod.year}`] : []),
    ...(forecast?.date && data?.charts.some(chart => chart.forecast) ? [`прогноз от ${date(forecast.date)}`] : [])].filter(Boolean).join(' · ');
  const evidenceNotes = [...(data?.notes ?? []), ...(forecast ? [`Лист «Прогнозы»: ${forecast.file ?? 'файл не указан'}; дата: ${date(forecast.date)}.`,
    ...forecast.notes, `Основание единиц: ${forecast.unitBasis}. Площади переведены из тыс. м² в млн м² делением на 1000.`,
    ...(data?.charts.filter(chart => chart.forecast).map(chart => `${chart.title}: прогноз только общего показателя «${chart.forecast!.metric}» (${chart.forecast!.sourceFields.join(' + ')}), без распределения по фактическим категориям.`) ?? [])] : [])];
  useEffect(() => { if (selection && params.get('region') !== selection.region) change(marketParams(selection), true); }, [selection?.region, params]);
  return <main className="commissioning-page annual-commissioning-page" id="annual-page"><div className="page-heading"><div><h1>Годовой ввод недвижимости</h1><p className="annual-subtitle">{subtitle}</p></div>{data && selection && <MarketExport kind="annual" selection={selection} version={data.version} />}</div>
    {catalog && selection && <div className="filters commissioning-filters"><label><span>Регион</span><select aria-label="Регион" value={selection.region} onChange={e => change({ region: e.target.value })}>{catalog.regions.map(r => <option key={r.id} value={r.id}>{r.label}</option>)}</select></label><a className="source-anchor" href="#annual-sources"><Database size={15} />Источники<ChevronRight size={14} /></a></div>}
    {error || !catalog || selection && !data ? <CommissioningState error={error} retry={retry} catalog={!!catalog} caption="Годовой ввод" /> : !selection ? <CommissioningEmpty text="Нет доступных регионов." /> : data && <>
      <nav className="section-nav" aria-label="Разделы годового ввода">{data.charts.map(c => <a key={c.id} href={`#annual-${c.id}`}>{c.title}</a>)}</nav>
      {data.charts.map(c => <section className="section annual-report-section" key={c.id} id={`annual-${c.id}`}><div className="section-heading"><div><h2>{c.title}</h2><p>{data.region.label} · {c.rows.length ? `${String(c.rows[0].year)}–${String(c.rows.at(-1)!.year)}` : 'Нет данных'} · {c.unit}</p></div></div>{c.rows.length && c.series.length ? <AnnualChartView key={`${data.version}-${selection.region}-${c.id}`} chart={c} theme={theme} filename={`annual-${selection.region}-${c.id}`} /> : <CommissioningEmpty />}
        {!!c.summaries.length && <details className="annual-summary-disclosure"><summary>Итоги периодов</summary><div className="annual-summaries"><div className="annual-summary-heading"><h3>Итоги периодов</h3><button className="icon-button" title={`Скачать итоги CSV: ${c.title}`} aria-label={`Скачать итоги CSV: ${c.title}`} onClick={() => exportCsv(annualSummaryRows(c), `annual-${selection.region}-${c.id}-totals.csv`)}><Download size={16} /></button></div>{c.summaries.map((s, i) => <div key={i} className="annual-summary-period"><h4>{s.from}–{s.to}</h4><dl>{s.values.map(v => <div key={v.id}><dt>{v.label}</dt><dd>{number(v.value, 1)}<span>{c.unit}</span></dd></div>)}</dl></div>)}</div></details>}
      </section>)}{!data.charts.length && <CommissioningEmpty />}</>}
    {metadata && <CommissioningEvidence metadata={metadata} anchor="annual-sources" notes={evidenceNotes} />}<footer className="page-footer"><span>Аналитика Москвы</span><span>Годовой ввод · API</span></footer></main>;
}
