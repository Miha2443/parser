import { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { DataZoomComponent } from 'echarts/components';
import { Database, RefreshCw } from 'lucide-react';
import { Chart } from './Chart';
import { ConstructionEmpty, ConstructionExport, ConstructionLoadState } from './PermitsPage';
import { date, number } from './format';
import { mortgageAnnualOption, mortgageAnnualRows, mortgageCumulativeLabel, mortgageHistory, mortgageMetricPeriods, mortgageMetricUnit, mortgageMonthlyOption, mortgageMonthlyRows, mortgagePeriodLabel, mortgageRange, mortgageRatesOption, mortgageSegments, mortgageUrl } from './mortgageData';
import type { MortgageMetric, MortgageMonth, MortgageReport } from './mortgageData';
import { useMortgageData } from './useMortgageData';
import type { CommissioningChange } from './Operational';
import type { Theme } from './types';
import { NextRelease } from './NextRelease';
import { periodFromId } from './releaseSchedule';
import './construction-pages.css';
import './mortgage.css';

echarts.use([LineChart, DataZoomComponent]);

function PeriodSelect({ label, periods, value, onChange }: { label: string; periods: string[]; value: string; onChange: (value: string) => void }) {
  return <label><span>{label}</span><select aria-label={label} value={value} disabled={!periods.length} onChange={event => onChange(event.target.value)}>{!periods.length && <option value="">Нет наблюдений</option>}{periods.map(period => <option key={period} value={period}>{mortgagePeriodLabel(period)}</option>)}</select></label>;
}
function RangeControls({ scope, label, rows, params, change }: { scope: string; label: string; rows: MortgageMonth[]; params: URLSearchParams; change: CommissioningChange }) {
  const range = mortgageRange(rows, params.get(`${scope}_from`), params.get(`${scope}_to`)), periods = rows.map(row => row.period);
  return <div className="cp-controls mortgage-range"><PeriodSelect label={`${label}: с`} periods={periods} value={range.from} onChange={value => change({ [`${scope}_from`]: value, ...(value > range.to ? { [`${scope}_to`]: value } : {}) })} /><PeriodSelect label={`${label}: по`} periods={periods} value={range.to} onChange={value => change({ [`${scope}_to`]: value, ...(value < range.from ? { [`${scope}_from`]: value } : {}) })} /></div>;
}
function AnnualChart({ data, metric, theme }: { data: MortgageReport; metric: MortgageMetric; theme: Theme }) {
  const ref = useRef<HTMLElement>(null), [width, setWidth] = useState(700);
  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(entries => setWidth(entries[0].contentRect.width));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  const latest = [...data.annual].reverse().find(row => row[metric].selected !== null), latestValue = latest?.[metric], divisor = metric === 'volume' ? 1e9 : 1000;
  const option = useMemo(() => mortgageAnnualOption(data.annual, metric, theme, width), [data.annual, metric, theme, width]);
  const title = metric === 'volume' ? 'Объём выданных ипотечных кредитов' : 'Количество ипотечных договоров';
  return <article className="mortgage-annual-chart" ref={ref} aria-label={title}><div className="mortgage-chart-title"><h3>{title}</h3><span>{mortgageMetricUnit(metric)}</span></div><div className="mortgage-annual-highlight"><strong>{number(latestValue?.selected == null ? null : latestValue.selected / divisor, 1)}</strong><span>{latest ? `${mortgageCumulativeLabel(latestValue?.actualThroughMonth ?? null)} ${latest.year}` : 'Нет наблюдений'}</span></div>{data.annual.some(row => row[metric].annual !== null || row[metric].selected !== null) ? <Chart key={`${data.version}-${data.selection.region}-${metric}-${data.selection.month}`} option={option} theme={theme} label={`${title} по годам`} rows={mortgageAnnualRows(data.annual, metric)} filename={`mortgage-${data.selection.region}-${metric}-annual`} height={400} /> : <ConstructionEmpty />}</article>;
}
function MonthlyChart({ data, metric, theme, params, change }: { data: MortgageReport; metric: MortgageMetric; theme: Theme; params: URLSearchParams; change: CommissioningChange }) {
  const scope = `mortgage_${metric}`, history = mortgageHistory(data.monthly, metric), range = mortgageRange(history, params.get(`${scope}_from`), params.get(`${scope}_to`));
  const title = metric === 'volume' ? 'Объём кредитов по рынкам' : 'Количество договоров по рынкам', field = metric === 'volume' ? 'volumeRub' : 'count';
  const option = useMemo(() => mortgageMonthlyOption(range.rows, metric, theme), [range.from, range.to, data, metric, theme]);
  return <section className="cp-section mortgage-monthly-section" aria-labelledby={`mortgage-${metric}-title`}><div className="cp-section-heading"><div><h2 id={`mortgage-${metric}-title`}>{title}</h2><p>{data.regionLabel} · {mortgageMetricUnit(metric)}</p></div></div><RangeControls scope={scope} label={metric === 'volume' ? 'Объём кредитов' : 'Количество договоров'} rows={history} params={params} change={change} />{range.rows.some(row => row.primary[field] !== null || row.secondary[field] !== null) ? <Chart key={`${data.version}-${data.selection.region}-${metric}-${range.from}-${range.to}`} option={option} theme={theme} label={`${title} по месяцам`} rows={mortgageMonthlyRows(range.rows, metric)} filename={`mortgage-${data.selection.region}-${metric}-${range.from}-${range.to}`} height={340} /> : <ConstructionEmpty>В выбранном периоде нет разбивки по рынкам.</ConstructionEmpty>}</section>;
}
function Terms({ data, params, change }: { data: MortgageReport; params: URLSearchParams; change: CommissioningChange }) {
  const periods = mortgageMetricPeriods(data.monthly), selected = periods.includes(params.get('mortgage_terms') ?? '') ? params.get('mortgage_terms')! : periods.at(-1) ?? '', row = data.monthly.find(row => row.period === selected);
  return <section className="mortgage-terms" aria-labelledby="mortgage-terms-title">
    <div className="mortgage-terms-heading"><h3 id="mortgage-terms-title">Параметры выданных кредитов</h3><div className="cp-controls"><PeriodSelect label="Месяц параметров кредита" periods={periods} value={selected} onChange={value => change({ mortgage_terms: value })} /></div></div>
    {row ? <dl className="mortgage-term-grid">
      <div className="mortgage-term-stat"><dt>Средний срок кредита</dt><dd>{number(row.all.termMonths, 1)}<span>мес.</span></dd></div>
      <div className="mortgage-term-stat"><dt>Средняя сумма кредита</dt><dd>{number(row.all.averageAmountRub === null ? null : row.all.averageAmountRub / 1e6, 1)}<span>млн руб.</span></dd></div>
    </dl> : <ConstructionEmpty>Средний срок и сумма кредита не предоставлены источником.</ConstructionEmpty>}
  </section>;
}
function Rates({ data, theme, params, change }: { data: MortgageReport; theme: Theme; params: URLSearchParams; change: CommissioningChange }) {
  const history = mortgageHistory(data.monthly, 'rates'), range = mortgageRange(history, params.get('mortgage_rates_from'), params.get('mortgage_rates_to'));
  const option = useMemo(() => mortgageRatesOption(range.rows, theme), [range.from, range.to, data, theme]);
  return <section className="cp-section mortgage-rates-section" aria-labelledby="mortgage-rates-title"><div className="cp-section-heading"><div><h2 id="mortgage-rates-title">Средневзвешенная ставка ипотечного кредитования</h2><p>{data.regionLabel} · % годовых</p></div></div><RangeControls scope="mortgage_rates" label="Ставки" rows={history} params={params} change={change} />{range.rows.some(row => mortgageSegments.some(segment => row[segment.id].ratePct !== null)) ? <Chart key={`${data.version}-${data.selection.region}-rates-${range.from}-${range.to}`} option={option} theme={theme} label="Средневзвешенная ставка по месяцам" rows={mortgageMonthlyRows(range.rows, 'rates')} filename={`mortgage-${data.selection.region}-rates-${range.from}-${range.to}`} height={370} /> : <ConstructionEmpty>Ставки в выбранном периоде отсутствуют.</ConstructionEmpty>}<Terms data={data} params={params} change={change} /></section>;
}
export default function Mortgage({ theme, query, change }: { theme: Theme; query: string; change: CommissioningChange }) {
  const params = useMemo(() => new URLSearchParams(query), [query]), { catalog, selection, data, error, retry } = useMortgageData(params);
  const latestCatalogPeriod = [...(catalog?.regions.find(region => region.id === selection?.region)?.periods ?? [])].sort().at(-1);
  useEffect(() => {
    if (!selection) return;
    const next: Record<string, string> = {};
    if (params.get('region') !== selection.region) next.region = selection.region;
    if (params.get('annualMonth') !== String(selection.month)) next.annualMonth = String(selection.month);
    if (Object.keys(next).length) change(next, true);
  }, [selection?.region, selection?.month, params, change]);
  return <main id="mortgage-page" className="cp-page mortgage-page"><header className="cp-page-heading mortgage-heading"><div><div className="eyebrow">ОТРАСЛЕВЫЕ ПОКАЗАТЕЛИ</div><h1><span>Ипотечное</span> кредитование</h1><NextRelease rule="mortgage" period={periodFromId(latestCatalogPeriod)} />{data && <p className="mortgage-source-stamp">ДОМ.РФ · {data.regionLabel} · {date(data.source.date)}</p>}</div><div className="mortgage-heading-controls"><label><span>Регион</span><select aria-label="Регион ипотечного кредитования" value={selection?.region ?? ''} disabled={!catalog} onChange={event => change({ region: event.target.value })}>{!catalog && <option value="">Загрузка регионов</option>}{catalog?.regions.map(region => <option key={region.id} value={region.id}>{region.label}</option>)}</select></label>{data && <ConstructionExport url={mortgageUrl(data.selection, data.version)} filename={`mortgage-${data.selection.region}.xlsx`} label="Скачать все ипотечные данные Excel" refresh={retry} />}</div></header>
    {error || !data ? <ConstructionLoadState error={error} retry={retry} label="ипотечное кредитование" /> : <><section className="cp-section mortgage-annual-section" aria-labelledby="mortgage-annual-title"><div className="mortgage-block-heading"><div><h2 id="mortgage-annual-title">Выдача ипотечных кредитов по годам</h2></div><div className="cp-controls"><label><span>Накопленный период</span><select aria-label="Накопленный период ипотеки" value={data.selection.month} onChange={event => change({ annualMonth: event.target.value })}>{catalog?.months.map(month => <option key={month.id} value={month.id}>{month.label}</option>)}</select></label></div></div><div className="mortgage-annual-grid"><AnnualChart data={data} metric="volume" theme={theme} /><AnnualChart data={data} metric="count" theme={theme} /></div></section><MonthlyChart data={data} metric="volume" theme={theme} params={params} change={change} /><MonthlyChart data={data} metric="count" theme={theme} params={params} change={change} /><Rates data={data} theme={theme} params={params} change={change} />
      <details className="cp-source mortgage-source"><summary><Database size={16} />Источник и даты</summary><div className="cp-source-content"><dl className="cp-metadata"><div><dt>Источник</dt><dd><a href={data.source.url} target="_blank" rel="noreferrer">ДОМ.РФ · ипотечное кредитование</a></dd></div><div><dt>Дата публикации источника</dt><dd>{date(data.source.date)}</dd></div><div><dt>Сохранено</dt><dd>{date(data.source.collectedAt)}</dd></div><div><dt>Исходные файлы</dt><dd>{data.source.files.map(file => <code key={file}>{file}</code>)}</dd></div></dl>{data.notes.map((note, index) => <p className="cp-note" key={index}>{note}</p>)}</div></details><footer className="page-footer"><span>Аналитика Москвы · ДОМ.РФ</span><button className="icon-button" type="button" title="Обновить ипотечные данные" aria-label="Обновить ипотечные данные" onClick={retry}><RefreshCw size={15} /></button></footer></>}
  </main>;
}
