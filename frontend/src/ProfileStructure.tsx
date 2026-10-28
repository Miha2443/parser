import { Chart, donutOption } from './Chart';
import { area, categories, sumAreas } from './format';
import type { Structure, Theme } from './types';
import './profile-structure.css';

export default function ProfileStructure({ structure, theme, filename }: { structure: Structure; theme: Theme; filename: string }) {
  if (structure.complete === false) return <div className="profile-partial-structure" role="note" aria-label="Неполная структура площади">
    <p>Состав площади подтверждён не полностью</p>
    <dl>{categories.map(category => <div key={category.key}><dt>{category.label}</dt><dd>{area(structure.areas[category.key])}<span>тыс. м²</span></dd></div>)}</dl>
  </div>;
  return (sumAreas(structure.areas) ?? 0) > 0 ? <Chart option={donutOption(structure.areas, theme)} theme={theme} label={structure.title} rows={[["Категория", "Площадь, м²"], ...categories.filter(category => structure.areas[category.key] != null).map(category => [category.label, structure.areas[category.key] ?? null])]} filename={filename} height={200} /> : <div className="empty-state">Нет подтверждённой структуры площади.</div>;
}
