import { ApiError, request } from './data.ts';

export type ExcelCell = string | number | boolean | null;
export const EXCEL_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

export function excelFilename(filename: string) {
  const name = filename.replace(/\.(?:csv|xlsx?)$/i, '').replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').trim();
  return `${name || 'data'}.xlsx`;
}

export async function excelBlob(rows: ExcelCell[][], signal: AbortSignal): Promise<Blob> {
  if (!rows.length || rows.some(row => !Array.isArray(row) || row.some(cell =>
    !(cell === null || typeof cell === 'string' || typeof cell === 'boolean' || typeof cell === 'number' && Number.isFinite(cell))))) {
    throw new Error('В данных выгрузки есть некорректные значения.');
  }
  try {
    const response = await request('/api/v1/exports/table', signal, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rows }),
    });
    if (!response.headers.get('Content-Type')?.toLowerCase().startsWith(EXCEL_MIME)) {
      throw new Error('Сервер не вернул файл Excel. Обновите сайт и повторите скачивание.');
    }
    return await response.blob();
  } catch (error) {
    if (error instanceof ApiError && error.status === 413) throw new Error('Таблица слишком большая для одной выгрузки. Выберите меньший период.');
    if (error instanceof ApiError && error.status === 404) throw new Error('Для скачивания Excel требуется обновление backend.');
    throw error;
  }
}
