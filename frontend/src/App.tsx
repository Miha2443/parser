import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, ArrowDownToLine, Building2, CalendarDays, ChevronRight, Database, ExternalLink, Menu, RefreshCw, Star } from 'lucide-react';
import Sidebar from './Sidebar';
import { Chart, annualOption, donutOption } from './Chart';
import Objects from './Objects';
import { area, categories, date, exportCsv, money, number, percent, sumAreas } from './format';
import ProfileExport from './ProfileExport';
import { dataMode, useDashboardData } from './useDashboardData';
import type { Areas, Sales, Source, Theme } from './types';
import Apartments from './Apartments';
import SalesPage from './Sales';

function SourceLine({ sources, ids, note }: { sources: Source[]; ids: string[]; note?: string }) {
  const selected = sources.filter(s => ids.includes(s.id));
  return <div className="source-line">{selected.map(s => <span key={s.id}>{s.label} · {date(s.date)}</span>)}{note && <span>{note}</span>}</div>;
}
function Empty({ text = 'Нет данных для выбранного застройщика и региона.' }: { text?: string }) { return <div className="empty-state"><Database size={20} /><span>{text}</span></div>; }
function SalesBlock({ label, data }: { label: string; data: Sales | null }) {
  return <div className="sales-region"><h3>{label}</h3><p className="muted">{data?.period ?? 'Период не указан'}</p>{data ? <><div className="progress-metrics">{[{ label: 'Распроданность', value: data.sold, color: '#2c9869' }, { label: 'Стройготовность', value: data.readiness, color: '#329d9c' }].map(m => <div key={m.label}><div className="progress-label"><span>{m.label}</span><strong>{percent(m.value)}</strong></div><div className="progress-track"><div style={{ width: `${Math.max(0, Math.min(100, m.value ?? 0))}%`, background: m.color }} /></div></div>)}</div><div className="ratio-row"><span>Отношение Р/С</span><strong>{percent(data.ratio)}</strong></div></> : <Empty />}</div>;
}
function Metric({ label, value, unit, foot, danger = false }: { label: string; value: string; unit?: string; foot?: string; danger?: boolean }) {
  return <div className={`metric ${danger ? 'danger' : ''}`}><span className="metric-label">{label}</span><div className="metric-number">{value}<span>{unit}</span></div>{foot && <span className="metric-foot">{foot}</span>}</div>;
}
const areaRows = (a: Areas, construction = false) => [['Категория', 'Площадь, м²'], ...categories.filter(c => a[c.key] != null).map(c => [construction && c.key === 'nonresidentialSeparate' ? 'Нежилое' : c.label, a[c.key] ?? null])] as (string | number | null)[][];

