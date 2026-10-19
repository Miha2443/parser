import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { AlertCircle, Database, FileSpreadsheet, RefreshCw } from 'lucide-react';
import { Chart } from './Chart';
import { ApiError, request } from './data';
import { date, number } from './format';
import { breakdownOption, exactNumber, forecastBasisNote, forecastFields, loadPermitsSelection, permitKind, permitKindChange, permitRequestFilters, permitsMainOption, permitsMainRows, permitsUrl } from './constructionPagesData';
import type { ConstructionPagesMetadata, PermitsData } from './constructionPagesData';
import type { CommissioningChange } from './Operational';
import type { Theme } from './types';
import './construction-pages.css';

export function useConstructionFeed<T>(url: string, read: (value: unknown) => T) {
  const [attempt, setAttempt] = useState(0), [result, setResult] = useState<{ key: string; data?: T; error?: string } | null>(null);
  const reader = useRef(read); reader.current = read;
  const key = JSON.stringify([url, attempt]);
  useEffect(() => {
    const controller = new AbortController(), parse = reader.current;
    setResult(null);
    request(url, controller.signal).then(r => r.json()).then(value => {
      if (!controller.signal.aborted) setResult({ key, data: parse(value) });
    }).catch(e => { if (!controller.signal.aborted) setResult({ key, error: e instanceof Error ? e.message : 'Не удалось загрузить данные.' }); });
    return () => controller.abort();
  }, [url, key]);
  return { data: result?.key === key ? result.data ?? null : null, error: result?.key === key ? result.error ?? '' : '', retry: () => setAttempt(n => n + 1) };
}

function usePermitsFeed(filters: ReturnType<typeof permitRequestFilters>) {
  const [attempt, setAttempt] = useState(0), [result, setResult] = useState<{ key: string; canonicalKey?: string; data?: PermitsData; error?: string } | null>(null);
  const key = JSON.stringify([permitsUrl(filters), attempt]);
  const matches = result?.key === key || result?.canonicalKey === key;
  useEffect(() => {
    // Reuse the discovery response after replaceState; do not enter a 404 loop.
    if (matches) return;
    const controller = new AbortController();
    setResult(null);
    loadPermitsSelection(filters, controller.signal).then(data => {
      if (!controller.signal.aborted) setResult({ key, canonicalKey: JSON.stringify([permitsUrl(data.selection), attempt]), data });
    }).catch(error => { if (!controller.signal.aborted) setResult({ key, error: error instanceof Error ? error.message : 'Не удалось загрузить разрешения.' }); });
    return () => controller.abort();
  }, [key]);
  return { data: matches ? result?.data ?? null : null, error: matches ? result?.error ?? '' : '', retry: () => setAttempt(n => n + 1) };
}

export function ConstructionLoadState({ error, retry, label }: { error: string; retry: () => void; label: string }) {
  return <div className="cp-state" role={error ? 'alert' : 'status'} aria-live="polite">{error ? <AlertCircle size={23} /> : <RefreshCw className="loading-icon" size={23} />}<p>{error || `Загрузка: ${label}…`}</p>{error && <button type="button" onClick={retry}><RefreshCw size={15} />Повторить</button>}</div>;
}
export function ConstructionEmpty({ children = 'Нет наблюдений в источнике.' }: { children?: ReactNode }) { return <p className="cp-empty"><Database size={17} />{children}</p>; }

