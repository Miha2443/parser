import { useEffect, useId, useRef, useState } from 'react';
import { ChevronDown, Check } from 'lucide-react';
import type { CatalogDeveloper } from './types';
import { matchingDevelopers } from './developerSelectData';
import './developer-select.css';

export default function DeveloperSelect({ developers, value, onChange }: { developers: CatalogDeveloper[]; value: string; onChange: (value: string) => void }) {
  const id = useId(), ref = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false), [query, setQuery] = useState(''), [active, setActive] = useState(0);
  const selected = developers.find(developer => developer.id === value);
  const options = matchingDevelopers(developers, query);
  useEffect(() => {
    const close = (event: PointerEvent) => { if (!ref.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, []);
  useEffect(() => { if (open) document.getElementById(`${id}-${active}`)?.scrollIntoView({ block: 'nearest' }); }, [active, open, id]);
  const choose = (developer: CatalogDeveloper) => { onChange(developer.id); setOpen(false); setQuery(''); };
  return <div className="developer-combobox" ref={ref}>
    <div className="developer-combobox-input"><input role="combobox" aria-label="Группа компаний" aria-autocomplete="list" aria-expanded={open} aria-controls={`${id}-options`} aria-activedescendant={open && options[active] ? `${id}-${active}` : undefined}
      value={open ? query : selected?.name ?? ''} placeholder={selected?.name ?? ''}
      onFocus={() => { setOpen(true); setQuery(''); setActive(0); }}
      onClick={() => { if (!open) { setOpen(true); setQuery(''); setActive(0); } }}
      onBlur={event => { if (!ref.current?.contains(event.relatedTarget as Node)) setOpen(false); }}
      onChange={event => { setQuery(event.target.value); setOpen(true); setActive(0); }}
      onKeyDown={event => {
        if (event.key === 'Escape') { event.preventDefault(); setOpen(false); return; }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault(); setOpen(true);
          if (!open) { setQuery(''); setActive(event.key === 'ArrowUp' ? Math.max(0, options.length - 1) : 0); }
          else setActive(index => Math.max(0, Math.min(options.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1))));
        } else if (event.key === 'Enter' && open) {
          event.preventDefault(); if (options[active]) choose(options[active]);
        } else if (event.key === 'Tab') setOpen(false);
      }} /><ChevronDown size={16} aria-hidden="true" /></div>
    {open && <div className="developer-combobox-options" id={`${id}-options`} role="listbox" aria-label="Группы компаний">
      {options.map((developer, index) => <button type="button" role="option" id={`${id}-${index}`} key={developer.id} tabIndex={-1} aria-selected={developer.id === value} className={active === index ? 'is-highlighted' : ''}
        onMouseDown={event => event.preventDefault()} onMouseMove={() => setActive(index)} onClick={() => choose(developer)}><span>{developer.name}</span>{developer.id === value && <Check size={15} aria-hidden="true" />}</button>)}
      {!options.length && <div className="developer-combobox-empty" role="status">Нет совпадений</div>}
    </div>}
  </div>;
}
