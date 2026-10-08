import { useState, type ReactNode } from 'react';
import { ArrowUpRight, Building2, ChevronDown, ChevronLeft, House, Landmark, Moon, PanelLeftClose, Settings2, Sun, UserRound, X } from 'lucide-react';
import type { Theme } from './types';

const root = 'http://localhost:8501';
function Link({ title, path }: { title: string; path: string }) {
  return <a className="nav-link" href={`${root}/${encodeURIComponent(path.replace(/^\d+_/, ''))}`} target="_blank" rel="noreferrer" title={`${title} · Streamlit`}><span>{title}</span><ArrowUpRight size={13} /></a>;
}
export default function Sidebar({ theme, onTheme, open, onClose, compact, onCompact, path, onNavigate }: { theme: Theme; onTheme: () => void; open: boolean; onClose: () => void; compact: boolean; onCompact: () => void; path: string; onNavigate: (href: string) => void }) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>(() => {
    try { return JSON.parse(localStorage.getItem('dashboard.navigation') || '{"market":true,"profile":true,"construction":true,"commissioning":false}'); } catch { return { market: true, profile: true }; }
  });
  function local(title: string, href: string, icon?: ReactNode) {
    return <a className={`nav-link ${path === href ? 'active' : ''}`} href={href} aria-current={path === href ? 'page' : undefined} onClick={e => { onClose(); if (!e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey && e.button === 0) { e.preventDefault(); onNavigate(href); } }}>{icon}<span>{title}</span></a>;
  }
  function group(id: string, label: string, icon: ReactNode, children: ReactNode, nested = false) {
    return <div className={nested ? 'nav-subgroup' : 'nav-group'}>
      <button className="nav-heading" aria-expanded={!!expanded[id]} title={label} onClick={() => { const next = { ...expanded, [id]: !expanded[id] }; setExpanded(next); localStorage.setItem('dashboard.navigation', JSON.stringify(next)); }}>{icon}<span>{label}</span><ChevronDown className={expanded[id] ? 'chevron expanded' : 'chevron'} size={14} /></button>
      {expanded[id] && <div className="nav-children">{children}</div>}
    </div>;
  }
  return <>
    {open && <button className="nav-scrim" onClick={onClose} aria-label="Закрыть навигацию" />}
    <aside className={`sidebar ${open ? 'is-open' : ''} ${compact ? 'is-compact' : ''}`}>
      <div className="brand"><div className="brand-mark"><img src="/brand-moscow.svg" alt="" /></div><div className="brand-text"><strong>Аналитика Москвы</strong><span>Недвижимость и экономика</span></div><button className="icon-button mobile-close" onClick={onClose} title="Закрыть навигацию"><X size={18} /></button></div>
      <div className="nav-caption">РАБОЧЕЕ ПРОСТРАНСТВО</div>
      <nav aria-label="Разделы аналитики">
        <a className="nav-heading home-link" href={root} target="_blank" rel="noreferrer" title="Главная · Streamlit"><House size={17} /><span>Главная</span><ArrowUpRight size={13} /></a>
        {group('market', 'Рынок недвижимости', <Building2 size={17} />, <>
          {group('construction', 'Текущее строительство', null, <><Link title="Оперативные данные" path="0_Текущее_строительство" />{local('Квартирография', '/apartments')}<Link title="Карта объектов" path="9_Карта_объектов" />{local('Распроданность', '/sales')}</>, true)}
          {group('commissioning', 'Ввод недвижимости', null, <><Link title="Оперативный ввод" path="8_Ввод_недвижимости_оперативные" /><Link title="Годовой ввод" path="8_Ввод_недвижимости" /><Link title="Линейные объекты" path="8_Ввод_линейных_объектов" /></>, true)}
          {group('profile', 'Профиль застройщика', null, <>{local('Профиль', '/', <UserRound size={15} />)}{local('Квартирография по застройщику', '/apartments/developer')}</>, true)}
        </>)}
        {group('statistics', 'Мосстат / Росстат', <Landmark size={17} />, <><Link title="ВВП и ВРП" path="3_ВРП_и_ВВП" /><Link title="ИПЦ" path="2_ИПЦ" /><Link title="Заработная плата" path="1_Заработная_плата" /></>)}
        {group('service', 'Сервис', <Settings2 size={17} />, <><Link title="Отправка в TDM" path="8_Отправка_в_TDM" /><Link title="Журнал обновлений" path="99_Обновления" /></>)}
      </nav>
      <div className="sidebar-bottom"><button className="theme-button" onClick={onTheme} title={theme === 'light' ? 'Включить тёмную тему' : 'Включить светлую тему'}>{theme === 'light' ? <Moon size={17} /> : <Sun size={17} />}<span>{theme === 'light' ? 'Тёмная тема' : 'Светлая тема'}</span></button><button className="icon-button collapse-nav" onClick={onCompact} title={compact ? 'Развернуть боковую панель' : 'Свернуть боковую панель'}>{compact ? <ChevronLeft className="rotate" size={17} /> : <PanelLeftClose size={17} />}</button></div>
    </aside>
  </>;
}
