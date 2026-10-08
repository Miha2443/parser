import { useEffect, useRef, useState } from 'react';
import { economicsFilterKey, economicsFilters, loadEconomicsPage } from './economicsData';
import type { EconomicsCatalog, EconomicsFamily, EconomicsReport, EconomicsSelection } from './economicsData';

type State = { key: string; catalog?: EconomicsCatalog; selection?: EconomicsSelection; data?: EconomicsReport; error?: string };
export function useEconomicsData(family: EconomicsFamily, params: URLSearchParams) {
  const key = economicsFilterKey(family, params);
  const catalogs = useRef<Partial<Record<EconomicsFamily, EconomicsCatalog>>>({});
  const [attempt, setAttempt] = useState(0), [state, setState] = useState<State | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setState({ key });
    loadEconomicsPage(family, params, controller.signal, catalogs.current[family], (catalog, selection) => {
      if (!controller.signal.aborted) { catalogs.current[family] = catalog; setState({ key, catalog, selection }); }
    }).then(result => { if (!controller.signal.aborted) setState({ key, ...result }); }).catch(error => {
      if (!controller.signal.aborted) setState(previous => ({ ...previous, key, error: error instanceof Error ? error.message : 'Не удалось загрузить экономические показатели.' }));
    });
    return () => controller.abort();
  }, [family, key, attempt]);
  const active = state?.key === key ? state : null;
  const catalog = active?.catalog ?? catalogs.current[family] ?? null;
  return { catalog, selection: active?.selection ?? (catalog ? economicsFilters(catalog, params) : null), data: active?.data ?? null,
    error: active?.error ?? '', retry: () => { delete catalogs.current[family]; setState(null); setAttempt(n => n + 1); } };
}
