import { useEffect, useMemo } from 'react';
import { AlertCircle, Building2, ChevronLeft, ChevronRight, Database, Download, RefreshCw, Search } from 'lucide-react';
import { Chart } from './Chart';
import ApartmentExport from './ApartmentExport';
import { useApartmentData } from './useApartmentData';
import { apartmentRows, roomColors, roomTypes, roomWidths, tablePage } from './apartmentData';
import type { ApartmentDetail, ApartmentMetadata, ApartmentOverview, ApartmentRow, Room } from './apartmentData';
import { distributionOption, roomsOption } from './apartmentCharts';
import { date, exportCsv, number, percent } from './format';
import type { Theme } from './types';

type Change = (values: Record<string, string | null>, replace?: boolean) => void;
function Empty({ text = 'Нет данных.' }: { text?: string }) { return <div className="empty-state"><Database size={20} /><span>{text}</span></div>; }
function Metric({ label, value, unit, foot, digits = 1 }: { label: string; value: number | null; unit: string; foot?: string; digits?: number }) {
  return <div className="metric"><span className="metric-label">{label}</span><div className="metric-number">{number(value, digits)}<span>{unit}</span></div>{foot && <span className="metric-foot">{foot}</span>}</div>;
}
function RoomStrip({ rooms }: { rooms: Room[] }) {
  const label = roomTypes.map(type => `${type}: ${percent(rooms.find(r => r.type === type)?.sharePercent)}`).join('; ');
  return <div className="apartment-room-strip" role="img" aria-label={label} title={label}>{roomWidths(rooms).map(r => r.width > 0 && <i key={r.type} style={{ width: `${r.width}%`, background: roomColors[roomTypes.indexOf(r.type)] }} title={`${r.type}: ${percent(r.sharePercent)}`} />)}</div>;
}
function VolumeTable({ rows, title, scope, params, change, navigate, region, selected }: { rows: ApartmentRow[]; title: string; scope: string; params: URLSearchParams; change: Change; navigate?: (href: string) => void; region: string; selected?: string }) {
  const searchKey = `${scope}Search`, pageKey = `${scope}Page`, sizeKey = `${scope}Size`;
  const query = params.get(searchKey) ?? '';
  const view = tablePage(rows, query, params.get(pageKey), params.get(sizeKey));
  useEffect(() => {
    if (params.has(pageKey) && params.get(pageKey) !== String(view.current)) change({ [pageKey]: String(view.current) }, true);
    if (params.has(sizeKey) && params.get(sizeKey) !== String(view.pageSize)) change({ [sizeKey]: String(view.pageSize) }, true);
  }, [params, pageKey, sizeKey, view.current, view.pageSize]);
  return <section className="section apartment-volume" aria-label={title}>
    <div className="section-heading"><div><h2>{title}</h2><p>{number(rows.length)} строк · {region === 'msk' ? 'Москва' : 'Российская Федерация'}</p></div><button className="icon-button" aria-label={`Скачать все строки CSV: ${title}`} title={`Скачать все строки CSV: ${title}`} onClick={() => exportCsv(apartmentRows(rows), `apartments-${region}-${scope}.csv`)}><Download size={17} /></button></div>
    <div className="object-toolbar"><label className="object-search"><Search size={14} /><input type="search" aria-label={`Поиск: ${title}`} placeholder="Поиск" value={query} onChange={e => change({ [searchKey]: e.target.value || null, [pageKey]: null })} /></label><label className="apartment-page-size"><span>Строк</span><select aria-label={`Строк на странице: ${title}`} value={view.pageSize} onChange={e => change({ [sizeKey]: e.target.value, [pageKey]: null })}>{[20, 50, 100].map(size => <option key={size}>{size}</option>)}</select></label></div>
    <div className="table-wrap" tabIndex={0} role="region" aria-label={`Таблица: ${title}`}><table className="apartment-volume-table"><thead><tr><th scope="col">Место</th><th scope="col">{scope === 'regions' ? 'Регион' : 'Девелопер'}</th><th scope="col">Квартиры, тыс. шт.</th><th scope="col">Площадь, тыс. м²</th>{roomTypes.map(type => <th scope="col" key={type}>{type}, %</th>)}<th scope="col">Структура</th></tr></thead><tbody>{view.shown.map((r, i) => <tr key={`${r.id}-${i}`} className={r.id === selected ? 'selected-developer' : ''} aria-selected={selected === undefined ? undefined : r.id === selected}><td>{number(r.place)}</td><th scope="row">{navigate ? <a href={`/apartments/developer?${new URLSearchParams({ region, developer: r.id })}`} onClick={e => { if (!e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey && e.button === 0) { e.preventDefault(); navigate(e.currentTarget.getAttribute('href')!); } }}>{r.name}</a> : r.name}{r.id === selected && <small className="apartment-selected-label">Выбранный девелопер</small>}</th><td>{number(r.apartmentThousandCount, 1)}</td><td>{number(r.areaThousandM2, 1)}</td>{roomTypes.map(type => <td key={type}>{percent(r.rooms.find(room => room.type === type)?.sharePercent)}</td>)}<td><RoomStrip rooms={r.rooms} /></td></tr>)}</tbody></table></div>
    {!view.shown.length && <Empty text={rows.length ? 'Ничего не найдено.' : 'Нет данных.'} />}
    <div className="pagination"><span aria-live="polite">{number(view.total)} строк · страница {view.current} из {view.pages}</span><button className="icon-button" title={`Предыдущая страница: ${title}`} aria-label={`Предыдущая страница: ${title}`} disabled={view.current === 1} onClick={() => change({ [pageKey]: String(view.current - 1) })}><ChevronLeft size={15} /></button><button className="icon-button" title={`Следующая страница: ${title}`} aria-label={`Следующая страница: ${title}`} disabled={view.current === view.pages} onClick={() => change({ [pageKey]: String(view.current + 1) })}><ChevronRight size={15} /></button></div>
  </section>;
}
function Evidence({ metadata }: { metadata: ApartmentMetadata }) {
  return <section id="apartment-sources" className="section sources-section"><div className="section-heading"><h2>Источники и даты</h2><Database size={17} /></div><p className="muted">Отчёт на {date(metadata.reportDate)} · дата источника: {date(metadata.source.date)}</p>{metadata.source.issues.map(([level, msg], i) => <p key={i} className="method-note warning-text"><AlertCircle size={14} /><span>{level}: {msg}</span></p>)}<details><summary>Метаданные источника</summary><p className="muted">Дата формирования: {date(metadata.generatedAt)} · версия: {metadata.version}</p><div className="source-list"><div><strong>Файлы-кандидаты</strong><span>{date(metadata.source.date)}</span><div>{metadata.source.files.map((file, i) => <code key={i}>{file}</code>)}<p>Кандидаты источника, не журнал прочитанных файлов.</p></div></div></div></details></section>;
}
export default function Apartments({ detail, theme, query, change, navigate }: { detail: boolean; theme: Theme; query: string; change: Change; navigate: (href: string) => void }) {
  const params = useMemo(() => new URLSearchParams(query), [query]);
  const { catalog, region, developer, developers, data, error, retry } = useApartmentData(params, detail);
  useEffect(() => {
    if (!region) return;
    const values: Record<string, string | null> = {};
    if (params.get('region') !== region.id) values.region = region.id;
    if (detail && params.get('developer') !== (developer?.id ?? null)) values.developer = developer?.id ?? null;
    if (Object.keys(values).length) change(values, true);
  }, [region?.id, developer?.id, detail, params]);
  const metadata = data ?? catalog;
  const overview = data && 'apartments' in data ? data as ApartmentOverview : null;
  const profile = data && 'summary' in data ? data as ApartmentDetail : null;
  const caption = detail ? 'Квартирография по девелоперу' : 'Квартирография';
  function changeRegion(value: string) {
    const rawId = detail ? developer?.id : params.get('developer');
    const destination = catalog?.developersByRegion[value as 'msk' | 'rf'] ?? [];
    const nextId = destination.find(item => item.id === rawId)?.id ?? (detail ? destination[0]?.id : undefined);
    change({ region: value, developer: nextId ?? null, developersPage: null, regionsPage: null, comparisonPage: null });
  }
  return <main className="apartments-page" id="apartments-page">
    <div className="page-heading"><div><div className="eyebrow">АНАЛИТИКА НЕДВИЖИМОСТИ</div><h1>{caption}</h1></div><div className="snapshot-label"><Database size={14} />Данные API<span>Отчёт на {metadata ? date(metadata.reportDate) : '—'}</span></div></div>
    {catalog && region && <div className="filters"><label><span>Регион</span><select aria-label="Регион" value={region.id} onChange={e => changeRegion(e.target.value)}>{catalog.regions.map(r => <option key={r.id} value={r.id}>{r.label}</option>)}</select></label>{detail && <label className="developer-filter"><span>Девелопер</span><select aria-label="Девелопер" value={developer?.id ?? ''} disabled={!developers.length} onChange={e => change({ developer: e.target.value, comparisonPage: null })}>{!developers.length && <option value="">Нет девелоперов</option>}{developers.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>}<a className="source-anchor" href="#apartment-sources"><Database size={15} />Источники<ChevronRight size={14} /></a></div>}
    {error ? <div className="load-state" role="alert"><AlertCircle size={26} /><h2>Квартирография API недоступна</h2><p>{error}</p><button onClick={retry}><RefreshCw size={15} />Повторить</button></div> : detail && catalog && !developer ? <Empty text="Нет девелоперов для выбранного региона." /> : !data ? <div className="load-state" role="status" aria-live="polite"><RefreshCw className="loading-icon" size={26} /><p>{catalog ? 'Загрузка квартирографии…' : 'Загрузка каталога квартирографии…'}</p></div> : <>
      <div className="developer-heading"><div className="developer-name"><Building2 size={21} /><h2>{profile?.developer.name ?? 'Жилищное строительство'}</h2><span className="region-label">{region?.label}</span></div>{region && <ApartmentExport region={region.id} developer={detail ? developer?.id : undefined} version={data.version} />}</div>
      {overview && <>
        <section className="section apartment-overview-grid"><div><div className="section-heading"><h2>Типы квартир</h2><button className="icon-button" title="Скачать типы квартир CSV" aria-label="Скачать типы квартир CSV" onClick={() => exportCsv([['Тип квартиры', 'Количество, шт.', 'Площадь, тыс. м²'], ...overview.apartments.map(r => [r.type, r.count, r.areaThousandM2])], `apartments-${region?.id}-types.csv`)}><Download size={17} /></button></div>{overview.apartments.length ? <div className="table-wrap" tabIndex={0} role="region" aria-label="Типы квартир"><table><thead><tr><th>Тип квартиры</th><th>Количество, шт.</th><th>Площадь, тыс. м²</th></tr></thead><tbody>{overview.apartments.map((r, i) => <tr key={i}><th scope="row">{r.type}</th><td>{number(r.count)}</td><td>{number(r.areaThousandM2, 1)}</td></tr>)}</tbody></table></div> : <Empty />}</div><div><div className="section-heading"><div><h2>Распределение квартир по площади</h2><p>Диапазоны, м² · доля квартир, %</p></div></div>{overview.distribution.some(r => r.sharePercent !== null) ? <Chart key={region?.id} theme={theme} label="Распределение квартир по площади" option={distributionOption(overview.distribution, theme)} height={300} rows={[["Площадь, м²", "Доля, %"], ...overview.distribution.map(r => [r.range, r.sharePercent])]} filename={`apartments-${region?.id}-distribution`} /> : <Empty />}</div></section>
        <VolumeTable rows={overview.developers} title="Объём строительства по девелоперам" scope="developers" params={params} change={change} navigate={navigate} region={overview.region} />
        <VolumeTable rows={overview.regions} title="Объём строительства по регионам" scope="regions" params={params} change={change} region={overview.region} />
      </>}
      {profile && <>
        <p className="muted apartment-ranking">Место по объёму строительства в регионе: {number(profile.summary.place)} из {number(profile.summary.totalDevelopers)}</p>
        <div className="kpi-band"><Metric label="Квартиры" value={profile.summary.countThousand} unit="тыс. шт." /><Metric label="Площадь" value={profile.summary.areaThousandM2} unit="тыс. м²" digits={0} /><Metric label="Средняя площадь квартиры" value={profile.summary.averageAreaM2} unit="м²" foot={profile.referenceAverages.map(r => `${catalog?.regions.find(item => item.id === r.region)?.label ?? r.region}: ${number(r.averageAreaM2, 1)} м²`).join(' · ')} /><Metric label="Доля рынка региона" value={profile.summary.marketSharePercent} unit="%" digits={2} foot={`База рынка: ${number(profile.summary.marketBaseAreaThousandM2, 1)} тыс. м²`} /></div>
        <section className="section"><div className="section-heading"><h2>Структура портфеля по комнатности</h2></div><div className="apartment-rooms-grid"><div>{profile.rooms.some(r => r.sharePercent !== null && r.sharePercent > 0) ? <Chart key={`${profile.region}-${profile.developer.id}`} theme={theme} label="Структура портфеля по комнатности" option={roomsOption(profile.rooms, theme)} height={260} rows={[["Тип", "Доля, %"], ...profile.rooms.map(r => [r.type, r.sharePercent])]} filename={`apartments-${profile.region}-${profile.developer.id}-rooms`} /> : <Empty text="Нет данных по комнатности." />}</div><div><table><thead><tr><th>Тип квартиры</th><th>Доля источника, %</th></tr></thead><tbody>{roomTypes.map((type, i) => <tr key={type}><th scope="row"><span className="apartment-room-label"><i style={{ background: roomColors[i] }} />{type}</span></th><td>{percent(profile.rooms.find(r => r.type === type)?.sharePercent)}</td></tr>)}</tbody></table><RoomStrip rooms={profile.rooms} /></div></div></section>
        <VolumeTable rows={profile.comparison} title="Сравнение с топ-10 девелоперов региона" scope="comparison" params={params} change={change} region={profile.region} selected={profile.developer.id} />
      </>}
    </>}
    {metadata && <Evidence metadata={metadata} />}
    <footer className="page-footer"><span>Аналитика Москвы</span><span>{caption} · API</span></footer>
  </main>;
}
