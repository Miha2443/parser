import { request } from './data.ts';

export const constructionTreeIds = ['total', 'housing', 'embedded', 'mop', 'living', 'investors', 'renovation', 'nonresidential', 'budget', 'nonbudget'] as const;
export type ConstructionTreeId = typeof constructionTreeIds[number];
export type ConstructionTreeMetric = { id: ConstructionTreeId; label: string; value: number | null; cell: string };
export type ConstructionTreeData = {
  schemaVersion: 1;
  version: string;
  generatedAt: string;
  unit: 'million_m2';
  source: { file: string; sheet: string; column: 'Итог'; downloadedAt: string; fileDate: string | null };
  metrics: ConstructionTreeMetric[];
};
export type ConstructionTreeState = { data: ConstructionTreeData | null; loading: boolean; error: string };

export const constructionTreeLabels: Record<ConstructionTreeId, string> = {
  total: 'Общая площадь',
  housing: 'Жилые объекты',
  embedded: 'Встроенное нежильё',
  mop: 'Места общего пользования',
  living: 'Жилая площадь',
  investors: 'Инвесторы с ДДУ',
  renovation: 'Реновация',
  nonresidential: 'Нежилые объекты',
  budget: 'Бюджетное строительство',
  nonbudget: 'Внебюджетное строительство',
};

const invalid = () => new Error('Данные дерева строительства не соответствуют схеме API версии 1.');
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid();
  return value as Record<string, unknown>;
}
function text(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) throw invalid();
  return value;
}
function calendarDate(value: unknown): string {
  const date = text(value);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) throw invalid();
  const parsed = new Date(`${date}T00:00:00Z`);
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date) throw invalid();
  return date;
}
function timestamp(value: unknown): string {
  const date = text(value);
  const match = /^(\d{4}-\d{2}-\d{2})T([01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/.exec(date);
  if (!match || !Number.isFinite(Date.parse(date))) throw invalid();
  calendarDate(match[1]);
  return date;
}
function cellAddress(value: unknown): string {
  const cell = text(value), match = /^\$?([A-Z]{1,3})\$?([1-9]\d{0,6})$/.exec(cell);
  if (!match) throw invalid();
  const column = [...match[1]].reduce((n, char) => n * 26 + char.charCodeAt(0) - 64, 0);
  if (column > 16384 || Number(match[2]) > 1048576) throw invalid();
  return cell;
}

export function readConstructionTree(value: unknown): ConstructionTreeData {
  const raw = record(value), source = record(raw.source);
  if (raw.schemaVersion !== 1 || raw.unit !== 'million_m2' || source.sheet !== 'Дерево текущ стр во' || source.column !== 'Итог' || !Array.isArray(raw.metrics)) throw invalid();
  const metrics = raw.metrics.map(value => {
    const metric = record(value), id = text(metric.id) as ConstructionTreeId;
    if (!constructionTreeIds.includes(id) || metric.value !== null && (typeof metric.value !== 'number' || !Number.isFinite(metric.value) || metric.value < 0)) throw invalid();
    return { id, label: text(metric.label), value: metric.value as number | null, cell: cellAddress(metric.cell) };
  });
  if (metrics.length !== constructionTreeIds.length || new Set(metrics.map(m => m.id)).size !== constructionTreeIds.length) throw invalid();
  return {
    schemaVersion: 1, version: text(raw.version), generatedAt: timestamp(raw.generatedAt), unit: 'million_m2',
    source: { file: text(source.file), sheet: source.sheet, column: 'Итог', downloadedAt: timestamp(source.downloadedAt), fileDate: source.fileDate === null ? null : calendarDate(source.fileDate) },
    metrics,
  };
}

export async function loadConstructionTree(signal: AbortSignal): Promise<ConstructionTreeData> {
  const response = await request('/api/v1/construction/tree', signal);
  return readConstructionTree(await response.json());
}

// One request at a time; a failed refresh removes the previous snapshot entirely.
export function watchConstructionTree(update: (state: ConstructionTreeState) => void) {
  let stopped = false, controller: AbortController | null = null;
  let state: ConstructionTreeState = { data: null, loading: true, error: '' };
  const publish = (next: ConstructionTreeState) => { state = next; update(next); };
  const refresh = async () => {
    if (stopped || controller || document.visibilityState !== 'visible') return;
    const active = new AbortController();
    controller = active;
    publish({ ...state, loading: true, error: '' });
    try {
      const data = await loadConstructionTree(active.signal);
      if (!stopped && !active.signal.aborted) publish({ data, loading: false, error: '' });
    } catch (error) {
      if (!stopped && !active.signal.aborted) publish({ data: null, loading: false, error: error instanceof Error ? error.message : 'Не удалось загрузить дерево строительства.' });
    } finally {
      if (controller === active) controller = null;
    }
  };
  const visible = () => { if (document.visibilityState === 'visible') void refresh(); };
  const timer = setInterval(visible, 60_000);
  document.addEventListener('visibilitychange', visible);
  void refresh();
  return {
    refresh,
    stop() {
      stopped = true;
      clearInterval(timer);
      document.removeEventListener('visibilitychange', visible);
      controller?.abort();
      controller = null;
    },
  };
}

const areaFormat = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const constructionTreeArea = (value: number | null) => value === null ? '—' : areaFormat.format(value);
export const constructionTreeExact = (value: number | null) => value === null ? '—' : String(value);
export function constructionTreeDate(value: string | null): string {
  if (value === null) return 'не указана';
  const [year, month, day] = value.slice(0, 10).split('-');
  return `${day}.${month}.${year}`;
}

export function constructionTreeCsv(data: ConstructionTreeData): string {
  const header = ['ID', 'Показатель', 'Подпись источника', 'Значение, млн м²', 'Ячейка', 'Файл', 'Лист', 'Колонка', 'Версия', 'Выгрузка мониторинга', 'Дата файла', 'Сформировано API'];
  const rows = data.metrics.map(m => [m.id, constructionTreeLabels[m.id], m.label, m.value === null ? '' : String(m.value), m.cell, data.source.file, data.source.sheet, data.source.column, data.version, data.source.downloadedAt, data.source.fileDate ?? '', data.generatedAt]);
  // Prevent source-controlled strings from becoming formulas in spreadsheet apps.
  const escape = (value: string) => `"${(/^[\s\uFEFF]*[=+@-]/.test(value) ? `'${value}` : value).replaceAll('"', '""')}"`;
  return '\uFEFF' + [header, ...rows].map(row => row.map(escape).join(';')).join('\r\n');
}

export function constructionTreeFilename(data: ConstructionTreeData): string {
  const version = data.version.replace(/[^a-zA-Z0-9._-]/g, '_').slice(0, 80);
  return `construction-tree-${version}.csv`;
}
