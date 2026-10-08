import { useState, type ReactNode } from 'react';
import { Building2, ChevronDown, ChevronLeft, House, Landmark, Moon, PanelLeftClose, Settings2, Sun, UserRound, X } from 'lucide-react';
import type { Theme } from './types';

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
        {local('Главная', '/home', <House size={17} />)}
        {group('market', 'Рынок недвижимости', <Building2 size={17} />, <>
          {group('construction', 'Текущее строительство', null, <>{local('Оперативные данные', '/construction')}{local('Квартирография', '/apartments')}{local('Карта объектов', '/map')}{local('Распроданность', '/sales')}</>, true)}
          {group('commissioning', 'Ввод недвижимости', null, <>{local('Оперативный ввод', '/commissioning/operational')}{local('Годовой ввод', '/commissioning/annual')}{local('Линейные объекты', '/commissioning/linear')}</>, true)}
          {group('profile', 'Профиль застройщика', null, <>{local('Профиль', '/', <UserRound size={15} />)}{local('Квартирография по застройщику', '/apartments/developer')}</>, true)}
        </>)}
        {group('statistics', 'Мосстат / Росстат', <Landmark size={17} />, <>{local('ВВП и ВРП', '/economics/accounts')}{local('ИПЦ', '/economics/ipc')}{local('Заработная плата', '/economics/salary')}</>)}
        {group('service', 'Сервис', <Settings2 size={17} />, <>{local('Отправка в TDM', '/tdm')}{local('Журнал обновлений', '/updates')}</>)}
      </nav>
      <div className="sidebar-bottom"><button className="theme-button" onClick={onTheme} title={theme === 'light' ? 'Включить тёмную тему' : 'Включить светлую тему'}>{theme === 'light' ? <Moon size={17} /> : <Sun size={17} />}<span>{theme === 'light' ? 'Тёмная тема' : 'Светлая тема'}</span></button><button className="icon-button collapse-nav" onClick={onCompact} title={compact ? 'Развернуть боковую панель' : 'Свернуть боковую панель'}>{compact ? <ChevronLeft className="rotate" size={17} /> : <PanelLeftClose size={17} />}</button></div>
    </aside>
  </>;
}
