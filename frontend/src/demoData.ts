import type { EChartsCoreOption } from 'echarts/core';
import { ApiError, loadCatalog, loadProfile, request, selectFilters } from './data.ts';
import { annualChartOption, annualChartRows, annualFilters, loadMarketCatalog, loadMarketReport } from './annualConstructionData.ts';
import type { AnnualReport } from './annualConstructionData';
import { apartmentFilters, loadApartmentCatalog, loadApartmentData } from './apartmentData.ts';
import { distributionOption, roomsOption } from './apartmentCharts.ts';
import { loadCommissioningCatalog, loadCommissioningReport, operationalChartOption, operationalChartRows, operationalFilters } from './commissioningData.ts';
import type { OperationalCatalog, OperationalReport } from './commissioningData';
import { breakdownOption, constructionHistoryChart, constructionHistoryRows, loadDomRfData, permitsMainOption, permitsMainRows, realizationHistoryRows, realizationStatuses } from './constructionPagesData.ts';
import { loadPermitPageSelection, selectPermitArea } from './permitPageData.ts';
import { permitBreakdownRows } from './permitPresentation.ts';
import { demoPresentationScale } from './demoPresentation.ts';
import { domrfDonutOption, domrfLineOption, domrfRealizationHistoryOption } from './domrfPresentation.ts';
import { economicsChartOption, economicsChartRows, loadEconomicsPage } from './economicsData.ts';
import { linearHistoryOption, linearYearRows, loadLinearDashboard } from './linearDashboardData.ts';
import { mortgageAnnualOption, mortgageAnnualRows, mortgageHistory, mortgageMonthlyOption, mortgageMonthlyRows, mortgageRatesOption, mortgageSelection, mortgageUrl, readMortgageCatalog, readMortgageReport } from './mortgageData.ts';
import { characteristicChartOption, characteristicRows, characteristics, flowChartOption, flowRows, preferentialSelection, preferentialUrl, programs, readPreferentialCatalog, readPreferentialReport } from './preferentialMortgageData.ts';
import { loadSalesCatalog, loadSalesData, salesChartRows, salesFilters } from './salesData.ts';
import type { SalesChart } from './salesData';
import type { Theme } from './types';

export const DEMO_MIN_SECONDS = 15;
export const DEMO_MAX_SECONDS = 1800;
export const DEMO_DEFAULT_SECONDS = 120;
export type DemoRows = (string | number | null)[][];
export type DemoChart = {
  id: string; title: string; context: string; href: string; empty: boolean; rows: DemoRows;
  option: (theme: Theme, width: number, hidden: string[], height?: number) => EChartsCoreOption;
};
export type DemoSource = { id: string; title: string; href: string; load: (signal: AbortSignal) => Promise<DemoChart[]> };

export function demoInterval(value: number): number {
  return Number.isFinite(value) ? Math.min(DEMO_MAX_SECONDS, Math.max(DEMO_MIN_SECONDS, Math.round(value))) : DEMO_DEFAULT_SECONDS;
}
export function demoIntervalLabel(value: number): string {
  const seconds = demoInterval(value);
  return seconds < 60 ? `${seconds} с` : seconds % 60 ? `${Math.floor(seconds / 60)} мин ${seconds % 60} с` : `${seconds / 60} мин`;
}
export function demoIndex(index: number, length: number): number { return length > 0 ? ((index % length) + length) % length : 0; }
export function demoSelection(charts: DemoChart[], selected: ReadonlySet<string> | null): DemoChart[] {
  return charts.filter(chart => !chart.empty && (selected === null || selected.has(chart.id)));
}
const moscow = () => new URLSearchParams('region=msk');
export const demoHasValues = (values: (number | null | undefined)[]) => values.some(value => typeof value === 'number' && Number.isFinite(value));
const hasPoints = (chart: { series: { points: { y: number | null }[] }[] }) => demoHasValues(chart.series.flatMap(series => series.points.map(point => point.y)));
const context = (region: string, data: { source: { date: string | null }; generatedAt: string }) => `${region} · ${data.source.date ?? data.generatedAt.slice(0, 10)}`;
function entry(id: string, title: string, caption: string, href: string, ready: boolean, rows: DemoRows, option: DemoChart['option']): DemoChart {
  return { id, title, context: caption, href, empty: !ready, rows, option };
}
class DemoVersionError extends Error {}

