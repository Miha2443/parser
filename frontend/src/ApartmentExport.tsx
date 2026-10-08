import { useEffect, useRef, useState } from 'react';
import { FileSpreadsheet, RefreshCw } from 'lucide-react';
import { ApiError, request } from './data';
import { apartmentUrl } from './apartmentData';
import type { ApartmentRegion } from './types';

export default function ApartmentExport({ region, developer, version }: { region: ApartmentRegion; developer?: string; version: string }) {
  const active = useRef<AbortController>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { setBusy(false); setError(''); return () => active.current?.abort(); }, [region, developer, version]);
  async function download() {
    active.current?.abort(); const controller = new AbortController(); active.current = controller;
    setBusy(true); setError('');
    try {
      const blob = await (await request(apartmentUrl(region, developer, version), controller.signal)).blob();
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = `apartments-${region}${developer === undefined ? '' : `-${developer.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_')}`}.xlsx`;
      document.body.append(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof ApiError && e.status === 409 ? 'Данные изменились. Перезагрузите страницу, затем скачайте Excel.' : `${e instanceof Error ? e.message : 'Не удалось скачать Excel.'} Повторите скачивание.`);
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <div className="profile-export apartment-export"><button className="icon-button" title="Скачать полную квартирографию Excel" aria-label="Скачать полную квартирографию Excel" disabled={busy} onClick={download}>{busy ? <RefreshCw size={17} className="loading-icon" /> : <FileSpreadsheet size={17} />}</button>{error && <span role="alert" className="warning-text">{error}</span>}</div>;
}
