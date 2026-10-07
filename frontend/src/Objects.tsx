import { useState } from 'react';
import { ChevronLeft, ChevronRight, Download, Search } from 'lucide-react';
import { exportCsv, number } from './format';
import type { JsonValue, RegionProfile } from './types';

const cell = (value: JsonValue | undefined): string | number | null => value == null ? null : typeof value === 'number' || typeof value === 'string' ? value : JSON.stringify(value);
export default function Objects({ tables, developerId }: { tables: RegionProfile['objects']; developerId: string }) {
  return <section className="section objects-section"><h2>Реестры объектов · Москва</h2>{tables.map((table, i) => <ObjectTable key={`${developerId}-${i}`} table={table} filename={`${developerId}-objects-${i}`} />)}</section>;
}
function ObjectTable({ table, filename }: { table: RegionProfile['objects'][number]; filename: string }) {
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const filtered = table.rows.filter(r => !query || table.columns.some(c => String(cell(r[c]) ?? '').toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru'))));
  const pages = Math.max(1, Math.ceil(filtered.length / 20));
  const safePage = Math.min(page, pages - 1);
  const shown = filtered.slice(safePage * 20, safePage * 20 + 20);
  return <details className="object-details"><summary><span>{table.title}</span><small>{number(table.rows.length)} объектов</small></summary><p className="muted">{table.note}</p><div className="object-toolbar"><label className="object-search"><Search size={14} /><input value={query} onChange={e => { setQuery(e.target.value); setPage(0); }} placeholder="Поиск объекта" aria-label={`Поиск: ${table.title}`} /></label><button className="icon-button" title="Скачать все строки CSV" onClick={() => exportCsv([table.columns, ...table.rows.map(r => table.columns.map(c => cell(r[c])))], `${filename}.csv`)}><Download size={15} /></button></div><div className="table-wrap"><table className="objects-table"><thead><tr>{table.columns.map(c => <th key={c}>{c}</th>)}</tr></thead><tbody>{shown.map((r, i) => <tr key={i}>{table.columns.map(c => <td key={c}>{typeof r[c] === 'number' ? number(r[c] as number, Number.isInteger(r[c]) ? 0 : 1) : cell(r[c]) ?? '—'}</td>)}</tr>)}</tbody></table></div>{!shown.length && <p className="muted">Объекты не найдены.</p>}<div className="pagination"><span>{number(filtered.length)} объектов · страница {safePage + 1} из {pages}</span><button className="icon-button" title="Предыдущая страница" disabled={safePage === 0} onClick={() => setPage(safePage - 1)}><ChevronLeft size={15} /></button><button className="icon-button" title="Следующая страница" disabled={safePage + 1 === pages} onClick={() => setPage(safePage + 1)}><ChevronRight size={15} /></button></div></details>;
}
