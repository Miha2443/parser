import { useEffect, useRef, useState } from 'react';
import { ApiError, loadCatalog, loadProfile, selectFilters } from './data';
import type { Catalog, Snapshot } from './types';

export const dataMode = import.meta.env.VITE_DATA_MODE === 'snapshot' ? 'snapshot' : 'api';
const message = (error: unknown) => error instanceof Error ? error.message : 'Не удалось прочитать данные сервера.';

export function useDashboardData(params: URLSearchParams) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogError, setCatalogError] = useState('');
  const [catalogAttempt, setCatalogAttempt] = useState(0);
  const refreshed404 = useRef(new Set<string>());
  const [result, setResult] = useState<{ key: string; snapshot?: Snapshot; error?: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setCatalogError('');
    loadCatalog(dataMode, controller.signal).then(data => {
      if (!controller.signal.aborted) setCatalog(data);
    }).catch(error => { if (!controller.signal.aborted) setCatalogError(message(error)); });
    return () => controller.abort();
  }, [catalogAttempt]);
  const { developer, region } = selectFilters(catalog, params);
  const key = developer && region ? JSON.stringify([developer.id, region]) : '';
  useEffect(() => {
    if (!catalog || !developer || !region) return;
    const controller = new AbortController();
    setResult(null);
    loadProfile(catalog, developer.id, region, controller.signal).then(({ snapshot, catalog: nextCatalog }) => {
      if (controller.signal.aborted) return;
      setResult({ key, snapshot });
      if (nextCatalog.metadata.version !== catalog.metadata.version) setCatalog(nextCatalog);
    }).catch(async error => {
      if (controller.signal.aborted) return;
      // One automatic refresh per filter pair; a persistent 404 becomes visible.
      if (error instanceof ApiError && error.status === 404 && !refreshed404.current.has(key)) {
        refreshed404.current.add(key);
        try {
          const nextCatalog = await loadCatalog(dataMode, controller.signal);
          if (!controller.signal.aborted) { setResult(null); setCatalog(nextCatalog); }
        } catch (refreshError) { if (!controller.signal.aborted) setCatalogError(message(refreshError)); }
      } else setResult({ key, error: message(error) });
    });
    return () => controller.abort();
  }, [catalog, developer?.id, region, key]);
  // Hide the previous response during the render before request cleanup runs.
  const snapshot = result?.key === key ? result.snapshot ?? null : null;
  const profile = snapshot?.developers.find(d => d.id === developer?.id)?.regions.find(r => r.id === region);
  return {
    catalog, developer, region, snapshot, profile,
    error: catalogError || (result?.key === key ? result.error ?? '' : ''),
    retry: () => {
      refreshed404.current.clear(); setCatalogError(''); setResult(null); setCatalog(null);
      setCatalogAttempt(n => n + 1);
    },
  };
}
