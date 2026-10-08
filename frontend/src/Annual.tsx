import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight, Database, Download, FileSpreadsheet, RefreshCw } from 'lucide-react';
import { Chart } from './Chart';
import { ApiError, request } from './data';
import { date, exportCsv, number } from './format';
import { CommissioningEmpty, CommissioningEvidence, CommissioningState, CommissioningTable } from './Operational';
import type { CommissioningChange } from './Operational';
import { annualChartOption, annualSummaryRows, marketParams, marketUrl, tableRows } from './annualConstructionData';
import type { MarketKind, MarketSelection } from './annualConstructionData';
import { useAnnualConstructionData } from './useAnnualConstructionData';
import type { Theme } from './types';

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
  useEffect(() => { if (selection && params.get('region') !== selection.region) change(marketParams(selection), true); }, [selection?.region, params]);
  return <main className="commissioning-page annual-commissioning-page" id="annual-page"><div className="page-heading"><div><div className="eyebrow">АНАЛИТИКА НЕДВИЖИМОСТИ</div><h1>Годовой ввод недвижимости</h1></div><div className="snapshot-label"><Database size={14} />Данные API<span>Дата файлов-кандидатов: {metadata ? date(metadata.source.date) : '—'}</span></div></div>
    {catalog && selection && <div className="filters commissioning-filters"><label><span>Регион</span><select aria-label="Регион" value={selection.region} onChange={e => change({ region: e.target.value })}>{catalog.regions.map(r => <option key={r.id} value={r.id}>{r.label}</option>)}</select></label><a className="source-anchor" href="#annual-sources"><Database size={15} />Источники<ChevronRight size={14} /></a></div>}
    {error || !catalog || selection && !data ? <CommissioningState error={error} retry={retry} catalog={!!catalog} caption="Годовой ввод" /> : !selection ? <CommissioningEmpty text="Нет доступных регионов." /> : data && <><div className="developer-heading commissioning-heading"><div className="developer-name"><h2>{data.region.label}</h2><span className="region-label">2011–2026 · млн м²</span></div><MarketExport kind="annual" selection={selection} version={data.version} /></div>
      {!!data.notes.length && <div className="annual-method-notes">{data.notes.map((n, i) => <p key={i} className="method-note">{n}</p>)}</div>}
      <nav className="section-nav" aria-label="Разделы годового ввода">{data.charts.map(c => <a key={c.id} href={`#annual-${c.id}`}>{c.title}</a>)}</nav>
      {data.charts.map(c => <section className="section annual-report-section" key={c.id} id={`annual-${c.id}`}><div className="section-heading"><div><h2>{c.title}</h2><p>{data.region.label} · {c.rows.length ? `${String(c.rows[0].year)}–${String(c.rows.at(-1)!.year)}` : 'Нет данных'} · {c.unit}</p></div></div>{c.rows.length && c.series.length ? <Chart key={`${data.version}-${selection.region}-${c.id}`} theme={theme} label={c.title} height={360} option={annualChartOption(c, theme)} rows={tableRows(c)} filename={`annual-${selection.region}-${c.id}`} /> : <CommissioningEmpty />}
        {!!c.summaries.length && <div className="annual-summaries"><div className="annual-summary-heading"><h3>Итоги периодов</h3><button className="icon-button" title={`Скачать итоги CSV: ${c.title}`} aria-label={`Скачать итоги CSV: ${c.title}`} onClick={() => exportCsv(annualSummaryRows(c), `annual-${selection.region}-${c.id}-totals.csv`)}><Download size={16} /></button></div>{c.summaries.map((s, i) => <div key={i} className="annual-summary-period"><h4>{s.from}–{s.to}</h4><dl>{s.values.map(v => <div key={v.id}><dt>{v.label}</dt><dd>{number(v.value, 1)}<span>{c.unit}</span></dd></div>)}</dl></div>)}</div>}
        <CommissioningTable table={c} params={params} change={change} scope={`annual${c.id}`} filename={`annual-${selection.region}-${c.id}`} cellText={(v, col) => typeof v === 'number' ? number(v, col === 'year' ? 0 : 1) : v ?? '—'} />
      </section>)}{!data.charts.length && <CommissioningEmpty />}</>}
    {metadata && <CommissioningEvidence metadata={metadata} anchor="annual-sources" />}<footer className="page-footer"><span>Аналитика Москвы</span><span>Годовой ввод · API</span></footer></main>;
}
