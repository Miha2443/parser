import { useEffect, useRef, useState } from 'react';
import { ApiError, request } from './data';
import { mortgageSelection, mortgageUrl, readMortgageCatalog, readMortgageReport } from './mortgageData';
import type { MortgageCatalog, MortgageReport } from './mortgageData';

const message = (error: unknown) => error instanceof Error ? error.message : 'Не удалось загрузить ипотечные показатели.';
export function useMortgageData(params: URLSearchParams) {
  const [catalog, setCatalog] = useState<MortgageCatalog | null>(null), [catalogError, setCatalogError] = useState(''), [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ key: string; data?: MortgageReport; error?: string } | null>(null);
  const recovered = useRef(new Set<string>());
  useEffect(() => {
    const controller = new AbortController();
    request('/api/v1/mortgage/catalog', controller.signal).then(response => response.json()).then(readMortgageCatalog).then(value => { if (!controller.signal.aborted) setCatalog(value); }).catch(error => { if (!controller.signal.aborted) setCatalogError(message(error)); });
    return () => controller.abort();
  }, [attempt]);
  const selection = mortgageSelection(catalog, params), key = JSON.stringify([catalog?.version, selection, attempt]);
  useEffect(() => {
    if (!selection || !catalog) return;
    const controller = new AbortController();
    const recover = async (reason: string) => {
      const recovery = JSON.stringify([reason, catalog.version, selection]);
      if (recovered.current.has(recovery)) throw new Error('Каталог ипотеки изменился или данные недоступны. Повторите загрузку.');
      recovered.current.add(recovery);
      const next = readMortgageCatalog(await (await request('/api/v1/mortgage/catalog', controller.signal)).json());
      if (next.version === catalog.version) throw new Error('Источник ипотеки изменился или не содержит выбранного периода. Повторите загрузку.');
      if (!controller.signal.aborted) setCatalog(next);
    };
    request(mortgageUrl(selection), controller.signal).then(response => response.json()).then(raw => readMortgageReport(raw, selection)).then(async data => {
      if (controller.signal.aborted) return;
      if (data.version !== catalog.version) await recover('version');
      else setResult({ key, data });
    }).catch(async error => {
      if (controller.signal.aborted) return;
      if (error instanceof ApiError && error.status === 404) {
        try { await recover('404'); } catch (reason) { if (!controller.signal.aborted) setResult({ key, error: message(reason) }); }
      } else setResult({ key, error: message(error) });
    });
    return () => controller.abort();
  }, [key]);
  return { catalog, selection, data: result?.key === key ? result.data ?? null : null, error: catalogError || (result?.key === key ? result.error ?? '' : ''), retry: () => { recovered.current.clear(); setCatalog(null); setCatalogError(''); setResult(null); setAttempt(value => value + 1); } };
}