// Catalog/report pairs must be coherent, including while an ETL publishes a new version.
export async function demoVersioned<C extends { version: string }, R extends { version: string }>(catalog: () => Promise<C>, report: (catalog: C) => Promise<R>): Promise<R> {
  for (let attempt = 0; ; attempt++) {
    try {
      const c = await catalog(), result = await report(c);
      if (c.version !== result.version) throw new DemoVersionError('Источник изменился. Повторите загрузку.');
      return result;
    } catch (error) {
      if (attempt || !(error instanceof DemoVersionError || error instanceof ApiError && error.status === 404)) throw error;
    }
  }
}

async function profileCharts(signal: AbortSignal): Promise<DemoChart[]> {
  const mode = import.meta.env?.VITE_DATA_MODE === 'snapshot' ? 'snapshot' : 'api';
  const result = await demoVersioned(async () => {
    const catalog = await loadCatalog(mode, signal);
    return { version: catalog.metadata.version ?? '', catalog };
  }, async ({ catalog }) => {
    const { developer, region } = selectFilters(catalog, moscow());
    if (!developer || !region) throw new Error('Профили застройщиков отсутствуют.');
    const loaded = await loadProfile(catalog, developer.id, region, signal);
    return { version: loaded.snapshot.version ?? '', snapshot: loaded.snapshot, developer, region };
  });
  const { developer, region, snapshot } = result;
  const profile = snapshot.developers.find(item => item.id === developer.id)?.regions.find(item => item.id === region);
  if (!profile) return [];
  const { demoProfileCharts } = await import('./DemoProfileCharts');
  return demoProfileCharts(profile, developer, snapshot);
}
async function apartmentCharts(signal: AbortSignal): Promise<DemoChart[]> {
  const result = await demoVersioned(() => loadApartmentCatalog(signal), async catalog => {
    const { region, developer } = apartmentFilters(catalog, moscow());
    if (!region) throw new Error('Регионы квартирографии отсутствуют.');
    const overview = await loadApartmentData(region.id, undefined, signal);
    const detail = developer ? await loadApartmentData(region.id, developer.id, signal) : null;
    if (detail && detail.version !== overview.version) throw new DemoVersionError('Квартирография обновляется. Повторите загрузку.');
    return { version: overview.version, overview, detail, label: region.label };
  });
  const { overview, detail, label } = result;
  const charts = 'distribution' in overview ? [entry('apartments/distribution', 'Распределение квартир по площади', context(label, overview), '/apartments', demoHasValues(overview.distribution.map(row => row.sharePercent)), [['Площадь, м²', 'Доля, %'], ...overview.distribution.map(row => [row.range, row.sharePercent])], theme => distributionOption(overview.distribution, theme))] : [];
  if (detail && 'rooms' in detail) charts.push(entry('apartments/rooms', 'Структура портфеля по комнатности', `${detail.developer.name} · ${context(label, detail)}`, `/apartments/developer?region=${detail.region}&developer=${encodeURIComponent(detail.developer.id)}`, detail.rooms.some(row => row.sharePercent !== null && row.sharePercent > 0), [['Тип', 'Доля, %'], ...detail.rooms.map(row => [row.type, row.sharePercent])], theme => roomsOption(detail.rooms, theme)));
  return charts;
}
async function permitsCharts(signal: AbortSignal): Promise<DemoChart[]> {
  const data = selectPermitArea(await loadPermitPageSelection({ permitKind: 'total' }, signal), true), caption = context(`${data.permits.region} · ${data.permits.periodLabel}`, data);
  return [entry('permits/main', 'Выдача разрешений по годам', caption, '/construction/permits', demoHasValues(data.permits.presentation.rows.flatMap(row => [row.selected, row.annualForecast])), permitsMainRows(data), (theme, width, hidden, height) => permitsMainOption(data, theme, { width, hidden, height, includeGrowth: true, presentation: true, fontScale: demoPresentationScale(width, height ?? 500) })),
    ...data.breakdowns.map(chart => entry(`permits/${chart.id}`, chart.title, `${caption} · ${chart.periodLabel}`, '/construction/permits', hasPoints(chart), permitBreakdownRows(chart, data.version), (theme, width, hidden, height) => breakdownOption(chart, theme, { width, hidden, height, includeGrowth: true, presentation: true, fontScale: demoPresentationScale(width, height ?? 500) })))];
}
async function domrfCharts(signal: AbortSignal): Promise<DemoChart[]> {
  const data = await loadDomRfData('msk', signal), caption = context(data.region.label, data), history = constructionHistoryChart(data.construction.history);
  const charts = [entry('domrf/construction', history.title, caption, '/construction/domrf', hasPoints(history), constructionHistoryRows(data.construction.history), theme => domrfLineOption(history, theme)),
    entry('domrf/realization', 'Реализация квартир по месяцам', caption, '/construction/domrf', demoHasValues(data.sales.history.flatMap(row => realizationStatuses.map(status => row.metrics[`${status.id}_thousand_m2`]))), realizationHistoryRows(data.sales.history), theme => domrfRealizationHistoryOption(data.sales.history, theme))];
  const metrics = data.sales.metrics;
  for (const status of realizationStatuses) {
    const pct = metrics?.[`${status.id}_pct`] ?? null, area = metrics?.[`${status.id}_thousand_m2`] ?? null;
    charts.push(entry(`domrf/${status.id}`, status.label, `${data.region.label} · ${data.sales.period?.label ?? 'Нет периода'}`, '/construction/domrf', pct !== null && pct >= 0 && pct <= 100, [['Показатель', 'Доля источника, %', 'Площадь, тыс. м²'], [status.label, pct, area]], theme => domrfDonutOption(status.label, pct, area, status.color, theme)));
  }
  return charts;
}
async function salesCharts(signal: AbortSignal): Promise<DemoChart[]> {
  const data = await demoVersioned(() => loadSalesCatalog(signal), async catalog => {
    const { region, period } = salesFilters(catalog, moscow());
    if (!region || !period) throw new Error('Отчётные периоды ДОМ.РФ отсутствуют.');
    return loadSalesData(region.id, period.id, signal);
  });
  return data.charts.filter(chart => chart.id !== 'monthly_volume').map((chart: SalesChart) => entry(`sales/${chart.id}`, chart.title, `${data.region.label} · ${data.period.label}`, '/construction/domrf', hasPoints(chart), salesChartRows(chart), theme => domrfLineOption(chart, theme)));
}
async function operationalCharts(signal: AbortSignal): Promise<DemoChart[]> {
  const data = await demoVersioned(() => loadCommissioningCatalog('operational', signal), async catalog => {
    const selection = operationalFilters(catalog as OperationalCatalog, new URLSearchParams());
    if (!selection) throw new Error('Периоды оперативного ввода отсутствуют.');
    return await loadCommissioningReport('operational', selection, signal) as OperationalReport;
  });
  return data.tables.filter(table => table.id === 'housing' || table.id === 'nonres').map(table => entry(`operational/${table.id}`, table.title, context(`Москва · ${data.periodLabel}`, data), '/commissioning/operational', demoHasValues([...table.chart.period, ...table.chart.remainder].map(point => point.value)), operationalChartRows(table), theme => operationalChartOption(table, data.periodLabel, theme)));
}
async function annualCharts(signal: AbortSignal): Promise<DemoChart[]> {
  const data = await demoVersioned(() => loadMarketCatalog('annual', signal), async catalog => {
    const selection = annualFilters(catalog, moscow());
    if (!selection) throw new Error('Регионы годового ввода отсутствуют.');
    return await loadMarketReport('annual', selection, signal) as AnnualReport;
  });
  return data.charts.map(chart => entry(`annual/${chart.id}`, chart.title, context(data.region.label, data), '/commissioning/annual', hasPoints(chart) || !!chart.forecast?.points.length, annualChartRows(chart), (theme, _width, hidden) => annualChartOption(chart, theme, [...chart.series.map(series => series.name), 'Прогноз'].filter(name => !hidden.includes(name)))));
}
async function linearCharts(signal: AbortSignal): Promise<DemoChart[]> {
  const data = await loadLinearDashboard(signal);
  return data.metrics.map(metric => entry(`linear/${metric.id}`, `Годовой ввод: ${metric.label}`, `Москва · ${metric.unit} · ${data.currentYear}`, '/commissioning/linear', demoHasValues(metric.years.flatMap(year => [year.fact, year.forecast])), linearYearRows(metric), (theme, width) => linearHistoryOption(metric, data.currentYear, theme, width)));
}
async function mortgageCharts(signal: AbortSignal): Promise<DemoChart[]> {
  const data = await demoVersioned(async () => readMortgageCatalog(await (await request('/api/v1/mortgage/catalog', signal)).json()), async catalog => {
    const selection = mortgageSelection(catalog, moscow());
    if (!selection) throw new Error('Периоды ипотечного кредитования отсутствуют.');
    return readMortgageReport(await (await request(mortgageUrl(selection), signal)).json(), selection);
  });
  const caption = context(data.regionLabel, data), href = '/economics/mortgage';
  const charts: DemoChart[] = [];
  for (const metric of ['volume', 'count'] as const) {
    const title = metric === 'volume' ? 'Объём ипотечных кредитов' : 'Количество ипотечных договоров', history = mortgageHistory(data.monthly, metric), field = metric === 'volume' ? 'volumeRub' : 'count';
    charts.push(entry(`mortgage/${metric}/annual`, `${title} по годам`, caption, href, demoHasValues(data.annual.flatMap(row => [row[metric].annual, row[metric].selected])), mortgageAnnualRows(data.annual, metric), (theme, width, _hidden, height) => mortgageAnnualOption(data.annual, metric, theme, width, height)));
    charts.push(entry(`mortgage/${metric}/monthly`, `${title} по месяцам`, caption, href, demoHasValues(history.flatMap(row => [row.primary[field], row.secondary[field]])), mortgageMonthlyRows(history, metric), theme => mortgageMonthlyOption(history, metric, theme)));
  }
  const history = mortgageHistory(data.monthly, 'rates');
  charts.push(entry('mortgage/rates', 'Средневзвешенная ставка по месяцам', caption, href, demoHasValues(history.flatMap(row => [row.all.ratePct, row.primary.ratePct, row.secondary.ratePct])), mortgageMonthlyRows(history, 'rates'), theme => mortgageRatesOption(history, theme)));
  return charts;
}
async function preferentialCharts(signal: AbortSignal): Promise<DemoChart[]> {
  const data = await demoVersioned(async () => readPreferentialCatalog(await (await request('/api/v1/preferential-mortgage/catalog', signal)).json()), async catalog => {
    const selection = preferentialSelection(catalog, moscow());
    if (!selection) throw new Error('Периоды льготной ипотеки отсутствуют.');
    return readPreferentialReport(await (await request(preferentialUrl(selection), signal)).json(), selection);
  });
  const href = '/economics/preferential-mortgage', caption = context(data.regionLabel, data), charts: DemoChart[] = [];
  const hiddenPrograms = (hidden: string[]) => programs.filter(program => hidden.includes(program.label)).map(program => program.id);
  for (const cumulative of [false, true]) {
    const suffix = cumulative ? 'cumulative' : 'annual', period = cumulative ? 'накопленный период' : 'по годам', flow = cumulative ? data.cumulative : data.annual, chars = cumulative ? data.characteristicsCumulative : data.characteristicsAnnual;
    for (const metric of ['volumeMln', 'count'] as const) charts.push(entry(`preferential/${metric}/${suffix}`, `${metric === 'volumeMln' ? 'Объём льготных кредитов' : 'Количество льготных кредитов'} · ${period}`, caption, href, demoHasValues(flow.flatMap(row => programs.map(program => row.programs[program.id][metric]))), flowRows(flow, metric, data.latestPeriodPartial ? data.source.date : null), (theme, width, hidden) => stableDemoSeries(flowChartOption(flow, metric, theme, width, [], cumulative), flowChartOption(flow, metric, theme, width, hiddenPrograms(hidden), cumulative))));
    for (const metric of characteristics) charts.push(entry(`preferential/${metric.id}/${suffix}`, `${metric.label} · ${period}`, `${caption} · ${metric.unit}`, href, demoHasValues(chars.flatMap(row => programs.map(program => row.programs[program.id][metric.id]))), characteristicRows(chars, metric.id), (theme, width, hidden) => stableDemoSeries(characteristicChartOption(chars, metric.id, theme, width, [], cumulative), characteristicChartOption(chars, metric.id, theme, width, hiddenPrograms(hidden), cumulative))));
  }
  return charts;
}
function stableDemoSeries(all: EChartsCoreOption, visible: EChartsCoreOption): EChartsCoreOption {
  type Series = { name?: string; label?: { show?: boolean }; markPoint?: unknown };
  const shown = visible.series as Series[];
  return { ...visible, series: (all.series as Series[]).map(series => shown.find(item => item.name === series.name) ?? { ...series, markPoint: undefined, label: { ...series.label, show: false } }) };
}
export const demoSources: DemoSource[] = [
  { id: 'profile', title: 'Профиль застройщика', href: '/', load: profileCharts },
  { id: 'permits', title: 'Разрешения на строительство', href: '/construction/permits', load: permitsCharts },
  { id: 'domrf', title: 'Данные ДОМ.РФ', href: '/construction/domrf', load: domrfCharts },
  { id: 'sales', title: 'Распроданность и прогноз ввода', href: '/construction/domrf', load: salesCharts },
  { id: 'apartments', title: 'Квартирография', href: '/apartments', load: apartmentCharts },
  { id: 'operational', title: 'Оперативный ввод', href: '/commissioning/operational', load: operationalCharts },
  { id: 'annual', title: 'Годовой ввод', href: '/commissioning/annual', load: annualCharts },
  { id: 'linear', title: 'Ввод линейных объектов', href: '/commissioning/linear', load: linearCharts },
  ...(['accounts', 'ipc', 'salary'] as const).map(family => ({ id: family, title: { accounts: 'ВРП и ВВП', ipc: 'ИПЦ', salary: 'Заработная плата' }[family], href: `/economics/${family}`, load: async (signal: AbortSignal) => {
    const { data } = await loadEconomicsPage(family, new URLSearchParams(), signal);
    return data.charts.map(chart => entry(`${family}/${chart.id}`, chart.title, context('Мосстат / Росстат', data), `/economics/${family}`, hasPoints(chart), economicsChartRows(chart), (theme, width, hidden) => economicsChartOption(chart, theme, { width, hidden })));
  } })),
  { id: 'mortgage', title: 'Ипотечное кредитование', href: '/economics/mortgage', load: mortgageCharts },
  { id: 'preferential', title: 'Льготное ипотечное кредитование', href: '/economics/preferential-mortgage', load: preferentialCharts },
];
