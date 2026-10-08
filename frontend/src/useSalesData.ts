import { useEffect, useRef, useState } from 'react';
import { ApiError } from './data';
import { loadSalesCatalog, loadSalesData, salesFilters } from './salesData';
import type { SalesCatalog, SalesData } from './salesData';

const message = (e: unknown) => e instanceof Error ? e.message : 'Не удалось прочитать распроданность.';
export function useSalesData(params: URLSearchParams) {
  const [catalog, setCatalog] = useState<SalesCatalog | null>(null);
  const [catalogError, setCatalogError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const refreshed = useRef(new Set<string>());
  const [result, setResult] = useState<{ key: string; data?: SalesData; error?: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    loadSalesCatalog(controller.signal).then(c => { if (!controller.signal.aborted) setCatalog(c); }).catch(e => { if (!controller.signal.aborted) setCatalogError(message(e)); });
    return () => controller.abort();
  }, [attempt]);
  const filters = salesFilters(catalog, params);
  const key = JSON.stringify([catalog?.version, filters.region?.id, filters.period?.id]);
  useEffect(() => {
    if (!catalog || !filters.region || !filters.period) return;
    const controller = new AbortController(), region = filters.region.id, period = filters.period.id;
    setResult(null);
    const recover = async (reason: string) => {
      const recoveryKey = `${reason}:${catalog.version}:${region}:${period}`;
      if (refreshed.current.has(recoveryKey)) throw new Error('Каталог и данные распроданности изменились или недоступны. Повторите загрузку.');
      refreshed.current.add(recoveryKey);
      const c = await loadSalesCatalog(controller.signal);
      if (!controller.signal.aborted) setCatalog(c);
    };
    loadSalesData(region, period, controller.signal).then(async data => {
      if (controller.signal.aborted) return;
      if (data.version !== catalog.version) await recover('version');
      else setResult({ key, data });
    }).catch(async e => {
      if (controller.signal.aborted) return;
      if (e instanceof ApiError && e.status === 404 && !refreshed.current.has(`404:${catalog.version}:${region}:${period}`)) {
        try { await recover('404'); } catch (error) { if (!controller.signal.aborted) setResult({ key, error: message(error) }); }
      } else setResult({ key, error: message(e) });
    });
    return () => controller.abort();
  }, [catalog, filters.region?.id, filters.period?.id, key]);
  return { catalog, ...filters, data: result?.key === key ? result.data ?? null : null, error: catalogError || (result?.key === key ? result.error ?? '' : ''), retry: () => { refreshed.current.clear(); setCatalogError(''); setResult(null); setCatalog(null); setAttempt(n => n + 1); } };
}
