import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { AlertCircle, ChevronDown, Database, Download, FileSpreadsheet, ImageDown, RefreshCw, RotateCcw } from 'lucide-react';
import * as echarts from 'echarts/core';
import { BarChart } from 'echarts/charts';
import { AriaComponent, GraphicComponent, GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { ApiError, request } from './data';
import { date, download, exportCsv, number } from './format';
import { breakdownOption, exactNumber, forecastBasisNote, forecastFields, loadPermitsSelection, permitKind, permitKindChange, permitPageFilters, permitStructureParams, permitsMainOption, permitsMainRows, permitsUrl } from './constructionPagesData';
import { permitBreakdownRows } from './permitPresentation';
import type { PermitChartView } from './permitPresentation';
import type { Cell } from './commissioningData';
import type { ConstructionPagesMetadata, PermitsData } from './constructionPagesData';
import type { CommissioningChange } from './Operational';
import type { Theme } from './types';
import './construction-pages.css';

echarts.use([BarChart, AriaComponent, GraphicComponent, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

function PermitChart({ buildOption, theme, label, rows, filename, height = 390 }: { buildOption: (view: PermitChartView) => echarts.EChartsCoreOption; theme: Theme; label: string; rows: Cell[][]; filename: string; height?: number }) {
  const container = useRef<HTMLDivElement>(null), instance = useRef<echarts.ECharts>();
  const [width, setWidth] = useState(600), [hidden, setHidden] = useState<string[]>([]), [showData, setShowData] = useState(false);
  const option = useMemo(() => buildOption({ width, height, hidden }), [buildOption, width, height, hidden]);
  const series = option.series as { name: string; itemStyle: { color: string; borderType?: string }; data: (number | null)[] }[];
  useEffect(() => {
    if (!container.current) return;
    const chart = echarts.init(container.current, undefined, { renderer: 'canvas' }); instance.current = chart;
    const observer = new ResizeObserver(entries => { setWidth(entries[0].contentRect.width); chart.resize(); });
    observer.observe(container.current); setWidth(container.current.clientWidth);
    return () => { observer.disconnect(); chart.dispose(); instance.current = undefined; };
  }, []);
  useEffect(() => {
    instance.current?.setOption({ ...option, legend: { show: false, selected: Object.fromEntries(series.map(s => [s.name, !hidden.includes(s.name)])) },
      backgroundColor: theme === 'light' ? '#ffffff' : '#202225', textStyle: { fontFamily: 'Segoe UI, Arial, sans-serif' }, aria: { enabled: true },
      animation: false,
    }, true);
  }, [option, theme, hidden]);
  function highlight(index: number) {
    if (hidden.includes(series[index].name)) return;
    instance.current?.dispatchAction({ type: 'highlight', seriesIndex: index });
    const dataIndex = series[index].data.reduce<number>((last, value, i) => value === null ? last : i, -1);
    if (dataIndex >= 0) instance.current?.dispatchAction({ type: 'showTip', seriesIndex: index, dataIndex });
  }
  function clearHighlight() { instance.current?.dispatchAction({ type: 'downplay' }); instance.current?.dispatchAction({ type: 'hideTip' }); }
  return <div className="chart-block permit-chart">
    <div className="legend chart-legend">{series.map((s, index) => <button key={s.name} type="button" className={hidden.includes(s.name) ? 'legend-off' : ''} aria-pressed={!hidden.includes(s.name)} title={`${hidden.includes(s.name) ? 'Показать' : 'Скрыть'}: ${s.name}`} onMouseEnter={() => highlight(index)} onMouseLeave={clearHighlight} onFocus={() => highlight(index)} onBlur={clearHighlight} onClick={() => { clearHighlight(); setHidden(previous => previous.includes(s.name) ? previous.filter(name => name !== s.name) : [...previous, s.name]); }}><i className={s.itemStyle.borderType === 'dashed' ? 'forecast-swatch' : undefined} style={{ background: s.itemStyle.color }} />{s.name}</button>)}</div>
    <div ref={container} className="chart-canvas" style={{ height }} role="img" aria-label={label} />
    <div className="chart-data"><button type="button" className="data-toggle" onClick={() => setShowData(value => !value)} aria-expanded={showData}><ChevronDown size={13} className={showData ? 'chevron expanded' : 'chevron'} />Данные и скачивание</button></div>
    {showData && <div className="chart-data-panel"><div className="export-actions"><button type="button" onClick={() => exportCsv(rows, `${filename}.csv`)}><Download size={15} />CSV</button><button type="button" onClick={() => { const chart = instance.current; if (chart) download(chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: theme === 'light' ? '#ffffff' : '#202225' }), `${filename}.png`); }}><ImageDown size={15} />PNG</button></div><div className="table-wrap"><table><thead><tr>{rows[0]?.map((cell, i) => <th key={i}>{cell}</th>)}</tr></thead><tbody>{rows.slice(1).map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{typeof cell === 'number' ? number(cell, 2) : cell ?? '—'}</td>)}</tr>)}</tbody></table></div></div>}
  </div>;
}

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

