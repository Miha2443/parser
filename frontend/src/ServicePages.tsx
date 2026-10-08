import { useEffect, useMemo, useState } from 'react';
import { AlertCircle, ArrowRight, ChevronLeft, ChevronRight, Database, Download, RefreshCw, Send } from 'lucide-react';
import { request } from './data';
import { date, exportCsv, number } from './format';
import './services.css';

type Change = (values: Record<string, string | null>, replace?: boolean) => void;
type Row = Record<string, string | number | boolean | null>;
type UpdatesReport = { version: string; generatedAt: string; lastRun: Row; realtyStatus: Record<string, unknown>; realtySummary: { label?: string; warnings?: string[]; error?: string }; indicators: Row[]; events: Row[]; monitoringChanges: Row[]; latestMonitoring: Row; marts: Row[]; telegramPreview: string };
function useUpdates(query = '') {
  const [data, setData] = useState<UpdatesReport>(), [error, setError] = useState(''), [retry, setRetry] = useState(0);
  useEffect(() => { const controller = new AbortController(); setData(undefined); setError('');
    request(`/api/v1/updates?${query}`, controller.signal).then(response => response.json()).then(data => { if (!controller.signal.aborted) setData(data); }).catch(e => { if (!controller.signal.aborted) setError(String(e)); });
    return () => controller.abort(); }, [query, retry]);
  return { data, error, retry: () => setRetry(value => value + 1) };
}
function State({ error, retry }: { error: string; retry: () => void }) {
  return <div className="load-state" role={error ? 'alert' : 'status'}>{error ? <><AlertCircle /><p>{error}</p><button onClick={retry}><RefreshCw size={16} />Повторить</button></> : <><RefreshCw className="loading-icon" /><p>Загрузка данных…</p></>}</div>;
}
const display = (value: Row[string]) => value == null ? '—' : typeof value === 'number' ? number(value, Number.isInteger(value) ? 0 : 2) : typeof value === 'boolean' ? value ? 'Да' : 'Нет' : /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString('ru-RU') : date(value);
function DataTable({ title, rows, columns, filename }: { title: string; rows: Row[]; columns: [string, string][]; filename: string }) {
  const [page, setPage] = useState(1), [search, setSearch] = useState('');
  useEffect(() => setPage(1), [rows, search]);
  const filtered = rows.filter(row => columns.some(([id]) => String(row[id] ?? '').toLocaleLowerCase('ru').includes(search.toLocaleLowerCase('ru'))));
  const pages = Math.max(1, Math.ceil(filtered.length / 20)), current = Math.min(page, pages);
  return <section className="section"><div className="section-heading"><div><h2>{title}</h2><p>{number(rows.length)} строк</p></div><button className="icon-button" title={`Скачать CSV: ${title}`} aria-label={`Скачать CSV: ${title}`} onClick={() => exportCsv([columns.map(([, label]) => label), ...rows.map(row => columns.map(([id]) => row[id] == null ? null : String(row[id])))], filename)}><Download size={17} /></button></div><input className="service-search" type="search" aria-label={`Поиск: ${title}`} placeholder="Поиск" value={search} onChange={e => setSearch(e.target.value)} /><div className="table-wrap"><table><thead><tr>{columns.map(([id, label]) => <th key={id}>{label}</th>)}</tr></thead><tbody>{filtered.slice((current - 1) * 20, current * 20).map((row, i) => <tr key={i}>{columns.map(([id], j) => j === 0 ? <th scope="row" key={id}>{display(row[id])}</th> : <td key={id}>{display(row[id])}</td>)}</tr>)}</tbody></table></div>{!filtered.length && <p className="empty-state">Нет записей.</p>}<div className="pagination"><span>Страница {current} из {pages}</span><button className="icon-button" title="Предыдущая страница" aria-label={`Предыдущая страница: ${title}`} disabled={current <= 1} onClick={() => setPage(current - 1)}><ChevronLeft size={16} /></button><button className="icon-button" title="Следующая страница" aria-label={`Следующая страница: ${title}`} disabled={current >= pages} onClick={() => setPage(current + 1)}><ChevronRight size={16} /></button></div></section>;
}
const sections = [
  ['Недвижимость', [['/apartments', 'Квартирография'], ['/apartments/developer', 'Квартирография по девелоперу'], ['/', 'Профиль застройщика'], ['/sales', 'Распроданность и стройготовность'], ['/construction', 'Текущее строительство'], ['/commissioning/annual', 'Ввод недвижимости'], ['/commissioning/operational', 'Оперативный ввод'], ['/commissioning/linear', 'Линейные объекты'], ['/map', 'Карта объектов']]],
  ['Экономика', [['/economics/salary', 'Заработная плата'], ['/economics/ipc', 'Индекс потребительских цен'], ['/economics/accounts', 'ВРП, ВВП и ВДС']]],
  ['Сервис', [['/updates', 'Обновления источников'], ['/tdm', 'Отправка в TDM']]],
] as const;
export function Home({ navigate }: { navigate: (path: string) => void }) {
  const { data, error, retry } = useUpdates();
  return <main className="service-page"><div className="page-heading"><div><div className="eyebrow">ЭКОНОМИКА И НЕДВИЖИМОСТЬ</div><h1>Аналитика Москвы</h1></div><button className="icon-button" title="Обновить сводку" aria-label="Обновить сводку" onClick={retry}><RefreshCw size={17} /></button></div>
    <nav className="home-sections">{sections.map(([title, links]) => <section key={title}><h2>{title}</h2>{links.map(([path, label]) => <a key={path} href={path} onClick={event => { event.preventDefault(); navigate(path); }}><span>{label}</span><ArrowRight size={16} /></a>)}</section>)}</nav>
    {error || !data ? <State error={error} retry={retry} /> : <><section className="section"><div className="section-heading"><h2>Последнее обновление</h2><Database size={18} /></div><div className="service-metrics"><div><span>Последний ETL-запуск</span><strong>{display(data.lastRun.ts)}</strong></div><div><span>Успешно</span><strong>{display(data.lastRun.success)}</strong></div><div><span>Ошибок</span><strong>{display(data.lastRun.error)}</strong></div><div><span>Обновление недвижимости</span><strong>{data.realtySummary.label || 'Нет статуса'}</strong></div></div></section><DataTable title="Свежесть источников" rows={data.marts} columns={[['mart', 'Витрина'], ['status', 'Статус'], ['latest_source_mtime', 'Свежий исходный файл'], ['built_at', 'Сборка'], ['rows', 'Строк']]} filename="source-freshness.csv" /></>}
    <footer className="page-footer">Аналитика Москвы · API</footer></main>;
}
export function Updates({ query, change }: { query: string; change: Change }) {
  const params = useMemo(() => new URLSearchParams(query), [query]);
  const api = new URLSearchParams(); for (const key of ['days', 'monitoring_days', 'only_errors']) if (params.has(key)) api.set(key, params.get(key)!);
  const { data, error, retry } = useUpdates(api.toString());
  return <main className="service-page"><div className="page-heading"><div><div className="eyebrow">СОСТОЯНИЕ ИСТОЧНИКОВ</div><h1>Журнал обновлений</h1></div><button className="icon-button" title="Обновить журнал" aria-label="Обновить журнал" onClick={retry}><RefreshCw size={17} /></button></div><div className="filters service-filters">{[['days', 'Период событий'], ['monitoring_days', 'История мониторинга']].map(([key, label]) => <label key={key}><span>{label}</span><select aria-label={label} value={params.get(key) ?? '30'} onChange={e => change({ [key]: e.target.value })}>{[7, 30, 90].map(days => <option key={days} value={days}>{days} дней</option>)}</select></label>)}<label className="service-checkbox"><input type="checkbox" checked={params.get('only_errors') === 'true'} onChange={e => change({ only_errors: String(e.target.checked) })} /><span>Только ошибки</span></label></div>
    {error || !data ? <State error={error} retry={retry} /> : <><section className="section"><h2>Последний запуск</h2><div className="service-metrics">{[['ts', 'Запуск'], ['success', 'Успехов'], ['skip', 'Без изменений'], ['error', 'Ошибок']].map(([key, label]) => <div key={key}><span>{label}</span><strong>{display(data.lastRun[key])}</strong></div>)}</div><p className="muted">Длительность: {display(data.lastRun.duration_sec)} c · run_id: {display(data.lastRun.run_id)}</p></section>
      <section className="section"><h2>Обновление недвижимости</h2><p>{data.realtySummary.label || 'Нет статуса'}</p>{data.realtySummary.error && <p role="alert">{data.realtySummary.error}</p>}{data.realtySummary.warnings?.map(warning => <p className="warning-text" key={warning}>{warning}</p>)}<dl className="service-status">{Object.entries(data.realtyStatus).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}</dl></section>
      <section className="section"><h2>Изменения мониторинга</h2><p className="muted">Последнее сравнение: {display(data.latestMonitoring.date)} · добавлено: {display(data.latestMonitoring.added)} · удалено: {display(data.latestMonitoring.removed)}</p></section>
      <DataTable title="История мониторинга" rows={data.monitoringChanges} columns={[['detected_at', 'Дата'], ['action', 'Изменение'], ['sheet', 'Лист'], ['uin', 'УИН'], ['document', 'РВ / РС'], ['object', 'Объект'], ['address', 'Адрес']]} filename="monitoring-changes.csv" />
      <DataTable title="Витрины сайта" rows={data.marts} columns={[['mart', 'Витрина'], ['status', 'Статус'], ['rows', 'Строк'], ['sources', 'Источников'], ['duration_sec', 'Сборка, c'], ['latest_source_mtime', 'Исходный файл'], ['error', 'Ошибка']]} filename="marts.csv" />
      <DataTable title="По показателям" rows={data.indicators} columns={[['title', 'Показатель'], ['source', 'Источник'], ['status', 'Последний статус'], ['ts', 'Проверка'], ['last_success_ts', 'Успех'], ['last_success_date', 'Дата данных'], ['rows', 'Строк']]} filename="indicators-status.csv" />
      <DataTable title="Журнал событий" rows={data.events.map(row => ({ ...row, message: row.reason || row.error || row.message || '' }))} columns={[['ts', 'Время'], ['title', 'Показатель'], ['status', 'Статус'], ['message', 'Сообщение'], ['rows', 'Строк']]} filename="etl-audit.csv" />
      <section className="section"><h2>Telegram · превью последнего сообщения</h2><pre className="service-preview">{data.telegramPreview || '(пусто)'}</pre></section></>}
    <footer className="page-footer">Аналитика Москвы · журнал обновлений</footer></main>;
}

