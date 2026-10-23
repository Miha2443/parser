import { useEffect, useRef, useState } from 'react';
import { ApiError, request } from './data';
import { preferentialSelection, preferentialUrl, readPreferentialCatalog, readPreferentialReport } from './preferentialMortgageData';
import type { PreferentialCatalog, PreferentialReport } from './preferentialMortgageData';

const message = (error: unknown) => error instanceof Error ? error.message : 'Не удалось загрузить льготную ипотеку.';
export function usePreferentialMortgageData(params: URLSearchParams) {
  const [catalog, setCatalog] = useState<PreferentialCatalog | null>(null), [catalogError, setCatalogError] = useState(''), [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ key: string; data?: PreferentialReport; error?: string } | null>(null), recovered = useRef(new Set<string>());
  useEffect(() => {
    const controller = new AbortController();
    request('/api/v1/preferential-mortgage/catalog', controller.signal).then(response => response.json()).then(readPreferentialCatalog).then(value => { if (!controller.signal.aborted) setCatalog(value); }).catch(error => { if (!controller.signal.aborted) setCatalogError(message(error)); });
    return () => controller.abort();
  }, [attempt]);
  const selection = preferentialSelection(catalog, params), key = JSON.stringify([catalog?.version, selection, attempt]);
  useEffect(() => {
    if (!catalog || !selection) return;
    const controller = new AbortController();
    const recover = async () => {
      const recovery = JSON.stringify([catalog.version, selection]);
      if (recovered.current.has(recovery)) throw new Error('Источник изменился. Повторите загрузку льготной ипотеки.');
      recovered.current.add(recovery);
      const next = readPreferentialCatalog(await (await request('/api/v1/preferential-mortgage/catalog', controller.signal)).json());
      if (next.version === catalog.version) throw new Error('Выбранные данные недоступны. Повторите загрузку.');
      if (!controller.signal.aborted) setCatalog(next);
    };
    request(preferentialUrl(selection), controller.signal).then(response => response.json()).then(raw => readPreferentialReport(raw, selection)).then(async data => {
      if (controller.signal.aborted) return;
      if (data.version !== catalog.version) await recover(); else setResult({ key, data });
    }).catch(async error => {
      if (controller.signal.aborted) return;
      if (error instanceof ApiError && error.status === 404) { try { await recover(); } catch (reason) { if (!controller.signal.aborted) setResult({ key, error: message(reason) }); } }
      else setResult({ key, error: message(error) });
    });
    return () => controller.abort();
  }, [key]);
  return { catalog, selection, data: result?.key === key ? result.data ?? null : null, error: catalogError || (result?.key === key ? result.error ?? '' : ''), retry: () => { recovered.current.clear(); setCatalog(null); setCatalogError(''); setResult(null); setAttempt(value => value + 1); } };
}
