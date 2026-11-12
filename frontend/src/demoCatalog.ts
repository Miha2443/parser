import type { DemoChart } from './demoData';

const mainIds = new Set([
  'domrf/construction', 'domrf/realization', 'domrf/sold', 'domrf/unsold', 'domrf/sales_not_open',
  'sales/monthly_percent', 'apartments/distribution',
  'annual/b1', 'annual/b2_1', 'operational/housing', 'operational/nonres',
  'linear/roads', 'linear/metro_stations',
  'permits/main', 'permits/area_composition', 'permits/housing_components', 'permits/nonresidential_components',
  'mortgage/volume/annual', 'mortgage/count/annual', 'mortgage/rates',
  'accounts/na_block1', 'accounts/na_block3', 'ipc/ipc_month', 'salary/salary_month',
  ...['annual', 'cumulative'].flatMap(period => [
    'volumeMln', 'count', 'averageAmountMln', 'ratePct', 'termMonths', 'ownFundsPct', 'areaM2', 'priceThousandM2',
  ].map(metric => `preferential/${metric}/${period}`)),
]);

// Curate the presentation only; ordinary pages retain every graph and export.
export function demoMainCharts(charts: DemoChart[]): DemoChart[] {
  const structures = new Map<string, DemoChart[]>();
  for (const chart of charts) {
    if (chart.empty || !/^profile\/structure\/\d+$/.test(chart.id)) continue;
    const cohort = JSON.stringify([chart.scope, chart.focus?.cohort]);
    const previous = structures.get(cohort) ?? [];
    structures.set(cohort, [...previous, chart]);
  }
  const recentStructures = new Set([...structures.values()].flatMap(group =>
    [...group].sort((a, b) => Number(a.id.split('/').at(-1)) - Number(b.id.split('/').at(-1))).slice(-2)));
  return charts.filter(chart => mainIds.has(chart.id) || chart.id === 'profile/annual' || recentStructures.has(chart));
}
