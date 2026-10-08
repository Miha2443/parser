import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, Check, ChevronDown, ChevronLeft, ChevronRight, Database, Download, FileSpreadsheet, ListChecks, RefreshCw, Search, X } from 'lucide-react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { Chart } from './Chart';
import { ApiError, request } from './data';
import { date, exportCsv, number } from './format';
import { economicsChartOption, economicsChartRows, economicsOptions, economicsQueryValue, economicsRepairs, economicsTablePage, economicsTableRows, economicsUrl } from './economicsData';
import type { EconomicsCatalog, EconomicsChart, EconomicsFamily, EconomicsMetadata, EconomicsOption, EconomicsReport, EconomicsSelection, EconomicsTable, EconomicsValue } from './economicsData';
import { useEconomicsData } from './useEconomicsData';
import type { Theme } from './types';
import './economics.css';

echarts.use([LineChart]);
export type EconomicsChange = (values: Record<string, string | null>, replace?: boolean) => void;
const titles = { salary: 'Среднемесячная заработная плата', ipc: 'Индексы потребительских цен', accounts: 'ВРП и ВВП' };
const labels: Record<string, string> = { period: 'Период', region: 'Регион', views: 'Отрасли', regions: 'Регионы', months: 'Месяцы', quarters: 'Кварталы', index_base: 'Тип индекса',
  structure_region: 'Регион структуры', structure_mode: 'Показатель структуры', structure_industries: 'Отрасли структуры', index_region: 'Регион индекса', index_industries: 'Отрасли индекса' };
function Empty({ text = 'Нет данных для выбранных параметров.' }: { text?: string }) { return <div className="empty-state"><Database size={20} /><span>{text}</span></div>; }