export default function App() {
  const [theme, setTheme] = useState<Theme>(() => { try { return localStorage.getItem('dashboard.theme') === 'light' ? 'light' : 'dark'; } catch { return 'dark'; } });
  const [compact, setCompact] = useState(() => localStorage.getItem('dashboard.compact') === 'true');
  const [menu, setMenu] = useState(false);
  const [query, setQuery] = useState(window.location.search);
  const [path, setPath] = useState(window.location.pathname);
  const routeQueries = useRef<Record<string, string>>({ [window.location.pathname]: window.location.search });
  const apartmentQuery = useRef(window.location.pathname.startsWith('/apartments') ? window.location.search : '');
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem('dashboard.theme', theme); }, [theme]);
  useEffect(() => { const listener = () => { setQuery(window.location.search); setPath(window.location.pathname); }; window.addEventListener('popstate', listener); return () => window.removeEventListener('popstate', listener); }, []);
  function change(values: Record<string, string | null>, replace = false) {
    const next = new URLSearchParams(window.location.search);
    for (const [key, value] of Object.entries(values)) value == null ? next.delete(key) : next.set(key, value);
    window.history[replace ? 'replaceState' : 'pushState'](null, '', `${window.location.pathname}?${next}${window.location.hash}`);
    setQuery(window.location.search);
  }
  function navigate(href: string) {
    const currentPath = window.location.pathname, currentQuery = window.location.search;
    routeQueries.current[currentPath] = currentQuery;
    if (currentPath === '/apartments' || currentPath === '/apartments/developer') apartmentQuery.current = currentQuery;
    const target = new URL(href, window.location.origin);
    if (!target.search && !target.hash) {
      const restored = new URLSearchParams(routeQueries.current[target.pathname] ?? '');
      if (target.pathname === '/apartments' || target.pathname === '/apartments/developer') {
        const common = new URLSearchParams(apartmentQuery.current);
        for (const key of ['region', 'developer']) { const value = common.get(key); if (value !== null) restored.set(key, value); }
      }
      target.search = restored.toString();
    }
    window.history.pushState(null, '', `${target.pathname}${target.search}${target.hash}`); setPath(window.location.pathname); setQuery(window.location.search); setMenu(false); window.scrollTo(0, 0);
  }
  const detail = path === '/apartments/developer';
  const apartments = detail || path === '/apartments';
  const sales = path === '/sales';
  const title = sales ? 'Распроданность и стройготовность' : apartments ? detail ? 'Квартирография по девелоперу' : 'Квартирография' : 'Профиль застройщика';
  return <div className={`app-shell ${compact ? 'compact-shell' : ''}`}>
    <Sidebar theme={theme} onTheme={() => setTheme(theme === 'light' ? 'dark' : 'light')} open={menu} onClose={() => setMenu(false)} compact={compact} onCompact={() => { setCompact(!compact); localStorage.setItem('dashboard.compact', String(!compact)); }} path={path} onNavigate={navigate} />
    <div className="workspace">
      <header className="topbar"><div className="breadcrumbs"><button className="icon-button menu-button" onClick={() => setMenu(true)} title="Открыть навигацию"><Menu size={20} /></button><span>Рынок недвижимости</span><ChevronRight size={13} /><strong>{title}</strong></div><div className="institutional-brand"><img src="/brand-gk.svg" alt="" /><span>Градостроительный<br />комплекс Москвы</span><img src="/brand-dgp.svg" alt="" /><span>Департамент градостроительной<br />политики города Москвы</span></div></header>
      {sales ? <SalesPage theme={theme} query={query} change={change} /> : apartments ? <Apartments key={path} detail={detail} theme={theme} query={query} change={change} navigate={navigate} /> : <ProfilePage theme={theme} query={query} change={change} />}
    </div>
  </div>;
}

