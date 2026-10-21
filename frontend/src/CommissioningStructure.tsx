import { ArrowUpRight, Building2, House, Info } from 'lucide-react';
import { chartNumberFormatter, number } from './format';
import type { MonthlyStructure } from './commissioningData';
import './commissioning-structure.css';

type Row = MonthlyStructure['rows'][number];
const months = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
function Amount({ row, large = false }: { row: Row; large?: boolean }) {
  const incomplete = row.value === null && row.knownSum !== null;
  return <span className={large ? 'commissioning-structure-amount large' : 'commissioning-structure-amount'} title={incomplete ? 'Сумма известных площадей. Часть значений в реестре не заполнена.' : undefined}>{incomplete ? '≥ ' : ''}{number(row.value ?? row.knownSum, 2)}{incomplete && <Info size={12} aria-label="Часть площадей не указана в источнике" />}</span>;
}
const signed = (value: number | null, format: (value: number | null) => string) => value === null ? '—' : `${value > 0 ? '+' : ''}${format(value)}`;
export default function CommissioningStructure({ data }: { data: MonthlyStructure }) {
  const find = (id: string) => data.rows.find(row => row.id === id);
  const total = find('total'), housing = find('housing_objects'), nonres = find('nonres_objects');
  const formatChange = chartNumberFormatter(data.rows.flatMap(row => [row.monthChange === null ? null : row.monthChange * 1000, row.renovationMonthChange === null ? null : row.renovationMonthChange * 1000]));
  const change = (value: number | null) => signed(value === null ? null : value * 1000, formatChange);
  const pct = (value: number | null) => `${signed(value, value => number(value, 1))}${value === null ? '' : '%'}`;
  const renderRows = (ids: string[], renovation = false) => <div className="commissioning-structure-rows">{ids.map(id => {
    const row = find(id); if (!row) return null;
    return <div key={id} className="commissioning-structure-row-group"><div className={`commissioning-structure-row ${['mkd_residential', 'mop', 'nonres_in_housing'].includes(id) ? 'detail' : ''}`} data-structure-id={id}><span>{row.label}</span><Amount row={row} /><span className="structure-delta">{change(row.monthChange)}</span><span className="structure-yoy">{pct(row.yearChangePct)}</span></div>{renovation && id !== 'izhs' && [row.renovation, row.renovationKnownSum, row.renovationMonthChange, row.renovationYearChangePct].some(value => value !== null) && <div className="commissioning-structure-row renovation"><span>из них — реновация</span><Amount row={{ ...row, value: row.renovation, knownSum: row.renovationKnownSum }} /><span>{change(row.renovationMonthChange)}</span><span>{pct(row.renovationYearChangePct)}</span></div>}</div>;
  })}</div>;
  const header = <div className="commissioning-structure-row column-head"><span>Структура площади</span><span>млн м²</span><span>Δ за месяц<br/>тыс. м²</span><span>к АППГ<br/>%</span></div>;
  const headline = (row: Row, title: string, icon: 'housing' | 'nonres') => <><header className="commissioning-structure-category-title">{icon === 'housing' ? <House size={22} /> : <Building2 size={22} />}<h3>{title}</h3></header><div className="commissioning-structure-category-total" data-structure-id={row.id}><strong><Amount row={row} large /><small>млн м²</small></strong><span>{change(row.monthChange)}<small>тыс. м² за месяц</small></span><span>{pct(row.yearChangePct)}<small>к АППГ</small></span></div></>;
  return <div className="commissioning-structure-infographic">
    <div className="commissioning-structure-top"><div className="commissioning-structure-main"><span>Общий ввод недвижимости</span>{total && <strong><Amount row={total} large /><small>млн м²</small></strong>}</div><div className="commissioning-structure-change"><span>Прирост за {months[(data.actualMonth ?? 1) - 1]}</span><strong><ArrowUpRight size={24} />{change(total?.monthChange ?? null)}</strong><small>тыс. м²</small></div><div className="commissioning-structure-comparison"><span>К аналогичному периоду {data.year - 1} года</span><strong>{pct(total?.yearChangePct ?? null)}</strong></div></div>
    <div className="commissioning-structure-columns"><section className="commissioning-structure-category">{housing && headline(housing, 'Жильё', 'housing')}{header}{renderRows(['mkd_total', 'mkd_residential', 'mop', 'nonres_in_housing', 'izhs'], true)}<footer>{renderRows(['residential_area'])}</footer></section><section className="commissioning-structure-category">{nonres && headline(nonres, 'Нежильё', 'nonres')}{header}{renderRows(['offices', 'social', 'warehouses', 'industrial', 'hotels', 'parking', 'trade', 'transport', 'other'])}<footer>{renderRows(['nonres_total'])}</footer></section></div>
  </div>;
}
