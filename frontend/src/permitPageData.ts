import { ApiError, request } from './data.ts';
import { permitsUrl, readPermitsData, reconcilePermitFilters } from './constructionPagesData.ts';
import type { PermitFilters, PermitsData } from './constructionPagesData';

export type PermitAreaVariant = Pick<PermitsData, 'permits' | 'forecast'>;
export type PermitPageData = PermitsData & { areaVariants: { living: PermitAreaVariant; full: PermitAreaVariant } };
export const permitLivingOnly = (params: URLSearchParams) => params.get('permit_living_only') !== '0';

export function readPermitPageData(value: unknown, expected?: PermitFilters): PermitPageData {
  const base = readPermitsData(value, expected);
  const raw = value as Record<string, unknown>, variants = raw.areaVariants;
  if (!variants || typeof variants !== 'object' || Array.isArray(variants)) throw new Error('В ответе API нет подтверждённых вариантов жилой и общей площади. Обновите backend.');
  function variant(key: 'living' | 'full'): PermitAreaVariant {
    const row = (variants as Record<string, unknown>)[key];
    if (!row || typeof row !== 'object' || Array.isArray(row)) throw new Error('Некорректный вариант площади разрешений.');
    const fields = row as Record<string, unknown>;
    const parsed = readPermitsData({ ...raw, permits: fields.permits, forecast: fields.forecast }, base.selection);
    return { permits: parsed.permits, forecast: parsed.forecast };
  }
  return { ...base, areaVariants: { living: variant('living'), full: variant('full') } };
}

export function selectPermitArea(data: PermitPageData, livingOnly = true): PermitsData {
  return data.selection.permitKind === 'nonresidential' ? data : { ...data, ...data.areaVariants[livingOnly ? 'living' : 'full'] };
}

export async function loadPermitPageSelection(filters: PermitFilters, signal: AbortSignal): Promise<PermitPageData> {
  const load = async (selection: PermitFilters) => readPermitPageData(await (await request(permitsUrl(selection), signal)).json(), selection);
  try { return await load(filters); }
  catch (error) {
    if (signal.aborted || !(error instanceof ApiError) || error.status !== 404 || filters.month === undefined && !filters.structure) throw error;
    const discovery = await load({ permitKind: filters.permitKind });
    if (signal.aborted) throw error;
    const canonical = reconcilePermitFilters(filters, discovery);
    return permitsUrl(canonical) === permitsUrl(discovery.selection) ? discovery : load(canonical);
  }
}