function usePermitsFeed(filters: ReturnType<typeof permitPageFilters>) {
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
  const params = useMemo(() => new URLSearchParams(query), [query]), filters = permitPageFilters(params);
  const { data, error, retry } = usePermitsFeed(filters);
  useEffect(() => {
    if (!data) return;
    const values: Record<string, string | null> = {};
    if (params.get('permit_kind') !== data.selection.permitKind) values.permit_kind = data.selection.permitKind;
    if (params.get('month') !== String(data.selection.month)) values.month = String(data.selection.month);
    for (const key of ['mode', 'monthTo'] as const) {
      if (params.get(permitStructureParams[key]) !== String(data.selection.structure[key])) values[permitStructureParams[key]] = String(data.selection.structure[key]);
    }
    for (const key of ['monthFrom', 'yearFrom', 'yearTo'] as const) if (params.has(permitStructureParams[key])) values[permitStructureParams[key]] = null;
    if (Object.keys(values).length) change(values, true);
  }, [data, params, change]);
  const presentation = data?.permits.presentation.rows ?? [], structure = data?.selection.structure;
  const filename = data && structure ? `permits-${data.selection.permitKind}-m${data.selection.month}-structure-${structure.mode}-${structure.yearFrom}-${structure.yearTo}-m${structure.monthFrom}-${structure.monthTo}` : 'permits';
  return <main className="permits-page cp-page" id="permits-page">
    <header className="cp-page-heading"><div><div className="eyebrow">ТЕКУЩЕЕ СТРОИТЕЛЬСТВО</div><h1>Разрешения на строительство</h1></div><button className="icon-button" type="button" title="Обновить разрешения" aria-label="Обновить разрешения" onClick={retry}><RefreshCw size={17} /></button></header>
    <p className="cp-context">Москва</p>
    {error || !data ? <><ConstructionLoadState error={error} retry={retry} label="разрешения на строительство" />{error && <div className="permit-recovery"><button type="button" onClick={() => change({ month: null, ...Object.fromEntries(Object.values(permitStructureParams).map(key => [key, null])) })}><RotateCcw size={15} />Сбросить фильтры разрешений</button></div>}</> : <>
      <section className="permit-main cp-section" aria-labelledby="permit-main-title">
        <div className="cp-section-heading"><div><h2 id="permit-main-title">Выдача разрешений по годам</h2><p>{data.permits.region} · {data.permits.unit} · {date(data.source.date)}</p></div><ConstructionExport url={permitsUrl(data.selection, data.version)} filename={`${filename}.xlsx`} label="Скачать разрешения Excel" refresh={retry} /></div>
        <div className="cp-controls permit-controls"><label><span>Вид объектов</span><select aria-label="Вид объектов" value={data.selection.permitKind} onChange={e => change(permitKindChange(data.controls, data.selection.month, permitKind(e.target.value)))}>{data.controls.permitKinds.map(k => <option key={k.id} value={k.id}>{k.label}</option>)}</select></label><label><span>Накопленный период</span><select aria-label="Месяц разрешений" value={data.selection.month} disabled={!data.controls.monthsByKind[data.selection.permitKind].length} onChange={e => change({ month: e.target.value })}>{data.controls.monthsByKind[data.selection.permitKind].map(m => <option key={m} value={m}>{months[m - 1]}</option>)}</select></label><span className="cp-period-label">{data.permits.periodLabel}</span></div>
        {presentation.some(r => r.selected !== null || r.annualForecast !== null) ? <PermitChart key={`${data.version}-${data.selection.permitKind}-${data.selection.month}`} buildOption={view => permitsMainOption(data, theme, view)} theme={theme} label="Разрешения по годам: выбранный период и остаток до годового прогноза или подтверждённого итога в одном столбце" rows={permitsMainRows(data)} filename={filename} /> : <ConstructionEmpty>Нет данных о выдаче разрешений за выбранный период.</ConstructionEmpty>}
      </section>
      <section className="permit-breakdowns cp-section" aria-labelledby="permit-breakdowns-title"><div className="cp-section-heading"><div><h2 id="permit-breakdowns-title">Структура разрешённой площади</h2><p>Москва · тыс. м² · {date(data.source.date)}</p></div></div>
        <div className="cp-controls permit-structure-controls">
          <label><span>Режим структуры</span><select aria-label="Режим структуры" value={data.selection.structure.mode} onChange={e => change({ structure_mode: e.target.value })}>{data.controls.structure.modes.map(mode => <option key={mode.id} value={mode.id}>{mode.label}</option>)}</select></label>
          {data.selection.structure.mode === 'period' && <label><span>Накопленный период</span><select aria-label="Структура: накопленный период" value={data.selection.structure.monthTo} onChange={e => change({ structure_month_to: e.target.value })}>{Array.from({ length: 12 }, (_, i) => i + 1).map(month => <option key={month} value={month}>{month === 1 ? 'Январь' : `Январь – ${months[month - 1].toLocaleLowerCase('ru')}`}</option>)}</select></label>}
        </div>
        {data.breakdowns.length ? data.breakdowns.map(b => {
          return <div className="cp-subsection" key={b.id}><div className="cp-section-heading"><div><h3>{b.title}</h3><p>Москва · {b.unit} · {date(data.source.date)}</p></div></div>
            {b.series.some(s => s.points.some(p => p.y !== null)) ? <PermitChart key={`${data.version}-${data.selection.permitKind}-${b.id}-${JSON.stringify(data.selection.structure)}`} buildOption={view => breakdownOption(b, theme, view)} theme={theme} label={b.title} rows={permitBreakdownRows(b, data.version)} filename={`${filename}-${b.id}`} height={350} /> : <ConstructionEmpty>Нет подтверждённых данных за выбранный интервал.</ConstructionEmpty>}
            <details className="cp-source cp-breakdown-source"><summary>Источник и основание: {b.title}</summary><div className="cp-source-content">{b.notes.map((note, i) => <p className="cp-note" key={i}>{note}</p>)}</div></details></div>;
        }) : <ConstructionEmpty>Детализация отсутствует в источнике.</ConstructionEmpty>}
      </section>
      <ConstructionSource metadata={data} label="Источники разрешений, прогноз и даты" notes={[...data.notes, forecastBasisNote, ...data.forecast.notes]}><p className="cp-note">Прогноз: {data.forecast.source.file ?? 'Файл не указан'} · лист «{data.forecast.source.sheet}»</p><div className="cp-raw-record">{data.forecast.rows.map(r => <div key={r.year}><strong>{r.year} · ячейки прогноза</strong>{Object.entries(r.cells).map(([key, cell]) => <p key={key}>{key}: {cell}{key === 'year' ? ` · ${r.year}` : forecastFields.includes(key as typeof forecastFields[number]) ? ` · ${exactNumber(r[key as typeof forecastFields[number]])}` : ''}</p>)}</div>)}</div></ConstructionSource>
    </>}
  </main>;
}