type TdmCatalog = { actionToken: string; maxUploadBytes: number; status: { tokenReady: boolean; workspaceReady: boolean; groupReady: boolean; disabled: boolean; ready: boolean }; datasets: { key: string; title: string; section: string; files: string[]; size: number }[]; files: { key: string; label: string; size: number }[] };
export function Tdm() {
  const [catalog, setCatalog] = useState<TdmCatalog>(), [error, setError] = useState(''), [retry, setRetry] = useState(0);
  const [kind, setKind] = useState('dataset'), [section, setSection] = useState(''), [key, setKey] = useState(''), [file, setFile] = useState<File>();
  const [caption, setCaption] = useState(''), [text, setText] = useState(''), [group, setGroup] = useState('');
  const [busy, setBusy] = useState(false), [result, setResult] = useState(''), [groups, setGroups] = useState<Row[]>();
  useEffect(() => { const controller = new AbortController(); setError(''); request('/api/v1/tdm/catalog', controller.signal).then(response => response.json()).then(data => { if (!controller.signal.aborted) setCatalog(data); }).catch(e => { if (!controller.signal.aborted) setError(String(e)); }); return () => controller.abort(); }, [retry]);
  const sections = [...new Set(catalog?.datasets.map(item => item.section) ?? [])], activeSection = sections.includes(section) ? section : sections[0];
  const datasets = catalog?.datasets.filter(item => item.section === activeSection) ?? [];
  const selected = kind === 'dataset' ? datasets.find(item => item.key === key) ?? datasets[0] : catalog?.files.find(item => item.key === key) ?? catalog?.files[0];
  async function post(path: string, body?: BodyInit, headers: Record<string, string> = {}) {
    const response = await fetch(`/api/v1/tdm/${path}`, { method: 'POST', headers: { 'X-TDM-Token': catalog!.actionToken, ...headers }, body });
    if (!response.ok) throw new Error(`Ошибка HTTP ${response.status}. Обновите статус и проверьте настройки.`);
    return response.json();
  }
  async function send(asText = false) {
    if (!catalog || busy) return; setBusy(true); setResult('');
    try { const id = crypto.randomUUID(); let response;
      if (!asText && kind === 'upload') {
        if (!file || file.size > catalog.maxUploadBytes) throw new Error('Выберите файл размером до 50 МиБ.');
        const query = new URLSearchParams({ filename: file.name, caption }); if (group.trim()) query.set('group', group.trim());
        response = await post(`upload?${query}`, file, { 'Content-Type': 'application/octet-stream', 'Idempotency-Key': id });
      } else response = await post('send', JSON.stringify({ kind: asText ? 'text' : kind, key: selected?.key ?? '', text: asText ? text : caption, group: group.trim() || null }), { 'Content-Type': 'application/json', 'Idempotency-Key': id });
      setResult(response.message);
    } catch (e) { setResult(`${String(e)} Отправка не подтверждена; проверьте чат перед повтором.`); } finally { setBusy(false); }
  }
  async function getGroups() { setBusy(true); setResult(''); try { setGroups((await post('groups')).groups); } catch (e) { setResult(String(e)); } finally { setBusy(false); } }
  return <main className="service-page tdm-page"><div className="page-heading"><div><div className="eyebrow">СЕРВИС</div><h1>Отправка в TDM</h1></div><button className="icon-button" title="Обновить статус TDM" aria-label="Обновить статус TDM" onClick={() => setRetry(value => value + 1)} disabled={busy}><RefreshCw size={17} /></button></div>
    {error || !catalog ? <State error={error} retry={() => setRetry(value => value + 1)} /> : <><section className="section"><h2>Подключение</h2><div className="service-metrics">{[['tokenReady', 'Токен бота'], ['workspaceReady', 'Пространство'], ['groupReady', 'Группа']].map(([key, label]) => <div key={key}><span>{label}</span><strong>{catalog.status[key as 'tokenReady'] ? 'Готово' : 'Нет'}</strong></div>)}<div><span>Состояние</span><strong>{catalog.status.disabled ? 'Выключено' : catalog.status.ready ? 'Готово' : 'Не настроено'}</strong></div></div><button disabled={!catalog.status.tokenReady || catalog.status.disabled || busy} onClick={getGroups}>Получить группы бота</button>{groups && <div className="table-wrap"><table><thead><tr>{['Название', 'Группа', 'Пространство'].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{groups.map((row, i) => <tr key={i}><td>{row.title}</td><td>{row.groupId}</td><td>{row.workspaceId}</td></tr>)}</tbody></table></div>}</section>
      <section className="section"><h2>Данные для отправки</h2><fieldset className="tdm-modes"><legend>Источник</legend>{[['dataset', 'Раздел дашборда'], ['file', 'Исходный файл'], ['upload', 'С компьютера']].map(([value, label]) => <label key={value}><input type="radio" name="tdm-source" value={value} checked={kind === value} disabled={busy} onChange={() => { setKind(value); setKey(''); setResult(''); }} /><span>{label}</span></label>)}</fieldset>
        {kind === 'dataset' && <div className="tdm-controls"><label><span>Раздел</span><select aria-label="Раздел TDM" disabled={busy} value={activeSection} onChange={e => { setSection(e.target.value); setKey(''); }}>{sections.map(value => <option key={value}>{value}</option>)}</select></label><label><span>Данные</span><select aria-label="Данные TDM" disabled={busy} value={selected?.key ?? ''} onChange={e => setKey(e.target.value)}>{datasets.map(item => <option key={item.key} value={item.key}>{item.title}</option>)}</select></label></div>}
        {kind === 'file' && <label className="tdm-field"><span>Файл</span><select aria-label="Исходный файл TDM" disabled={busy} value={selected?.key ?? ''} onChange={e => setKey(e.target.value)}>{catalog.files.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label>}
        {kind === 'upload' && <label className="tdm-field"><span>Файл с компьютера · до 50 МиБ</span><input aria-label="Загрузить файл TDM" type="file" disabled={busy} onChange={e => setFile(e.target.files?.[0])} /></label>}
        {selected && kind === 'dataset' && <details><summary>Исходные файлы</summary>{('files' in selected ? selected.files : []).map(path => <p key={path}><code>{path}</code></p>)}</details>}
        <label className="tdm-field"><span>Подпись к файлу</span><textarea aria-label="Подпись к файлу" value={caption} maxLength={10000} disabled={busy} onChange={e => setCaption(e.target.value)} /></label>
        <details><summary>Другая группа</summary><label className="tdm-field"><span>groupId</span><input aria-label="Группа TDM" value={group} disabled={busy} onChange={e => setGroup(e.target.value)} /></label></details>
        <button className="tdm-send" disabled={busy || !catalog.status.ready || (kind === 'upload' ? !file || file.size > catalog.maxUploadBytes : !selected)} onClick={() => send()}><Send size={16} />{busy ? 'Отправка…' : 'Отправить файл'}</button></section>
      <section className="section"><h2>Сообщение</h2><label className="tdm-field"><span>Текст</span><textarea aria-label="Текст TDM" value={text} disabled={busy} maxLength={10000} onChange={e => setText(e.target.value)} /></label><button disabled={busy || !catalog.status.ready || !text.trim()} onClick={() => send(true)}><Send size={16} />Отправить текст</button></section>{result && <p className="tdm-result" role="status">{result}</p>}
    </>}<footer className="page-footer">Аналитика Москвы · TDM</footer></main>;
}
