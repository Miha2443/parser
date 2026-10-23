import { useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, Building2, Database, FileSpreadsheet, Footprints, RefreshCw, Route, TrainFront, TramFront, Waypoints } from 'lucide-react';
import { Chart } from './Chart';
import { ApiError, request } from './data';
import { date, number } from './format';
import { linearDashboardUrl, linearHistoryOption, linearValue, linearYearRows } from './linearDashboardData';
import type { LinearMetric } from './linearDashboardData';
import { CommissioningEmpty, CommissioningState } from './Operational';
import type { CommissioningChange } from './Operational';
import { useLinearDashboard } from './useLinearDashboard';
import type { Theme } from './types';
import './linear-dashboard.css';

const icons = { roads: Route, bridges: Waypoints, crossings: Footprints, metro_stations: TrainFront, metro_tunnels: TramFront, depots: Building2 };
function MetricTile({ metric, year }: { metric: LinearMetric; year: number }) {
  const Icon = icons[metric.id];
  return <article className="linear-kpi" data-metric={metric.id}>
    <header><Icon size={20} /><h2>{metric.label}</h2><span>{metric.unit}</span></header>
    <div className="linear-kpi-fact"><strong>{linearValue(metric.fact, metric.unit)}</strong><span>Факт · {year}</span></div>
    <p className="linear-kpi-period">{metric.factPeriod ?? 'Факт в источниках отсутствует'}</p>
    <dl className="linear-kpi-targets"><div><dt>Прогноз на год</dt><dd>{linearValue(metric.forecast, metric.unit)}</dd></div></dl>
    <div className="linear-progress-heading"><span>Выполнение прогноза</span><strong>{metric.percent === null ? '—' : `${number(metric.percent, 1)}%`}</strong></div>
    <div className={`linear-progress ${metric.percent === null ? 'unavailable' : ''}`} role={metric.percent === null ? undefined : 'progressbar'} aria-label={`Выполнение: ${metric.label}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={metric.percent === null ? undefined : Math.min(100, metric.percent)} aria-valuetext={metric.percent === null ? undefined : `${number(metric.percent, 1)}%`}><span style={{ width: `${Math.min(100, metric.percent ?? 0)}%` }} /></div>
    <div className="linear-kpi-remaining"><span><ArrowDownToLine size={13} />Осталось до прогноза</span><strong>{linearValue(metric.remaining, metric.unit)} <small>{metric.unit}</small></strong></div>
  </article>;
}
function DashboardExport({ version }: { version: string }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const active = useRef<AbortController>();
  useEffect(() => { setError(''); setBusy(false); return () => active.current?.abort(); }, [version]);
  async function download() {
    active.current?.abort(); const controller = new AbortController(); active.current = controller;
    setBusy(true); setError('');
    try {
      const blob = await (await request(linearDashboardUrl(version), controller.signal)).blob();
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = 'linear-dashboard.xlsx'; document.body.append(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      if (!controller.signal.aborted) setError(error instanceof ApiError && error.status === 409 ? 'Источники изменились. Обновите данные перед скачиванием.' : 'Не удалось скачать Excel. Повторите скачивание.');
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <div className="linear-export"><button className="icon-button" disabled={busy} title="Скачать факты, прогнозы и источники Excel" aria-label="Скачать факты, прогнозы и источники Excel" onClick={download}>{busy ? <RefreshCw size={17} className="loading-icon" /> : <FileSpreadsheet size={17} />}</button>{error && <span role="alert">{error}</span>}</div>;
}
export default function Linear({ theme }: { theme: Theme; query: string; change: CommissioningChange }) {
  const { data, error, retry } = useLinearDashboard();
  const sourceDate = data?.sources.map(source => source.date).filter((value): value is string => value !== null).sort().at(-1);
  return <main className="commissioning-page linear-page" id="linear-page">
    <div className="page-heading"><div><div className="eyebrow">АНАЛИТИКА НЕДВИЖИМОСТИ</div><h1>Ввод линейных объектов</h1></div><div className="snapshot-label"><Database size={14} />Москва<span>Обновлено: {sourceDate ? date(sourceDate) : '—'}</span></div></div>
    {error || !data ? <CommissioningState error={error} retry={retry} catalog={false} caption="Ввод линейных объектов" /> : <>
      <div className="linear-dashboard-heading"><h2>{data.currentYear} год</h2><div><a href="#linear-sources" className="linear-sources-link"><Database size={15} />Источники</a><button className="icon-button" aria-label="Обновить линейные объекты" title="Обновить линейные объекты" onClick={retry}><RefreshCw size={16} /></button><DashboardExport version={data.version} /></div></div>
      <section className="linear-kpi-grid" aria-label="Шесть показателей текущего года">{data.metrics.map(metric => <MetricTile key={metric.id} metric={metric} year={data.currentYear} />)}</section>
      <div className="linear-history-list">{data.metrics.map(metric => <History key={metric.id} metric={metric} theme={theme} year={data.currentYear} version={data.version} />)}</div>
      <section className="section sources-section" id="linear-sources"><details><summary><Database size={17} /><span>Исходные файлы и даты</span></summary><div className="source-list">{data.sources.map(source => <div key={source.id}><strong>{({ dsti: 'ДСТИ · квартальный факт', monitoring: 'Мониторинг · годовая история', forecast: 'Мониторинг · прогноз', static_depots: 'Электродепо · статичная годовая история' } as Record<string, string>)[source.id]}</strong><span>{source.date ? date(source.date) : '—'}</span><div><code>{source.file ?? 'Источник отсутствует'}</code>{source.sheet && <p>{source.sheet}</p>}</div></div>)}</div>{data.notes.map((note, i) => <p className="method-note" key={i}>{note}</p>)}<p className="muted">Сформировано: {date(data.generatedAt)} · версия {data.version}</p></details></section>
    </>}
    <footer className="page-footer"><span>Аналитика Москвы</span><span>Ввод линейных объектов · API</span></footer>
  </main>;
}
function History({ metric, year, theme, version }: { metric: LinearMetric; year: number; theme: Theme; version: string }) {
  const current = metric.years.find(row => row.year === year);
  const periods = metric.years.filter(row => row.fact !== null).map(row => row.year);
  const range = periods.length ? `${Math.min(...periods)}–${Math.max(...periods)}` : null;
  return <section className="section linear-year-chart" data-history={metric.id} aria-label={`История: ${metric.label}`}>
    <div className="section-heading"><div><h2>{metric.label}</h2><p>{range ? `${range} · ` : ''}{metric.unit} · {year}: {current?.factPeriod ?? 'факт отсутствует'}</p></div></div>
    {metric.years.some(row => row.fact !== null || row.forecast !== null) ? <Chart key={`${version}-${metric.id}`} option={linearHistoryOption(metric, year, theme)} theme={theme} label={`Годовой ввод: ${metric.label}`} rows={linearYearRows(metric)} filename={`linear-${metric.id}-yearly`} height={290} legendHints={{ Прогноз: 'Годовой прогноз мониторинга, не фактический ввод' }} /> : <CommissioningEmpty text="Годовые факты и прогнозы в источниках отсутствуют." />}
  </section>;
}
