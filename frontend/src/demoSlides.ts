import type { DemoChart } from './demoData';

export type DemoSlide = {
  id: string; title: string; sourceTitle: string; context: string;
  layout: 'single' | 'pair' | 'rings' | 'overview' | 'grid'; charts: DemoChart[];
};
const sourceLabels: Record<string, string> = {
  profile: 'Профиль застройщика', permits: 'Разрешения на строительство · РС',
  domrf: 'Текущее строительство · Данные ДОМ.РФ', sales: 'Данные ДОМ.РФ · Распроданность',
  apartments: 'Квартирография', operational: 'Ввод недвижимости · Оперативные данные · РВ',
  annual: 'Ввод недвижимости · Годовой ввод · РВ', linear: 'Ввод недвижимости · Линейные объекты',
  accounts: 'Мосстат / Росстат · ВРП и ВВП', ipc: 'Мосстат / Росстат · ИПЦ',
  salary: 'Мосстат / Росстат · Заработная плата', mortgage: 'Ипотечное кредитование',
  preferential: 'Льготное ипотечное кредитование',
};
export const demoSourceTitle = (id: string) => sourceLabels[id.split('/')[0]] ?? 'Аналитика Москвы';
export function demoPanelContext(chart: DemoChart, slide: DemoSlide) {
  if (chart.context === slide.context) return '';
  return chart.context.startsWith(`${slide.context} · `) ? chart.context.slice(slide.context.length + 3) : chart.context;
}
function group(chart: DemoChart): { id: string; title: string; layout: DemoSlide['layout'] } {
  const id = chart.id, scope = chart.scope ?? 'profile';
  if (id.startsWith('profile/')) {
    if (id.endsWith('/annual') || id.includes('/structure/')) return { id: `${scope}/commissioning`, title: 'Ввод недвижимости', layout: 'overview' };
    if (id.includes('/geography/')) return { id: `${scope}/geography`, title: 'География деятельности', layout: 'rings' };
  }
  if (id.startsWith('sales/') || id.startsWith('apartments/') || id.startsWith('linear/')) {
    const source = id.split('/')[0];
    const title = source === 'sales' ? 'Распроданность' : source === 'apartments' ? 'Квартирография' : 'Годовой ввод линейных объектов';
    return { id: `${source}/overview${chart.scope ? `/${chart.scope}` : ''}`, title, layout: 'grid' };
  }
  if (id.startsWith('domrf/') && id !== 'domrf/construction' && id !== 'domrf/realization') return { id: 'domrf/realization-structure', title: 'Структура реализации квартир', layout: 'rings' };
  if (id === 'operational/housing' || id === 'operational/nonres') return { id: 'operational/commissioning', title: 'Ввод жилья и нежилой недвижимости', layout: 'pair' };
  if (id === 'annual/b2_1' || id === 'annual/b2_3') return { id: 'annual/housing', title: 'Состав ввода жилья и реновация', layout: 'pair' };
  if (id === 'annual/b2_2' || id === 'annual/b3_2') return { id: 'annual/funding', title: 'Бюджетный и небюджетный ввод', layout: 'pair' };
  if (/^mortgage\/(volume|count)\/(annual|monthly)$/.test(id)) {
    const monthly = id.endsWith('/monthly');
    return { id: `mortgage/${monthly ? 'monthly' : 'annual'}`, title: monthly ? 'Выдача ипотеки по месяцам' : 'Выдача ипотеки по годам', layout: 'pair' };
  }
  if (id.startsWith('preferential/')) {
    const [, metric, period] = id.split('/'), suffix = period === 'cumulative' ? 'накопленный период' : 'по годам';
    if (metric === 'volumeMln' || metric === 'count') return { id: `preferential/flows/${period}`, title: `Выдача льготной ипотеки · ${suffix}`, layout: 'pair' };
    const groups = [['averageAmountMln', 'ratePct'], ['termMonths', 'ownFundsPct'], ['areaM2', 'priceThousandM2']];
    const index = groups.findIndex(metrics => metrics.includes(metric));
    if (index >= 0) return { id: `preferential/characteristics/${index}/${period}`, title: `${['Сумма кредита и ставка', 'Срок кредита и собственные средства', 'Площадь и стоимость жилья'][index]} · ${suffix}`, layout: 'pair' };
  }
  return { id, title: chart.title, layout: 'single' };
}
function sharedContext(charts: DemoChart[]) {
  const parts = charts[0].context.split(' · ');
  let count = parts.length;
  for (const chart of charts.slice(1)) {
    const other = chart.context.split(' · ');
    count = Math.min(count, other.length);
    for (let i = 0; i < count; i++) if (parts[i] !== other[i]) { count = i; break; }
  }
  return count ? parts.slice(0, count).join(' · ') : charts[0].context;
}
export function demoSlides(charts: DemoChart[]): DemoSlide[] {
  const groups = new Map<string, DemoSlide>();
  for (const chart of charts) {
    if (chart.empty) continue;
    const spec = group(chart), previous = groups.get(spec.id);
    if (previous) previous.charts.push(chart);
    else groups.set(spec.id, { ...spec, sourceTitle: demoSourceTitle(chart.id), context: chart.context, charts: [chart] });
  }
  return [...groups.values()].map(slide => {
    const panels = [...slide.charts];
    if (slide.layout === 'overview') panels.sort((a, b) => Number(a.id.endsWith('/annual') || a.id === 'domrf/realization') - Number(b.id.endsWith('/annual') || b.id === 'domrf/realization'));
    const hasHistory = panels.some(chart => chart.id.endsWith('/annual') || chart.id === 'domrf/realization');
    return { ...slide, charts: panels, context: sharedContext(panels),
      title: panels.length === 1 ? panels[0].title : slide.title,
      layout: panels.length === 1 ? 'single' : slide.layout === 'overview' && !hasHistory ? 'rings' : slide.layout };
  });
}
