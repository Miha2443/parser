import type { EChartsCoreOption } from 'echarts/core';
import { ApiError, loadCatalog, loadProfile, request, selectFilters } from './data.ts';
import { annualChartOption, annualChartRows, annualFilters, loadMarketCatalog, loadMarketReport } from './annualConstructionData.ts';
import type { AnnualReport } from './annualConstructionData';
import { apartmentFilters, loadApartmentCatalog, loadApartmentData } from './apartmentData.ts';
import { distributionOption, roomsOption } from './apartmentCharts.ts';
import { loadCommissioningCatalog, loadCommissioningReport, operationalChartOption, operationalChartRows, operationalFilters } from './commissioningData.ts';
import type { OperationalCatalog, OperationalReport } from './commissioningData';
import { constructionHistoryChart, constructionHistoryRows, loadDomRfData, permitsMainOption, permitsMainRows, realizationHistoryRows, realizationStatuses } from './constructionPagesData.ts';
import { loadPermitPageSelection, selectPermitArea } from './permitPageData.ts';
import { permitBreakdownRows } from './permitPresentation.ts';
import { demoPresentationScale } from './demoPresentation.ts';
import { demoPermitOption } from './demoPermitOption.ts';
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
export const demoMonthNames = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
const mortgageMonths = demoMonthNames;
const summaryPeriod = (month: number | null | undefined) => month === 12 ? 'за год' : month === 1 ? 'январь' : month ? `январь–${demoMonthNames[month - 1]}` : 'период неизвестен';
export type DemoRows = (string | number | null)[][];
export type DemoSummary = { value: number | null; unit: string; context: string; digits?: number };
export type DemoHighlight = DemoSummary & { label: string; color: string };
export type DemoFocus = { group: string; cohort: string; title: string; detailTitle?: string; role: 'primary' | 'detail'; detailLayout: 'rings' | 'grid' };
export type DemoChart = {
  id: string; title: string; context: string; href: string; empty: boolean; rows: DemoRows;
  scope?: string;
  focus?: DemoFocus;
  summary?: DemoSummary;
  highlights?: DemoHighlight[];
  option: (theme: Theme, width: number, hidden: string[], height?: number) => EChartsCoreOption;
};
export type DemoOptions = { region?: 'msk' | 'rf'; developer?: string; month?: number; permitKind?: 'total' | 'housing' | 'nonresidential'; livingOnly?: boolean };
export type DemoSource = { id: string; title: string; href: string; load: (signal: AbortSignal, options?: DemoOptions) => Promise<DemoChart[]> };

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
const sourceParams = (options: DemoOptions = {}) => {
  const params = new URLSearchParams({ region: options.region ?? 'msk' });
  if (options.developer) params.set('developer', options.developer);
  if (options.month) for (const key of ['month', 'annualMonth', 'characteristicMonth']) params.set(key, String(options.month));
  return params;
};
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

