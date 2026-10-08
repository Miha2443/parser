import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, CalendarDays, ChevronLeft, ChevronRight, Database, Download, FileSpreadsheet, RefreshCw, Search } from 'lucide-react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { Chart } from './Chart';
import { ApiError, request } from './data';
import { date, exportCsv, number } from './format';
import { salesChartOption, salesChartRows, salesTablePage, salesTableRows, salesUrl } from './salesData';
import type { SalesMetadata, SalesTable } from './salesData';
import { useSalesData } from './useSalesData';
import type { Theme } from './types';

echarts.use([LineChart]);
type Change = (values: Record<string, string | null>, replace?: boolean) => void;
function Empty({ text = 'Нет данных.' }: { text?: string }) { return <div className="empty-state"><Database size={20} /><span>{text}</span></div>; }
function SalesExport({ region, period, version }: { region: string; period: string; version: string }) {
  const active = useRef<AbortController>();
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  useEffect(() => { setBusy(false); setError(''); return () => active.current?.abort(); }, [region, period, version]);
  async function download() {
    active.current?.abort(); const controller = new AbortController(); active.current = controller;
    setBusy(true); setError('');
    try {
      const blob = await (await request(salesUrl(region, period, version), controller.signal)).blob();
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = `sales-${region.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_')}-${period}.xlsx`;
      document.body.append(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof ApiError && e.status === 409 ? 'Данные изменились. Перезагрузите страницу, затем скачайте Excel.' : `${e instanceof Error ? e.message : 'Не удалось скачать Excel.'} Повторите скачивание.`);
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <div className="profile-export sales-export"><button className="icon-button" title="Скачать распроданность Excel" aria-label="Скачать распроданность Excel" disabled={busy} onClick={download}>{busy ? <RefreshCw size={17} className="loading-icon" /> : <FileSpreadsheet size={17} />}</button>{error && <span role="alert" className="warning-text">{error}</span>}</div>;
}
function SegmentTable({ table, params, change, filename }: { table: SalesTable; params: URLSearchParams; change: Change; filename: string }) {
  const scope = `sales${table.id}`, searchKey = `${scope}Search`, pageKey = `${scope}Page`, sizeKey = `${scope}Size`;
  const query = params.get(searchKey) ?? '', view = salesTablePage(table, query, params.get(pageKey), params.get(sizeKey));
  useEffect(() => {
    const values: Record<string, string> = {};
    if (params.has(pageKey) && params.get(pageKey) !== String(view.current)) values[pageKey] = String(view.current);
    if (params.has(sizeKey) && params.get(sizeKey) !== String(view.pageSize)) values[sizeKey] = String(view.pageSize);
    if (Object.keys(values).length) change(values, true);
  }, [params, pageKey, sizeKey, view.current, view.pageSize]);
  return <div role="tabpanel" id={`sales-panel-${table.id}`} aria-labelledby={`sales-tab-${table.id}`} tabIndex={0}>
    <div className="section-heading sales-table-heading"><div><h3>{table.title}</h3><p>{number(table.rows.length)} строк</p></div><button className="icon-button" title={`Скачать все строки CSV: ${table.title}`} aria-label={`Скачать все строки CSV: ${table.title}`} onClick={() => exportCsv(salesTableRows(table), `${filename}-${table.id}.csv`)}><Download size={17} /></button></div>
    <div className="object-toolbar"><label className="object-search"><Search size={14} /><input type="search" aria-label={`Поиск: ${table.title}`} placeholder="Поиск" value={query} onChange={e => change({ [searchKey]: e.target.value || null, [pageKey]: null })} /></label><label className="apartment-page-size"><span>Строк</span><select aria-label={`Строк на странице: ${table.title}`} value={view.pageSize} onChange={e => change({ [sizeKey]: e.target.value, [pageKey]: null })}>{[20, 50, 100].map(size => <option key={size}>{size}</option>)}</select></label></div>
    <div className="table-wrap" role="region" tabIndex={0} aria-label={`Таблица: ${table.title}`}><table className="sales-segment-table"><thead><tr>{table.columns.map(c => <th scope="col" key={c.id}>{c.label}</th>)}</tr></thead><tbody>{view.shown.map((row, i) => <tr key={i}>{table.columns.map((c, j) => { const value = row[c.id], text = typeof value === 'number' ? value.toLocaleString('ru-RU', { maximumFractionDigits: 20 }) : value ?? '—'; return j === 0 ? <th scope="row" key={c.id}>{text}</th> : <td key={c.id}>{text}</td>; })}</tr>)}</tbody></table></div>
    {!view.shown.length && <Empty text={table.rows.length ? 'Ничего не найдено.' : 'Нет данных для выбранного периода.'} />}
    <div className="pagination"><span aria-live="polite">{number(view.total)} строк · страница {view.current} из {view.pages}</span><button className="icon-button" title={`Предыдущая страница: ${table.title}`} aria-label={`Предыдущая страница: ${table.title}`} disabled={view.current === 1} onClick={() => change({ [pageKey]: String(view.current - 1) })}><ChevronLeft size={15} /></button><button className="icon-button" title={`Следующая страница: ${table.title}`} aria-label={`Следующая страница: ${table.title}`} disabled={view.current === view.pages} onClick={() => change({ [pageKey]: String(view.current + 1) })}><ChevronRight size={15} /></button></div>
  </div>;
}
function Evidence({ metadata }: { metadata: SalesMetadata }) {
  return <section id="sales-sources" className="section sources-section"><div className="section-heading"><h2>Источники и даты</h2><Database size={17} /></div><p className="muted">Дата файлов-кандидатов: {date(metadata.source.date)}</p>{metadata.source.issues.map(([level, msg], i) => <p key={i} className="method-note warning-text"><AlertCircle size={14} /><span>{level}: {msg}</span></p>)}<details><summary>Метаданные источника</summary><p className="muted">Дата формирования: {date(metadata.generatedAt)} · версия: {metadata.version}</p>{metadata.source.dateEvidence && <p className="muted">{metadata.source.dateEvidence}</p>}<div className="source-list"><div><strong>Файлы-кандидаты</strong><span>{date(metadata.source.date)}</span><div>{metadata.source.files.map((file, i) => <code key={i}>{file}</code>)}<p>Кандидаты источника, не журнал прочитанных файлов.</p></div></div></div></details></section>;
}
export default function Sales({ theme, query, change }: { theme: Theme; query: string; change: Change }) {
  const params = useMemo(() => new URLSearchParams(query), [query]);
  const { catalog, region, periods, period, data, error, retry } = useSalesData(params);
  const table = data?.tables.find(t => t.id === params.get('table')) ?? data?.tables[0];
  useEffect(() => {
    if (!catalog) return;
    const values: Record<string, string | null> = {};
    if (params.get('region') !== (region?.id ?? null)) values.region = region?.id ?? null;
    if (params.get('period') !== (period?.id ?? null)) values.period = period?.id ?? null;
    if (data && params.get('table') !== (table?.id ?? null)) values.table = table?.id ?? null;
    if (Object.keys(values).length) change(values, true);
  }, [catalog, region?.id, period?.id, data, table?.id, params]);
  const metadata = data ?? catalog, filename = `sales-${region?.id}-${period?.id}`;
  const index = periods.findIndex(p => p.id === period?.id);
  return <main className="sales-page" id="sales-page">
    <div className="page-heading"><div><div className="eyebrow">АНАЛИТИКА НЕДВИЖИМОСТИ</div><h1>Распроданность и стройготовность жилья</h1></div><div className="snapshot-label"><Database size={14} />Данные API<span>Дата файлов-кандидатов: {metadata ? date(metadata.source.date) : '—'}</span></div></div>
    {catalog && region && <div className="filters sales-filters"><label><span>Регион</span><select aria-label="Регион" value={region.id} onChange={e => change({ region: e.target.value, period: null })}>{catalog.regions.map(r => <option key={r.id} value={r.id}>{r.label}</option>)}</select></label><div className="sales-period"><label><span><CalendarDays size={13} />Отчётный период</span><select aria-label="Отчётный период" value={period?.id ?? ''} disabled={!periods.length} onChange={e => change({ period: e.target.value })}>{!periods.length && <option value="">Нет периодов</option>}{periods.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label><input type="range" aria-label="Месяц отчёта" aria-valuetext={period?.label ?? 'Нет периодов'} min={0} max={Math.max(0, periods.length - 1)} step={1} value={Math.max(0, index)} disabled={periods.length < 2} onChange={e => change({ period: periods[Number(e.target.value)].id })} /></div><a className="source-anchor" href="#sales-sources"><Database size={15} />Источники<ChevronRight size={14} /></a></div>}
    {error ? <div className="load-state" role="alert"><AlertCircle size={26} /><h2>Распроданность API недоступна</h2><p>{error}</p><button onClick={retry}><RefreshCw size={15} />Повторить</button></div> : catalog && (!region || !period) ? <Empty text="Нет доступных отчётных периодов." /> : !data ? <div className="load-state" role="status" aria-live="polite"><RefreshCw className="loading-icon" size={26} /><p>{catalog ? 'Загрузка распроданности…' : 'Загрузка каталога распроданности…'}</p></div> : <>
      <div className="developer-heading sales-heading"><div className="developer-name"><h2>{data.region.label}</h2><span className="region-label">{data.period.label}</span></div><SalesExport region={data.region.id} period={data.period.id} version={data.version} /></div>
      <div className="kpi-band sales-kpis">{data.metrics.map(m => <div className="metric" key={m.id}><span className="metric-label">{m.label}</span><div className="metric-number">{number(m.value, m.digits)}<span>{m.unit}</span></div></div>)}</div>
      {data.charts.map(c => <section className="section sales-chart-section" key={c.id} aria-label={c.title}><div className="section-heading"><div><h2>{c.title}</h2><p>{data.region.label}{c.kind === 'bar' ? ` · ${data.period.label}` : ''}{c.unit ? ` · ${c.unit}` : ''}</p></div></div>{c.series.some(s => s.points.some(p => p.y !== null)) ? <Chart key={`${data.region.id}-${data.period.id}-${data.version}-${c.id}`} option={salesChartOption(c, theme)} theme={theme} label={c.title} rows={salesChartRows(c)} filename={`${filename}-${c.id}`} height={320} /> : <Empty />}</section>)}
      <section className="section sales-tables"><div className="section-heading"><div><h2>Срезы по сегментам</h2><p>{data.region.label} · {data.period.label}</p></div></div><div role="tablist" className="sales-tabs" aria-label="Сегменты">{data.tables.map((t, i) => <button role="tab" id={`sales-tab-${t.id}`} aria-controls={`sales-panel-${t.id}`} aria-selected={table?.id === t.id} tabIndex={table?.id === t.id ? 0 : -1} key={t.id} onClick={() => change({ table: t.id })} onKeyDown={e => { const next = e.key === 'ArrowRight' ? (i + 1) % data.tables.length : e.key === 'ArrowLeft' ? (i + data.tables.length - 1) % data.tables.length : e.key === 'Home' ? 0 : e.key === 'End' ? data.tables.length - 1 : -1; if (next !== -1) { e.preventDefault(); change({ table: data.tables[next].id }); document.getElementById(`sales-tab-${data.tables[next].id}`)?.focus(); } }}>{t.title}</button>)}</div>{table ? <SegmentTable key={table.id} table={table} params={params} change={change} filename={filename} /> : <Empty />}</section>
    </>}
    {metadata && <Evidence metadata={metadata} />}
    <footer className="page-footer"><span>Аналитика Москвы</span><span>Распроданность и стройготовность · API</span></footer>
  </main>;
}
