import { request } from './data.ts';

export const constructionTreeIds = ['total', 'housing', 'embedded', 'mop', 'living', 'investors', 'renovation', 'nonresidential', 'budget', 'nonbudget'] as const;
export type ConstructionTreeId = typeof constructionTreeIds[number];
export type ConstructionTreeMetric = { id: ConstructionTreeId; label: string; value: number | null; cell: string };
export type ConstructionTreeSnapshot = { date: string; capturedAt: string; publishedAt: string; release: string; delayedCapture: boolean; sha256: string };
export type ConstructionTreeSector = { id: string; label: string; value: number; rows: number[]; count: number };
export type ConstructionTreeSectors = { status: 'available' | 'unavailable'; items: ConstructionTreeSector[]; sheet?: string; filters?: Record<string, string>; areaColumn?: string; sectorColumn?: string; canonicalValue?: number; registryValue?: number };
export type ConstructionTreeData = {
  schemaVersion: 1;
  version: string;
  generatedAt: string;
  unit: 'million_m2';
  source: { file: string; sheet: string; column: 'Итог'; downloadedAt: string; fileDate: string | null };
  metrics: ConstructionTreeMetric[];
  snapshot?: ConstructionTreeSnapshot;
  nonresidentialSectors?: ConstructionTreeSectors;
};
export type ConstructionTreeState = { data: ConstructionTreeData | null; loading: boolean; error: string };

export const constructionTreeLabels: Record<ConstructionTreeId, string> = {
  total: 'Общая площадь',
  housing: 'Жилые объекты',
  embedded: 'Нежилье в жилье',
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
  const result: ConstructionTreeData = {
    schemaVersion: 1, version: text(raw.version), generatedAt: timestamp(raw.generatedAt), unit: 'million_m2',
    source: { file: text(source.file), sheet: source.sheet, column: 'Итог', downloadedAt: timestamp(source.downloadedAt), fileDate: source.fileDate === null ? null : calendarDate(source.fileDate) },
    metrics,
  };
  if (raw.snapshot !== undefined) result.snapshot = readSnapshot(raw.snapshot);
  if (raw.nonresidentialSectors !== undefined) {
    const sectors = record(raw.nonresidentialSectors);
    if (!['available', 'unavailable'].includes(String(sectors.status)) || !Array.isArray(sectors.items)) throw invalid();
    if (sectors.status === 'unavailable') {
      if (sectors.items.length) throw invalid();
      result.nonresidentialSectors = { status: 'unavailable', items: [] };
    } else {
      const items = sectors.items.map(value => {
        const item = record(value);
        if (typeof item.value !== 'number' || !Number.isFinite(item.value) || item.value < 0 || !Array.isArray(item.rows)
          || !item.rows.length || !item.rows.every(row => Number.isInteger(row) && row >= 2 && row <= 1048576)
          || item.count !== item.rows.length) throw invalid();
        return { id: text(item.id), label: text(item.label), value: item.value, rows: item.rows as number[], count: item.count as number };
      });
      const canonical = metrics.find(m => m.id === 'nonresidential')!.value;
      const total = items.reduce((sum, item) => sum + item.value, 0);
      const rows = items.flatMap(item => item.rows);
      if (!items.length || new Set(items.map(item => item.id)).size !== items.length || new Set(rows).size !== rows.length
        || canonical === null || typeof sectors.registryValue !== 'number' || !Number.isFinite(sectors.registryValue)
        || sectors.canonicalValue !== canonical || Math.abs(total - canonical) > 1e-8 || Math.abs(sectors.registryValue - canonical) > 1e-8) throw invalid();
      const filters = record(sectors.filters);
      for (const [key, value] of Object.entries({ 'Назначение': 'Нежилье', 'Статус объекта': 'Строящийся', 'Статус РС': 'Действует' })) if (filters[key] !== value) throw invalid();
      result.nonresidentialSectors = { status: 'available', items, sheet: text(sectors.sheet), filters: filters as Record<string, string>,
        areaColumn: text(sectors.areaColumn), sectorColumn: text(sectors.sectorColumn), canonicalValue: canonical, registryValue: sectors.registryValue };
    }
  }
  return result;
}

