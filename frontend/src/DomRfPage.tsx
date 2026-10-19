import { useEffect, useMemo, useState } from 'react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { Chart } from './Chart';
import { date, number } from './format';
import { SalesExport, SegmentTable } from './Sales';
import { useSalesData } from './useSalesData';
import { salesChartOption, salesChartRows } from './salesData';
import type { SalesChart, SalesData } from './salesData';
import { constructionHistoryChart, constructionHistoryRows, constructionRegion, domRfForecastSelection, domRfUrl, exactNumber, legacySalesForecastSeed, observedRange, readDomRfData, readinessChartOption, realizationDonutOption, realizationHistoryOption, realizationHistoryRows, realizationStatuses } from './constructionPagesData';
import type { ConstructionObservation, ConstructionPagesMetadata, DomRfData, DomRfPeriod, RealizationObservation } from './constructionPagesData';
import { ConstructionEmpty, ConstructionExport, ConstructionLoadState, ConstructionMetric, ConstructionSource, useConstructionFeed } from './PermitsPage';
import type { CommissioningChange } from './Operational';
import type { Theme } from './types';
import './construction-pages.css';

echarts.use([LineChart]);

type PeriodOption = { id: string; label: string };
function PeriodControl({ label, options, value, onChange }: { label: string; options: PeriodOption[]; value: string; onChange: (value: string) => void }) {
  return <label><span>{label}</span><select aria-label={label} value={value} disabled={!options.length} onChange={e => onChange(e.target.value)}>{!options.length && <option value="">Нет периодов</option>}{options.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>;
}
function RangeControls({ scope, label, history, params, change }: { scope: string; label: string; history: { period: string; label: string }[]; params: URLSearchParams; change: CommissioningChange }) {
  const range = observedRange(history, params.get(`${scope}_from`), params.get(`${scope}_to`)), options = [...history].sort((a, b) => a.period.localeCompare(b.period)).map(h => ({ id: h.period, label: h.label }));
  return <div className="cp-controls cp-range"><PeriodControl label={`${label}: с`} value={range.from} options={options} onChange={value => change({ [`${scope}_from`]: value, ...(value > range.to ? { [`${scope}_to`]: value } : {}) })} /><PeriodControl label={`${label}: по`} value={range.to} options={options} onChange={value => change({ [`${scope}_to`]: value, ...(value < range.from ? { [`${scope}_from`]: value } : {}) })} /></div>;
}
function periodOptions(current: DomRfPeriod | null, history: { period: string; label: string }[]): PeriodOption[] {
  const options = history.map(h => ({ id: h.period, label: h.label }));
  if (current?.id && !options.some(o => o.id === current.id)) options.push({ id: current.id, label: current.label });
  return options.sort((a, b) => a.id.localeCompare(b.id));
}
function PeriodStamp({ period }: { period: DomRfPeriod | null }) {
  return <p className="cp-period-stamp"><span>{period?.label || 'Период источника не указан'}</span><span>Дата отчёта: {date(period?.reportDate)}</span></p>;
}
function LegacySource({ data, label }: { data: SalesData; label: string }) {
  const metadata: ConstructionPagesMetadata = { schemaVersion: 1, version: data.version, generatedAt: data.generatedAt, source: data.source };
  return <ConstructionSource metadata={metadata} label={label} notes={['Дата файлов-кандидатов не подтверждает дату выбранного отчёта.', ...(data.source.dateEvidence ? [data.source.dateEvidence] : [])]}><p className="cp-note">Период отчёта распроданности: {data.period.label} · {data.region.label}</p></ConstructionSource>;
}
function LegacyChart({ chart, scope, data, theme, params, change }: { chart: SalesChart; scope: string; data: SalesData; theme: Theme; params: URLSearchParams; change: CommissioningChange }) {
  const periods = [...new Set(chart.series.flatMap(s => s.points.map(p => p.x)))].sort(), history = periods.map(period => ({ period, label: period })), range = observedRange(history, params.get(`${scope}_from`), params.get(`${scope}_to`));
  const shown = { ...chart, series: chart.series.map(s => ({ ...s, points: s.points.filter(p => p.x >= range.from && p.x <= range.to) })) };
  return <div className="cp-subsection"><div className="cp-section-heading"><div><h3>{chart.title}</h3><p>{data.region.label} · {chart.unit} · источник распроданности: {data.period.label}</p></div></div><RangeControls scope={scope} label={chart.id === 'monthly_volume' ? 'История объёма' : 'История распроданности'} history={history} params={params} change={change} />{shown.series.some(s => s.points.some(p => p.y !== null)) ? <Chart key={`${data.version}-${data.region.id}-${data.period.id}-${chart.id}-${range.from}-${range.to}`} option={readinessChartOption(shown, theme)} theme={theme} label={chart.title} rows={salesChartRows(shown)} filename={`sales-${data.region.id}-${data.period.id}-${chart.id}-${range.from}-${range.to}`} height={320} /> : <ConstructionEmpty />}</div>;
}
function SourceRecord({ metrics }: { metrics: Record<string, number | null> }) {
  return <dl className="cp-raw-record">{Object.entries(metrics).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{exactNumber(value)}</dd></div>)}</dl>;
}
function ConstructionBlock({ data, theme, params, change, retry }: { data: DomRfData; theme: Theme; params: URLSearchParams; change: CommissioningChange; retry: () => void }) {
  const c = data.construction, options = periodOptions(c.period, c.history), chosen = c.history.find(h => h.period === params.get('construction_period'));
  const selected = chosen && chosen.period !== c.period?.id ? chosen : null, period = selected ? { id: selected.period, label: selected.label, reportDate: selected.reportDate } : c.period;
  const metrics = selected ?? c.metrics, range = observedRange(c.history, params.get('construction_from'), params.get('construction_to'));
  const chart = constructionHistoryChart(range.rows);
  return <><div className="cp-controls"><PeriodControl label="Период строительства ДОМ.РФ" value={period?.id ?? ''} options={options} onChange={value => change({ construction_period: value })} /><ConstructionExport url={domRfUrl(data.region.id, data.version)} filename={`domrf-${data.region.id}.xlsx`} label="Скачать строительство ДОМ.РФ Excel" refresh={retry} /></div><PeriodStamp period={period} /><div className="cp-metrics cp-metrics-two"><ConstructionMetric label="Жилая площадь" value={metrics.living === null ? null : metrics.living / 1000} unit="млн м²" /><ConstructionMetric label="Общая площадь" value={metrics.total === null ? null : metrics.total / 1000} unit="млн м²" /></div>
    <div className="cp-subsection"><div className="cp-section-heading"><div><h3>Динамика текущего строительства по ДДУ</h3><p>{data.region.label} · млн м²</p></div></div>{c.history.length ? <><RangeControls scope="construction" label="История строительства ДОМ.РФ" history={c.history} params={params} change={change} /><Chart key={`${data.version}-${data.region.id}-construction-${range.from}-${range.to}`} option={salesChartOption(chart, theme)} theme={theme} label={chart.title} rows={constructionHistoryRows(range.rows)} filename={`domrf-${data.region.id}-construction-${range.from}-${range.to}`} height={320} /></> : <ConstructionEmpty>История текущего строительства не предоставлена источником.</ConstructionEmpty>}</div>
    <ConstructionSource metadata={data} label="Источник строительства ДОМ.РФ и даты" notes={data.notes}><p className="cp-note">Период: {period?.label ?? 'Не указан'} · дата отчёта: {date(period?.reportDate)}. Исходная единица living / total: тыс. м².</p><SourceRecord metrics={{ living: metrics.living, total: metrics.total }} /><div className="cp-source-observations">{c.history.map((h: ConstructionObservation) => <p key={h.period}>{h.label} · дата отчёта: {date(h.reportDate)} · {h.sourceFile}</p>)}</div></ConstructionSource>
  </>;
}
function RealizationBlock({ data, theme, params, change, retry }: { data: DomRfData; theme: Theme; params: URLSearchParams; change: CommissioningChange; retry: () => void }) {
  const s = data.sales, options = periodOptions(s.period, s.history), chosen = s.history.find(h => h.period === params.get('realization_period'));
  const selected = chosen && chosen.period !== s.period?.id ? chosen : null, period = selected ? { id: selected.period, label: selected.label, reportDate: selected.reportDate } : s.period;
  const metrics = selected?.metrics ?? s.metrics, range = observedRange(s.history, params.get('realization_from'), params.get('realization_to'));
  const millions = (key: string) => metrics?.[key] == null ? null : metrics[key]! / 1000;
  return <><div className="cp-controls"><PeriodControl label="Период реализации ДОМ.РФ" value={period?.id ?? ''} options={options} onChange={value => change({ realization_period: value })} /><ConstructionExport url={domRfUrl(data.region.id, data.version)} filename={`domrf-${data.region.id}.xlsx`} label="Скачать реализацию ДОМ.РФ Excel" refresh={retry} /></div><PeriodStamp period={period} />{metrics ? <><div className="cp-metrics cp-metrics-two"><ConstructionMetric label="Всего жилой площади" value={millions('total_living_thousand_m2')} unit="млн м²" /><ConstructionMetric label="Продажи открыты" value={millions('sales_open_thousand_m2')} unit="млн м²" /></div>
    <div className="cp-donuts">{realizationStatuses.map(status => { const pct = metrics[`${status.id}_pct`] ?? null, area = metrics[`${status.id}_thousand_m2`] ?? null; return <div className="cp-donut" key={status.id}><h3>{status.label}</h3><Chart key={`${data.version}-${data.region.id}-${period?.id}-${status.id}`} option={realizationDonutOption(status.label, pct, area, status.color, theme)} theme={theme} label={status.label} rows={[["Показатель", "Доля источника, %", "Площадь, тыс. м²"], [status.label, pct, area]]} filename={`domrf-${data.region.id}-${period?.id}-${status.id}`} height={200} /><p className="cp-donut-area" title={`${exactNumber(area)} тыс. м²`}>{number(area === null ? null : area / 1000, 2)} <span>млн м²</span></p>{pct !== null && (pct < 0 || pct > 100) && <p className="cp-note warning-text">Доля источника вне диапазона 0–100 %; круговая диаграмма не построена.</p>}</div>; })}</div>
    </> : <ConstructionEmpty>Показатели реализации отсутствуют в источнике.</ConstructionEmpty>}
    <div className="cp-subsection"><div className="cp-section-heading"><div><h3>Реализация квартир по месяцам</h3><p>{data.region.label} · млн м² · только наблюдаемые периоды</p></div></div>{s.history.length ? <><RangeControls scope="realization" label="История реализации" history={s.history} params={params} change={change} /><Chart key={`${data.version}-${data.region.id}-realization-${range.from}-${range.to}`} option={realizationHistoryOption(range.rows, theme)} theme={theme} label="Реализация квартир по месяцам" rows={realizationHistoryRows(range.rows)} filename={`domrf-${data.region.id}-realization-${range.from}-${range.to}`} height={320} /></> : <ConstructionEmpty>Месячные наблюдения реализации отсутствуют.</ConstructionEmpty>}</div>
    {metrics && <div className="cp-metrics cp-metrics-two"><ConstructionMetric label="Стоимость 1 м²" value={metrics.price_per_m2_rub} unit="руб." /><ConstructionMetric label="Объём привлечённых средств" value={metrics.funds_million_rub} unit="млн руб." /></div>}
    <ConstructionSource metadata={data} label="Источник реализации, исходные значения и даты" notes={data.notes}><p className="cp-note">Период реализации: {period?.label ?? 'Не указан'} · дата отчёта: {date(period?.reportDate)}. Площади *_thousand_m2 — тыс. м²; *_pct — проценты источника; price_per_m2_rub — руб.; funds_million_rub — млн руб.</p>{metrics && <SourceRecord metrics={metrics} />}<div className="cp-source-observations">{s.history.map((h: RealizationObservation) => <p key={h.period}>{h.label} · дата отчёта: {date(h.reportDate)} · {h.sourceFile}</p>)}</div></ConstructionSource>
  </>;
}

