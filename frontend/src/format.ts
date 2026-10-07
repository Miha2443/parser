export const number = (value: number | null | undefined, digits = 0): string =>
  value == null || !Number.isFinite(value) ? '—' : new Intl.NumberFormat('ru-RU', { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(value);
export const area = (value: number | null | undefined): string => value == null ? '—' : number(value / 1000, 1);
export const percent = (value: number | null | undefined): string => value == null ? '—' : `${number(value, 1)}%`;
export const money = (value: number | null | undefined): string => value == null ? '—' : number(value / 1e9, 1);
export const date = (value: string | null | undefined): string => {
  if (!value) return 'Дата не указана';
  const parsed = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  return parsed ? `${parsed[3]}.${parsed[2]}.${parsed[1]}` : value;
};
export const categories = [
  { key: 'housing', label: 'Жилая площадь', color: '#2c9869' },
  { key: 'common', label: 'МОП', color: '#d6a23f' },
  { key: 'nonresidentialEmbedded', label: 'Нежилое в жилом', color: '#329d9c' },
  { key: 'nonresidentialSeparate', label: 'Нежилое отдельное', color: '#8993a1' },
] as const;
export function sumAreas(values: import('./types').Areas): number | null {
  const present = categories.map(c => values[c.key]).filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  return present.length ? present.reduce((sum, v) => sum + v, 0) : null;
}
export function download(url: string, filename: string) {
  const link = document.createElement('a');
  link.href = url; link.download = filename; link.click();
}
export function exportCsv(rows: (string | number | null)[][], filename: string) {
  const safe = (value: string | number | null) => {
    let text = value == null ? '' : String(value);
    if (typeof value === 'string' && /^[=+@\-\t\r]/.test(text)) text = `'${text}`;
    return `"${text.replaceAll('"', '""')}"`;
  };
  const blob = new Blob(['\uFEFF' + rows.map(row => row.map(safe).join(';')).join('\r\n')], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  download(url, filename);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