function readSnapshot(value: unknown): ConstructionTreeSnapshot {
  const raw = record(value), date = calendarDate(raw.date);
  if (new Date(`${date}T00:00:00Z`).getUTCDay() !== 0 || typeof raw.delayedCapture !== 'boolean' || !/^[a-f0-9]{64}$/.test(text(raw.sha256))) throw invalid();
  const capturedAt = timestamp(raw.capturedAt), publishedAt = timestamp(raw.publishedAt);
  const boundary = Date.parse(`${date}T21:00:00Z`); // Monday midnight in Moscow.
  if (Date.parse(publishedAt) >= boundary || Date.parse(capturedAt) < boundary) throw invalid();
  return { date, capturedAt, publishedAt, release: text(raw.release), delayedCapture: raw.delayedCapture, sha256: raw.sha256 as string };
}

export async function loadConstructionTreeHistory(signal: AbortSignal): Promise<ConstructionTreeSnapshot[]> {
  const response = await request('/api/v1/construction-tree/history', signal), raw = record(await response.json());
  if (raw.schemaVersion !== 1 || !Array.isArray(raw.snapshots)) throw invalid();
  const snapshots = raw.snapshots.map(readSnapshot);
  if (new Set(snapshots.map(row => row.date)).size !== snapshots.length) throw invalid();
  return snapshots.sort((a, b) => b.date.localeCompare(a.date));
}

export async function loadConstructionTree(signal: AbortSignal, asOf = ''): Promise<ConstructionTreeData> {
  const endpoint = asOf ? `/api/v1/construction-tree/history/${calendarDate(asOf)}` : '/api/v1/construction/tree';
  const response = await request(endpoint, signal);
  const data = readConstructionTree(await response.json());
  if (asOf ? data.snapshot?.date !== asOf : data.snapshot !== undefined) throw invalid();
  return data;
}

// One request at a time; a failed refresh removes the previous snapshot entirely.
export function watchConstructionTree(update: (state: ConstructionTreeState) => void, asOf = '') {
  let stopped = false, controller: AbortController | null = null;
  let state: ConstructionTreeState = { data: null, loading: true, error: '' };
  const publish = (next: ConstructionTreeState) => { state = next; update(next); };
  const refresh = async () => {
    if (stopped || controller || document.visibilityState !== 'visible') return;
    const active = new AbortController();
    controller = active;
    publish({ ...state, loading: true, error: '' });
    try {
      const data = await loadConstructionTree(active.signal, asOf);
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

export function constructionTreeRows(data: ConstructionTreeData): (string | number | null)[][] {
  const header = ['ID', 'Показатель', 'Подпись источника', 'Значение, млн м²', 'Ячейка / строки', 'Файл', 'Лист', 'Колонка', 'Версия', 'Выгрузка мониторинга', 'Дата файла', 'Сформировано API', 'Дата снимка', 'Опубликовано', 'Снимок сохранён'];
  const metadata = [data.version, data.source.downloadedAt, data.source.fileDate ?? '', data.generatedAt, data.snapshot?.date ?? '', data.snapshot?.publishedAt ?? '', data.snapshot?.capturedAt ?? ''];
  const rows = data.metrics.map(m => [m.id, constructionTreeLabels[m.id], m.label, m.value, m.cell, data.source.file, data.source.sheet, data.source.column, ...metadata]);
  for (const item of data.nonresidentialSectors?.items ?? []) rows.push([item.id, item.label, item.label, item.value, item.rows.join(','), data.source.file, data.nonresidentialSectors!.sheet!, data.nonresidentialSectors!.areaColumn!, ...metadata]);
  return [header, ...rows];
}

export function constructionTreeCsv(data: ConstructionTreeData): string {
  // Prevent source-controlled strings from becoming formulas in spreadsheet apps.
  const escape = (value: string) => `"${(/^[\s\uFEFF]*[=+@-]/.test(value) ? `'${value}` : value).replaceAll('"', '""')}"`;
  return '\uFEFF' + constructionTreeRows(data).map(row => row.map(value => escape(value === null ? '' : String(value))).join(';')).join('\r\n');
}

export function constructionTreeFilename(data: ConstructionTreeData): string {
  const version = data.version.replace(/[^a-zA-Z0-9._-]/g, '_').slice(0, 80);
  return `construction-tree-${data.snapshot ? data.snapshot.date + '-' : ''}${version}.csv`;
}
