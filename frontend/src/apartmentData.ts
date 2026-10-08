import { request } from './data.ts';
import type { ApartmentRegion } from './types.ts';

export const roomTypes = ['1 комн', '2 комн', '3 комн', '4+ комн'] as const;
export const roomColors = ['#2c9869', '#398dcc', '#d6a23f', '#cf5d64'];
export type Room = { type: typeof roomTypes[number]; sharePercent: number | null };
export type ApartmentRow = { id: string; name: string; apartmentThousandCount: number | null; areaThousandM2: number | null; rooms: Room[]; place?: number | null };
export type ApartmentMetadata = { schemaVersion: 1; version: string; generatedAt: string; reportDate: string | null; source: { date: string | null; files: string[]; issues: [string, string][] } };
export type ApartmentCatalog = ApartmentMetadata & { regions: { id: ApartmentRegion; label: string }[]; developersByRegion: Partial<Record<ApartmentRegion, { id: string; name: string; place: number | null }[]>> };
export type ApartmentOverview = ApartmentMetadata & { region: ApartmentRegion; apartments: { type: string; count: number | null; areaThousandM2: number | null }[]; distribution: { range: string; sharePercent: number | null }[]; developers: ApartmentRow[]; regions: ApartmentRow[]; developerCount: number; regionCount: number };
export type ApartmentDetail = ApartmentMetadata & { region: ApartmentRegion; developer: ApartmentRow; summary: { countThousand: number | null; areaThousandM2: number | null; averageAreaM2: number | null; marketSharePercent: number | null; marketBaseAreaThousandM2: number | null; place: number | null; totalDevelopers: number }; referenceAverages: { region: string; averageAreaM2: number | null }[]; rooms: Room[]; comparison: ApartmentRow[] };

