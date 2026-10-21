import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, ChevronLeft, ChevronRight, Database, Download, FileSpreadsheet, RefreshCw, Search } from 'lucide-react';
import { Chart } from './Chart';
import { ApiError, request } from './data';
import { date, exportCsv, number } from './format';
import { commissioningUrl, operationalChartOption, operationalChartRows, reportTablePage, tableRows } from './commissioningData';
import { operationalScopeParams } from './operationalViewData';
import './visual-polish.css';
import type { Cell, CommissioningKind, CommissioningMetadata, CommissioningSelection, OperationalReport, ReportTable } from './commissioningData';
import { useCommissioningData } from './useCommissioningData';
import CommissioningStructure from './CommissioningStructure';
import type { Theme } from './types';

export type CommissioningChange = (values: Record<string, string | null>, replace?: boolean) => void;
export function CommissioningEmpty({ text = 'Нет данных.' }: { text?: string }) { return <div className="empty-state"><Database size={20} /><span>{text}</span></div>; }
export function CommissioningState({ error, retry, catalog, caption }: { error: string; retry: () => void; catalog: boolean; caption: string }) {
  return error ? <div className="load-state" role="alert"><AlertCircle size={26} /><h2>{caption} API недоступен</h2><p>{error}</p><button onClick={retry}><RefreshCw size={15} />Повторить</button></div> : <div className="load-state" role="status" aria-live="polite"><RefreshCw className="loading-icon" size={26} /><p>{catalog ? 'Загрузка отчёта…' : 'Загрузка каталога…'}</p></div>;
}
export function CommissioningExport({ kind, selection, version, caption: suppliedCaption }: { kind: CommissioningKind; selection: CommissioningSelection; version: string; caption?: string }) {
  const active = useRef<AbortController>(), scope = JSON.stringify([kind, selection, version]);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  useEffect(() => { setBusy(false); setError(''); return () => active.current?.abort(); }, [scope]);
  async function download() {
    active.current?.abort(); const controller = new AbortController(); active.current = controller;
    setBusy(true); setError('');
    try {
      const blob = await (await request(commissioningUrl(kind, selection, version), controller.signal)).blob();
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = `${kind}-${selection.year}-q${selection.quarter}.xlsx`;
      document.body.append(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof ApiError && e.status === 409 ? 'Данные изменились. Перезагрузите страницу, затем скачайте Excel.' : `${e instanceof Error ? e.message : 'Не удалось скачать Excel.'} Повторите скачивание.`);
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }
  const caption = suppliedCaption ?? (kind === 'operational' ? 'Скачать оперативный ввод Excel' : 'Скачать линейные объекты Excel');
  return <div className="profile-export commissioning-export"><button className="icon-button" title={caption} aria-label={caption} disabled={busy} onClick={download}>{busy ? <RefreshCw size={17} className="loading-icon" /> : <FileSpreadsheet size={17} />}</button>{error && <span role="alert" className="warning-text">{error}</span>}</div>;
}
export function CommissioningEvidence({ metadata, anchor, notes = [], detail }: { metadata: CommissioningMetadata; anchor: string; notes?: string[]; detail?: OperationalReport['sourceDetails'] }) {
  const candidates = metadata.source.fileEvidence === 'candidates';
  return <section className="section sources-section" id={anchor}><details><summary><Database size={17} /><span>Исходные файлы и даты</span></summary><p className="muted">{candidates ? 'Дата файлов-кандидатов' : 'Дата отчёта'}: {date(metadata.source.date)}</p>{metadata.source.issues.map(([level, text], i) => <p className="method-note warning-text" key={i}><AlertCircle size={14} /><span>{level}: {text}</span></p>)}{notes.map((text, i) => <p key={i} className="method-note">{text}</p>)}<p className="muted">Дата формирования: {date(metadata.generatedAt)} · версия: {metadata.version}</p>{metadata.source.dateEvidence && <p className="muted">{metadata.source.dateEvidence}</p>}<div className="source-list"><div><strong>{candidates ? 'Файлы-кандидаты' : 'Выбранный файл отчёта'}</strong><span>{date(metadata.source.date)}</span><div>{metadata.source.files.map((file, i) => <code key={i}>{file}</code>)}{candidates && <p>Кандидаты источника, не журнал прочитанных файлов.</p>}</div></div>{detail && <div><strong>Источник месячной истории</strong><span>{date(detail.date)}</span><div><code>{detail.file ?? 'Файл не указан'}</code></div></div>}</div></details></section>;
}
export function QuarterControls({ years, quarters, year, quarter, cumulative, change, resetQuarterOnYearChange = false }: { years: { id: string; label: string }[]; quarters: number[]; year: number; quarter: number; cumulative: boolean; change: CommissioningChange; resetQuarterOnYearChange?: boolean }) {
  return <><label><span>Год</span><select aria-label="Год" value={year} onChange={e => change({ year: e.target.value, ...(resetQuarterOnYearChange ? { quarter: null } : {}) })}>{years.map(y => <option key={y.id} value={y.id}>{y.label}</option>)}</select></label><label><span>Квартал</span><select aria-label="Квартал" value={quarter} onChange={e => change({ quarter: e.target.value })}>{quarters.map(q => <option key={q} value={q}>{q} квартал</option>)}</select></label><fieldset className="commissioning-mode"><legend>Расчёт периода</legend>{[{ value: false, label: 'За квартал' }, { value: true, label: 'С начала года' }].map(mode => <label key={String(mode.value)}><input type="radio" name="commissioning-mode" checked={cumulative === mode.value} onChange={() => change({ cumulative: String(mode.value) })} /><span>{mode.label}</span></label>)}</fieldset></>;
}
export function CommissioningTable({ table, params, change, scope, filename, cellText }: { table: ReportTable; params: URLSearchParams; change: CommissioningChange; scope: string; filename: string; cellText?: (value: Cell, column: string, row: Record<string, Cell>) => string }) {
  const searchKey = `${scope}Search`, pageKey = `${scope}Page`, sizeKey = `${scope}Size`, query = params.get(searchKey) ?? '';
  const view = reportTablePage(table, query, params.get(pageKey), params.get(sizeKey));
  useEffect(() => {
    const values: Record<string, string> = {};
    if (params.has(pageKey) && params.get(pageKey) !== String(view.current)) values[pageKey] = String(view.current);
    if (params.has(sizeKey) && params.get(sizeKey) !== String(view.pageSize)) values[sizeKey] = String(view.pageSize);
    if (Object.keys(values).length) change(values, true);
  }, [params, pageKey, sizeKey, view.current, view.pageSize]);
return <details className="visual-data-disclosure"><summary>Данные: {table.title}</summary><div className="commissioning-table" aria-label={table.title}><div className="section-heading commissioning-table-heading"><div><h3>{table.title}</h3><p>{number(table.rows.length)} строк</p></div><button className="icon-button" title={`Скачать все строки CSV: ${table.title}`} aria-label={`Скачать все строки CSV: ${table.title}`} onClick={() => exportCsv(tableRows(table), `${filename}.csv`)}><Download size={17} /></button></div><div className="object-toolbar"><label className="object-search"><Search size={14} /><input type="search" aria-label={`Поиск: ${table.title}`} placeholder="Поиск" value={query} onChange={e => change({ [searchKey]: e.target.value || null, [pageKey]: null })} /></label><label className="apartment-page-size"><span>Строк</span><select aria-label={`Строк на странице: ${table.title}`} value={view.pageSize} onChange={e => change({ [sizeKey]: e.target.value, [pageKey]: null })}>{[20, 50, 100].map(size => <option key={size}>{size}</option>)}</select></label></div><div className="table-wrap" tabIndex={0} role="region" aria-label={`Таблица: ${table.title}`}><table><thead><tr>{table.columns.map(c => <th scope="col" key={c.id}>{c.label}</th>)}</tr></thead><tbody>{view.shown.map((r, i) => <tr key={i}>{table.columns.map((c, j) => { const value = r[c.id], text = cellText ? cellText(value, c.id, r) : typeof value === 'number' ? value.toLocaleString('ru-RU', { maximumFractionDigits: 20 }) : value ?? '—'; return j === 0 ? <th scope="row" key={c.id}>{text}</th> : <td key={c.id}>{text}</td>; })}</tr>)}</tbody></table></div>{!view.shown.length && <CommissioningEmpty text={table.rows.length ? 'Ничего не найдено.' : 'Нет данных для выбранного периода.'} />}<div className="pagination"><span aria-live="polite">{number(view.total)} строк · страница {view.current} из {view.pages}</span><button className="icon-button" title={`Предыдущая страница: ${table.title}`} aria-label={`Предыдущая страница: ${table.title}`} disabled={view.current === 1} onClick={() => change({ [pageKey]: String(view.current - 1) })}><ChevronLeft size={15} /></button><button className="icon-button" title={`Следующая страница: ${table.title}`} aria-label={`Следующая страница: ${table.title}`} disabled={view.current === view.pages} onClick={() => change({ [pageKey]: String(view.current + 1) })}><ChevronRight size={15} /></button></div></div></details>;
}
export function periodCaption(year: number, quarter: number, cumulative: boolean) { return `${cumulative && quarter > 1 ? `1–${quarter} кварталы` : `${quarter} квартал`} ${year}`; }
function OperationalSeries({ scope, theme, params, change }: { scope: 'housing' | 'nonres'; theme: Theme; params: URLSearchParams; change: CommissioningChange }) {
  const scoped = useMemo(() => operationalScopeParams(params, scope), [params, scope]);
  const { catalog, selection, data, error, retry } = useCommissioningData('operational', scoped);
  const title = scope === 'housing' ? 'Ввод жилья' : 'Ввод нежилой недвижимости', table = data?.tables.find(item => item.id === scope);
  useEffect(() => {
    if (selection && params.get(`${scope}Month`) !== String(selection.month)) change({ [`${scope}Month`]: String(selection.month) }, true);
  }, [selection?.month, params, scope]);
  return <section className="section operational-series" aria-label={title}>
    <div className="section-heading"><div><h2>{title}</h2><p>Москва · с 2011 года · млн м²</p></div>{data && <CommissioningExport kind="operational" selection={data.selection} version={data.version} caption={`Скачать ${title.toLocaleLowerCase('ru')} Excel`} />}</div>
    {catalog && selection && <div className="filters commissioning-filters chart-scope-filters"><label><span>Период с начала года</span><select aria-label={`Период: ${title}`} value={selection.month} onChange={e => change({ [`${scope}Month`]: e.target.value })}>{catalog.months.map(month => <option key={month.id} value={month.id}>{month.label}</option>)}</select></label>{scope === 'nonres' && <label className="commissioning-checkbox"><input type="checkbox" aria-label="Без нежилых помещений в жилых объектах" checked={selection.excludeMkd} onChange={e => change({ nonresExcludeMkd: String(e.target.checked) })} /><span>Без нежилых помещений в жилых объектах</span></label>}</div>}
    {error || !data ? <CommissioningState error={error} retry={retry} catalog={!!catalog} caption={title} /> : table?.chart.x.length ? <Chart key={`${data.version}-${scope}-${selection?.month}-${selection?.excludeMkd}`} theme={theme} label={title} option={operationalChartOption(table, data.periodLabel, theme)} height={380} rows={operationalChartRows(table)} filename={`operational-${scope}-m${selection?.month}`} /> : <CommissioningEmpty />}
  </section>;
}
export default function Operational({ theme, query, change }: { theme: Theme; query: string; change: CommissioningChange }) {
  const params = useMemo(() => new URLSearchParams(query), [query]);
  const structureParams = useMemo(() => operationalScopeParams(params, 'structure'), [params]);
  const { catalog, selection, data, error, retry } = useCommissioningData('operational', structureParams);
  useEffect(() => {
    if (!selection) return;
    const values = { year: String(selection.year), quarter: String(selection.quarter), cumulative: String(selection.cumulative) };
    const repairs = Object.fromEntries(Object.entries(values).filter(([key, value]) => params.get(key) !== value));
    if (Object.keys(repairs).length) change(repairs, true);
  }, [selection?.year, selection?.quarter, selection?.cumulative, params]);
  const metadata = data ?? catalog;
  return <main className="commissioning-page operational-page" id="operational-page">
    <div className="page-heading"><div><div className="eyebrow">АНАЛИТИКА НЕДВИЖИМОСТИ</div><h1>Ввод недвижимости · оперативные данные</h1></div></div>
    <section className="section operational-structure"><div className="section-heading"><div><h2>Структура ввода</h2><p>Москва · {data?.structure?.actualMonth ? `${catalog?.months.find(month => month.id === data.structure!.actualMonth)?.label} ${data.structure.year}` : 'С начала года'}{data?.sourceDetails.date ? ` · ${date(data.sourceDetails.date)}` : ''}</p></div>{data && <CommissioningExport kind="operational" selection={data.selection} version={data.version} caption="Скачать структуру ввода Excel" />}</div>
      {catalog && selection && <div className="filters commissioning-filters chart-scope-filters"><label><span>Год</span><select aria-label="Год структуры ввода" value={selection.year} onChange={e => change({ year: e.target.value })}>{catalog.years.map(year => <option key={year} value={year}>{year}</option>)}</select></label><label><span>Накопленный период</span><select aria-label="Период структуры ввода" value={selection.month} onChange={e => change({ structureMonth: e.target.value })}>{catalog.months.map(month => <option key={month.id} value={month.id}>{month.label}</option>)}</select></label></div>}
      {error || !data ? <CommissioningState error={error} retry={retry} catalog={!!catalog} caption="Структура ввода" /> : data.structure ? <><CommissioningStructure data={data.structure} /><details className="visual-data-disclosure"><summary>Данные и скачивание</summary><button onClick={() => exportCsv([['Показатель', 'млн м²', 'Известная сумма, млн м²', 'Прирост за месяц, млн м²', 'К АППГ, %', 'Реновация, млн м²'], ...data.structure!.rows.map(row => [row.label, row.value, row.knownSum, row.monthChange, row.yearChangePct, row.renovation])], `operational-structure-${selection?.year}-m${selection?.month}.csv`)}><Download size={16} />CSV</button><div className="table-wrap"><table><thead><tr><th>Показатель</th><th>млн м²</th><th>Прирост за месяц, млн м²</th><th>К АППГ, %</th></tr></thead><tbody>{data.structure.rows.map(row => <tr key={row.id}><th scope="row">{row.label}</th><td>{number(row.value, 2)}</td><td>{number(row.monthChange, 2)}</td><td>{number(row.yearChangePct, 1)}</td></tr>)}</tbody></table></div></details></> : <CommissioningEmpty text="Месячная структура недоступна." />}
    </section>
    <OperationalSeries scope="housing" theme={theme} params={params} change={change} />
    <OperationalSeries scope="nonres" theme={theme} params={params} change={change} />
    {metadata && <CommissioningEvidence metadata={metadata} anchor="operational-sources" notes={data?.notes} detail={data?.sourceDetails} />}
    <footer className="page-footer"><span>Аналитика Москвы</span><span>Оперативный ввод · API</span></footer>
  </main>;
}
