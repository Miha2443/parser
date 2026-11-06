import Decimal from 'decimal.js';
import Papa from 'papaparse';
import { loadEconomicsCatalog, loadEconomicsPage } from './economicsData.ts';
import type { EconomicsReport } from './economicsData';

const Money = Decimal.clone({ precision: 50, rounding: Decimal.ROUND_HALF_UP });
export type Frequency = 'month' | 'quarter' | 'year';
export type PriceMethod = 'average' | 'end';
export type CpiPoint = { year: number; month: number; value: number | null };
export type PriceSeries = { region: string; first: number; last: number; points: CpiPoint[]; gaps: string[]; nodes: Map<number, { level: Decimal; group: number }> };
export type PriceData = { report: EconomicsReport; series: PriceSeries[] };
export type Sheet = { columns: string[]; rows: { period: string; values: string[] }[] };
export const MAX_ROWS = 1000, MAX_COLUMNS = 12;
const months = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
const roman = ['I', 'II', 'III', 'IV'];
const ordinal = (year: number, month: number) => year * 12 + month - 1;
const parts = (serial: number) => ({ year: Math.floor(serial / 12), month: serial % 12 + 1 });
export const periodStep = (frequency: Frequency) => frequency === 'year' ? 12 : frequency === 'quarter' ? 3 : 1;
export function periodKey(serial: number, frequency: Frequency): string {
  const { year, month } = parts(serial);
  return frequency === 'year' ? String(year) : frequency === 'quarter' ? `${year}-Q${Math.ceil(month / 3)}` : `${year}-${String(month).padStart(2, '0')}`;
}
export function periodStart(key: string, frequency: Frequency): number | null {
  const match = (frequency === 'year' ? /^(\d{4})$/ : frequency === 'quarter' ? /^(\d{4})-Q([1-4])$/ : /^(\d{4})-(0[1-9]|1[0-2])$/).exec(key);
  if (!match || Number(match[1]) < 1900 || Number(match[1]) > 2199) return null;
  return ordinal(Number(match[1]), frequency === 'year' ? 1 : frequency === 'quarter' ? (Number(match[2]) - 1) * 3 + 1 : Number(match[2]));
}
export function periodLabel(key: string, frequency: Frequency): string {
  const start = periodStart(key, frequency); if (start === null) return key;
  const { year, month } = parts(start);
  return frequency === 'year' ? `${year}` : frequency === 'quarter' ? `${roman[Math.floor((month - 1) / 3)]} квартал ${year}` : `${months[month - 1]} ${year}`;
}
export function nextPeriod(key: string, frequency: Frequency, count = 1): string {
  const start = periodStart(key, frequency);
  if (start === null) throw new Error('Некорректный период.');
  return periodKey(start + periodStep(frequency) * count, frequency);
}
export function buildPriceSeries(region: string, points: CpiPoint[]): PriceSeries {
  const entries = new Map<number, CpiPoint>();
  for (const point of points) {
    if (!Number.isInteger(point.year) || point.year < 1900 || point.year > 2199 || !Number.isInteger(point.month) || point.month < 1 || point.month > 12 || point.value !== null && (!Number.isFinite(point.value) || point.value <= 0)) throw new Error('Некорректный месячный индекс ИПЦ.');
    const serial = ordinal(point.year, point.month);
    if (entries.has(serial)) throw new Error(`Дублирующийся ИПЦ: ${periodLabel(periodKey(serial, 'month'), 'month')}.`);
    entries.set(serial, point);
  }
  const observed = [...entries].filter(([, point]) => point.value !== null).map(([serial]) => serial).sort((a, b) => a - b);
  const first = observed[0] ?? 0, last = observed.at(-1) ?? -1, nodes: PriceSeries['nodes'] = new Map();
  let level = new Money(1), group = first;
  const gaps: string[] = [];
  for (let serial = first; serial <= last; serial++) {
    const index = entries.get(serial)?.value;
    // An index is an edge from the previous month, not a requirement for that month's price node.
    if (serial > first) {
      if (index === null || index === undefined) { level = new Money(1); group = serial; gaps.push(periodKey(serial, 'month')); }
      else level = level.mul(new Money(index).div(100));
    }
    nodes.set(serial, { level, group });
  }
  return { region, first, last, nodes, gaps, points: [...entries.values()].sort((a, b) => a.year - b.year || a.month - b.month) };
}
export function readPriceData(report: EconomicsReport): PriceData {
  if (report.family !== 'ipc' || report.selection.period !== 'month' || report.selection.index_base !== 'month_to_month' || JSON.stringify(report.selection.months) !== JSON.stringify(Array.from({ length: 12 }, (_, i) => i + 1))) throw new Error('Для расчёта нужен полный месячный ряд ИПЦ к предыдущему месяцу.');
  const rows = report.charts.find(chart => chart.id === 'ipc_month')?.rows ?? [];
  const regions = report.selection.regions as string[];
  if (!regions.length) throw new Error('Регионы ИПЦ отсутствуют.');
  if (rows.some(row => !regions.includes(String(row.region)) || typeof row.year !== 'number' || typeof row.month !== 'number' || row['ипц_%'] !== null && typeof row['ипц_%'] !== 'number')) throw new Error('Ответ ИПЦ не содержит точных дат и индексов.');
  return { report, series: regions.map(region => buildPriceSeries(region, rows.filter(row => row.region === region).map(row => ({ year: row.year as number, month: row.month as number, value: row['ипц_%'] as number | null })))) };
}
export async function loadPriceData(signal: AbortSignal): Promise<PriceData> {
  const catalog = await loadEconomicsCatalog('ipc', signal);
  const params = new URLSearchParams({ period: 'month', index_base: 'month_to_month', regions: JSON.stringify((catalog.regions ?? []).map(region => String(region.id))), months: JSON.stringify(Array.from({ length: 12 }, (_, i) => i + 1)) });
  const { data } = await loadEconomicsPage('ipc', params, signal, catalog);
  return readPriceData(data);
}
export function priceLevel(series: PriceSeries, key: string, frequency: Frequency, method: PriceMethod): { level: Decimal; group: number } | null {
  const start = periodStart(key, frequency); if (start === null) return null;
  const end = start + periodStep(frequency) - 1;
  // Partial years/quarters are never silently substituted for a complete period.
  if (start < series.first || end > series.last) return null;
  const required = method === 'end' ? [end] : Array.from({ length: end - start + 1 }, (_, i) => start + i);
  const nodes = required.map(serial => series.nodes.get(serial));
  if (nodes.some(node => !node) || new Set(nodes.map(node => node!.group)).size !== 1) return null;
  return { level: nodes.reduce((sum, node) => sum.add(node!.level), new Money(0)).div(nodes.length), group: nodes[0]!.group };
}
export function pricePeriods(series: PriceSeries, frequency: Frequency, method?: PriceMethod): string[] {
  if (series.last < series.first) return [];
  const first = periodStart(periodKey(series.first, frequency), frequency)!, keys: string[] = [];
  for (let serial = first; serial <= series.last; serial += periodStep(frequency)) {
    if (serial < series.first || serial + periodStep(frequency) - 1 > series.last) continue;
    const key = periodKey(serial, frequency);
    if (!method || priceLevel(series, key, frequency, method)) keys.push(key);
  }
  return keys;
}
export function conversion(series: PriceSeries, key: string, frequency: Frequency, base: string, baseFrequency: Frequency, method: PriceMethod): { coefficient: Decimal | null; error: string } {
  const source = priceLevel(series, key, frequency, method), target = priceLevel(series, base, baseFrequency, method);
  if (!target) return { coefficient: null, error: 'Нет полного ИПЦ для выбранного периода цен.' };
  if (!source) return { coefficient: null, error: 'Нет полного ИПЦ для исходного периода.' };
  if (source.group !== target.group) {
    const start = Math.min(periodStart(key, frequency)!, periodStart(base, baseFrequency)!), end = Math.max(periodStart(key, frequency)! + periodStep(frequency) - 1, periodStart(base, baseFrequency)! + periodStep(baseFrequency) - 1);
    const missing = series.gaps.filter(period => periodStart(period, 'month')! > start && periodStart(period, 'month')! <= end).map(period => periodLabel(period, 'month'));
    return { coefficient: null, error: `Пропуск ИПЦ между периодами${missing.length ? `: ${missing.join(', ')}` : ''}.` };
  }
  return { coefficient: target.level.div(source.level), error: '' };
}

