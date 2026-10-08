export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
export type RawSnapshot = { [key: string]: JsonValue };
export type Theme = 'light' | 'dark';
export type CategoryKey = 'housing' | 'common' | 'nonresidentialEmbedded' | 'nonresidentialSeparate';
export type Areas = Partial<Record<CategoryKey, number | null>>;
export interface Source {
  id: string;
  label: string;
  date: string | null;
  files: string[];
  note?: string;
}
export interface AnnualRow extends Areas { year: number }
export interface Structure { title: string; areas: Areas; sourceId: string; note?: string }
export interface Rating { label: string; region: string; place: number | null; score?: number | null; note?: string }
export interface Apartment { label: string; count: number | null; area: number | null; share: number | null }
export interface Sales { sold: number | null; readiness: number | null; ratio: number | null; period: string | null; note?: string }
export interface Delay { year: number; completed: number | null; delayed: number | null; clarification: number | null }
export interface DelayMetric { label: string; value: number | null; base?: number | null; note?: string; displayValue?: string; displayBase?: string; displayPercent?: string; region?: string }
export interface Escrow { credit: number | null; debt: number | null; revenue: number | null; coverage: number | null; debtShare: number | null; note?: string }
export interface RegionProfile {
  id: string;
  label: string;
  sourceContributions: string[];
  annual: AnnualRow[];
  structures: Structure[];
  construction: Areas | null;
  ratings: Rating[];
  apartments: Apartment[];
  apartmentNote?: string;
  sales: Sales | null;
  salesByRegion: { label: string; data: Sales | null }[];
  delays: Delay[];
  delayMetrics: DelayMetric[];
  escrow: Escrow | null;
  notes: string[];
  geography?: { title: string; moscow: number | null; others: number | null; note?: string }[];
  apartmentSummary: { count: number | null; area: number | null; average: number | null; marketShare: number | null };
  objects: { title: string; columns: string[]; rows: Record<string, JsonValue>[]; note: string }[];
}
export interface Developer { id: string; name: string; regions: RegionProfile[] }
export interface Snapshot { generatedAt: string | null; version: string | null; frozen: boolean; sources: Source[]; developers: Developer[]; notes: string[] }
export type ApartmentRegion = 'msk' | 'rf';
export interface CatalogDeveloper { id: string; name: string; regions: ApartmentRegion[] }
export interface Catalog { metadata: Snapshot; developers: CatalogDeveloper[]; demo?: Snapshot }
