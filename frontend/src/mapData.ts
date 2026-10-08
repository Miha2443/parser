export type MapObject = { id: string; registry: string; object_id: string; address: string; developer: string; builder: string; object_name: string; status: string; okrug: string; district: string; year: number | null; lat: number | null; lon: number | null; quality: string; coord_source: string; precision: string; area_total: number; area_living: number; apartments: number; has_coords: boolean; [key: string]: string | number | boolean | null };
export type MapMetadata = { version: string; generatedAt: string; source: { date: string; files: string[]; issues: [string, string][]; dateEvidence?: string }; notes: string[]; scope: { rvRows: number; oksRows: number; excludedOksRows: number; missingIssueDateRows: number } };
export type MapCatalog = MapMetadata & { developers: string[]; statuses: string[]; okrugs: string[]; years: { min: number | null; max: number | null } };
export type MapCounts = { total: number; onMap: number; located: number; approximate: number; missing: number };
export type MapReport = MapMetadata & { rows: MapObject[]; totals: MapCounts; filteredTotals: MapCounts; geojson: { type: 'FeatureCollection'; features: { type: 'Feature'; id: string; geometry: { type: 'Point'; coordinates: [number, number] }; properties: MapObject }[] } };
function metadata(value: unknown): Record<string, unknown> {
  const raw = value as Record<string, unknown>, source = raw?.source as MapMetadata['source'];
  if (!raw || raw.schemaVersion !== 1 || typeof raw.version !== 'string' || !raw.version || !source || !Array.isArray(source.files) || !Array.isArray(source.issues)) throw new Error('Ответ карты не соответствует схеме API.');
  return raw;
}
export function normalizeMapCatalog(value: unknown): MapCatalog {
  const raw = metadata(value);
  if (!['developers', 'statuses', 'okrugs'].every(key => Array.isArray(raw[key]) && (raw[key] as unknown[]).every(item => typeof item === 'string')) || !raw.years || !raw.scope) throw new Error('Некорректный каталог карты.');
  return value as MapCatalog;
}
export function normalizeMapReport(value: unknown): MapReport {
  const raw = metadata(value), report = raw as unknown as MapReport;
  if (!Array.isArray(report.rows) || !report.geojson || report.geojson.type !== 'FeatureCollection' || !Array.isArray(report.geojson.features) || !Array.isArray(report.notes)) throw new Error('Некорректный реестр карты.');
  if (new Set(report.rows.map(row => row.id)).size !== report.rows.length || report.rows.some(row => typeof row.id !== 'string' || typeof row.address !== 'string' || typeof row.has_coords !== 'boolean' || row.has_coords && (!Number.isFinite(row.lat) || !Number.isFinite(row.lon)))) throw new Error('Некорректные координаты или ID объектов.');
  for (const counts of [report.totals, report.filteredTotals]) if (!counts || Object.values(counts).some(value => !Number.isFinite(value) || value < 0)) throw new Error('Некорректные счётчики карты.');
  return value as MapReport;
}
export function mapQuery(params: URLSearchParams) {
  const query = new URLSearchParams();
  for (const key of ['developer', 'year_from', 'year_to', 'quality', 'only_with_coords']) if (params.has(key)) query.set(key, params.get(key)!);
  for (const key of ['status', 'okrug']) for (const value of params.getAll(key).flatMap(value => value.split('|')).filter(Boolean)) query.append(key, value);
  return query;
}
export function mapRepairs(catalog: MapCatalog, params: URLSearchParams): Record<string, string | null> {
  const repairs: Record<string, string | null> = {};
  const developer = params.get('developer');
  if (developer && !catalog.developers.includes(developer)) repairs.developer = null;
  for (const key of ['status', 'okrug'] as const) {
    const requested = params.getAll(key).flatMap(value => value.split('|')).filter(Boolean);
    const allowed = key === 'status' ? catalog.statuses : catalog.okrugs;
    const selected = requested.filter(value => allowed.includes(value));
    if (selected.length !== requested.length) repairs[key] = selected.join('|') || null;
  }
  if (params.has('quality') && !['all', 'located', 'approximate', 'missing'].includes(params.get('quality')!)) repairs.quality = null;
  if (params.has('only_with_coords') && !['true', 'false'].includes(params.get('only_with_coords')!)) repairs.only_with_coords = null;
  for (const key of ['year_from', 'year_to']) if (params.has(key) && !/^-?\d+$/.test(params.get(key)!)) repairs[key] = null;
  if (!('year_from' in repairs) && !('year_to' in repairs) && params.has('year_from') && params.has('year_to') && Number(params.get('year_from')) > Number(params.get('year_to'))) {
    repairs.year_from = null; repairs.year_to = null;
  }
  if (Object.keys(repairs).length) { repairs.page = null; repairs.object = null; }
  return repairs;
}
export function mapBounds(rows: MapObject[]): [[number, number], [number, number]] | null {
  const coordinates = rows.filter(row => row.has_coords && row.lat != null && row.lon != null);
  if (!coordinates.length) return null;
  return [[Math.min(...coordinates.map(row => row.lon!)), Math.min(...coordinates.map(row => row.lat!))],
          [Math.max(...coordinates.map(row => row.lon!)), Math.max(...coordinates.map(row => row.lat!))]];
}
export function mapCsvRows(rows: MapObject[]): (string | number | null)[][] {
  const keys = Object.keys(rows[0] ?? {});
  return [keys, ...rows.map(row => keys.map(key => typeof row[key] === 'boolean' ? String(row[key]) : row[key]))];
}
