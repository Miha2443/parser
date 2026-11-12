import type { DemoChart } from './demoData';

export type DemoSlide = {
  id: string; title: string; sourceTitle: string; context: string;
  layout: 'single' | 'pair' | 'rings' | 'overview' | 'grid' | 'focus'; charts: DemoChart[];
  primaryId?: string;
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
export function demoPanelTitle(chart: DemoChart, slide: DemoSlide) {
  if (chart.id.startsWith('linear/')) return chart.title.replace(/^Годовой ввод: /, '');
  if (slide.charts.length > 1 && /^mortgage\/(volume|count)\//.test(chart.id)) return chart.id.includes('/volume/') ? 'Объём выданных кредитов' : 'Количество договоров';
  if (slide.charts.length > 1 && /^preferential\/(volumeMln|count)\//.test(chart.id)) return chart.id.includes('/volumeMln/') ? 'Объём выданных кредитов' : 'Количество договоров';
  return slide.id.startsWith('preferential/characteristics/') ? chart.title.replace(/ · (?:по годам|накопленный период)$/, '') : chart.title;
}
export function demoPanelContext(chart: DemoChart, slide: DemoSlide) {
  if (chart.context === slide.context) return '';
  return chart.context.startsWith(`${slide.context} · `) ? chart.context.slice(slide.context.length + 3) : chart.context;
}
function group(chart: DemoChart): { id: string; title: string; layout: DemoSlide['layout'] } {
  const id = chart.id, scope = chart.scope ?? 'profile';
  if (chart.focus) return { id: chart.focus.group, title: chart.focus.title, layout: 'focus' };
  if (id.startsWith('profile/')) {
    if (id.endsWith('/annual') || id.includes('/structure/')) return { id: `${scope}/commissioning`, title: 'Ввод недвижимости', layout: 'overview' };
    if (id.includes('/geography/')) return { id: `${scope}/geography`, title: 'География деятельности', layout: 'rings' };
  }
  if (chart.scope?.startsWith('market/') && (id === 'sales/monthly_percent' || id === 'apartments/distribution')) return {
    id: `sales/overview${chart.scope ? `/${chart.scope}` : ''}`, title: 'Распроданность, стройготовность и квартирография', layout: 'pair',
  };
  if (id.startsWith('sales/') || id.startsWith('apartments/') || id.startsWith('linear/')) {
    const source = id.split('/')[0];
    const title = source === 'sales' ? 'Распроданность' : source === 'apartments' ? 'Квартирография' : 'Годовой ввод линейных объектов';
    return { id: `${source}/overview${chart.scope ? `/${chart.scope}` : ''}`, title, layout: 'grid' };
  }
  if (id.startsWith('domrf/') && id !== 'domrf/construction' && id !== 'domrf/realization') return { id: 'domrf/realization-structure', title: 'Структура реализации квартир', layout: 'rings' };
  if (id === 'operational/housing' || id === 'operational/nonres') return { id: 'operational/commissioning', title: 'Ввод жилья и нежилой недвижимости', layout: 'pair' };
  if (id === 'annual/b1' || id === 'annual/b2_1') return { id: 'annual/overview', title: 'Ввод недвижимости и состав жилья', layout: 'pair' };
  if (id === 'annual/b2_3') return { id: 'annual/housing', title: 'Реновация', layout: 'single' };
  if (id === 'annual/b2_2' || id === 'annual/b3_2') return { id: 'annual/funding', title: 'Бюджетный и небюджетный ввод', layout: 'pair' };
  if (id === 'accounts/na_block1' || id === 'accounts/na_block3') return { id: 'accounts/overview', title: 'ВРП и ВВП: объём и динамика', layout: 'pair' };
  if (/^mortgage\/(volume|count)\/(annual|monthly)$/.test(id)) {
    const monthly = id.endsWith('/monthly');
    return { id: `mortgage/${monthly ? 'monthly' : 'annual'}`, title: monthly ? 'Выдача ипотеки по месяцам' : 'Выдача ипотеки по годам', layout: 'pair' };
  }
  if (id.startsWith('preferential/')) {
    const [, metric, period] = id.split('/'), suffix = period === 'cumulative' ? 'накопленный период' : 'по годам';
    if (metric === 'volumeMln' || metric === 'count') return { id: `preferential/flows/${period}`, title: `Выдача льготной ипотеки · ${suffix}`, layout: 'pair' };
    if (['averageAmountMln', 'ratePct', 'termMonths', 'ownFundsPct', 'areaM2', 'priceThousandM2'].includes(metric)) {
      return { id: `preferential/characteristics/${period}${chart.scope ? `/${chart.scope}` : ''}`, title: `Характеристики льготных ипотечных кредитов · ${suffix}`, layout: 'grid' };
    }
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
    const spec = group(chart), key = JSON.stringify([spec.id, chart.focus?.cohort ?? null, chart.focus ? chart.id.split('/')[0] : null]), previous = groups.get(key);
    if (previous) previous.charts.push(chart);
    else groups.set(key, { ...spec, sourceTitle: demoSourceTitle(chart.id), context: chart.context, charts: [chart] });
  }
  const identifiers = new Map<string, number>();
  return [...groups.values()].map(slide => {
    const panels = [...slide.charts];
    const primaries = panels.filter(chart => chart.focus?.role === 'primary');
    const primaryId = slide.layout === 'focus' && primaries.length === 1 && panels.length > 1 ? primaries[0].id : undefined;
    if (primaryId) panels.sort((a, b) => Number(b.id === primaryId) - Number(a.id === primaryId));
    if (slide.layout === 'overview') panels.sort((a, b) => Number(a.id.endsWith('/annual') || a.id === 'domrf/realization') - Number(b.id.endsWith('/annual') || b.id === 'domrf/realization'));
    const hasHistory = panels.some(chart => chart.id.endsWith('/annual') || chart.id === 'domrf/realization');
    const index = identifiers.get(slide.id) ?? 0;
    identifiers.set(slide.id, index + 1);
    const focusLayout = primaryId ? 'focus' : panels[0].focus?.detailLayout ?? 'grid';
    return { ...slide, id: index ? `${slide.id}/cohort-${index + 1}` : slide.id, charts: panels, context: sharedContext(panels),
      ...(primaryId ? { primaryId } : {}),
      title: panels.length === 1 ? panels[0].title : slide.layout === 'focus' && !primaryId ? panels[0].focus?.detailTitle ?? slide.title : slide.title,
      layout: panels.length === 1 ? 'single' : slide.layout === 'focus' ? focusLayout : slide.layout === 'overview' && !hasHistory ? 'rings' : slide.layout };
  });
}
