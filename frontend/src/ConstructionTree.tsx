import { AnalystOnly, useAccess } from './auth';
import { useEffect, useRef, useState } from 'react';
import { AlertCircle, Building2, Database, Download, Info, RefreshCw } from 'lucide-react';
import { constructionTreeArea, constructionTreeCsv, constructionTreeDate, constructionTreeExact, constructionTreeFilename, constructionTreeLabels, loadConstructionTreeHistory, watchConstructionTree } from './constructionTreeData';
import type { ConstructionTreeData, ConstructionTreeId, ConstructionTreeSnapshot, ConstructionTreeState } from './constructionTreeData';
import './construction-tree.css';

function Area({ value }: { value: number | null }) {
  return <span className="ct-area"><strong>{constructionTreeArea(value)}</strong><span>млн м²</span></span>;
}

function SourceData({ data }: { data: ConstructionTreeData }) {
  const { canAnalyze } = useAccess();
  if (!canAnalyze) return null;
  const download = () => {
    if (!canAnalyze) return;
    const url = URL.createObjectURL(new Blob([constructionTreeCsv(data)], { type: 'text/csv;charset=utf-8;' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = constructionTreeFilename(data);
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <AnalystOnly><details className="ct-source">
    <summary><Database size={16} aria-hidden="true" /><span>Данные и источник</span></summary>
    <div className="ct-source-toolbar"><h2>Исходные значения · млн м²</h2><button type="button" onClick={download}><Download size={15} aria-hidden="true" />Скачать CSV</button></div>
    <dl className="ct-metadata">
      <div><dt>Файл</dt><dd>{data.source.file}</dd></div>
      <div><dt>Лист / колонка</dt><dd>«{data.source.sheet}» / «{data.source.column}»</dd></div>
      <div><dt>Версия</dt><dd>{data.version}</dd></div>
      <div><dt>Выгрузка мониторинга</dt><dd><time dateTime={data.source.downloadedAt}>{data.source.downloadedAt}</time></dd></div>
      <div><dt>Дата файла</dt><dd>{constructionTreeDate(data.source.fileDate)}</dd></div>
      <div><dt>Сформировано API</dt><dd><time dateTime={data.generatedAt}>{data.generatedAt}</time></dd></div>
      {data.snapshot && <><div><dt>Последняя публикация перед закрытием недели</dt><dd>{data.snapshot.publishedAt}</dd></div><div><dt>Снимок сохранён</dt><dd>{data.snapshot.capturedAt}{data.snapshot.delayedCapture ? ' · после пропущенного понедельника' : ''}</dd></div></>}
      <div><dt>Итоги</dt><dd>Значения колонки «Итог» исходного листа, без пересчёта и суммирования.</dd></div>
      <div><dt>Справочное нежильё</dt><dd>Повтор показателя «Нежилье в жилье». Не прибавляется к нежилым объектам или общей площади.</dd></div>
      {data.nonresidentialSectors?.status === 'available' && <div><dt>Отрасли нежилых объектов</dt><dd>«{data.nonresidentialSectors.sheet}»: {Object.entries(data.nonresidentialSectors.filters!).map(([key, value]) => `${key}: ${value}`).join('; ')}. Площадь: {data.nonresidentialSectors.areaColumn}; отрасль: {data.nonresidentialSectors.sectorColumn}. Сумма сверена с каноническим итогом нежилья.</dd></div>}
    </dl>
    <table className="ct-source-table">
      <caption>Все 10 показателей колонки «Итог», без округления</caption>
      <thead><tr><th scope="col">Показатель / подпись источника</th><th scope="col">млн м²</th><th scope="col">Ячейка</th></tr></thead>
      <tbody>{data.metrics.map(metric => <tr key={metric.id}>
        <th scope="row" data-column="Показатель"><span>{constructionTreeLabels[metric.id]}</span><small>{metric.label}</small></th>
        <td data-column="млн м²">{constructionTreeExact(metric.value)}</td><td data-column="Ячейка">{metric.cell}</td>
      </tr>)}</tbody>
    </table>
    {data.nonresidentialSectors?.status === 'available' && <table className="ct-source-table ct-sector-source"><caption>Отрасли · исходные строки реестра</caption><thead><tr><th scope="col">Отрасль</th><th scope="col">млн м²</th><th scope="col">Строки</th></tr></thead><tbody>{data.nonresidentialSectors.items.map(item => <tr key={item.id}><th scope="row">{item.label}</th><td data-column="млн м²">{constructionTreeExact(item.value)}</td><td data-column="Строки">{item.rows.join(', ')}</td></tr>)}</tbody></table>}
  </details></AnalystOnly>;
}

function ReadyTree({ data }: { data: ConstructionTreeData }) {
  const values = Object.fromEntries(data.metrics.map(metric => [metric.id, metric.value])) as Record<ConstructionTreeId, number | null>;
  const leaf = (id: ConstructionTreeId, label = constructionTreeLabels[id]) => <div className="ct-node" data-metric={id}><span className="ct-node-label">{label}</span><Area value={values[id]} /></div>;
  return <>
    <section className="ct-diagram" aria-label="Структура текущего строительства">
      <div className="ct-total" data-metric="total"><div><span className="ct-kicker">Текущее строительство</span><h2>Общая площадь</h2></div><Area value={values.total} /></div>
      <ul className="ct-branches">
        <li className="ct-branch ct-housing">
          <div className="ct-branch-heading" data-metric="housing"><h3><Building2 size={19} aria-hidden="true" />Жилые объекты</h3><Area value={values.housing} /></div>
          <ul className="ct-children">
            <li>{leaf('living')}
              <ul className="ct-children ct-living-children"><li>{leaf('investors')}</li><li>{leaf('renovation')}</li></ul>
            </li>
            <li>{leaf('embedded')}</li>
            <li>{leaf('mop', 'Места общего пользования (МОП)')}</li>
          </ul>
        </li>
        <li className="ct-branch ct-nonresidential">
          <div className="ct-branch-heading" data-metric="nonresidential"><h3><Building2 size={19} aria-hidden="true" />Нежилые объекты</h3><Area value={values.nonresidential} /></div>
          {data.nonresidentialSectors?.status === 'available' ? <ul className="ct-children ct-sectors">{data.nonresidentialSectors.items.map(item => <li key={item.id}><div className="ct-node" data-sector={item.id}><span className="ct-node-label">{item.label.replace(/^\d+\s+/, '')}</span><Area value={item.value} /></div></li>)}</ul> : <p className="ct-node-note">Отраслевая разбивка недоступна: нет сверенного с итогом набора источника.</p>}
          <div className="ct-reference" role="note" aria-label="Справочно: нежильё в составе жилья">
            <Area value={values.embedded} /><p>Нежильё в составе жилых объектов</p>
            <div className="ct-reference-heading"><Info size={16} aria-hidden="true" /><span>Справочно · уже учтено в жилье</span></div>
          </div>
        </li>
      </ul>
    </section>
    <SourceData data={data} />
  </>;
}

export default function ConstructionTree() {
  const [state, setState] = useState<ConstructionTreeState>({ data: null, loading: true, error: '' });
  const [asOf, setAsOf] = useState('');
  const [history, setHistory] = useState<ConstructionTreeSnapshot[]>([]);
  const [historyError, setHistoryError] = useState('');
  const feed = useRef<ReturnType<typeof watchConstructionTree> | null>(null);
  useEffect(() => {
    setState({ data: null, loading: true, error: '' });
    const subscription = watchConstructionTree(setState, asOf);
    feed.current = subscription;
    return () => { subscription.stop(); if (feed.current === subscription) feed.current = null; };
  }, [asOf]);
  useEffect(() => {
    let active: AbortController | null = null, stopped = false;
    const refresh = async () => {
      if (active || stopped || document.visibilityState !== 'visible') return;
      active = new AbortController();
      try { const snapshots = await loadConstructionTreeHistory(active.signal); if (!stopped) { setHistory(snapshots); setHistoryError(''); } }
      catch { if (!stopped) setHistoryError('История временно недоступна'); }
      finally { active = null; }
    };
    const visible = () => { void refresh(); };
    const timer = setInterval(visible, 60_000);
    document.addEventListener('visibilitychange', visible);
    void refresh();
    return () => { stopped = true; clearInterval(timer); document.removeEventListener('visibilitychange', visible); active?.abort(); };
  }, []);
  const refresh = () => { void feed.current?.refresh(); };
  return <main className="construction-tree-page" id="construction-tree-page">
    <header className="ct-page-heading"><div><div className="eyebrow">АНАЛИТИКА НЕДВИЖИМОСТИ</div><h1>Дерево текущего строительства</h1></div>
      <div className="ct-refresh-wrap"><button className="ct-refresh" type="button" aria-label="Обновить дерево строительства" title="Обновить данные" disabled={state.loading} onClick={refresh}><RefreshCw size={18} className={state.loading ? 'ct-spinning' : ''} aria-hidden="true" /></button><span className="ct-tooltip" role="tooltip">Обновить данные</span></div>
    </header>
    <div className="ct-history-toolbar"><label htmlFor="ct-history">Данные</label><select id="ct-history" value={asOf} onChange={event => { setState({ data: null, loading: true, error: '' }); setAsOf(event.target.value); }}><option value="">Актуальные данные</option>{history.map(row => <option key={row.date} value={row.date}>Данные на {constructionTreeDate(row.date)}</option>)}</select>{historyError && <span role="status">{historyError}</span>}</div>
    <div className="ct-context"><AnalystOnly><span><Database size={14} aria-hidden="true" />Мониторинг · колонка «Итог»</span></AnalystOnly><span>Выгрузка мониторинга: {state.data ? <time dateTime={state.data.source.downloadedAt}>{constructionTreeDate(state.data.source.downloadedAt)}</time> : '—'}</span>{state.data?.snapshot && <span>Опубликовано: {constructionTreeDate(state.data.snapshot.publishedAt)}{state.data.snapshot.delayedCapture ? ' · сохранено после пропущенного понедельника' : ''}</span>}</div>
    <div className="ct-update-status" role="status" aria-live="polite">{state.loading && state.data ? 'Обновление данных…' : ''}</div>
    {state.error ? <div className="ct-state" role="alert"><AlertCircle size={26} aria-hidden="true" /><h2>Дерево строительства недоступно</h2><p>{state.error}</p><button type="button" onClick={refresh}><RefreshCw size={15} aria-hidden="true" />Повторить</button></div>
      : state.data ? <ReadyTree data={state.data} /> : <div className="ct-state" role="status"><RefreshCw size={26} className="ct-spinning" aria-hidden="true" /><p>Загрузка дерева строительства…</p></div>}
  </main>;
}