export function ConstructionExport({ url, filename, label, refresh }: { url: string; filename: string; label: string; refresh: () => void }) {
  const active = useRef<AbortController>();
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [mismatch, setMismatch] = useState(false);
  useEffect(() => { setBusy(false); setError(''); setMismatch(false); return () => active.current?.abort(); }, [url]);
  async function download() {
    active.current?.abort(); const controller = new AbortController(); active.current = controller;
    setBusy(true); setError(''); setMismatch(false);
    try {
      const blob = await (await request(url, controller.signal)).blob();
      if (controller.signal.aborted) return;
      const objectUrl = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = objectUrl; link.download = filename;
      document.body.append(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    } catch (e) {
      if (!controller.signal.aborted) {
        const changed = e instanceof ApiError && e.status === 409;
        setMismatch(changed); setError(changed ? 'Версия источника изменилась. Обновите данные перед скачиванием.' : e instanceof Error ? e.message : 'Не удалось скачать Excel. Повторите скачивание.');
      }
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <div className="cp-export"><button type="button" className="icon-button" title={label} aria-label={label} disabled={busy} onClick={download}>{busy ? <RefreshCw size={17} className="loading-icon" /> : <FileSpreadsheet size={17} />}</button>{error && <span className="warning-text" role="alert">{error}</span>}{mismatch && <button type="button" className="icon-button" title="Обновить данные источника" aria-label="Обновить данные источника" onClick={refresh}><RefreshCw size={17} /></button>}</div>;
}
export function ConstructionSource({ metadata, label, notes = [], children }: { metadata: ConstructionPagesMetadata; label: string; notes?: string[]; children?: ReactNode }) {
  return <details className="cp-source"><summary><Database size={15} /><span>{label}</span></summary><div className="cp-source-content">
    <dl className="cp-metadata"><div><dt>Версия</dt><dd>{metadata.version}</dd></div><div><dt>Сформировано API</dt><dd>{metadata.generatedAt}</dd></div><div><dt>Дата источника / файлов</dt><dd>{date(metadata.source.date)}</dd></div><div><dt>{metadata.source.fileEvidence === 'selected' ? 'Выбранные файлы источника' : metadata.source.fileEvidence === 'candidates' ? 'Файлы-кандидаты' : 'Файлы'}</dt><dd>{metadata.source.files.length ? metadata.source.files.map((f, i) => <code key={i}>{f}</code>) : 'Не указаны'}</dd></div></dl>
    {metadata.source.dateEvidence && <p className="cp-note">{metadata.source.dateEvidence}</p>}
    {metadata.source.issues.map(([level, message], i) => <p className="cp-note warning-text" key={i}><AlertCircle size={14} /><span>{level}: {message}</span></p>)}
    {notes.map((note, i) => <p className="cp-note" key={i}>{note}</p>)}{children}
  </div></details>;
}
export function ConstructionMetric({ label, value, unit, foot, digits = unit === '%' ? 1 : 2 }: { label: string; value: number | null | undefined; unit: string; foot?: string; digits?: number }) {
  return <div className="cp-metric"><span>{label}</span><strong title={`${exactNumber(value)} ${unit}`}>{number(value, digits)}<small>{unit}</small></strong>{foot && <p>{foot}</p>}</div>;
}
const months = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

export default function PermitsPage({ theme, query, change }: { theme: Theme; query: string; change: CommissioningChange }) {
  const params = useMemo(() => new URLSearchParams(query), [query]), filters = permitRequestFilters(params);
  const { data, error, retry } = usePermitsFeed(filters);
  useEffect(() => {
    if (!data) return;
    const values: Record<string, string> = {};
    if (params.get('permit_kind') !== data.selection.permitKind) values.permit_kind = data.selection.permitKind;
    if (params.get('month') !== String(data.selection.month)) values.month = String(data.selection.month);
    if (Object.keys(values).length) change(values, true);
  }, [data, params, change]);
  const actual = data?.permits.chart;
  const latestIndex = actual ? actual.x.map((year, i) => ({ year: Number(year), i })).filter(({ i }) => actual.period[i].value !== null || actual.totals[i].value !== null).sort((a, b) => b.year - a.year)[0]?.i : undefined;
  const filename = data ? `permits-${data.selection.permitKind}-m${data.selection.month}` : 'permits';
  return <main className="permits-page cp-page" id="permits-page">
    <header className="cp-page-heading"><div><div className="eyebrow">ТЕКУЩЕЕ СТРОИТЕЛЬСТВО</div><h1>Разрешения на строительство</h1></div><button className="icon-button" type="button" title="Обновить разрешения" aria-label="Обновить разрешения" onClick={retry}><RefreshCw size={17} /></button></header>
    <p className="cp-context">Москва</p>
    {error || !data ? <ConstructionLoadState error={error} retry={retry} label="разрешения на строительство" /> : <>
      <section className="permit-main cp-section" aria-labelledby="permit-main-title">
        <div className="cp-section-heading"><div><h2 id="permit-main-title">Выдача разрешений по годам</h2><p>{data.permits.region} · {data.permits.unit}</p></div><ConstructionExport url={permitsUrl(data.selection, data.version)} filename={`${filename}.xlsx`} label="Скачать разрешения Excel" refresh={retry} /></div>
        <div className="cp-controls permit-controls"><label><span>Вид объектов</span><select aria-label="Вид объектов" value={data.selection.permitKind} onChange={e => change(permitKindChange(data.controls, data.selection.month, permitKind(e.target.value)))}>{data.controls.permitKinds.map(k => <option key={k.id} value={k.id}>{k.label}</option>)}</select></label><label><span>Накопленный период</span><select aria-label="Месяц разрешений" value={data.selection.month} disabled={!data.controls.monthsByKind[data.selection.permitKind].length} onChange={e => change({ month: e.target.value })}>{data.controls.monthsByKind[data.selection.permitKind].map(m => <option key={m} value={m}>{months[m - 1]}</option>)}</select></label><span className="cp-period-label">{data.permits.periodLabel}</span></div>
        {actual?.x.length ? <Chart key={`${data.version}-${data.selection.permitKind}-${data.selection.month}`} option={permitsMainOption(data, theme)} theme={theme} label="Разрешения: факт, остаток и отдельный годовой прогноз" rows={permitsMainRows(data)} filename={filename} height={390} /> : <ConstructionEmpty>Нет фактических данных о выдаче разрешений.</ConstructionEmpty>}
      </section>
      <section className="permit-breakdowns cp-section" aria-labelledby="permit-breakdowns-title"><div className="cp-section-heading"><div><h2 id="permit-breakdowns-title">Структура разрешённой площади</h2><p>Фактические данные за выбранный период</p></div></div>
        {data.breakdowns.length ? data.breakdowns.map(b => <div className="cp-subsection" key={b.id}><div className="cp-section-heading"><div><h3>{b.title}</h3><p>{b.periodLabel} · {b.unit}</p></div></div>{b.series.some(s => s.points.some(p => p.y !== null)) ? <Chart key={`${data.version}-${data.selection.permitKind}-${b.id}`} option={breakdownOption(b, theme)} theme={theme} label={b.title} rows={[b.columns.map(c => c.label), ...b.rows.map(r => b.columns.map(c => r[c.id]))]} filename={`${filename}-${b.id}`} height={320} /> : <ConstructionEmpty />}<details className="cp-source cp-breakdown-source"><summary>Источник и основание: {b.title}</summary><div className="cp-source-content">{b.notes.map((note, i) => <p className="cp-note" key={i}>{note}</p>)}</div></details></div>) : <ConstructionEmpty>Детализация отсутствует в источнике.</ConstructionEmpty>}
      </section>
      <section className="cp-section permit-summary" aria-labelledby="permit-summary-title"><div className="cp-section-heading"><h2 id="permit-summary-title">Последние значения</h2></div><div className="cp-metrics">
        {actual && latestIndex !== undefined && <><ConstructionMetric label={`${actual.x[latestIndex]} · ${data.permits.periodLabel}`} value={actual.period[latestIndex].value} unit={data.permits.unit} /><ConstructionMetric label={`${actual.x[latestIndex]} · итог / доступный период`} value={actual.totals[latestIndex].value} unit={data.permits.unit} /><ConstructionMetric label={`${actual.x[latestIndex]} · изменение к аналогичному периоду`} value={actual.growth[latestIndex].value} unit="%" /></>}
        {data.forecast.rows.filter(r => r.value !== null).map(r => <ConstructionMetric key={r.year} label={`${r.year} · годовой прогноз`} value={r.value} unit={data.forecast.unit} foot="Единица принята по структуре листа" />)}
      </div><p className="cp-note">Дата источника / файлов: {date(data.source.date)}. Отчётный период факта: {data.permits.periodLabel}.</p></section>
      <ConstructionSource metadata={data} label="Источники разрешений, прогноз и даты" notes={[...data.notes, forecastBasisNote, ...data.forecast.notes]}><p className="cp-note">Прогноз: {data.forecast.source.file ?? 'Файл не указан'} · лист «{data.forecast.source.sheet}»</p><div className="cp-raw-record">{data.forecast.rows.map(r => <div key={r.year}><strong>{r.year} · ячейки прогноза</strong>{Object.entries(r.cells).map(([key, cell]) => <p key={key}>{key}: {cell}{key === 'year' ? ` · ${r.year}` : forecastFields.includes(key as typeof forecastFields[number]) ? ` · ${exactNumber(r[key as typeof forecastFields[number]])}` : ''}</p>)}</div>)}</div></ConstructionSource>
    </>}
  </main>;
}
