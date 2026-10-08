import { useEffect, useRef, useState } from 'react';
import { ApiError } from './data';
import { apartmentFilters, loadApartmentCatalog, loadApartmentData } from './apartmentData';
import type { ApartmentCatalog, ApartmentDetail, ApartmentOverview } from './apartmentData';

const message = (e: unknown) => e instanceof Error ? e.message : 'Не удалось прочитать квартирографию.';
export function useApartmentData(params: URLSearchParams, detail: boolean) {
  const [catalog, setCatalog] = useState<ApartmentCatalog | null>(null);
  const [catalogError, setCatalogError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const refreshed = useRef(new Set<string>());
  const [result, setResult] = useState<{ key: string; data?: ApartmentOverview | ApartmentDetail; error?: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    loadApartmentCatalog(controller.signal).then(c => { if (!controller.signal.aborted) setCatalog(c); }).catch(e => { if (!controller.signal.aborted) setCatalogError(message(e)); });
    return () => controller.abort();
  }, [attempt]);
  const filters = apartmentFilters(catalog, params);
  const selected = detail ? filters.developer?.id : undefined;
  const key = JSON.stringify([catalog?.version, filters.region?.id, selected]);
  useEffect(() => {
    if (!catalog || !filters.region || (detail && selected === undefined)) return;
    const controller = new AbortController();
    const region = filters.region.id;
    setResult(null);
    const recover = async (reason: string) => {
      const recoveryKey = reason === 'version' ? `${reason}:${catalog.version}:${region}:${selected ?? ''}` : `${reason}:${region}:${selected ?? ''}`;
      if (refreshed.current.has(recoveryKey)) throw new Error('Каталог и данные квартирографии изменились или недоступны. Повторите загрузку.');
      refreshed.current.add(recoveryKey);
      const c = await loadApartmentCatalog(controller.signal);
      if (!controller.signal.aborted) setCatalog(c);
    };
    loadApartmentData(region, selected, controller.signal).then(async data => {
      if (controller.signal.aborted) return;
      if (data.version !== catalog.version) await recover('version');
      else setResult({ key, data });
    }).catch(async e => {
      if (controller.signal.aborted) return;
      if (e instanceof ApiError && e.status === 404 && !refreshed.current.has(`404:${region}:${selected ?? ''}`)) {
        try { await recover('404'); } catch (error) { if (!controller.signal.aborted) setResult({ key, error: message(error) }); }
      } else setResult({ key, error: message(e) });
    });
    return () => controller.abort();
  }, [catalog, filters.region?.id, selected, detail, key]);
  return { catalog, ...filters, data: result?.key === key ? result.data ?? null : null, error: catalogError || (result?.key === key ? result.error ?? '' : ''), retry: () => { refreshed.current.clear(); setCatalogError(''); setResult(null); setCatalog(null); setAttempt(n => n + 1); } };
}