type Obj = Record<string, unknown>;
const invalid = () => new Error('Ответ квартирографии API не соответствует схеме версии 1.');
function obj(v: unknown): Obj { if (!v || typeof v !== 'object' || Array.isArray(v)) throw invalid(); return v as Obj; }
function str(v: unknown): string { if (typeof v !== 'string') throw invalid(); return v; }
function num(v: unknown): number | null { if (v === null) return null; if (typeof v !== 'number' || !Number.isFinite(v)) throw invalid(); return v; }
function nullableStr(v: unknown): string | null { return v === null ? null : str(v); }
function list<T>(v: unknown, read: (v: unknown) => T): T[] { if (!Array.isArray(v)) throw invalid(); return v.map(read); }
function region(v: unknown): ApartmentRegion { if (v !== 'msk' && v !== 'rf') throw invalid(); return v; }
function count(v: unknown): number { const n = num(v); if (n === null || n < 0 || !Number.isInteger(n)) throw invalid(); return n; }
function metadata(v: Obj): ApartmentMetadata {
  if (v.schemaVersion !== 1 || !str(v.version) || !Number.isFinite(Date.parse(str(v.generatedAt)))) throw invalid();
  const s = obj(v.source);
  return { schemaVersion: 1, version: str(v.version), generatedAt: str(v.generatedAt), reportDate: nullableStr(v.reportDate), source: { date: nullableStr(s.date), files: list(s.files, str), issues: list(s.issues, value => { if (!Array.isArray(value) || value.length !== 2) throw invalid(); return [str(value[0]), str(value[1])]; }) } };
}
function rooms(v: unknown): Room[] {
  const rows = list(v, value => { const r = obj(value); if (!roomTypes.includes(r.type as Room['type'])) throw invalid(); return { type: r.type as Room['type'], sharePercent: num(r.sharePercent) }; });
  if (new Set(rows.map(r => r.type)).size !== rows.length) throw invalid();
  return rows;
}
function row(v: unknown): ApartmentRow {
  const r = obj(v);
  return { id: str(r.id), name: str(r.name), apartmentThousandCount: num(r.apartmentThousandCount), areaThousandM2: num(r.areaThousandM2), rooms: rooms(r.rooms), ...(r.place === undefined ? {} : { place: num(r.place) }) };
}
export function readApartmentCatalog(value: unknown): ApartmentCatalog {
  const v = obj(value), d = obj(v.developersByRegion);
  const regions = list(v.regions, value => { const r = obj(value); return { id: region(r.id), label: str(r.label) }; });
  if (!regions.length || new Set(regions.map(r => r.id)).size !== regions.length) throw invalid();
  const developersByRegion: ApartmentCatalog['developersByRegion'] = {};
  for (const r of regions) {
    const seen = new Set<string>();
    developersByRegion[r.id] = list(d[r.id], value => { const item = obj(value); return { id: str(item.id), name: str(item.name), place: num(item.place) }; }).filter(item => {
      if (seen.has(item.id)) return false;
      seen.add(item.id); return true;
    });
  }
  return { ...metadata(v), regions, developersByRegion };
}
export function readApartmentOverview(value: unknown, selected: ApartmentRegion): ApartmentOverview {
  const v = obj(value);
  if (region(v.region) !== selected) throw invalid();
  return { ...metadata(v), region: selected, apartments: list(v.apartments, value => { const r = obj(value); return { type: str(r.type), count: num(r.count), areaThousandM2: num(r.areaThousandM2) }; }), distribution: list(v.distribution, value => { const r = obj(value); return { range: str(r.range), sharePercent: num(r.sharePercent) }; }), developers: list(v.developers, row), regions: list(v.regions, row), developerCount: count(v.developerCount), regionCount: count(v.regionCount) };
}
export function readApartmentDetail(value: unknown, selected: ApartmentRegion, developer: string): ApartmentDetail {
  const v = obj(value), s = obj(v.summary), dev = row(v.developer);
  if (region(v.region) !== selected || dev.id !== developer) throw invalid();
  return { ...metadata(v), region: selected, developer: dev, summary: { countThousand: num(s.countThousand), areaThousandM2: num(s.areaThousandM2), averageAreaM2: num(s.averageAreaM2), marketSharePercent: num(s.marketSharePercent), marketBaseAreaThousandM2: num(s.marketBaseAreaThousandM2), place: num(s.place), totalDevelopers: count(s.totalDevelopers) }, referenceAverages: list(v.referenceAverages, value => { const r = obj(value); return { region: str(r.region), averageAreaM2: num(r.averageAreaM2) }; }), rooms: rooms(v.rooms), comparison: list(v.comparison, row) };
}
export function apartmentFilters(catalog: ApartmentCatalog | null, params: URLSearchParams) {
  const selectedRegion = catalog?.regions.find(r => r.id === params.get('region')) ?? catalog?.regions.find(r => r.id === 'msk') ?? catalog?.regions[0];
  const developers = selectedRegion ? catalog?.developersByRegion[selectedRegion.id] ?? [] : [];
  return { region: selectedRegion, developers, developer: developers.find(d => d.id === params.get('developer')) ?? developers[0] };
}
export function apartmentUrl(selected: ApartmentRegion, developer?: string, version?: string) {
  const params = new URLSearchParams({ region: selected });
  if (developer !== undefined) params.set('developer', developer);
  if (version !== undefined) params.set('required_version', version);
  return `/api/v1/apartments${version !== undefined ? '/export' : developer !== undefined ? '/developer' : ''}?${params}`;
}
export async function loadApartmentCatalog(signal: AbortSignal) { return readApartmentCatalog(await (await request('/api/v1/apartments/catalog', signal)).json()); }
export async function loadApartmentData(selected: ApartmentRegion, developer: string | undefined, signal: AbortSignal) {
  const value: unknown = await (await request(apartmentUrl(selected, developer), signal)).json();
  return developer === undefined ? readApartmentOverview(value, selected) : readApartmentDetail(value, selected, developer);
}
export function roomWidths(values: Room[]) {
  const total = values.reduce((sum, r) => sum + Math.max(0, r.sharePercent ?? 0), 0);
  return values.map(r => ({ ...r, width: total > 0 ? Math.max(0, r.sharePercent ?? 0) / total * 100 : 0 }));
}
export function tablePage(rows: ApartmentRow[], query: string, page: string | null, size: string | null) {
  const pageSize = [20, 50, 100].includes(Number(size)) ? Number(size) : 50;
  const filtered = rows.filter(r => r.name.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru')));
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const current = Math.min(pages, Math.max(1, Number.isSafeInteger(Number(page)) ? Number(page) : 1));
  return { pageSize, pages, current, total: filtered.length, shown: filtered.slice((current - 1) * pageSize, current * pageSize) };
}
export function apartmentRows(rows: ApartmentRow[]): (string | number | null)[][] {
  return [['Место', 'Наименование', 'Квартиры, тыс. шт.', 'Площадь, тыс. м²', ...roomTypes.map(type => `${type}, %`)], ...rows.map(r => [r.place ?? null, r.name, r.apartmentThousandCount, r.areaThousandM2, ...roomTypes.map(type => r.rooms.find(room => room.type === type)?.sharePercent ?? null)])];
}
