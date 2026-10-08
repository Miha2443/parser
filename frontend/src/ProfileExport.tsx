import { useEffect, useRef, useState } from 'react';
import { FileSpreadsheet, RefreshCw } from 'lucide-react';
import { ApiError, profileUrl, request } from './data';
import type { ApartmentRegion } from './types';

export default function ProfileExport({ developer, region, version, frozen }: { developer: string; region: ApartmentRegion; version: string | null; frozen: boolean }) {
  const active = useRef<AbortController>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    setBusy(false); setError('');
    return () => active.current?.abort();
  }, [developer, region, version]);
  async function download() {
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setError('');
    try {
      if (!version) throw new Error('Версия профиля отсутствует. Перезагрузите страницу.');
      const response = await request(profileUrl(developer, region, true, version), controller.signal);
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a'); link.href = url;
      link.download = `${developer.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_')}-${region}-profile.xlsx`;
      document.body.append(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      if (!controller.signal.aborted) setError(error instanceof ApiError && error.status === 409 ? 'Данные изменились после загрузки профиля. Перезагрузите страницу, затем скачайте Excel.' : `${error instanceof Error ? error.message : 'Не удалось скачать Excel.'} Повторите скачивание.`);
    }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  const label = frozen ? 'Excel доступен только в режиме API' : busy ? 'Загрузка полного профиля Excel' : 'Скачать полный профиль Excel';
  return <div className="profile-export"><button className="icon-button" title={label} aria-label={label} disabled={frozen || busy} onClick={download}>{busy ? <RefreshCw size={17} className="loading-icon" /> : <FileSpreadsheet size={17} />}</button>{error && <span role="alert" className="warning-text">{error}</span>}</div>;
}
