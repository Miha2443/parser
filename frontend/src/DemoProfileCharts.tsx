import { annualOption, donutOption } from './Chart';
import { categories, sumAreas } from './format';
import type { Areas, CatalogDeveloper, RegionProfile, Snapshot } from './types';
import type { DemoChart, DemoRows } from './demoData';

export function demoProfileCharts(profile: RegionProfile, developer: CatalogDeveloper, snapshot: Snapshot): DemoChart[] {
  const context = `${developer.name} · ${profile.label} · ${snapshot.generatedAt?.slice(0, 10) ?? ''}`, href = `/?developer=${encodeURIComponent(developer.id)}&region=${profile.id}`;
  const areaRows = (areas: Areas): DemoRows => [['Категория', 'Площадь, м²'], ...categories.map(category => [category.label, areas[category.key] ?? null])];
  const charts: DemoChart[] = [{ id: 'profile/annual', title: 'Ввод по годам', context, href, empty: !profile.annual.some(row => categories.some(category => row[category.key] != null)), rows: [['Год', ...categories.map(category => category.label)], ...profile.annual.map(row => [row.year, ...categories.map(category => row[category.key] ?? null)])], option: theme => annualOption(profile.annual, theme) }];
  for (const [i, structure] of profile.structures.entries()) if (structure.complete !== false) charts.push({ id: `profile/structure/${i}`, title: structure.title, context, href, empty: (sumAreas(structure.areas) ?? 0) <= 0, rows: areaRows(structure.areas), option: theme => donutOption(structure.areas, theme) });
  for (const [i, geography] of (profile.geography ?? []).entries()) charts.push({ id: `profile/geography/${i}`, title: geography.title, context, href, empty: geography.moscow === null || geography.others === null || geography.moscow + geography.others <= 0, rows: [['Регион', 'Жилая площадь, м²'], ['Москва', geography.moscow], ['Другие регионы РФ', geography.others]], option: theme => donutOption({ housing: geography.moscow, nonresidentialEmbedded: geography.others }, theme, { housing: 'Москва', nonresidentialEmbedded: 'Другие регионы РФ' }) });
  const construction = profile.construction;
  charts.push({ id: 'profile/construction', title: 'Структура текущего строительства', context, href, empty: !construction || (sumAreas(construction) ?? 0) <= 0, rows: construction ? areaRows(construction) : [], option: theme => donutOption(construction ?? {}, theme, { nonresidentialSeparate: 'Нежилое' }) });
  return charts;
}