async function profileCharts(signal: AbortSignal, options: DemoOptions = {}): Promise<DemoChart[]> {
  const mode = import.meta.env?.VITE_DATA_MODE === 'snapshot' ? 'snapshot' : 'api';
  const result = await demoVersioned(async () => {
    const catalog = await loadCatalog(mode, signal);
    return { version: catalog.metadata.version ?? '', catalog };
  }, async ({ catalog }) => {
    const { developer, region } = selectFilters(catalog, sourceParams(options));
    if (!developer || !region) throw new Error('Профили застройщиков отсутствуют.');
    if (options.developer && developer.id !== options.developer || options.region && region !== options.region) throw new Error('Выбранный профиль или регион недоступен.');
    const loaded = await loadProfile(catalog, developer.id, region, signal);
    return { version: loaded.snapshot.version ?? '', snapshot: loaded.snapshot, developer, region };
  });
  const { developer, region, snapshot } = result;
  const profile = snapshot.developers.find(item => item.id === developer.id)?.regions.find(item => item.id === region);
  if (!profile) return [];
  const { demoProfileCharts } = await import('./DemoProfileCharts');
  const charts = demoProfileCharts(profile, developer, snapshot);
  return charts.map(chart => ({ ...chart, scope: `profile/${encodeURIComponent(developer.id)}` }));
}
async function apartmentCharts(signal: AbortSignal, options: DemoOptions = {}): Promise<DemoChart[]> {
  const result = await demoVersioned(() => loadApartmentCatalog(signal), async catalog => {
    const { region, developer } = apartmentFilters(catalog, sourceParams(options));
    if (!region) throw new Error('Регионы квартирографии отсутствуют.');
    if (region.id !== (options.region ?? 'msk')) throw new Error('Для выбранного региона нет данных квартирографии.');
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
async function permitsCharts(signal: AbortSignal, options: DemoOptions = {}): Promise<DemoChart[]> {
  const data = selectPermitArea(await loadPermitPageSelection({ permitKind: options.permitKind ?? 'total',
    ...(options.month ? { month: options.month, structure: { mode: 'period', monthFrom: 1, monthTo: options.month } } : {}) }, signal), options.livingOnly !== false), caption = context(`${data.permits.region} · ${data.permits.periodLabel}`, data);
  const charts = [entry('permits/main', 'Выдача разрешений по годам', caption, '/construction/permits', demoHasValues(data.permits.presentation.rows.flatMap(row => [row.selected, row.annualForecast])), permitsMainRows(data), (theme, width, hidden, height) => permitsMainOption(data, theme, { width, hidden, height, includeGrowth: true, presentation: true, fontScale: demoPresentationScale(width, height ?? 500) })),
    ...data.breakdowns.map(chart => {
      const result = entry(`permits/${chart.id}`, chart.title, `${caption} · ${chart.periodLabel}`, '/construction/permits', hasPoints(chart), permitBreakdownRows(chart, data.version), (theme, width, hidden, height) => demoPermitOption(chart, theme, { width, hidden, height, includeGrowth: true, presentation: true, fontScale: demoPresentationScale(width, height ?? 500) }));
      return result;
    })];
  const latest = data.permits.presentation.rows.filter(row => row.actualMonth != null).at(-1);
  if (latest) charts[0].summary = { value: latest.selected, unit: data.permits.unit, context: `Всего · ${latest.year} · ${summaryPeriod(latest.actualMonth)}` };
  const selection = data.selection;
  const samePeriod = selection.structure.mode === 'period' && selection.structure.monthFrom === 1 && selection.structure.monthTo === selection.month;
  if (samePeriod) {
    // Living-only totals exclude common/embedded areas: their primary must be
    // the full composition, never the differently defined living-only main plot.
    const full = options.livingOnly === false || selection.permitKind === 'nonresidential';
    const primary = full ? 'permits/main' : selection.permitKind === 'total' ? 'permits/area_composition' : null;
    if (primary) for (const chart of charts) {
      if (chart.id !== primary && !['permits/housing_components', 'permits/nonresidential_components'].includes(chart.id)) continue;
      chart.focus = { group: 'permits/area-structure', cohort: JSON.stringify([data.version, selection, full]),
        title: 'Выдача разрешений и структура площади', detailTitle: 'Структура разрешённой площади', role: chart.id === primary ? 'primary' : 'detail', detailLayout: 'grid' };
    }
  }
  return charts;
}
async function domrfCharts(signal: AbortSignal, options: DemoOptions = {}): Promise<DemoChart[]> {
  const data = await loadDomRfData(options.region ?? 'msk', signal), caption = context(data.region.label, data), history = constructionHistoryChart(data.construction.history);
  const charts = [entry('domrf/construction', history.title, caption, '/construction/domrf', hasPoints(history), constructionHistoryRows(data.construction.history), theme => {
    const option = domrfLineOption(history, theme);
    return { ...option, yAxis: { ...(option.yAxis as object), scale: true,
      min: ({ min }: { min: number }) => Math.floor(min * .9 * 10) / 10,
      max: ({ max }: { max: number }) => Math.ceil(max * 1.08 * 10) / 10 } };
  }),
    entry('domrf/realization', 'Реализация квартир по месяцам', caption, '/construction/domrf', demoHasValues(data.sales.history.flatMap(row => realizationStatuses.map(status => row.metrics[`${status.id}_thousand_m2`]))), realizationHistoryRows(data.sales.history), theme => domrfRealizationHistoryOption(data.sales.history, theme))];
  const metrics = data.sales.metrics;
  for (const status of realizationStatuses) {
    const pct = metrics?.[`${status.id}_pct`] ?? null, area = metrics?.[`${status.id}_thousand_m2`] ?? null;
    charts.push(entry(`domrf/${status.id}`, status.label, `${data.region.label} · ${data.sales.period?.label ?? 'Нет периода'}`, '/construction/domrf', pct !== null && pct >= 0 && pct <= 100, [['Показатель', 'Доля источника, %', 'Площадь, тыс. м²'], [status.label, pct, area]], theme => domrfDonutOption(status.label, pct, area, status.color, theme)));
  }
  const current = data.sales.period;
  const latestConstruction = data.construction.history.at(-1);
  charts[0].highlights = history.series.map((series, index) => ({ label: series.name, value: series.points.at(-1)?.y ?? null,
    unit: history.unit, context: latestConstruction?.label ?? '', digits: 2, color: index === 0 ? '#367ba4' : '#b75160' }));
  for (const chart of charts.filter(chart => chart.id !== 'domrf/construction')) {
    if (current?.id && data.sales.history.at(-1)?.period === current.id) chart.context = `${data.region.label} · ${current.label}`;
    else if (chart.id === 'domrf/realization') chart.context = `${data.region.label} · История по ${data.sales.history.at(-1)?.label ?? 'нет данных'}`;
    chart.focus = { group: 'domrf/realization-structure', cohort: JSON.stringify([data.version, data.region.id, current?.id ?? null, data.sales.history.at(-1)?.period ?? null]),
      title: 'Реализация квартир: динамика и структура', detailTitle: 'Структура реализации квартир', role: chart.id === 'domrf/realization' ? 'primary' : 'detail', detailLayout: 'rings' };
  }
  return charts;
}
async function salesCharts(signal: AbortSignal, options: DemoOptions = {}): Promise<DemoChart[]> {
  const data = await demoVersioned(() => loadSalesCatalog(signal), async catalog => {
    const { region, period } = salesFilters(catalog, sourceParams(options));
    if (!region || !period) throw new Error('Отчётные периоды ДОМ.РФ отсутствуют.');
    if (region.id !== (options.region ?? 'msk')) throw new Error('Для выбранного региона нет данных реализации.');
    return loadSalesData(region.id, period.id, signal);
  });
  return data.charts.filter(chart => chart.id !== 'monthly_volume').map((chart: SalesChart) => entry(`sales/${chart.id}`, chart.title, `${data.region.label} · ${data.period.label}`, '/construction/domrf', hasPoints(chart), salesChartRows(chart), theme => domrfLineOption(chart, theme)));
}
async function operationalCharts(signal: AbortSignal, options: DemoOptions = {}): Promise<DemoChart[]> {
  const data = await demoVersioned(() => loadCommissioningCatalog('operational', signal), async catalog => {
    const selection = operationalFilters(catalog as OperationalCatalog, sourceParams(options));
    if (!selection) throw new Error('Периоды оперативного ввода отсутствуют.');
    return await loadCommissioningReport('operational', selection, signal) as OperationalReport;
  });
  return data.tables.filter(table => table.id === 'housing' || table.id === 'nonres').map(table => entry(`operational/${table.id}`, table.title, context(`Москва · ${data.periodLabel}`, data), '/commissioning/operational', demoHasValues([...table.chart.period, ...table.chart.remainder].map(point => point.value)), operationalChartRows(table), theme => operationalChartOption(table, data.periodLabel, theme)));
}
async function annualCharts(signal: AbortSignal, options: DemoOptions = {}): Promise<DemoChart[]> {
  const data = await demoVersioned(() => loadMarketCatalog('annual', signal), async catalog => {
    const selection = annualFilters(catalog, sourceParams(options));
    if (!selection) throw new Error('Регионы годового ввода отсутствуют.');
    if (selection.region !== (options.region ?? 'msk')) throw new Error('Для выбранного региона нет годовых данных.');
    return await loadMarketReport('annual', selection, signal) as AnnualReport;
  });
  return data.charts.map(chart => entry(`annual/${chart.id}`, chart.title, context(data.region.label, data), '/commissioning/annual', hasPoints(chart) || !!chart.forecast?.points.length, annualChartRows(chart), (theme, _width, hidden) => annualChartOption(chart, theme, [...chart.series.map(series => series.name), 'Прогноз'].filter(name => !hidden.includes(name)))));
}
async function linearCharts(signal: AbortSignal): Promise<DemoChart[]> {
  const data = await loadLinearDashboard(signal);
  return data.metrics.map(metric => entry(`linear/${metric.id}`, `Годовой ввод: ${metric.label}`, `Москва · ${metric.unit} · ${data.currentYear}`, '/commissioning/linear', demoHasValues(metric.years.flatMap(year => [year.fact, year.forecast])), linearYearRows(metric), (theme, width) => linearHistoryOption(metric, data.currentYear, theme, width)));
}
async function mortgageCharts(signal: AbortSignal, options: DemoOptions = {}): Promise<DemoChart[]> {
  const data = await demoVersioned(async () => readMortgageCatalog(await (await request('/api/v1/mortgage/catalog', signal)).json()), async catalog => {
    const selection = mortgageSelection(catalog, sourceParams(options));
    if (!selection) throw new Error('Периоды ипотечного кредитования отсутствуют.');
    if (selection.region !== (options.region ?? 'msk')) throw new Error('Для выбранного региона нет ипотечных данных.');
    return readMortgageReport(await (await request(mortgageUrl(selection), signal)).json(), selection);
  });
  const caption = context(data.regionLabel, data), annualCaption = `${caption} · ${data.selection.month === 1 ? 'январь' : `январь–${mortgageMonths[data.selection.month - 1]}`}`;
  const href = `/economics/mortgage?region=${data.selection.region}&annualMonth=${data.selection.month}`;
  const charts: DemoChart[] = [];
  for (const metric of ['volume', 'count'] as const) {
    const title = metric === 'volume' ? 'Объём ипотечных кредитов' : 'Количество ипотечных договоров', history = mortgageHistory(data.monthly, metric), field = metric === 'volume' ? 'volumeRub' : 'count';
    const annual = entry(`mortgage/${metric}/annual`, `${title} по годам`, annualCaption, href, demoHasValues(data.annual.flatMap(row => [row[metric].annual, row[metric].selected])), mortgageAnnualRows(data.annual, metric), (theme, width, _hidden, height) => mortgageAnnualOption(data.annual, metric, theme, width, height));
    const latest = data.annual.at(-1), measure = latest?.[metric];
    if (latest && measure) annual.summary = { value: measure.selected === null ? null : measure.selected / (metric === 'volume' ? 1e9 : 1000),
      unit: metric === 'volume' ? 'млрд руб.' : 'тыс. шт.', context: `Всего · ${latest.year} · ${summaryPeriod(measure.actualThroughMonth)}`, digits: 2 };
    charts.push(annual);
    charts.push(entry(`mortgage/${metric}/monthly`, `${title} по месяцам`, caption, href, demoHasValues(history.flatMap(row => [row.primary[field], row.secondary[field]])), mortgageMonthlyRows(history, metric), theme => mortgageMonthlyOption(history, metric, theme)));
  }
  const history = mortgageHistory(data.monthly, 'rates');
  charts.push(entry('mortgage/rates', 'Средневзвешенная ставка по месяцам', caption, href, demoHasValues(history.flatMap(row => [row.all.ratePct, row.primary.ratePct, row.secondary.ratePct])), mortgageMonthlyRows(history, 'rates'), theme => mortgageRatesOption(history, theme)));
  return charts;
}
async function preferentialCharts(signal: AbortSignal, options: DemoOptions = {}): Promise<DemoChart[]> {
  const data = await demoVersioned(async () => readPreferentialCatalog(await (await request('/api/v1/preferential-mortgage/catalog', signal)).json()), async catalog => {
    const selection = preferentialSelection(catalog, sourceParams(options));
    if (!selection) throw new Error('Периоды льготной ипотеки отсутствуют.');
    if (selection.region !== (options.region ?? 'msk')) throw new Error('Для выбранного региона нет данных льготной ипотеки.');
    return readPreferentialReport(await (await request(preferentialUrl(selection), signal)).json(), selection);
  });
  const href = `/economics/preferential-mortgage?region=${data.selection.region}&month=${data.selection.month}&characteristicMonth=${data.selection.characteristicMonth}`, caption = context(data.regionLabel, data), charts: DemoChart[] = [];
  const hiddenPrograms = (hidden: string[]) => programs.filter(program => hidden.includes(program.label)).map(program => program.id);
  for (const cumulative of [false, true]) {
    const suffix = cumulative ? 'cumulative' : 'annual', period = cumulative ? 'накопленный период' : 'по годам', flow = cumulative ? data.cumulative : data.annual, chars = cumulative ? data.characteristicsCumulative : data.characteristicsAnnual;
    const flowCaption = cumulative ? `${caption} · ${data.selection.month === 1 ? 'январь' : `январь–${mortgageMonths[data.selection.month - 1]}`}` : caption;
    const characteristicCaption = cumulative ? `${caption} · ${data.selection.characteristicMonth === 1 ? 'январь' : `январь–${mortgageMonths[data.selection.characteristicMonth - 1]}`}` : caption;
    for (const metric of ['volumeMln', 'count'] as const) {
      const chart = entry(`preferential/${metric}/${suffix}`, `${metric === 'volumeMln' ? 'Объём льготных кредитов' : 'Количество льготных кредитов'} · ${period}`, flowCaption, href, demoHasValues(flow.flatMap(row => programs.map(program => row.programs[program.id][metric]))), flowRows(flow, metric, data.latestPeriodPartial ? data.source.date : null), (theme, width, hidden) => stableDemoSeries(flowChartOption(flow, metric, theme, width, [], cumulative), flowChartOption(flow, metric, theme, width, hiddenPrograms(hidden), cumulative)));
      const latest = flow.at(-1), value = latest?.total[metric];
      if (latest) chart.summary = { value: value == null ? null : value / 1000, unit: metric === 'volumeMln' ? 'млрд руб.' : 'тыс. шт.',
        context: `Всего · ${latest.year} · ${summaryPeriod(latest.throughMonth)}`, digits: 2 };
      charts.push(chart);
    }
    for (const metric of characteristics) {
      const chart = entry(`preferential/${metric.id}/${suffix}`, `${metric.label} · ${period}`, `${characteristicCaption} · ${metric.unit}`, href, demoHasValues(chars.flatMap(row => programs.map(program => row.programs[program.id][metric.id]))), characteristicRows(chars, metric.id), (theme, width, hidden) => stableDemoSeries(characteristicChartOption(chars, metric.id, theme, width, [], cumulative), characteristicChartOption(chars, metric.id, theme, width, hiddenPrograms(hidden), cumulative)));
      const latest = chars.at(-1), available = latest ? programs.filter(program => latest.programs[program.id][metric.id] !== null) : [];
      // There is no overall weighted mean here. Show a named program only
      // when it is the sole observed program in the latest source year.
      if (latest && available.length === 1) {
        const program = available[0], month = latest.programThroughMonths[program.id][metric.id];
        chart.summary = { value: latest.programs[program.id][metric.id], unit: metric.unit,
          context: `${program.label} · ${latest.year}${month && month < 12 ? ` · ${summaryPeriod(month)}` : ''}`, digits: metric.id === 'ratePct' ? 2 : 1 };
      }
      charts.push(chart);
    }
  }
  return charts;
}
function stableDemoSeries(all: EChartsCoreOption, visible: EChartsCoreOption): EChartsCoreOption {
  type Series = { name?: string; label?: { show?: boolean }; markPoint?: unknown };
  const shown = visible.series as Series[];
  return { ...visible, series: (all.series as Series[]).map(series => shown.find(item => item.name === series.name) ?? { ...series, markPoint: undefined, label: { ...series.label, show: false } }) };
}
export const demoSources: DemoSource[] = [
  { id: 'domrf', title: 'Текущее строительство · ДОМ.РФ', href: '/construction/domrf', load: domrfCharts },
  { id: 'sales', title: 'Распроданность и стройготовность', href: '/construction/domrf', load: salesCharts },
  { id: 'apartments', title: 'Квартирография', href: '/apartments', load: apartmentCharts },
  { id: 'annual', title: 'Годовой ввод', href: '/commissioning/annual', load: annualCharts },
  { id: 'operational', title: 'Оперативный ввод', href: '/commissioning/operational', load: operationalCharts },
  { id: 'linear', title: 'Ввод линейных объектов', href: '/commissioning/linear', load: linearCharts },
  { id: 'permits', title: 'Разрешения на строительство', href: '/construction/permits', load: permitsCharts },
  { id: 'mortgage', title: 'Ипотечное кредитование', href: '/economics/mortgage', load: mortgageCharts },
  { id: 'preferential', title: 'Льготное ипотечное кредитование', href: '/economics/preferential-mortgage', load: preferentialCharts },
  ...(['accounts', 'ipc', 'salary'] as const).map(family => ({ id: family, title: { accounts: 'ВРП и ВВП', ipc: 'ИПЦ', salary: 'Заработная плата' }[family], href: `/economics/${family}`, load: async (signal: AbortSignal) => {
    const { data } = await loadEconomicsPage(family, new URLSearchParams(), signal);
    return data.charts.map(chart => entry(`${family}/${chart.id}`, chart.title, context('Мосстат / Росстат', data), `/economics/${family}`, hasPoints(chart), economicsChartRows(chart), (theme, width, hidden) => economicsChartOption(chart, theme, { width, hidden })));
  } })),
  { id: 'profile', title: 'Профиль застройщика', href: '/', load: profileCharts },
];