export default function DomRfPage({ theme, query, change, legacySales = false }: { theme: Theme; query: string; change: CommissioningChange; legacySales?: boolean }) {
  const params = useMemo(() => new URLSearchParams(query), [query]), region = constructionRegion(params);
  const [forecastSeed, setForecastSeed] = useState(() => legacySalesForecastSeed(legacySales, params));
  const forecastSelection = domRfForecastSelection(params, forecastSeed), forecastPeriod = forecastSelection.period;
  useEffect(() => { if (forecastSeed !== forecastSelection.seed) setForecastSeed(forecastSelection.seed); }, [forecastSeed, forecastSelection.seed]);
  const b = useConstructionFeed(domRfUrl(region), value => readDomRfData(value, region));
  const readinessParams = useMemo(() => new URLSearchParams({ region, ...(params.get('period') ? { period: params.get('period')! } : {}) }), [region, params]);
  const forecastParams = useMemo(() => new URLSearchParams({ region, ...(forecastPeriod ? { period: forecastPeriod } : {}) }), [region, forecastPeriod]);
  const readiness = useSalesData(readinessParams), forecast = useSalesData(forecastParams);
  const readyData = readiness.data?.region.id === region ? readiness.data : null, forecastData = forecast.data?.region.id === region ? forecast.data : null;
  const readyPeriods = readiness.catalog?.regions.find(r => r.id === region)?.periods ?? [], forecastPeriods = forecast.catalog?.regions.find(r => r.id === region)?.periods ?? [];
  const table = forecastData?.tables.find(t => t.id === params.get('table')) ?? forecastData?.tables[0];
  useEffect(() => {
    const values: Record<string, string | null> = {};
    if (params.get('region') !== region) values.region = region;
    if (readiness.region?.id === region && readiness.period && params.get('period') !== readiness.period.id) values.period = readiness.period.id;
    if (forecast.region?.id === region && forecast.period && params.get('forecast_period') !== forecast.period.id) values.forecast_period = forecast.period.id;
    if (forecastData && params.get('table') !== (table?.id ?? null)) values.table = table?.id ?? null;
    if (Object.keys(values).length) change(values, true);
  }, [region, readiness.region?.id, readiness.period?.id, forecast.region?.id, forecast.period?.id, forecastData, table?.id, params, change]);
  const renderLegacyState = (source: typeof readiness, matching: SalesData | null, label: string) => source.error || !matching && (!source.catalog || source.region?.id === region && !!source.period) ? <ConstructionLoadState error={source.error} retry={source.retry} label={label} /> : <ConstructionEmpty>Нет отчётных периодов для выбранного региона.</ConstructionEmpty>;
  return <main className="domrf-page cp-page" id="domrf-page"><header className="cp-page-heading"><div><div className="eyebrow">ТЕКУЩЕЕ СТРОИТЕЛЬСТВО</div><h1>Данные ДОМ.РФ</h1></div></header>
    <div className="cp-controls cp-global-controls"><PeriodControl label="Регион показателей" options={[{ id: 'msk', label: 'Москва' }, { id: 'rf', label: 'Российская Федерация' }]} value={region} onChange={value => change({ region: value, period: null, forecast_period: null, construction_period: null, realization_period: null, construction_from: null, construction_to: null, realization_from: null, realization_to: null, volume_from: null, volume_to: null, readiness_from: null, readiness_to: null })} /></div>
    <section className="domrf-construction cp-section" aria-labelledby="domrf-construction-title"><div className="cp-section-heading"><div><h2 id="domrf-construction-title">Объём текущего строительства по ДДУ</h2><p>{b.data?.region.label ?? (region === 'msk' ? 'Москва' : 'Российская Федерация')}</p></div></div>
      {b.data ? <ConstructionBlock data={b.data} theme={theme} params={params} change={change} retry={b.retry} /> : <ConstructionLoadState error={b.error} retry={b.retry} label="строительство ДОМ.РФ" />}
      {readyData ? <>{readyData.metrics.filter(m => m.id === 'volume').map(m => <p className="cp-note" key={m.id}>{m.label} в источнике распроданности: <strong title={`${exactNumber(m.value)} ${m.unit}`}>{number(m.value, 2)} {m.unit}</strong> · {readyData.period.label}</p>)}{readyData.charts.filter(c => c.id === 'monthly_volume').map(c => <LegacyChart key={c.id} chart={c} scope="volume" data={readyData} theme={theme} params={params} change={change} />)}</> : renderLegacyState(readiness, readyData, 'история объёма из распроданности')}
    </section>
    <section className="domrf-readiness cp-section" aria-labelledby="domrf-readiness-title"><div className="cp-section-heading"><div><h2 id="domrf-readiness-title">Распроданность и стройготовность</h2><p>Источник распроданности · {region === 'msk' ? 'Москва' : 'Российская Федерация'}</p></div></div>
      <div className="cp-controls"><PeriodControl label="Период распроданности" options={readyPeriods} value={readiness.region?.id === region ? readiness.period?.id ?? '' : ''} onChange={value => change({ period: value })} />{readyData && <SalesExport region={readyData.region.id} period={readyData.period.id} version={readyData.version} />}</div>
      {readyData ? <><p className="cp-period-stamp">Отчётный период: {readyData.period.label}</p><div className="cp-metrics">{readyData.metrics.filter(m => m.id !== 'volume').map(m => <ConstructionMetric key={m.id} label={m.label} value={m.value} unit={m.unit} />)}</div>{readyData.charts.filter(c => c.id === 'monthly_percent').map(c => <LegacyChart key={c.id} chart={c} scope="readiness" data={readyData} theme={theme} params={params} change={change} />)}<LegacySource data={readyData} label="Источник распроданности, история и даты" /></> : renderLegacyState(readiness, readyData, 'распроданность и стройготовность')}
    </section>
    <section className="domrf-realization cp-section" aria-labelledby="domrf-realization-title"><div className="cp-section-heading"><h2 id="domrf-realization-title">Реализация квартир</h2></div>{b.data ? <RealizationBlock data={b.data} theme={theme} params={params} change={change} retry={b.retry} /> : <ConstructionLoadState error={b.error} retry={b.retry} label="реализация ДОМ.РФ" />}</section>
    <section className="domrf-forecast cp-section" aria-labelledby="domrf-forecast-title"><div className="cp-section-heading"><div><h2 id="domrf-forecast-title">Прогноз ввода по годам</h2><p>Источник распроданности · {region === 'msk' ? 'Москва' : 'Российская Федерация'}</p></div></div><div className="cp-controls"><PeriodControl label="Период прогноза и сегментов" options={forecastPeriods} value={forecast.region?.id === region ? forecast.period?.id ?? '' : ''} onChange={value => change({ forecast_period: value })} />{forecastData && <SalesExport region={forecastData.region.id} period={forecastData.period.id} version={forecastData.version} />}</div>
      {forecastData ? <>{forecastData.charts.filter(c => c.id !== 'monthly_volume' && c.id !== 'monthly_percent').map(c => <div className="cp-subsection" key={c.id}>{c.id !== 'forecast' && <h3>{c.title}</h3>}{c.series.some(s => s.points.some(p => p.y !== null)) ? <Chart key={`${forecastData.version}-${forecastData.region.id}-${forecastData.period.id}-${c.id}`} option={salesChartOption(c, theme)} theme={theme} label={c.title} rows={salesChartRows(c)} filename={`sales-${forecastData.region.id}-${forecastData.period.id}-${c.id}`} height={340} /> : <ConstructionEmpty>Прогнозные значения отсутствуют.</ConstructionEmpty>}</div>)}<LegacySource data={forecastData} label="Источник прогноза и сегментов, единицы и даты" /></> : renderLegacyState(forecast, forecastData, 'прогноз и сегменты')}
    </section>
    <section className="domrf-segments cp-section" aria-label="Данные по сегментам"><details className="cp-segments-disclosure"><summary>Данные по сегментам</summary>{forecastData ? <><div className="cp-section-heading"><div><h2>Срезы по сегментам</h2><p>{forecastData.region.label} · {forecastData.period.label}</p></div></div><div role="tablist" className="sales-tabs" aria-label="Сегменты">{forecastData.tables.map((t, i) => <button type="button" role="tab" id={`sales-tab-${t.id}`} aria-controls={`sales-panel-${t.id}`} aria-selected={table?.id === t.id} tabIndex={table?.id === t.id ? 0 : -1} key={t.id} onClick={() => change({ table: t.id })} onKeyDown={e => {
      const next = e.key === 'ArrowRight' ? (i + 1) % forecastData.tables.length : e.key === 'ArrowLeft' ? (i + forecastData.tables.length - 1) % forecastData.tables.length : e.key === 'Home' ? 0 : e.key === 'End' ? forecastData.tables.length - 1 : -1;
      if (next !== -1) { e.preventDefault(); change({ table: forecastData.tables[next].id }); document.getElementById(`sales-tab-${forecastData.tables[next].id}`)?.focus(); }
    }}>{t.title}</button>)}</div>{table ? <SegmentTable key={table.id} table={table} params={params} change={change} filename={`sales-${forecastData.region.id}-${forecastData.period.id}`} /> : <ConstructionEmpty />}</> : renderLegacyState(forecast, forecastData, 'сегменты')}</details></section>
  </main>;
}
