import { useEffect, useRef, useState } from 'react';
import { ApiError } from './data';
import { linearFilters, loadCommissioningCatalog, loadCommissioningReport, operationalFilters } from './commissioningData';
import type { CommissioningCatalog, CommissioningKind, CommissioningReport, CommissioningSelection, LinearCatalog, LinearReport, LinearSelection, OperationalCatalog, OperationalReport, OperationalSelection } from './commissioningData';

type LoadState<C, R, S> = { catalog: C | null; data: R | null; selection: S | null; error: string; retry: () => void };
const message = (e: unknown) => e instanceof Error ? e.message : 'Не удалось прочитать данные ввода недвижимости.';
export function useCommissioningData(kind: 'operational', params: URLSearchParams): LoadState<OperationalCatalog, OperationalReport, OperationalSelection>;
export function useCommissioningData(kind: 'linear', params: URLSearchParams): LoadState<LinearCatalog, LinearReport, LinearSelection>;
export function useCommissioningData(kind: CommissioningKind, params: URLSearchParams): LoadState<CommissioningCatalog, CommissioningReport, CommissioningSelection> {
  const [catalog, setCatalog] = useState<CommissioningCatalog | null>(null), [catalogError, setCatalogError] = useState(''), [attempt, setAttempt] = useState(0);
  const refreshed = useRef(new Set<string>());
  const [result, setResult] = useState<{ key: string; data?: CommissioningReport; error?: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    loadCommissioningCatalog(kind, controller.signal).then(c => { if (!controller.signal.aborted) setCatalog(c); }).catch(e => { if (!controller.signal.aborted) setCatalogError(message(e)); });
    return () => controller.abort();
  }, [kind, attempt]);
  const selection = kind === 'operational' ? operationalFilters(catalog as OperationalCatalog | null, params) : linearFilters(catalog as LinearCatalog | null, params);
  const filterKey = JSON.stringify(selection), key = JSON.stringify([kind, catalog?.version, selection]);
  useEffect(() => {
    if (!catalog || !selection) return;
    const controller = new AbortController(); setResult(null);
    const recoveryKey = (reason: string) => JSON.stringify([reason, catalog.version, selection]);
    const recover = async (reason: string) => {
      const recovery = recoveryKey(reason);
      if (refreshed.current.has(recovery)) throw new Error('Каталог и данные ввода недвижимости изменились или недоступны. Повторите загрузку.');
      refreshed.current.add(recovery);
      const next = await loadCommissioningCatalog(kind, controller.signal);
      if (!controller.signal.aborted) setCatalog(next);
    };
    loadCommissioningReport(kind, selection, controller.signal).then(async data => {
      if (controller.signal.aborted) return;
      if (data.version !== catalog.version) await recover('version');
      else setResult({ key, data });
    }).catch(async e => {
      if (controller.signal.aborted) return;
      if (e instanceof ApiError && e.status === 404 && !refreshed.current.has(recoveryKey('404'))) {
        try { await recover('404'); } catch (error) { if (!controller.signal.aborted) setResult({ key, error: message(error) }); }
      } else setResult({ key, error: message(e) });
    });
    return () => controller.abort();
  }, [kind, catalog, filterKey, key]);
  return { catalog, selection, data: result?.key === key ? result.data ?? null : null, error: catalogError || (result?.key === key ? result.error ?? '' : ''), retry: () => { refreshed.current.clear(); setCatalogError(''); setCatalog(null); setResult(null); setAttempt(n => n + 1); } };
}
