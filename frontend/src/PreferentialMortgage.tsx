import { useEffect, useMemo, useRef, useState } from 'react';
import { Database, RefreshCw } from 'lucide-react';
import * as echarts from 'echarts/core';
import { MarkPointComponent } from 'echarts/components';
import { Chart } from './Chart';
import { ConstructionEmpty, ConstructionExport, ConstructionLoadState } from './PermitsPage';
import { date } from './format';
import { characteristicChartOption, characteristicRows, characteristics, flowChartOption, flowRows, preferentialUrl, programs } from './preferentialMortgageData';
import type { CharacteristicYear, FlowMetric, FlowYear, PreferentialCatalog, Program } from './preferentialMortgageData';
import { usePreferentialMortgageData } from './usePreferentialMortgageData';
import { NextRelease } from './NextRelease';
import type { CommissioningChange } from './Operational';
import type { Theme } from './types';
import './construction-pages.css';
import './preferential-mortgage.css';

echarts.use([MarkPointComponent]);

function useChartWidth() {
  const ref = useRef<HTMLDivElement>(null), [width, setWidth] = useState(600);
  useEffect(() => { if (!ref.current) return; const observer = new ResizeObserver(entries => setWidth(entries[0].contentRect.width)); observer.observe(ref.current); return () => observer.disconnect(); }, []);
  return { ref, width };
}
function ProgramLegend({ hidden, change }: { hidden: Program[]; change: (value: Program[]) => void }) {
  return <div className="pm-programs legend" aria-label="Ипотечные программы">{programs.map(program => <button type="button" key={program.id} aria-pressed={!hidden.includes(program.id)} className={hidden.includes(program.id) ? 'legend-off' : ''} title={`${hidden.includes(program.id) ? 'Показать' : 'Скрыть'}: ${program.label}`} onClick={() => change(hidden.includes(program.id) ? hidden.filter(id => id !== program.id) : [...hidden, program.id])}><i style={{ background: program.color }} />{program.label}</button>)}</div>;
}
function MonthControl({ catalog, value, label, onChange }: { catalog: PreferentialCatalog | null; value: number; label: string; onChange: (value: string) => void }) {
  return <label><span>Накопленный период</span><select aria-label={label} value={value} onChange={event => onChange(event.target.value)}>{catalog?.months.map(month => <option key={month.id} value={month.id}>{month.label}</option>)}</select></label>;
}
function FlowChart({ rows, metric, cumulative, theme, hidden, filename, partialDate }: { rows: FlowYear[]; metric: FlowMetric; cumulative: boolean; theme: Theme; hidden: Program[]; filename: string; partialDate?: string | null }) {
  const { ref, width } = useChartWidth(), title = metric === 'volumeMln' ? 'Объём выданных кредитов' : 'Количество договоров';
  const option = useMemo(() => flowChartOption(rows, metric, theme, width, hidden, cumulative), [rows, metric, theme, width, hidden, cumulative]);
  return <article className="pm-flow" ref={ref}><h3>{title}</h3>{rows.some(row => programs.some(program => row.programs[program.id][metric] !== null)) ? <Chart option={option} theme={theme} label={`${title} · ${cumulative ? 'накопленный период' : 'по годам'}`} rows={flowRows(rows, metric, partialDate)} filename={`${filename}-${metric}`} height={320} /> : <ConstructionEmpty />}</article>;
}
function Characteristics({ annual, cumulativeRows, theme, catalog, month, params, change }: { annual: CharacteristicYear[]; cumulativeRows: CharacteristicYear[]; theme: Theme; catalog: PreferentialCatalog | null; month: number; params: URLSearchParams; change: CommissioningChange }) {
  const [hidden, setHidden] = useState<Program[]>([]), cumulative = params.get('characteristicMode') === 'cumulative', rows = cumulative ? cumulativeRows : annual;
  const { ref, width } = useChartWidth();
  return <section className="cp-section pm-characteristics" ref={ref} aria-labelledby="pm-characteristics-title">
    <div className="cp-section-heading"><h2 id="pm-characteristics-title">Характеристики выданных кредитов</h2><div className="pm-characteristic-controls"><div className="pm-segmented" role="group" aria-label="Период характеристик"><button type="button" aria-pressed={!cumulative} onClick={() => change({ characteristicMode: 'annual' })}>По годам</button><button type="button" aria-pressed={cumulative} onClick={() => change({ characteristicMode: 'cumulative' })}>Накопленно</button></div>{cumulative && <div className="cp-controls"><MonthControl catalog={catalog} value={month} label="Накопленный период характеристик" onChange={value => change({ characteristicMonth: value })} /></div>}</div></div>
    <ProgramLegend hidden={hidden} change={setHidden} />
    <div className="pm-characteristic-rows">{characteristics.map(metric => <article className="pm-characteristic" key={metric.id}><div className="pm-characteristic-name"><h3>{metric.label}</h3><span>{metric.unit}</span></div>{rows.some(row => programs.some(program => row.programs[program.id][metric.id] !== null)) ? <Chart option={characteristicChartOption(rows, metric.id, theme, Math.max(260, width - (width > 760 ? 206 : 0)), hidden, cumulative)} theme={theme} label={`${metric.label} · ${cumulative ? 'накопленный период' : 'по годам'}`} rows={characteristicRows(rows, metric.id)} filename={`preferential-${metric.id}-${cumulative ? month : 'annual'}`} height={210} /> : <ConstructionEmpty>Нет данных для выбранного периода.</ConstructionEmpty>}</article>)}</div>
  </section>;
}
export default function PreferentialMortgage({ theme, query, change }: { theme: Theme; query: string; change: CommissioningChange }) {
  const params = useMemo(() => new URLSearchParams(query), [query]), { catalog, selection, data, error, retry } = usePreferentialMortgageData(params);
  const [annualHidden, setAnnualHidden] = useState<Program[]>([]), [cumulativeHidden, setCumulativeHidden] = useState<Program[]>([]);
  const partialDate = data?.latestPeriodPartial ? data.source.date : null;
  return <main id="preferential-mortgage-page" className="cp-page pm-page">
    <header className="cp-page-heading pm-heading"><div><div className="eyebrow">ОТРАСЛЕВЫЕ ПОКАЗАТЕЛИ</div><h1>Льготное ипотечное кредитование</h1><NextRelease rule="preferentialMortgage" period={null} schedule="10-го числа второго месяца после отчётного периода; точная дата уточняется" />{data && <p className="pm-source-stamp">ДОМ.РФ · {data.regionLabel} · {date(data.source.date)}</p>}</div><div className="pm-heading-controls"><label><span>Регион</span><select aria-label="Регион льготной ипотеки" value={selection?.region ?? ''} disabled={!catalog} onChange={event => change({ region: event.target.value, month: null, characteristicMonth: null })}>{!catalog && <option value="">Загрузка регионов</option>}{catalog?.regions.map(region => <option key={region.id} value={region.id}>{region.label}</option>)}</select></label>{data && <ConstructionExport url={preferentialUrl(data.selection, data.version)} filename={`preferential-mortgage-${data.selection.region}.xlsx`} label="Скачать льготную ипотеку Excel" refresh={retry} />}</div></header>
    {error || !data ? <ConstructionLoadState error={error} retry={retry} label="льготное ипотечное кредитование" /> : <>
      <section className="cp-section" aria-labelledby="pm-annual-title"><div className="cp-section-heading"><h2 id="pm-annual-title">Выдача льготной ипотеки по годам</h2></div><ProgramLegend hidden={annualHidden} change={setAnnualHidden} /><div className="pm-flow-grid"><FlowChart rows={data.annual} metric="volumeMln" cumulative={false} theme={theme} hidden={annualHidden} filename="preferential-annual" partialDate={partialDate} /><FlowChart rows={data.annual} metric="count" cumulative={false} theme={theme} hidden={annualHidden} filename="preferential-annual" partialDate={partialDate} /></div>{data.latestPeriodPartial && <p className="pm-period-note">{data.latestPeriod.slice(0, 4)}: последний месяц неполный, данные на {date(data.source.date)}.</p>}</section>
      <section className="cp-section" aria-labelledby="pm-cumulative-title"><div className="cp-section-heading"><h2 id="pm-cumulative-title">Выдача за накопленный период</h2><div className="cp-controls"><MonthControl catalog={catalog} value={data.selection.month} label="Накопленный период выдачи" onChange={value => change({ month: value })} /></div></div><ProgramLegend hidden={cumulativeHidden} change={setCumulativeHidden} /><div className="pm-flow-grid"><FlowChart rows={data.cumulative} metric="volumeMln" cumulative theme={theme} hidden={cumulativeHidden} filename={`preferential-cumulative-${data.selection.month}`} partialDate={partialDate} /><FlowChart rows={data.cumulative} metric="count" cumulative theme={theme} hidden={cumulativeHidden} filename={`preferential-cumulative-${data.selection.month}`} partialDate={partialDate} /></div>{data.latestPeriodPartial && data.selection.month >= Number(data.latestPeriod.slice(5)) && <p className="pm-period-note">{data.latestPeriod.slice(0, 4)}: последний месяц неполный, данные на {date(data.source.date)}.</p>}</section>
      <Characteristics annual={data.characteristicsAnnual} cumulativeRows={data.characteristicsCumulative} theme={theme} catalog={catalog} month={data.selection.characteristicMonth} params={params} change={change} />
      <details className="cp-source"><summary><Database size={16} />Источник и расчёты</summary><div className="cp-source-content"><dl className="cp-metadata"><div><dt>Источник</dt><dd><a href={data.source.url} target="_blank" rel="noreferrer">ДОМ.РФ · статистические ряды по регионам</a></dd></div><div><dt>Дата источника</dt><dd>{date(data.source.date)}</dd></div><div><dt>Сохранено</dt><dd>{date(data.source.collectedAt)}</dd></div><div><dt>Листы отчёта</dt><dd>01_02_01 · выдача кредитов<br />01_02_05 · характеристики кредитов</dd></div></dl>{data.notes.map((note, index) => <p className="cp-note" key={index}>{note}</p>)}</div></details>
      <footer className="page-footer"><span>Аналитика Москвы · ДОМ.РФ</span><button type="button" className="icon-button" title="Обновить льготную ипотеку" aria-label="Обновить льготную ипотеку" onClick={retry}><RefreshCw size={15} /></button></footer>
    </>}
  </main>;
}
