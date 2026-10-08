import { normalizeSnapshot } from './snapshot.ts';
import type { ApartmentRegion, Catalog, CatalogDeveloper, Snapshot } from './types';

export type DataMode = 'api' | 'snapshot';
type RecordValue = Record<string, unknown>;
const object = (value: unknown): RecordValue => value && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : {};

export function normalizeCatalog(value: unknown): Catalog {
  const raw = object(value), controls = object(raw.controls);
  if (raw.schemaVersion !== 1 || controls.frozen !== false || typeof raw.version !== 'string' || !raw.version || typeof raw.generatedAt !== 'string' || !Number.isFinite(Date.parse(raw.generatedAt)) || !Array.isArray(controls.developers)) {
    throw new Error('Каталог API не соответствует схеме версии 1.');
  }
  const developers: CatalogDeveloper[] = controls.developers.map(value => {
    const row = object(value);
    if (typeof row.developer !== 'string' || !row.developer || typeof row.developerKey !== 'string' || !row.developerKey || !Array.isArray(row.regions) || row.regions.some(r => r !== 'msk' && r !== 'rf')) {
      throw new Error('В каталоге API некорректные фильтры застройщика.');
    }
    return { id: row.developerKey, name: row.developer, regions: row.regions.length ? [...new Set(row.regions as ApartmentRegion[])] : ['msk'] };
  });
  if (!developers.length || new Set(developers.map(d => d.id)).size !== developers.length) throw new Error('Каталог API не содержит уникальных застройщиков.');
  return { metadata: normalizeSnapshot({ ...raw, profiles: [] }), developers };
}

export function selectFilters(catalog: Catalog | null, params: URLSearchParams) {
  const developer = catalog?.developers.find(d => d.id === params.get('developer')) ?? catalog?.developers[0];
  const requested = params.get('region');
  const region = developer?.regions.find(r => r === requested) ?? developer?.regions.find(r => r === 'msk') ?? developer?.regions[0];
  return { developer, region };
}

export function profileUrl(developer: string, region: ApartmentRegion, exportFile = false, requiredVersion?: string) {
  const params = new URLSearchParams({ developer, region });
  if (exportFile && requiredVersion) params.set('required_version', requiredVersion);
  return `/api/v1/profile${exportFile ? '/export' : ''}?${params}`;
}

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number) {
    super(`Сервер данных вернул ошибку HTTP ${status}.`);
    this.name = 'ApiError'; this.status = status;
  }
}

export async function request(url: string, signal: AbortSignal): Promise<Response> {
  let response: Response;
  try { response = await fetch(url, { signal, cache: 'no-store' }); }
  catch (error) {
    if (signal.aborted) throw error;
    throw new Error('Не удалось подключиться к серверу данных. Проверьте доступность API.');
  }
  if (!response.ok) throw new ApiError(response.status);
  return response;
}

export async function loadCatalog(mode: DataMode, signal: AbortSignal): Promise<Catalog> {
  const response = await request(mode === 'snapshot' ? '/profile-snapshot.json' : '/api/v1/catalog', signal);
  const raw: unknown = await response.json();
  if (mode === 'api') return normalizeCatalog(raw);
  const demo = normalizeSnapshot(raw);
  demo.frozen = true;
  if (!demo.developers.length) throw new Error('Снимок не содержит профилей застройщиков.');
  return { metadata: demo, demo, developers: demo.developers.map(d => ({ id: d.id, name: d.name, regions: d.regions.map(r => r.id as ApartmentRegion) })) };
}

export async function loadProfile(catalog: Catalog, developer: string, region: ApartmentRegion, signal: AbortSignal): Promise<{ snapshot: Snapshot; catalog: Catalog }> {
  if (catalog.demo) return { snapshot: catalog.demo, catalog };
  const raw = object(await (await request(profileUrl(developer, region), signal)).json());
  if (object(raw.controls).frozen !== false || typeof raw.version !== 'string' || !raw.version || typeof raw.generatedAt !== 'string' || !Number.isFinite(Date.parse(raw.generatedAt)) || !Array.isArray(raw.profiles) || raw.profiles.length !== 1) {
    throw new Error('Ответ профиля API не соответствует схеме версии 1.');
  }
  const p = object(raw.profiles[0]);
  if (p.developerKey !== developer || p.region !== region) throw new Error('API вернул профиль, не соответствующий выбранным фильтрам.');
  const snapshot = normalizeSnapshot(raw);
  if (!snapshot.developers[0]?.regions[0]) throw new Error('API не вернул профиль застройщика.');
  return { snapshot, catalog: normalizeCatalog(raw) };
}