export function parseAmount(raw: string): { value: Decimal | null; error: string } {
  let text = raw.trim().replace(/\u2212/g, '-').replace(/[\u00a0\u202f]/g, ' ');
  if (!text || /^[-—–]$/.test(text)) return { value: null, error: '' };
  if (text.length > 128) return { value: null, error: 'Слишком длинное число.' };
  if (/^\(.*\)$/.test(text)) text = `-${text.slice(1, -1).trim()}`;
  const match = /^([+-]?)([\d .,]+)([eE][+-]?\d{1,2})?$/.exec(text);
  if (!match) return { value: null, error: 'Некорректное число.' };
  let mantissa = match[2];
  if (mantissa.includes(' ')) {
    if (!/^\d{1,3}(?: \d{3})+(?:[.,]\d+)?$/.test(mantissa)) return { value: null, error: 'Некорректный разделитель тысяч.' };
    mantissa = mantissa.replaceAll(' ', '');
  }
  const dots = (mantissa.match(/\./g) ?? []).length, commas = (mantissa.match(/,/g) ?? []).length;
  if (dots && commas) {
    const decimal = mantissa.lastIndexOf('.') > mantissa.lastIndexOf(',') ? '.' : ',', group = decimal === '.' ? ',' : '.';
    const [integer, fraction, extra] = mantissa.split(decimal);
    if (extra !== undefined || !/^\d+$/.test(fraction) || !new RegExp(`^\\d{1,3}(?:\\${group}\\d{3})+$`).test(integer)) return { value: null, error: 'Некорректные разделители числа.' };
    mantissa = `${integer.replaceAll(group, '')}.${fraction}`;
  } else if (dots > 1 || commas > 1) {
    const group = dots ? '.' : ',';
    if (!new RegExp(`^\\d{1,3}(?:\\${group}\\d{3})+$`).test(mantissa)) return { value: null, error: 'Некорректные разделители числа.' };
    mantissa = mantissa.replaceAll(group, '');
  } else mantissa = mantissa.replace(',', '.');
  if (!/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(mantissa)) return { value: null, error: 'Некорректное число.' };
  const value = new Money(`${match[1]}${mantissa}${match[3] ?? ''}`);
  return value.isFinite() && value.abs().lte('1e24') && value.sd() <= 40 ? { value, error: '' } : { value: null, error: 'Число вне допустимого диапазона.' };
}
export const resultText = (value: Decimal | null, digits: number) => value === null ? '' : value.toFixed(digits).replace('.', ',');
export const displayResult = (value: Decimal | null, digits: number) => {
  if (value === null) return '—';
  const [integer, fraction] = resultText(value, digits).split(',');
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g, '\u202f') + (fraction === undefined ? '' : `,${fraction}`);
};
export function makeSheet(series: PriceSeries, frequency: Frequency): Sheet {
  const periods = pricePeriods(series, frequency), count = frequency === 'month' ? 12 : 5;
  return { columns: ['Показатель 1'], rows: periods.slice(-count).map(period => ({ period, values: [''] })) };
}
export function parseExcel(text: string): string[][] {
  if (text.length > 500000) throw new Error('Диапазон слишком большой.');
  const parsed = Papa.parse<string[]>(text.replace(/^\uFEFF/, ''), { delimiter: text.includes('\t') ? '\t' : text.includes(';') ? ';' : '\t', skipEmptyLines: false });
  if (parsed.errors.length) throw new Error('Некорректный диапазон Excel.');
  const rows = parsed.data;
  while (rows.length && rows.at(-1)!.every(cell => !cell.trim())) rows.pop();
  const width = Math.max(0, ...rows.map(row => row.length));
  if (!rows.length || !width) throw new Error('Диапазон пуст.');
  if (rows.length > MAX_ROWS + 1 || width > MAX_ROWS + 1 || rows.length * width > (MAX_ROWS + 1) * (MAX_COLUMNS + 1)) throw new Error(`Не больше ${MAX_ROWS} строк и ${MAX_COLUMNS} показателей.`);
  return rows.map(row => Array.from({ length: width }, (_, i) => row[i] ?? ''));
}
export function pasteValues(sheet: Sheet, frequency: Frequency, row: number, column: number, matrix: string[][]): Sheet {
  const width = Math.max(...matrix.map(row => row.length)), height = row + matrix.length;
  if (height > MAX_ROWS || column + width > MAX_COLUMNS || !sheet.rows.length) throw new Error(`Не больше ${MAX_ROWS} строк и ${MAX_COLUMNS} показателей.`);
  matrix.forEach((cells, r) => cells.forEach((cell, c) => { if (parseAmount(cell).error) throw new Error(`Строка ${r + 1}, столбец ${c + 1}: некорректное число.`); }));
  const columns = Array.from({ length: Math.max(sheet.columns.length, column + width) }, (_, i) => sheet.columns[i] ?? `Показатель ${i + 1}`);
  const rows = Array.from({ length: Math.max(sheet.rows.length, height) }, (_, i) => ({ period: sheet.rows[i]?.period ?? nextPeriod(sheet.rows.at(-1)!.period, frequency, i - sheet.rows.length + 1), values: Array.from({ length: columns.length }, (_, j) => sheet.rows[i]?.values[j] ?? '') }));
  matrix.forEach((cells, r) => cells.forEach((cell, c) => { rows[row + r].values[column + c] = cell; }));
  return { columns, rows };
}
export function importedPeriod(raw: string, frequency: Frequency): string | null {
  const text = raw.trim().toLocaleLowerCase('ru').replace(/\s*г\.?$/, '');
  if (periodStart(raw, frequency) !== null) return raw;
  let key = '';
  if (frequency === 'year' && /^\d{4}$/.test(text)) key = text;
  if (frequency === 'month') {
    const ym = /^(\d{4})[-.](\d{1,2})$/.exec(text), my = /^(\d{1,2})[./](\d{4})$/.exec(text), date = /^\d{1,2}\.(\d{1,2})\.(\d{4})$/.exec(text);
    if (ym) key = `${ym[1]}-${ym[2].padStart(2, '0')}`;
    else if (my || date) { const match = (my ?? date)!; key = `${match[2]}-${match[1].padStart(2, '0')}`; }
    else {
      const name = /^([а-яё]+)\.?\s+(\d{4})$/.exec(text), index = name ? months.findIndex(month => month.toLocaleLowerCase('ru').startsWith(name[1].slice(0, 3))) : -1;
      if (name && index >= 0) key = `${name[2]}-${String(index + 1).padStart(2, '0')}`;
    }
  }
  if (frequency === 'quarter') {
    const yq = /^(\d{4})[- ]q([1-4])$/.exec(text), qy = /^(i{1,3}|iv|[1-4])\s*(?:кв(?:артал)?\.?)?\s+(\d{4})$/.exec(text);
    if (yq) key = `${yq[1]}-Q${yq[2]}`;
    else if (qy) key = `${qy[2]}-Q${roman.map(value => value.toLowerCase()).includes(qy[1]) ? roman.map(value => value.toLowerCase()).indexOf(qy[1]) + 1 : qy[1]}`;
  }
  return periodStart(key, frequency) === null ? null : key;
}
export function importSheet(text: string, frequency: Frequency, start: string, transpose: boolean, headings: boolean, dated: boolean): Sheet {
  let matrix = parseExcel(text);
  if (transpose) matrix = matrix[0].map((_, c) => matrix.map(row => row[c]));
  const headers = headings ? matrix.shift() : undefined;
  if (!matrix.length) throw new Error('Нет строк со значениями.');
  const width = matrix[0].length - (dated ? 1 : 0);
  if (width < 1 || width > MAX_COLUMNS || matrix.length > MAX_ROWS) throw new Error(`Не больше ${MAX_ROWS} строк и ${MAX_COLUMNS} показателей.`);
  const columns = Array.from({ length: width }, (_, i) => headers?.[i + (dated ? 1 : 0)]?.trim().replace(/\s+/g, ' ').slice(0, 80) || `Показатель ${i + 1}`);
  const rows = matrix.map((row, r) => {
    const period = dated ? importedPeriod(row[0], frequency) : nextPeriod(start, frequency, r);
    if (!period) throw new Error(`Строка ${r + 1}: некорректный период.`);
    const values = row.slice(dated ? 1 : 0);
    values.forEach((cell, c) => { if (parseAmount(cell).error) throw new Error(`Строка ${r + 1}, столбец ${c + 1}: некорректное число.`); });
    return { period, values };
  });
  return { columns, rows };
}
const safeLabel = (label: string) => /^\s*[=+@\-\t\r]/.test(label) ? `'${label}` : label;
export function copyResults(sheet: Sheet, frequency: Frequency, results: (Decimal | null)[][], digits: number, headings: boolean): string {
  let last = results.length - 1;
  while (last >= 0 && results[last].every(value => value === null)) last--;
  return Papa.unparse([...(headings ? [['Период', ...sheet.columns.map(safeLabel)]] : []), ...sheet.rows.slice(0, last + 1).map((row, i) => [...(headings ? [periodLabel(row.period, frequency)] : []), ...row.values.map((_, j) => resultText(results[i][j], digits))])], { delimiter: '\t', newline: '\r\n' });
}
export function calculationCsv(sheet: Sheet, frequency: Frequency, results: (Decimal | null)[][], coefficients: (Decimal | null)[], digits: number, baseLabel: string): string {
  const rows = [['Период', ...sheet.columns.map(name => safeLabel(`${name} · исходное`)), 'Коэффициент', ...sheet.columns.map(name => safeLabel(`${name} · цены ${baseLabel}`))],
    ...sheet.rows.map((row, i) => [periodLabel(row.period, frequency), ...row.values.map(value => parseAmount(value).value?.toFixed().replace('.', ',') ?? ''), coefficients[i]?.toFixed().replace('.', ',') ?? '', ...results[i].map(value => resultText(value, digits))])];
  return '\uFEFF' + Papa.unparse(rows, { delimiter: ';', newline: '\r\n' });
}