function ProfilePage({ theme, query, change }: { theme: Theme; query: string; change: (values: Record<string, string | null>, replace?: boolean) => void }) {
  const params = useMemo(() => new URLSearchParams(query), [query]);
  const { catalog, developer, region, snapshot, profile, error, retry } = useDashboardData(params);
  const years = profile?.annual.map(r => r.year).sort((a, b) => a - b) ?? [];
  const fromParam = Number(params.get('from'));
  const toParam = Number(params.get('to'));
  const from = years.includes(fromParam) ? fromParam : years[0];
  const to = years.includes(toParam) && toParam >= from ? toParam : years.at(-1);
  const annual = profile?.annual.filter(r => r.year >= from && r.year <= (to ?? from)).sort((a, b) => a.year - b.year) ?? [];
  const periodAreas: Areas = {};
  for (const c of categories) {
    const present = annual.map(r => r[c.key]).filter((v): v is number => v != null);
    if (present.length) periodAreas[c.key] = present.reduce((sum, value) => sum + value, 0);
  }
  useEffect(() => {
    if (!developer || !region) return;
    const next = new URLSearchParams(query); next.set('developer', developer.id); next.set('region', region);
    if (profile) {
      if (from != null && to != null) { next.set('from', String(from)); next.set('to', String(to)); } else { next.delete('from'); next.delete('to'); }
    }
    if (next.toString() !== new URLSearchParams(query).toString()) change(Object.fromEntries([...new Set([...new URLSearchParams(query).keys(), ...next.keys()])].map(key => [key, next.get(key)])), true);
  }, [developer, region, profile, from, to, query]);
  const sources = snapshot?.sources ?? [];
  const metadata = snapshot ?? catalog?.metadata;
  const last = profile ? [...profile.annual].sort((a, b) => b.year - a.year)[0] : undefined;
  const score = profile?.ratings.find(r => r.score != null)?.score;
  return <main id="profile">
        <div className="page-heading"><div><div className="eyebrow">АНАЛИТИКА НЕДВИЖИМОСТИ</div><h1>Профиль застройщика</h1></div><div className="snapshot-label"><Database size={14} />{dataMode === 'snapshot' ? 'Зафиксированный срез' : 'Данные API'}<span>{metadata?.generatedAt ? date(metadata.generatedAt) : 'Загрузка источников'}</span></div></div>
        {catalog && developer && region && <div className="filters"><label className="developer-filter"><span>Группа компаний</span><select aria-label="Группа компаний" value={developer.id} onChange={e => change({ developer: e.target.value, region: null, from: null, to: null })}>{catalog.developers.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label><label><span>Регион квартирографии</span><select aria-label="Регион" value={region} onChange={e => change({ region: e.target.value })}>{developer.regions.map(r => <option key={r} value={r}>{r === 'msk' ? 'Москва' : 'Российская Федерация'}</option>)}</select></label>{profile && <a className="source-anchor" href="#sources" title="Исходные файлы и даты"><Database size={15} />Источники<ChevronRight size={14} /></a>}</div>}
        {error ? <div className="load-state" role="alert"><AlertCircle size={26} /><h2>{dataMode === 'snapshot' ? 'Нет доступного снимка' : 'Данные API недоступны'}</h2><p>{error}</p><button onClick={retry}><RefreshCw size={15} />Повторить</button></div> : !snapshot || !developer || !region || !profile ? <div className="load-state" role="status" aria-live="polite"><RefreshCw className="loading-icon" size={26} /><p>{catalog ? 'Загрузка профиля застройщика…' : 'Загрузка каталога застройщиков…'}</p></div> : <>
          <div className="developer-heading"><div className="developer-name"><Building2 size={21} /><h2>{developer.name}</h2><span className="region-label">Мониторинг Москвы</span></div><div className="profile-actions"><ProfileExport developer={developer.id} region={region} version={snapshot.version} frozen={dataMode === 'snapshot'} /><a className="streamlit-link" href={`http://localhost:8501/${encodeURIComponent('Профиль_застройщика')}`} target="_blank" rel="noreferrer" title="Профиль застройщика · Streamlit">Streamlit<ExternalLink size={13} /></a></div></div>
          <div className="kpi-band"><Metric label="В строительстве" value={area(profile.construction ? sumAreas(profile.construction) : null)} unit="тыс. м²" foot="Мониторинг 2.0 · действующие РС" /><Metric label={`Введено · ${from ?? '—'}–${to ?? '—'}`} value={area(sumAreas(periodAreas))} unit="тыс. м²" foot="Все типы площади · выбранные годы" /><Metric label={`Ввод · ${last?.year ?? '—'}`} value={area(last ? sumAreas(last) : null)} unit="тыс. м²" foot="Последний год в реестре" /><Metric label="Оценка ЕРЗ" value={number(score, 1)} foot="По доступной таблице рейтинга" /></div>
          <nav className="section-nav" aria-label="Разделы профиля"><a href="#dynamics">Ввод недвижимости</a><a href="#structure">Структура площади</a><a href="#construction">Строительство</a><a href="#apartments">Квартирография</a><a href="#delays">Сроки ввода</a><a href="#escrow">Эскроу</a></nav>
          <section id="dynamics" className="section annual-section"><div className="annual-main"><div className="section-heading"><div><h2>Динамика ввода</h2><p>Москва · все типы площади</p></div>{years.length > 0 && <div className="year-range"><CalendarDays size={15} /><select aria-label="Начальный год" value={from} onChange={e => change({ from: e.target.value, to: Number(e.target.value) > (to ?? 0) ? e.target.value : String(to) })}>{years.map(y => <option key={y}>{y}</option>)}</select><span>—</span><select aria-label="Конечный год" value={to} onChange={e => change({ to: e.target.value })}>{years.filter(y => y >= from).map(y => <option key={y}>{y}</option>)}</select></div>}</div>{annual.length ? <Chart option={annualOption(annual, theme)} theme={theme} label="Годовой ввод по типам площади" rows={[["Год", ...categories.map(c => `${c.label}, м²`)], ...annual.map(r => [r.year, ...categories.map(c => r[c.key] ?? null)])]} filename={`${developer.id}-msk-annual-${from}-${to}`} height={320} /> : <Empty />}<SourceLine sources={sources} ids={['monitoring']} /></div><aside className="rating-section"><div className="section-heading"><h2>Рейтинги ЕРЗ</h2><Star size={16} /></div><p className="muted">Место в рейтинге</p>{profile.ratings.length ? <div className="rating-list">{profile.ratings.map((r, i) => <div className="rating-row" key={i}><div><span>{r.label}</span><small>{r.region}</small>{r.note && <small className="warning-text">{r.note}</small>}</div><strong>{number(r.place)}</strong></div>)}</div> : <Empty text="Подтверждённые места в рейтингах отсутствуют." />}<SourceLine sources={sources} ids={['erz_top']} /></aside></section>
          <section id="structure" className="section"><div className="section-heading"><div><h2>Структура площади</h2><p>Москва · мониторинг ввода</p></div></div><div className="donut-grid">{profile.structures.length ? profile.structures.map((s, i) => <div className="donut-item" key={i}><h3>{s.title}</h3>{(sumAreas(s.areas) ?? 0) > 0 ? <Chart option={donutOption(s.areas, theme)} theme={theme} label={s.title} rows={areaRows(s.areas)} filename={`${developer.id}-structure-${i}`} height={200} /> : <Empty />}<SourceLine sources={sources} ids={[s.sourceId]} note={s.note} /></div>) : <Empty />}</div></section>
          {!!profile.geography?.length && <section className="section"><div className="section-heading"><div><h2>География ввода жилой площади</h2><p>Москва и другие регионы РФ · сопоставимые годы</p></div></div><div className="donut-grid">{profile.geography.map((g, i) => <div className="donut-item" key={i}><h3>{g.title}</h3>{g.moscow != null && g.others != null && g.moscow + g.others > 0 ? <Chart option={donutOption({ housing: g.moscow, nonresidentialEmbedded: g.others }, theme, { housing: 'Москва', nonresidentialEmbedded: 'Другие регионы РФ' })} theme={theme} label={g.title} rows={[["Регион", "Жилая площадь, м²"], ['Москва', g.moscow], ['Другие регионы РФ', g.others]]} filename={`${developer.id}-geography-${i}`} height={200} /> : <Empty text="Нет сопоставимых данных за одинаковые годы." />}<SourceLine sources={sources} ids={['monitoring', 'erz_cards']} note={g.note} /></div>)}</div></section>}
          <section id="construction" className="section two-columns"><div><div className="section-heading"><div><h2>В строительстве</h2><p>Москва · структура площади</p></div></div>{profile.construction && (sumAreas(profile.construction) ?? 0) > 0 ? <div className="construction-structure"><Chart option={donutOption(profile.construction, theme, { nonresidentialSeparate: 'Нежилое' })} theme={theme} label="Структура текущего строительства" rows={areaRows(profile.construction, true)} filename={`${developer.id}-construction`} height={210} /><div className="area-list">{categories.filter(c => profile.construction?.[c.key] != null).map(c => <div key={c.key}><i style={{ background: c.color }} /><span>{c.key === 'nonresidentialSeparate' ? 'Нежилое' : c.label}</span><strong>{area(profile.construction?.[c.key])}</strong></div>)}<small>тыс. м²</small></div></div> : <Empty />}<SourceLine sources={sources} ids={['monitoring']} /></div><div className="sales-section"><div className="section-heading"><h2>Распроданность и готовность</h2></div><div className="sales-regions">{profile.salesByRegion.map(s => <SalesBlock key={s.label} label={s.label} data={s.data} />)}</div><SourceLine sources={sources} ids={['sales']} /></div></section>
          <section id="apartments" className="section">
            <div className="section-heading"><div><h2>Квартирография</h2><p>{profile.label} · квартиры в строительстве</p></div>{profile.apartments.length > 0 && <button className="icon-button" title="Скачать квартирографию CSV" onClick={() => exportCsv([['Тип квартиры', 'Количество', 'Площадь, м²', 'Доля, %'], ...profile.apartments.map(a => [a.label, a.count, a.area, a.share])], `${developer.id}-${profile.id}-apartments.csv`)}><ArrowDownToLine size={17} /></button>}</div>
            <div className="apartment-summary">
              <Metric label="Всего квартир" value={number(profile.apartmentSummary.count)} unit="шт." />
              <Metric label="Общая площадь" value={area(profile.apartmentSummary.area)} unit="тыс. м²" />
              <Metric label="Средняя площадь" value={number(profile.apartmentSummary.average, 1)} unit="м²" />
              <Metric label="Доля в площади рынка" value={percent(profile.apartmentSummary.marketShare)} foot={profile.label} />
            </div>
            {profile.apartments.length ? <div className="table-wrap"><table className="apartment-table"><thead><tr><th>Тип квартиры</th><th>Количество, шт.</th><th>Площадь, м²</th><th>Доля квартир</th></tr></thead><tbody>{profile.apartments.map((a, i) => <tr key={i}><td>{a.label}</td><td>{number(a.count)}</td><td>{number(a.area, 1)}</td><td><div className="share-cell"><div className="share-track"><i style={{ width: `${Math.max(0, Math.min(100, a.share ?? 0))}%` }} /></div><span>{percent(a.share)}</span></div></td></tr>)}</tbody></table></div> : <Empty />}
            <SourceLine sources={sources} ids={['apartments']} note={profile.apartmentNote} />
          </section>
          <section id="delays" className="section"><div className="section-heading"><div><h2>Переносы сроков ввода</h2><p>Москва и другие регионы РФ (РФ минус Москва)</p></div><button className="icon-button" title="Скачать переносы сроков CSV" onClick={() => exportCsv([['Показатель', 'Перенос, м²', 'База, м²', 'Примечание'], ...profile.delayMetrics.map(m => [m.label, m.value, m.base ?? null, m.note ?? ''])], `${developer.id}-delays.csv`)}><ArrowDownToLine size={17} /></button></div>{profile.delayMetrics.length ? <div className="delay-band">{profile.delayMetrics.map((m, i) => <Metric key={i} label={m.label} value={m.displayValue || '—'} danger foot={`${m.displayPercent || '—'} · ${m.displayBase || ''}${m.note ? `. ${m.note}` : ''}`} />)}</div> : <Empty />}<SourceLine sources={sources} ids={['erz_top', 'erz_cards', 'monitoring']} note="Разные базы площади: Москва — Мониторинг 2.0; другие регионы — разность ЕРЗ РФ и Москвы." /></section>
          <section id="escrow" className="section"><div className="section-heading"><div><h2>Кредитные лимиты и эскроу</h2><p>Москва · финансовые показатели</p></div></div>{profile.escrow ? <div className="escrow-band"><Metric label="Объём займов" value={money(profile.escrow.credit)} unit="млрд ₽" /><Metric label="Остаток задолженности" value={money(profile.escrow.debt)} unit="млрд ₽" /><Metric label="Доля остатка" value={percent(profile.escrow.debtShare)} /><Metric label="Выручка от продаж" value={money(profile.escrow.revenue)} unit="млрд ₽" /><Metric label="Покрытие выручкой" value={percent(profile.escrow.coverage)} /></div> : <Empty text="В московском реестре эскроу нет данных выбранной группы компаний." />}<SourceLine sources={sources} ids={['escrow']} note={profile.escrow?.note} /></section>
          <Objects tables={profile.objects} developerId={developer.id} />
          <section id="sources" className="section sources-section"><details><summary><Database size={17} /><span>Исходные файлы и даты</span><span className="source-count">{sources.length} источников</span></summary><p className="muted">Дата формирования: {date(snapshot.generatedAt)}.{snapshot.version && ` Версия: ${snapshot.version}.`} Даты ниже относятся к исходным документам, а не к сегодняшнему дню.</p><div className="source-list">{sources.map(s => <div key={s.id}><strong>{s.label}</strong><span>{date(s.date)}</span><div>{s.files.map((f, i) => <code key={i}>{f}</code>)}{s.note && <p>{s.note}</p>}</div></div>)}{!!profile.sourceContributions.length && <div><strong>Выбор источников профиля</strong><span>quality.source</span><div>{profile.sourceContributions.map(path => <code key={path}>{path}</code>)}<p>Пути, указанные в метаданных качества профиля. Не исчерпывающий журнал чтений.</p></div></div>}</div>{[...snapshot.notes, ...profile.notes].map((n, i) => <p className="method-note" key={i}><AlertCircle size={14} />{n}</p>)}</details></section>
          <footer className="page-footer"><span>Аналитика Москвы</span><span>Профиль застройщика · {dataMode === 'snapshot' ? 'зафиксированный срез' : 'API'}</span></footer>
        </>}
      </main>;
}
