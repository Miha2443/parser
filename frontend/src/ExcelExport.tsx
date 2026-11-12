import { useEffect, useRef, useState } from 'react';
import { FileSpreadsheet, RefreshCw } from 'lucide-react';
import { useAccess } from './auth';
import { download } from './format';
import { excelBlob, excelFilename } from './excelTable';
import type { ExcelCell } from './excelTable';

export function ExcelExportButton({ rows, filename, label = 'Скачать Excel', text = false, disabled = false, className }: {
  rows: ExcelCell[][]; filename: string; label?: string; text?: boolean; disabled?: boolean; className?: string;
}) {
  const { canAnalyze, user } = useAccess(), allowed = useRef(canAnalyze);
  allowed.current = canAnalyze;
  const active = useRef<AbortController>();
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  useEffect(() => {
    active.current?.abort(); setBusy(false); setError('');
    return () => active.current?.abort();
  }, [rows, filename, canAnalyze, user?.username]);
  async function save() {
    if (!allowed.current || disabled || busy) return;
    const controller = new AbortController(); active.current?.abort(); active.current = controller;
    setBusy(true); setError('');
    try {
      const blob = await excelBlob(rows, controller.signal);
      if (controller.signal.aborted || !allowed.current) return;
      const url = URL.createObjectURL(blob);
      try { download(url, excelFilename(filename)); }
      finally { window.setTimeout(() => URL.revokeObjectURL(url), 1000); }
    } catch (reason) {
      if (!controller.signal.aborted && allowed.current) setError(reason instanceof Error ? reason.message : 'Не удалось скачать Excel. Повторите попытку.');
    } finally {
      if (active.current === controller && !controller.signal.aborted) { active.current = undefined; setBusy(false); }
    }
  }
  if (!canAnalyze) return null;
  return <span className="excel-export-control"><button type="button" className={className ?? (text ? undefined : 'icon-button')}
    title={label} aria-label={label} disabled={disabled || busy || !rows.length} onClick={() => void save()}>
    {busy ? <RefreshCw size={16} className="loading-icon" /> : <FileSpreadsheet size={16} />}{text && 'Excel'}
  </button>{error && <span className="warning-text" role="alert">{error}</span>}</span>;
}
