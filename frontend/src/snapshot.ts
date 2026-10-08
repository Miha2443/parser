import type { Areas, CategoryKey, Developer, JsonValue, RegionProfile, Snapshot, Source } from './types';

type RecordValue = Record<string, unknown>;
const object = (value: unknown): RecordValue => value && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : {};
const list = (value: unknown): RecordValue[] => Array.isArray(value) ? value.map(object) : [];
const text = (value: unknown): string => typeof value === 'string' ? value : '';
const numeric = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) ? value : null;
const keys: Record<string, CategoryKey> = { residential: 'housing', common: 'common', nonresidentialInHousing: 'nonresidentialEmbedded', standaloneNonresidential: 'nonresidentialSeparate' };
function areas(value: unknown): Areas {
  const result: Areas = {};
  for (const [key, valueM2] of Object.entries(object(value))) if (keys[key]) result[keys[key]] = numeric(valueM2);
  return result;
}
function segments(value: unknown): Areas {
  const result: Areas = {};
  for (const s of list(value)) if (keys[text(s.key)]) result[keys[text(s.key)]] = numeric(s.valueM2);
  return result;
}
function sales(value: unknown) {
  const row = object(value), values = object(row.values), period = object(row.period);
  if (row.status !== 'available') return null;
  const year = numeric(period.year), month = numeric(period.month);
  return { sold: numeric(values.soldPercent), readiness: numeric(values.readinessPercent), ratio: numeric(values.ratioPercent), period: year && month ? new Intl.DateTimeFormat('ru-RU', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(Date.UTC(year, month - 1, 1))) : null };
}
function structureTitle(donut: RecordValue): string {
  if (donut.id !== 'commissionedAll') return text(donut.title);
  const years = Array.isArray(donut.years) ? donut.years.filter((year): year is number => typeof year === 'number' && Number.isFinite(year)).sort((a, b) => a - b) : [];
  return years.length ? `Ввод за ${years[0]}–${years.at(-1)} гг.` : 'Ввод за доступный период';
}
function delayNote(card: RecordValue, inputs: RecordValue): string | undefined {
  if (text(card.id).startsWith('current')) return 'Год в заголовке сохранён из прежней страницы; период таблицы ЕРЗ не подтверждён.';
  if (card.id !== 'historicalOtherRf') return undefined;
  const years = Array.isArray(inputs.pairedYears) ? inputs.pairedYears.filter((year): year is number => typeof year === 'number' && Number.isFinite(year)).sort((a, b) => a - b) : [];
  if (!years.length) return 'Нет подтверждённых парных наблюдений; расчётный ноль не подтверждает отсутствие переносов.';
  const missing = [2022, 2023, 2024, 2025].filter(year => !years.includes(year));
  return missing.length ? `Учтены сопоставимые годы: ${years.join(', ')}. Нет данных за ${missing.join(', ')}.` : undefined;
}
function qualitySources(value: unknown, inQuality = false): string[] {
  if (Array.isArray(value)) return value.flatMap(v => qualitySources(v, inQuality));
  return Object.entries(object(value)).flatMap(([key, child]) => inQuality && key === 'source' && typeof child === 'string' && child ? [child] : qualitySources(child, inQuality || key === 'quality'));
}
function adaptProfile(raw: RecordValue): RegionProfile {
  const annual = object(raw.annual), ratings = object(raw.ratings), apartments = object(raw.apartments), rasprod = object(raw.rasprod), delays = object(raw.delays), escrow = object(raw.escrow);
  const donuts = list(raw.categoryDonuts), construction = donuts.find(d => d.id === 'construction');
  const allYears = list(annual.allYears);
  const chartRows = list(annual.rows);
  const rowsByYear = new Map<number, RecordValue>();
  for (const row of [...chartRows, ...allYears]) { const year = numeric(row.year); if (year != null) rowsByYear.set(year, row); }
  const score = numeric(ratings.score);
  const quality = object(object(ratings.quality).regions);
  return {
    id: text(raw.region), label: raw.region === 'msk' ? 'Москва' : 'Российская Федерация',
    sourceContributions: [...new Set(qualitySources(raw))],
    annual: [...rowsByYear.values()].map(r => ({ year: numeric(r.year)!, ...areas(r.valuesM2) })).sort((a, b) => a.year - b.year),
    structures: donuts.filter(d => d.id !== 'construction').map(d => ({ title: structureTitle(d), areas: d.status === 'available' ? segments(d.segments) : {}, sourceId: 'monitoring' })),
    construction: construction?.status === 'available' ? segments(construction.segments) : null,
    ratings: list(ratings.rows).flatMap(r => ['rf', 'msk'].map(scope => ({ label: text(r.title), region: scope === 'rf' ? 'Российская Федерация' : 'Москва', place: numeric(object(r[scope]).place), score, note: r.id === 'cumulative' && object(quality[scope]).status === 'unavailable' ? 'Нет подтверждённой таблицы накопленного ввода' : undefined }))),
    apartments: list(apartments.rows).map(a => ({ label: text(a.type), count: numeric(a.count), area: numeric(a.areaThousandM2) == null ? null : numeric(a.areaThousandM2)! * 1000, share: numeric(a.sharePercent) })),
    apartmentNote: apartments.source === 'developers' ? 'Число квартир по типам рассчитано из общего количества и долей источника. Площади по типам не подтверждены.' : undefined,
    apartmentSummary: { count: numeric(apartments.totalCount), area: numeric(apartments.totalAreaThousandM2) == null ? null : numeric(apartments.totalAreaThousandM2)! * 1000, average: numeric(apartments.averageAreaM2), marketShare: numeric(apartments.marketSharePercent) },
    sales: sales(rasprod.msk), salesByRegion: [{ label: 'Москва', data: sales(rasprod.msk) }, { label: 'Российская Федерация', data: sales(rasprod.rf) }],
    delays: [],
    delayMetrics: list(delays.cards).map(d => ({ label: text(d.title), value: numeric(d.valueM2), base: numeric(d.baseM2), displayValue: text(d.displayValue), displayBase: text(d.displayBase), displayPercent: text(d.displayPercent), region: text(d.region), note: delayNote(d, object(delays.inputs)) })),
    escrow: escrow.status === 'available' ? { credit: numeric(escrow.loanRub), debt: numeric(escrow.debtRub), revenue: numeric(escrow.revenueRub), coverage: numeric(escrow.coveragePercent), debtShare: numeric(escrow.debtSharePercent), note: 'Покрытие = выручка от реализации всех площадей / остаток задолженности.' } : null,
    geography: list(raw.housingComparison).map(g => ({ title: text(g.title), moscow: numeric(list(g.segments).find(s => s.key === 'msk')?.valueM2), others: numeric(list(g.segments).find(s => s.key === 'otherRf')?.valueM2), note: 'Москва — Мониторинг 2.0; другие регионы — РФ по ЕРЗ минус Москва за одинаковые годы.' })),
    objects: ['commissioned', 'permitted'].map(key => { const table = object(object(raw.objects)[key]); return { title: key === 'commissioned' ? 'Введённые объекты · Реестр РВ' : 'Объекты с разрешением на строительство · Реестр ОКС', columns: Array.isArray(table.columns) ? table.columns.map(text) : [], rows: list(table.rows) as Record<string, JsonValue>[], note: key === 'permitted' ? 'Все объекты реестра ОКС, включая введённые и планируемые. Без ограничений карты и координат.' : 'Все введённые объекты выбранной группы компаний.' }; }),
    notes: ['Регион квартирографии не изменяет московские показатели, рейтинги, сроки и эскроу.', 'Нулевые значения, рассчитанные прежней страницей при отсутствии данных, не подтверждают наблюдаемый ноль.', 'Показатели разных источников имеют разные составы площади; общий ввод мониторинга и жилой ввод ЕРЗ не взаимозаменяемы.'],
  };
}
export function normalizeSnapshot(value: unknown): Snapshot {
  const raw = object(value);
  if (raw.schemaVersion !== 1 || !Array.isArray(raw.profiles)) throw new Error('Формат снимка не поддерживается. Ожидается схема версии 1.');
  const provenance = object(raw.provenance);
  const sourceDates = object(raw.sourceDates), evidence = object(provenance.sources);
  const diagnostics = Array.isArray(provenance.issues) ? provenance.issues.flatMap(issue => Array.isArray(issue) && typeof issue[0] === 'string' && typeof issue[1] === 'string' ? [`Диагностика источника (${issue[0]}): ${issue[1]}`] : []) : [];
  const frozen = object(raw.controls).frozen !== false;
  const definitions = [ ['monitoring', 'monitoring_2_0', 'Мониторинг 2.0'], ['erz_top', 'erzrf_top', 'ЕРЗ · рейтинги'], ['erz_cards', 'erzrf_cards', 'ЕРЗ · карточки'], ['sales', 'rasprodannost', 'Распроданность'], ['apartments', 'kvartirografia', 'Квартирография'], ['escrow', 'escrow_manual', 'Эскроу'] ];
  const sources: Source[] = definitions.map(([id, family, label]) => {
    const entry = object(evidence[family]), files = frozen ? entry.openedInputs : entry.candidateRawFiles;
    return { id, label, date: text(sourceDates[family]) || null, files: Array.isArray(files) ? files.map(text) : [], note: `${frozen ? '' : 'Перечень файлов-кандидатов реестра источников, не журнал чтений и не подтверждение вклада в показатели. '}Дата по метаданным витрины или времени изменения исходного файла; не сертифицированная дата наблюдения либо скачивания.` };
  });
  const developers = new Map<string, Developer>();
  for (const p of list(raw.profiles)) {
    const id = text(p.developerKey), name = text(p.developer);
    if (!id || !name) continue;
    if (!developers.has(id)) developers.set(id, { id, name, regions: [] });
    developers.get(id)!.regions.push(adaptProfile(p));
  }
  return { generatedAt: text(raw.generatedAt) || null, version: text(raw.version) || null, frozen, sources, developers: [...developers.values()], notes: [frozen ? 'Снимок содержит только реально выгруженные профили и доступные регионы квартирографии. Источники не обновляются при открытии страницы.' : 'Профиль получен через API. Дата формирования ответа не подтверждает свежесть исходных документов.', ...diagnostics] };
}
