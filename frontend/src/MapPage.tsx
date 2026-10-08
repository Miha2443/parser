import { useEffect, useMemo, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import { GeoJSONSource, Map as LibreMap } from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import 'maplibre-gl/dist/maplibre-gl.css';
import './map.css';
import { AlertCircle, ChevronLeft, ChevronRight, Crosshair, Database, Download, FileSpreadsheet, LocateFixed, RefreshCw, X } from 'lucide-react';
import { request } from './data';
import { date, exportCsv, number } from './format';
import { mapBounds, mapCsvRows, mapQuery, mapRepairs, normalizeMapCatalog, normalizeMapReport } from './mapData';
import type { MapCatalog, MapReport } from './mapData';
import type { Theme } from './types';

type Change = (values: Record<string, string | null>, replace?: boolean) => void;
const qualityText: Record<string, string> = { located: 'По геометрии / кэшу', approximate: 'Приблизительно', missing: 'Без координат' };
maplibregl.setWorkerUrl(workerUrl);

function MapCanvas({ report, theme, selected, select }: { report: MapReport; theme: Theme; selected: string | null; select: (id: string | null) => void }) {
  const container = useRef<HTMLDivElement>(null), map = useRef<LibreMap>(), payload = useRef(report), callback = useRef(select);
  payload.current = report; callback.current = select;
  const [error, setError] = useState(''), [ready, setReady] = useState(false);
  useEffect(() => {
    if (!container.current) return;
    setError(''); setReady(false);
    let instance: LibreMap;
    try {
      instance = new maplibregl.Map({ container: container.current, center: [37.62, 55.75], zoom: 10,
        maxZoom: 19, attributionControl: false,
        style: { version: 8, sources: { base: { type: 'raster', tileSize: 256,
          tiles: [(import.meta.env.VITE_MAP_TILE_URL as string | undefined) || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
          attribution: (import.meta.env.VITE_MAP_ATTRIBUTION as string | undefined) || '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors' } },
          layers: [{ id: 'base', type: 'raster', source: 'base', paint: { 'raster-saturation': -.7 } }] } });
    } catch { setError('Карта не запустилась: требуется WebGL. Данные доступны в таблице ниже.'); return; }
    map.current = instance;
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    instance.addControl(new maplibregl.FullscreenControl(), 'top-right');
    instance.addControl(new maplibregl.AttributionControl({ compact: false }), 'bottom-right');
    instance.on('error', () => setError('Часть подложки не загрузилась. Проверьте соединение или настройку картографического сервиса.'));
    instance.on('load', () => {
      instance.addSource('objects', { type: 'geojson', data: payload.current.geojson, cluster: true, clusterRadius: 42, clusterMaxZoom: 16 });
      instance.addLayer({ id: 'clusters', type: 'circle', source: 'objects', filter: ['has', 'point_count'], paint: {
        'circle-color': '#347f9c', 'circle-radius': ['step', ['get', 'point_count'], 17, 25, 22, 150, 29], 'circle-stroke-width': 2, 'circle-stroke-color': '#ffffff' } });
      // Cluster size remains inspectable without a remote font service.
      instance.addLayer({ id: 'points', type: 'circle', source: 'objects', filter: ['!', ['has', 'point_count']], paint: {
        'circle-color': ['case', ['==', ['get', 'status'], 'Введено'], '#27ba80', '#4d9fcc'],
        'circle-radius': ['case', ['==', ['get', 'quality'], 'approximate'], 7, 5],
        'circle-stroke-width': ['case', ['==', ['get', 'quality'], 'approximate'], 3, 1],
        'circle-stroke-color': ['case', ['==', ['get', 'quality'], 'approximate'], '#e8b14c', '#ffffff'] } });
      const labels = new Map<number, maplibregl.Marker>();
      instance.on('render', () => {
        const visible = new Set<number>();
        for (const feature of instance.queryRenderedFeatures(undefined, { layers: ['clusters'] })) {
          if (feature.geometry.type !== 'Point') continue;
          const id = Number(feature.properties.cluster_id); visible.add(id);
          if (!labels.has(id)) {
            const label = document.createElement('span'); label.className = 'map-cluster-label';
            labels.set(id, new maplibregl.Marker({ element: label }).setLngLat(feature.geometry.coordinates as [number, number]).addTo(instance));
          }
          const marker = labels.get(id)!;
          marker.getElement().textContent = String(feature.properties.point_count_abbreviated);
          marker.setLngLat(feature.geometry.coordinates as [number, number]);
        }
        for (const [id, marker] of labels) if (!visible.has(id)) { marker.remove(); labels.delete(id); }
      });
      instance.on('click', 'clusters', async event => {
        const feature = event.features?.[0]; if (!feature || feature.geometry.type !== 'Point') return;
        try { const source = instance.getSource('objects') as GeoJSONSource;
          const zoom = await source.getClusterExpansionZoom(Number(feature.properties.cluster_id));
          instance.easeTo({ center: feature.geometry.coordinates as [number, number], zoom });
        } catch { setError('Не удалось раскрыть кластер. Попробуйте приблизить карту.'); }
      });
      instance.on('click', 'points', event => callback.current(String(event.features?.[0]?.properties.id ?? '')));
      instance.on('mousemove', 'clusters', event => {
        if (container.current) container.current.title = `${event.features?.[0]?.properties.point_count ?? ''} объектов`;
      });
      for (const layer of ['clusters', 'points']) {
        instance.on('mouseenter', layer, () => { instance.getCanvas().style.cursor = 'pointer'; });
        instance.on('mouseleave', layer, () => { instance.getCanvas().style.cursor = ''; if (container.current) container.current.title = ''; });
      }
      const bounds = mapBounds(payload.current.rows); if (bounds) instance.fitBounds(bounds, { padding: 55, maxZoom: 12, duration: 0 });
      setReady(true);
    });
    const observer = new ResizeObserver(() => instance.resize()); observer.observe(container.current);
    return () => { observer.disconnect(); instance.remove(); map.current = undefined; };
  }, []);
  useEffect(() => { if (ready) (map.current?.getSource('objects') as GeoJSONSource)?.setData(report.geojson); }, [report, ready]);
  useEffect(() => {
    const row = report.rows.find(row => row.id === selected);
    if (ready && row?.has_coords) map.current?.easeTo({ center: [row.lon!, row.lat!], zoom: Math.max(map.current.getZoom(), 16) });
  }, [selected, ready]);
  useEffect(() => { if (ready) map.current?.setPaintProperty('base', 'raster-brightness-max', theme === 'dark' ? .65 : 1); }, [theme, ready]);
  function fit() { const bounds = mapBounds(report.rows); if (bounds) map.current?.fitBounds(bounds, { padding: 55, maxZoom: 13 }); }
  return <div className="map-surface"><div ref={container} className="map-canvas" aria-label="Карта объектов строительства" />
    <button className="icon-button map-fit" title="Показать все объекты" aria-label="Показать все объекты" onClick={fit} disabled={!ready || !report.filteredTotals.onMap}><Crosshair size={19} /></button>
      {error && <div className="map-warning" role="alert"><AlertCircle size={16} /><span>{error}</span></div>}
    <div className="map-legend"><span><i className="map-dot introduced" />Введено</span><span><i className="map-dot building" />Строится</span><span><i className="map-dot approximate" />Приблизительно</span></div></div>;
}

export default function MapPage({ theme, query, change }: { theme: Theme; query: string; change: Change }) {
  const params = useMemo(() => new URLSearchParams(query), [query]);
  const apiQuery = mapQuery(params).toString();
  const [catalogState, setCatalog] = useState<{ data: MapCatalog; attempt: number }>(), [report, setReport] = useState<MapReport>(), [error, setError] = useState(''), [retry, setRetry] = useState(0);
  const catalog = catalogState?.attempt === retry ? catalogState.data : undefined;
  const [exportError, setExportError] = useState(''), [busy, setBusy] = useState(false);
  const exportController = useRef<AbortController>();
  useEffect(() => { setBusy(false); setExportError(''); return () => exportController.current?.abort(); }, [apiQuery, report?.version]);
  useEffect(() => { const abort = new AbortController(); setError('');
    request('/api/v1/map/catalog', abort.signal).then(response => response.json()).then(normalizeMapCatalog).then(data => { if (!abort.signal.aborted) setCatalog({ data, attempt: retry }); }).catch(e => { if (!abort.signal.aborted) setError(String(e)); });
    return () => abort.abort(); }, [retry]);
  useEffect(() => { const abort = new AbortController(); setReport(undefined); setError(''); setExportError('');
    if (!catalog) return () => abort.abort();
    const repairs = mapRepairs(catalog, params);
    if (Object.keys(repairs).length) { change(repairs, true); return () => abort.abort(); }
    request(`/api/v1/map?${apiQuery}`, abort.signal).then(response => response.json()).then(normalizeMapReport).then(data => { if (!abort.signal.aborted) setReport(data); }).catch(e => { if (!abort.signal.aborted) setError(String(e)); });
    return () => abort.abort(); }, [apiQuery, retry, catalog]);
  const selected = params.get('object'), chosen = report?.rows.find(row => row.id === selected);
  const search = params.get('search') ?? '', rows = report?.rows.filter(row => `${row.address} ${row.developer} ${row.object_name}`.toLocaleLowerCase('ru').includes(search.toLocaleLowerCase('ru'))) ?? [];
  const pages = Math.max(1, Math.ceil(rows.length / 20)), page = Math.min(pages, Math.max(1, Number(params.get('page')) || 1)), shown = rows.slice((page - 1) * 20, page * 20);
  function filter(values: Record<string, string | null>) { change({ ...values, page: null, object: null }); }
  async function excel() { if (!report) return; const abort = new AbortController(); exportController.current?.abort(); exportController.current = abort; setBusy(true); setExportError('');
    try { const q = new URLSearchParams(apiQuery); q.set('required_version', report.version); const response = await request(`/api/v1/map/export?${q}`, abort.signal);
      const blob = await response.blob(); if (abort.signal.aborted) return;
      const url = URL.createObjectURL(blob), link = document.createElement('a'); link.href = url; link.download = 'map-objects.xlsx'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch { if (!abort.signal.aborted) setExportError('Не удалось скачать Excel. Обновите данные и повторите.'); } finally { if (!abort.signal.aborted) setBusy(false); } }
  return <main className="map-page"><div className="page-heading"><div><div className="eyebrow">АНАЛИТИКА НЕДВИЖИМОСТИ</div><h1>Карта объектов</h1></div><div className="snapshot-label"><Database size={14} />Мониторинг 2.0<span>{catalog ? date(catalog.source.date) : '—'}</span></div></div>
    {catalog && <div className="filters map-filters"><label><span>Застройщик</span><select aria-label="Застройщик карты" value={params.get('developer') ?? ''} onChange={e => filter({ developer: e.target.value || null })}><option value="">Все</option>{catalog.developers.map(value => <option key={value}>{value}</option>)}</select></label>
      {(['status', 'okrug'] as const).map(key => <label key={key}><span>{key === 'status' ? 'Статусы' : 'Округа'}</span><select multiple aria-label={key === 'status' ? 'Статусы карты' : 'Округа карты'} value={params.getAll(key).flatMap(value => value.split('|'))} onChange={e => filter({ [key]: Array.from(e.target.selectedOptions).map(option => option.value).join('|') || null })}>{(key === 'status' ? catalog.statuses : catalog.okrugs).map(value => <option key={value}>{value}</option>)}</select></label>)}
      <label><span>Год от</span><input aria-label="Год от" type="number" value={params.get('year_from') ?? ''} min={catalog.years.min ?? undefined} max={catalog.years.max ?? undefined} onChange={e => filter({ year_from: e.target.value || null })} /></label><label><span>Год до</span><input aria-label="Год до" type="number" value={params.get('year_to') ?? ''} min={catalog.years.min ?? undefined} max={catalog.years.max ?? undefined} onChange={e => filter({ year_to: e.target.value || null })} /></label>
      <label><span>Координаты</span><select aria-label="Качество координат" value={params.get('quality') ?? 'all'} onChange={e => filter({ quality: e.target.value })}><option value="all">Все</option>{Object.entries(qualityText).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="map-check"><input type="checkbox" checked={params.get('only_with_coords') !== 'false'} onChange={e => filter({ only_with_coords: String(e.target.checked) })} /><span>Только с координатами</span></label></div>}
    {error ? <div className="load-state" role="alert"><AlertCircle /><p>{error}</p><button onClick={() => setRetry(value => value + 1)}><RefreshCw size={16} />Повторить</button></div> : !report ? <div className="load-state" role="status">Загрузка объектов…</div> : <>
      <div className="map-counts">{([['total', 'Объектов'], ['onMap', 'На карте'], ['located', 'По геометрии / кэшу'], ['approximate', 'Приблизительные'], ['missing', 'Без координат']] as const).map(([key, label]) => <div key={key}><span>{label}</span><strong>{number(report.filteredTotals[key])}</strong><small>из {number(report.totals[key])}</small></div>)}</div>
      <MapCanvas report={report} theme={theme} selected={selected} select={id => change({ object: id })} />
      {chosen && <section className="map-object"><div className="section-heading"><h2>{chosen.object_name || chosen.address || chosen.id}</h2><button className="icon-button" aria-label="Закрыть объект" title="Закрыть объект" onClick={() => change({ object: null })}><X size={18} /></button></div><dl>{[['Адрес', chosen.address], ['Застройщик', chosen.developer], ['Организация', chosen.builder], ['Реестр', chosen.source_sheet], ['Разрешение', chosen.permit], ['Статус', chosen.status], ['Год', chosen.year], ['Округ / район', `${chosen.okrug} / ${chosen.district}`], ['Общая площадь, м²', number(chosen.area_total)], ['Жилая площадь, м²', number(chosen.area_living)], ['Квартир', number(chosen.apartments)], ['Координаты', `${qualityText[chosen.quality]} · ${chosen.coord_source}`], ['Широта / долгота', `${chosen.lat ?? '—'} / ${chosen.lon ?? '—'}`]].map(([label, value]) => <div key={String(label)}><dt>{label}</dt><dd>{value ?? '—'}</dd></div>)}</dl></section>}
      <section className="section"><div className="section-heading"><h2>Реестр объектов · {number(rows.length)}</h2><div className="chart-actions"><button className="icon-button" title="Скачать полный реестр CSV" aria-label="Скачать полный реестр CSV" onClick={() => exportCsv(mapCsvRows(report.rows), 'map-objects.csv')}><Download size={18} /></button><button className="icon-button" title="Скачать реестр Excel" aria-label="Скачать реестр Excel" onClick={excel} disabled={busy}><FileSpreadsheet size={18} /></button></div></div>{exportError && <p role="alert">{exportError}</p>}<input className="map-search" aria-label="Поиск объектов" placeholder="Адрес, объект, застройщик" value={search} onChange={e => change({ search: e.target.value || null, page: null })} />
      <div className="table-wrap"><table><thead><tr>{['Адрес', 'Застройщик', 'Статус', 'Год', 'Координаты', ''].map((label, i) => <th key={i}>{label}</th>)}</tr></thead><tbody>{shown.map(row => <tr key={row.id}><th scope="row">{row.address || '—'}</th><td>{row.developer || '—'}</td><td>{row.status}</td><td>{row.year ?? '—'}</td><td>{qualityText[row.quality]}</td><td><button className="icon-button" aria-label={`Показать объект ${row.id}`} title="Показать объект" onClick={() => change({ object: row.id })}><LocateFixed size={16} /></button></td></tr>)}</tbody></table></div>{!rows.length && <p className="empty-state">Нет объектов по выбранным фильтрам.</p>}<div className="pagination"><span>Страница {page} из {pages}</span><button className="icon-button" title="Предыдущая страница" aria-label="Предыдущая страница карты" disabled={page <= 1} onClick={() => change({ page: String(page - 1) })}><ChevronLeft size={16} /></button><button className="icon-button" title="Следующая страница" aria-label="Следующая страница карты" disabled={page >= pages} onClick={() => change({ page: String(page + 1) })}><ChevronRight size={16} /></button></div></section>
      <section className="section"><h2>Источники и координаты</h2>{report.notes.map(note => <p className="method-note" key={note}>{note}</p>)}<p className="muted">РВ: {number(report.scope.rvRows)} строк · ОКС с 2011: {number(report.scope.oksRows)} · ОКС без даты выдачи: {number(report.scope.missingIssueDateRows)}</p><details><summary>Файлы и версия</summary>{report.source.files.map(file => <p key={file}><code>{file}</code></p>)}<code>{report.version}</code></details></section>
    </>}<footer className="page-footer">Аналитика Москвы · карта объектов</footer></main>;
}
