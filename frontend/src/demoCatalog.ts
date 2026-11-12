import { demoSources, type DemoChart, type DemoSource } from './demoData';
import { demoSlides, type DemoSlide } from './demoSlides';

const mainIds = new Set([
  'domrf/construction', 'domrf/realization', 'domrf/sold', 'domrf/unsold', 'domrf/sales_not_open',
  'sales/monthly_percent', 'apartments/distribution',
  'annual/b1', 'annual/b2_1', 'operational/housing', 'operational/nonres',
  ...['roads', 'bridges', 'crossings', 'metro_stations', 'metro_tunnels', 'depots'].map(id => `linear/${id}`),
  'permits/area_composition', 'permits/housing_components', 'permits/nonresidential_components',
  'mortgage/volume/annual', 'mortgage/count/annual', 'mortgage/rates',
  'accounts/na_block1', 'accounts/na_block3', 'ipc/ipc_month', 'salary/salary_month',
  ...['annual', 'cumulative'].flatMap(period => [
    'volumeMln', 'count', 'averageAmountMln', 'ratePct', 'termMonths', 'ownFundsPct', 'areaM2', 'priceThousandM2',
  ].map(metric => `preferential/${metric}/${period}`)),
]);

// Curate the presentation only; ordinary pages retain every graph and export.
export function demoMainCharts(charts: DemoChart[]): DemoChart[] {
  return charts.filter(chart => mainIds.has(chart.id));
}

export const demoBlockSources: DemoSource[] = demoSources.filter(source => !['profile', 'apartments'].includes(source.id)).map(source => source.id === 'sales' ? {
  ...source, title: 'Распроданность, стройготовность и квартирография',
  load: async (signal, options) => {
    const sales = await source.load(signal, options);
    const apartments = await demoSources.find(source => source.id === 'apartments')!.load(signal, options);
    return [...sales, ...apartments].map(chart => ({ ...chart, scope: `market/${options?.region ?? 'msk'}` }));
  },
} : source);

export type DemoBlock = DemoSlide & { empty: boolean };
export function demoBlocks(charts: DemoChart[]): DemoBlock[] {
  // Keep missing panels in a block as placeholders, never as independent choices.
  const byCopy = new Map(charts.map(chart => [{ ...chart, empty: false }, chart]));
  return demoSlides([...byCopy.keys()]).map(slide => {
    const entries = slide.charts.map(chart => byCopy.get(chart)!);
    return { ...slide, charts: entries, empty: entries.every(chart => chart.empty) };
  });
}
export function demoBlockSelection(blocks: DemoBlock[], selected: ReadonlySet<string>): DemoBlock[] {
  return blocks.filter(block => !block.empty && selected.has(block.id));
}