function MultiSelect({ label, options, selected, onChange }: { label: string; options: EconomicsOption[]; selected: (string | number)[]; onChange: (values: (string | number)[]) => void }) {
  const ref = useRef<HTMLDetailsElement>(null), [search, setSearch] = useState('');
  useEffect(() => {
    const outside = (event: PointerEvent) => { if (ref.current && !ref.current.contains(event.target as Node)) ref.current.open = false; };
    document.addEventListener('pointerdown', outside); return () => document.removeEventListener('pointerdown', outside);
  }, []);
  const chosen = options.filter(o => selected.includes(o.id)), shown = options.filter(o => o.label.toLocaleLowerCase('ru').includes(search.toLocaleLowerCase('ru')));
  return <div className="economics-field economics-multi"><span>{label}</span><details ref={ref} onKeyDown={e => { if (e.key === 'Escape') { if (ref.current) ref.current.open = false; ref.current?.querySelector('summary')?.focus(); } }}>
    <summary aria-label={label} title={chosen.map(o => o.label).join(', ') || 'Ничего не выбрано'}><span>{chosen.length === options.length && options.length ? 'Все' : chosen.length ? chosen.map(o => o.label).join(', ') : 'Ничего не выбрано'}</span><small>{chosen.length}</small><ChevronDown size={14} /></summary>
    <div className="economics-multi-menu"><div className="economics-multi-tools"><label className="economics-option-search"><Search size={13} /><input type="search" aria-label={`Найти: ${label}`} placeholder="Поиск" value={search} onChange={e => setSearch(e.target.value)} /></label><button className="icon-button" title={`Выбрать все: ${label}`} aria-label={`Выбрать все: ${label}`} onClick={() => onChange(options.map(o => o.id))}><ListChecks size={15} /></button><button className="icon-button" title={`Очистить: ${label}`} aria-label={`Очистить: ${label}`} onClick={() => onChange([])}><X size={15} /></button></div>
      <div className="economics-options" role="group" aria-label={`Варианты: ${label}`}>{shown.map(o => <label key={o.id}><input type="checkbox" checked={selected.includes(o.id)} onChange={e => onChange(e.target.checked ? [...selected, o.id] : selected.filter(v => v !== o.id))} /><span>{o.label}</span></label>)}{!shown.length && <span className="muted">Нет вариантов.</span>}</div>
    </div>
  </details></div>;
}
function Control({ catalog, selected, name, change, label = labels[name] ?? name }: { catalog: EconomicsCatalog; selected: EconomicsSelection; name: string; change: EconomicsChange; label?: string }) {
  const control = catalog.controls.find(c => c.id === name); if (!control) return null;
  const options = economicsOptions(catalog, name, selected), update = (value: EconomicsValue) => change({ [name]: economicsQueryValue(value) });
  if (control.type === 'multiselect') return <MultiSelect label={label} options={options} selected={selected[name] as (string | number)[]} onChange={update} />;
  if (control.type === 'checkbox') return <label className="economics-check"><input type="checkbox" checked={Boolean(selected[name])} onChange={e => update(e.target.checked)} /><span>{label}</span></label>;
  return <label className="economics-field"><span>{label}</span><select aria-label={label} value={String(selected[name])} onChange={e => update(e.target.value)}>{options.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}</select></label>;
}
function TableView({ table, unit, scope, params, change }: { table: EconomicsTable; unit: string; scope: string; params: URLSearchParams; change: EconomicsChange }) {
  const prefix = `econ_${scope}`, searchKey = `${prefix}_search`, pageKey = `${prefix}_page`, sizeKey = `${prefix}_size`;
  const query = params.get(searchKey) ?? '', view = economicsTablePage(table, query, params.get(pageKey), params.get(sizeKey));
  useEffect(() => {
    const updates: Record<string, string> = {};
    if (params.has(pageKey) && params.get(pageKey) !== String(view.current)) updates[pageKey] = String(view.current);
    if (params.has(sizeKey) && params.get(sizeKey) !== String(view.pageSize)) updates[sizeKey] = String(view.pageSize);
    if (Object.keys(updates).length) change(updates, true);
  }, [params, pageKey, sizeKey, view.current, view.pageSize]);
  return <div className="economics-table-view"><div className="economics-table-toolbar"><label className="object-search"><Search size={14} /><input type="search" placeholder="Поиск" aria-label={`Поиск: ${scope}`} value={query} onChange={e => change({ [searchKey]: e.target.value || null, [pageKey]: null })} /></label><label className="economics-size"><span>Строк</span><select aria-label={`Строк на странице: ${scope}`} value={view.pageSize} onChange={e => change({ [sizeKey]: e.target.value, [pageKey]: null })}>{[20, 50, 100].map(size => <option key={size}>{size}</option>)}</select></label><span className="economics-table-unit">{unit}</span><button className="icon-button" title={`Скачать все строки CSV: ${scope}`} aria-label={`Скачать все строки CSV: ${scope}`} onClick={() => exportCsv(economicsTableRows(table), `economics-${scope}.csv`)}><Download size={16} /></button></div>
    <div className="table-wrap" role="region" tabIndex={0} aria-label={`Таблица: ${scope}`}><table className="economics-table"><thead><tr>{table.columns.map(c => <th scope="col" key={c.id}>{c.label}</th>)}</tr></thead><tbody>{view.shown.map((row, i) => <tr key={i}>{table.columns.map((c, j) => { const value = row[c.id], text = typeof value === 'number' ? value.toLocaleString('ru-RU', { maximumFractionDigits: 20 }) : value ?? '—'; return j === 0 ? <th scope="row" key={c.id}>{text}</th> : <td key={c.id}>{text}</td>; })}</tr>)}</tbody></table></div>
    {!view.shown.length && <Empty text={table.rows.length ? 'Ничего не найдено.' : 'Нет строк.'} />}
    <div className="pagination"><span aria-live="polite">{number(view.total)} строк · страница {view.current} из {view.pages}</span><button className="icon-button" title={`Предыдущая страница: ${scope}`} aria-label={`Предыдущая страница: ${scope}`} disabled={view.current === 1} onClick={() => change({ [pageKey]: String(view.current - 1) })}><ChevronLeft size={15} /></button><button className="icon-button" title={`Следующая страница: ${scope}`} aria-label={`Следующая страница: ${scope}`} disabled={view.current === view.pages} onClick={() => change({ [pageKey]: String(view.current + 1) })}><ChevronRight size={15} /></button></div>
  </div>;
}
function ChartTables({ chart, pivot, params, change }: { chart: EconomicsChart; pivot?: EconomicsTable; params: URLSearchParams; change: EconomicsChange }) {
  const key = `econ_${chart.id}_view`, mode = params.get(key) === 'rows' || !pivot ? 'rows' : 'pivot';
  const table = mode === 'pivot' ? pivot! : chart, scope = table.id;
  const modes = (['pivot', 'rows'] as const).filter(m => m !== 'pivot' || pivot);
  return <div className="economics-tables"><div className="economics-table-tabs" role="tablist" aria-label={`Таблицы: ${chart.title}`}>
    {modes.map((m, i) => <button key={m} id={`econ-tab-${chart.id}-${m}`} role="tab" tabIndex={m === mode ? 0 : -1} aria-selected={m === mode} aria-controls={`econ-panel-${chart.id}`} onClick={() => change({ [key]: m })} onKeyDown={e => {
      const next = e.key === 'ArrowRight' ? (i + 1) % modes.length : e.key === 'ArrowLeft' ? (i + modes.length - 1) % modes.length : e.key === 'Home' ? 0 : e.key === 'End' ? modes.length - 1 : -1;
      if (next !== -1) { e.preventDefault(); change({ [key]: modes[next] }); document.getElementById(`econ-tab-${chart.id}-${modes[next]}`)?.focus(); }
    }}>{m === 'pivot' ? 'Показанная таблица' : 'Данные выгрузки'}{m === mode && <Check size={12} />}</button>)}
  </div><div id={`econ-panel-${chart.id}`} role="tabpanel" aria-labelledby={`econ-tab-${chart.id}-${mode}`}><TableView key={scope} table={table} unit={chart.unit} scope={scope} params={params} change={change} /></div></div>;
}
function ExcelExport({ family, data, retry }: { family: EconomicsFamily; data: EconomicsReport | null; retry: () => void }) {
  const active = useRef<AbortController>(), [busy, setBusy] = useState(false), [error, setError] = useState(''), [conflict, setConflict] = useState(false);
  const key = JSON.stringify([data?.version, data?.selection]);
  useEffect(() => { setBusy(false); setError(''); setConflict(false); return () => active.current?.abort(); }, [family, key]);
  async function download() {
    if (!data) return;
    active.current?.abort(); const controller = new AbortController(); active.current = controller; setBusy(true); setError(''); setConflict(false);
    try {
      const response = await request(economicsUrl(family, data.selection, data.version), controller.signal);
      if (response.headers.has('X-Data-Version') && response.headers.get('X-Data-Version') !== data.version) throw new ApiError(409);
      const blob = await response.blob(); if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob), link = document.createElement('a'); link.href = url; link.download = `economics-${family}.xlsx`; document.body.append(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      if (!controller.signal.aborted) { const changed = e instanceof ApiError && e.status === 409; setConflict(changed); setError(changed ? 'Источники изменились. Обновите показатели перед выгрузкой Excel.' : `${e instanceof Error ? e.message : 'Не удалось скачать Excel.'} Повторите скачивание.`); }
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <div className="economics-export"><button className="icon-button" title="Скачать полный отчёт Excel" aria-label="Скачать полный отчёт Excel" disabled={!data || busy} onClick={download}>{busy ? <RefreshCw size={17} className="loading-icon" /> : <FileSpreadsheet size={17} />}</button>{error && <div role="alert" className="warning-text"><span>{error}</span>{conflict && <button onClick={retry}><RefreshCw size={14} />Обновить данные</button>}</div>}</div>;
}
function Evidence({ metadata, params, change }: { metadata: EconomicsMetadata; params: URLSearchParams; change: EconomicsChange }) {
  const source = metadata.source, provenance = { ...source.provenance, id: 'provenance', title: 'Исходные файлы и даты' };
  return <section id="economics-sources" className="section sources-section"><div className="section-heading"><h2>Источники и даты</h2><Database size={17} /></div><p className="muted">{source.downloadSummary}</p>{source.issues.map(([level, message], i) => <p className="method-note warning-text" key={i}><AlertCircle size={14} /><span>{level}: {message}</span></p>)}
    <details className="economics-evidence"><summary>Метаданные и происхождение данных</summary><p className="muted">Дата формирования: <time dateTime={metadata.generatedAt}>{metadata.generatedAt}</time> · версия: <code>{metadata.version}</code> · схема: {metadata.schemaVersion}</p><p className="muted">Дата исходных файлов (МСК): {date(source.date)}</p><p className="muted">Дата файла — время изменения, не дата скачивания. Файлы-кандидаты не подтверждают участие каждой строки.</p><div className="source-list"><div><strong>Файлы-кандидаты</strong><span>{source.files.length}</span><div>{source.files.map(file => <code key={file}>{file}</code>)}</div></div><div><strong>Источники в загруженных строках</strong><span>{source.recordedSources.length}</span><div>{source.recordedSources.map(file => <code key={file}>{file}</code>)}</div></div></div><p className="muted">{source.dateEvidence}</p><p className="muted">{source.recordedSourceEvidence}</p><TableView table={provenance} unit="" scope="provenance" params={params} change={change} /></details>
  </section>;
}
export default function Economics({ family, theme, query, change }: { family: EconomicsFamily; theme: Theme; query: string; change: EconomicsChange }) {
  const params = useMemo(() => new URLSearchParams(query), [query]), { catalog, selection, data, error, retry } = useEconomicsData(family, params);
  useEffect(() => { if (catalog && data) { const repairs = economicsRepairs(catalog, params, data.selection); if (Object.keys(repairs).length) change(repairs, true); } }, [catalog, data, params]);
  const metadata = data ?? catalog;
  const renderChart = (chart?: EconomicsChart) => chart ? <><Chart key={`${family}-${data?.version}-${JSON.stringify(data?.selection)}-${chart.id}`} option={economicsChartOption(chart, theme)} theme={theme} label={chart.title} rows={economicsChartRows(chart)} filename={`economics-${family}-${chart.id}`} height={320} dataContent={<ChartTables chart={chart} pivot={data?.tables.find(t => t.id === `${chart.id}_pivot`)} params={params} change={change} />} />{!chart.series.some(s => s.points.some(p => p.y !== null)) && <Empty />}
    </> : !data && !error ? <div className="economics-chart-loading" role="status"><RefreshCw size={18} className="loading-icon" /><span>Загрузка показателей…</span></div> : <Empty />;
  return <main className="economics-page" id="economics-page" data-family={family}>
    <div className="page-heading"><div><div className="eyebrow">ЭКОНОМИКА</div><h1>{titles[family]}</h1></div><div className="snapshot-label"><Database size={14} />Данные API<span>Дата исходных файлов: {date(metadata?.source.date)}</span></div></div>
    <div className="economics-actions"><a className="source-anchor" href="#economics-sources"><Database size={15} />Источники<ChevronRight size={14} /></a><button className="icon-button" title="Обновить показатели" aria-label="Обновить показатели" onClick={retry}><RefreshCw size={16} /></button><ExcelExport family={family} data={data} retry={retry} /></div>

    {error && <div className="load-state" role="alert"><AlertCircle size={26} /><h2>Экономические показатели API недоступны</h2><p>{error}</p><button onClick={retry}><RefreshCw size={15} />Повторить</button></div>}
    {!catalog && !error && <div className="load-state" role="status"><RefreshCw size={26} className="loading-icon" /><p>Загрузка каталога экономики…</p></div>}
    {family === 'accounts' && catalog && selection ? <>{catalog.blocks?.map((block, i) => <section className="section economics-chart-section" id={`econ-${block.id}`} key={block.id} aria-label={block.title}>
      <div className="section-heading"><div><h2>{i + 1}. {block.title}</h2><p>{data?.charts.find(c => c.id === block.id)?.unit ?? block.unit ?? (selection.structure_mode === 'share' ? '%' : 'трлн руб')} · с {catalog.minimumYear} года</p></div></div>
      <div className="economics-block-filters">{i < 4 ? <Control catalog={catalog} selected={selection} name={block.regionControl} label={`Регионы блока ${i + 1}`} change={change} /> : i === 4 ? <><Control catalog={catalog} selected={selection} name="structure_region" change={change} /><Control catalog={catalog} selected={selection} name="structure_mode" change={change} /><Control catalog={catalog} selected={selection} name="structure_industries" change={change} /></> : <><Control catalog={catalog} selected={selection} name="index_region" change={change} /><Control catalog={catalog} selected={selection} name="index_industries" change={change} /><Control catalog={catalog} selected={selection} name="show_total" label="Всего по всем отраслям" change={change} /></>}</div>
      {renderChart(data?.charts.find(c => c.id === block.id))}
    </section>)}</> : catalog && !error && <section className="section economics-chart-section" aria-label={titles[family]}><div className="section-heading"><div><h2>{data?.charts[0]?.title ?? titles[family]}</h2><p>{family === 'salary' ? 'руб.' : '%'}</p></div></div>{catalog && selection && family !== 'accounts' && <div className="filters economics-filters"><Control catalog={catalog} selected={selection} name="period" change={change} /><Control catalog={catalog} selected={selection} name={family === 'salary' ? 'region' : 'regions'} change={change} />{family === 'salary' && <Control catalog={catalog} selected={selection} name="views" change={change} />}
      {selection.period !== 'year' && (family === 'salary' ? <div className="economics-field economics-value-mode"><span>Значение</span><div className="economics-segmented" role="group" aria-label="Значение">{[false, true].map(ytd => <button key={String(ytd)} aria-pressed={selection.ytd === ytd} onClick={() => change({ ytd: String(ytd) })}>{ytd ? 'С начала года' : selection.period === 'quarter' ? 'За квартал' : 'За месяц'}</button>)}</div></div> : <Control catalog={catalog} selected={selection} name="index_base" change={change} />)}
      {selection.period !== 'year' && <Control catalog={catalog} selected={selection} name={selection.period === 'quarter' ? 'quarters' : 'months'} change={change} />}</div>}{renderChart(data?.charts[0])}</section>}
    {metadata && <Evidence metadata={metadata} params={params} change={change} />}
    <footer className="page-footer"><span>Аналитика Москвы</span><span>{titles[family]} · API</span></footer>
  </main>;
}
