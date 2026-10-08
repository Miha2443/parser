import { useEffect, useRef, useState } from 'react';
import { ApiError } from './data';
import { annualFilters, constructionFilters, loadMarketCatalog, loadMarketReport } from './annualConstructionData';
import type { AnnualCatalog, AnnualReport, AnnualSelection, ConstructionCatalog, ConstructionReport, ConstructionSelection, MarketCatalog, MarketKind, MarketReport, MarketSelection } from './annualConstructionData';

type State<C, R, S> = { catalog: C | null; data: R | null; selection: S | null; error: string; retry: () => void };
const message = (e: unknown) => e instanceof Error ? e.message : 'Не удалось загрузить отчёт.';
export function useAnnualConstructionData(kind: 'annual', params: URLSearchParams): State<AnnualCatalog, AnnualReport, AnnualSelection>;
export function useAnnualConstructionData(kind: 'construction', params: URLSearchParams): State<ConstructionCatalog, ConstructionReport, ConstructionSelection>;
export function useAnnualConstructionData(kind: MarketKind, params: URLSearchParams): State<MarketCatalog, MarketReport, MarketSelection> {
  const [catalog, setCatalog] = useState<MarketCatalog | null>(null), [catalogError, setCatalogError] = useState(''), [attempt, setAttempt] = useState(0), [result, setResult] = useState<{ key: string; data?: MarketReport; error?: string } | null>(null);
  const refreshed = useRef(new Set<string>());
  useEffect(() => { const c = new AbortController(); loadMarketCatalog(kind, c.signal).then(v => { if (!c.signal.aborted) setCatalog(v); }).catch(e => { if (!c.signal.aborted) setCatalogError(message(e)); }); return () => c.abort(); }, [kind, attempt]);
  const selection = kind === 'annual' ? annualFilters(catalog, params) : constructionFilters(catalog as ConstructionCatalog | null, params), filterKey = JSON.stringify(selection), key = JSON.stringify([kind, catalog?.version, selection]);
  useEffect(() => {
    if (!catalog || !selection) return;
    const c = new AbortController(); setResult(null);
    const recoveryKey = (reason: string) => JSON.stringify([reason, catalog.version, selection]);
    const recover = async (reason: string) => { const k = recoveryKey(reason); if (refreshed.current.has(k)) throw new Error('Каталог и отчёт изменились или недоступны. Повторите загрузку.'); refreshed.current.add(k); const next = await loadMarketCatalog(kind, c.signal); if (!c.signal.aborted) setCatalog(next); };
    loadMarketReport(kind, selection, c.signal).then(async data => { if (c.signal.aborted) return; if (data.version !== catalog.version) await recover('version'); else setResult({ key, data }); }).catch(async e => { if (c.signal.aborted) return; if (e instanceof ApiError && e.status === 404 && !refreshed.current.has(recoveryKey('404'))) { try { await recover('404'); } catch (error) { if (!c.signal.aborted) setResult({ key, error: message(error) }); } } else setResult({ key, error: message(e) }); });
    return () => c.abort();
  }, [kind, catalog, filterKey, key]);
  return { catalog, selection, data: result?.key === key ? result.data ?? null : null, error: catalogError || (result?.key === key ? result.error ?? '' : ''), retry: () => { refreshed.current.clear(); setCatalog(null); setCatalogError(''); setResult(null); setAttempt(v => v + 1); } };
}
