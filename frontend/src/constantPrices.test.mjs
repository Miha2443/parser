import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import Decimal from 'decimal.js';
import fixtures from './constantPricesFixtures.cjs';
import { buildPriceSeries, calculationCsv, conversion, copyResults, displayResult, importedPeriod, importSheet, loadPriceData, makeSheet, nextPeriod, parseAmount, parseExcel, pasteValues, periodKey, periodLabel, priceLevel, pricePeriods, readPriceData, resultText } from './constantPricesData.ts';

const near = (value, expected) => assert.ok(Math.abs(value.toNumber() - expected) < 1e-10, `${value} != ${expected}`);
const months = values => values.map((value, i) => ({ year: 2024 + Math.floor(i / 12), month: i % 12 + 1, value }));
test('monthly conversion multiplies only the edges after the source month, in either direction', () => {
  const series = buildPriceSeries('Москва', months([110, 120, 90, 100]));
  near(conversion(series, '2024-01', 'month', '2024-03', 'month', 'average').coefficient, 1.08);
  near(conversion(series, '2024-03', 'month', '2024-01', 'month', 'end').coefficient, 1 / 1.08);
  assert.equal(conversion(series, '2024-02', 'month', '2024-02', 'month', 'average').coefficient.toString(), '1');
});
test('quarter/year means average chained levels, never monthly percentage changes', () => {
  const series = buildPriceSeries('Москва', months(Array(24).fill(110)));
  const q1 = priceLevel(series, '2024-Q1', 'quarter', 'average');
  near(q1.level, (1 + 1.1 + 1.21) / 3);
  near(conversion(series, '2024-Q1', 'quarter', '2024-Q2', 'quarter', 'average').coefficient, 1.1 ** 3);
  near(conversion(series, '2024', 'year', '2025', 'year', 'average').coefficient, 1.1 ** 12);
  near(priceLevel(series, '2024-Q1', 'quarter', 'end').level, 1.21);
});
test('a missing edge splits the chain, but that month remains comparable to its following month', () => {
  const series = buildPriceSeries('Москва', months([101, 102, null, 104, 105]));
  assert.equal(conversion(series, '2024-02', 'month', '2024-04', 'month', 'average').coefficient, null);
  near(conversion(series, '2024-03', 'month', '2024-04', 'month', 'average').coefficient, 1.04);
  assert.equal(priceLevel(series, '2024-Q1', 'quarter', 'average'), null);
  assert.ok(priceLevel(series, '2024-Q1', 'quarter', 'end'));
  assert.match(conversion(series, '2024-02', 'month', '2024-04', 'month', 'end').error, /Пропуск ИПЦ/);
});
test('missing calendar rows also break the chain and unknown trailing months are not offered', () => {
  const series = buildPriceSeries('Москва', [...months([100, 101, 102, 103]).filter(row => row.month !== 3), { year: 2024, month: 5, value: null }]);
  assert.equal(conversion(series, '2024-02', 'month', '2024-04', 'month', 'average').coefficient, null);
  assert.deepEqual(pricePeriods(series, 'month'), ['2024-01', '2024-02', '2024-03', '2024-04']);
  assert.equal(priceLevel(series, '2024', 'year', 'end'), null);
  assert.equal(priceLevel(series, '2024-Q2', 'quarter', 'average'), null);
});
test('duplicate, zero, negative, nonfinite or invalid source periods fail closed', () => {
  for (const value of [0, -1, NaN, Infinity]) assert.throws(() => buildPriceSeries('Москва', months([value])));
  assert.throws(() => buildPriceSeries('Москва', [...months([100]), ...months([100])]), /Дублирующийся/);
  assert.throws(() => buildPriceSeries('Москва', [{ year: 2024, month: 13, value: 100 }]));
  assert.equal(buildPriceSeries('Москва', []).nodes.size, 0);
});
test('periods, complete source defaults and quarter arithmetic do not depend on the current date', () => {
  const series = readPriceData(fixtures.report()).series[0];
  assert.deepEqual(makeSheet(series, 'year').rows.map(row => row.period), ['2023', '2024', '2025']);
  assert.equal(pricePeriods(series, 'month').at(-1), '2026-08');
  assert.equal(pricePeriods(series, 'quarter').at(-1), '2026-Q2');
  assert.equal(nextPeriod('2024-Q4', 'quarter'), '2025-Q1');
  assert.equal(nextPeriod('2024-12', 'month'), '2025-01');
  assert.equal(periodKey(2024 * 12 + 4, 'year'), '2024');
  assert.equal(periodLabel('2024-Q2', 'quarter'), 'II квартал 2024');
});
test('source reader insists on exact monthly dates and the previous-month CPI series', () => {
  const report = fixtures.report(), before = JSON.stringify(report), data = readPriceData(report);
  assert.deepEqual(data.series.map(series => series.region), ['Москва', 'Российская Федерация']);
  assert.equal(data.series[0].points.length, 44); assert.equal(JSON.stringify(report), before);
  for (const patch of [{ period: 'year' }, { index_base: 'ytd_to_yago' }, { months: [1, 2] }]) assert.throws(() => readPriceData({ ...report, selection: { ...report.selection, ...patch } }));
  const broken = structuredClone(report); delete broken.charts[0].rows[0].year; assert.throws(() => readPriceData(broken));
});
test('loader fetches the existing catalog/report with all months/regions and propagates failure/abort', async t => {
  const urls = [], signal = new AbortController().signal;
  t.mock.method(globalThis, 'fetch', async (url, options) => { urls.push(url); assert.equal(options.signal, signal); return Response.json(String(url).endsWith('/catalog') ? fixtures.catalog() : fixtures.report()); });
  const data = await loadPriceData(signal); assert.equal(data.series.length, 2); assert.equal(urls.length, 2);
  const url = new URL(urls[1], 'http://localhost');
  assert.deepEqual(url.searchParams.getAll('months').map(Number), Array.from({ length: 12 }, (_, i) => i + 1));
  assert.deepEqual(url.searchParams.getAll('regions'), ['Москва', 'Российская Федерация']);
  assert.equal(url.searchParams.get('period'), 'month'); assert.equal(url.searchParams.get('index_base'), 'month_to_month');
  t.mock.method(globalThis, 'fetch', async () => new Response('', { status: 503 }));
  await assert.rejects(loadPriceData(signal), /503/);
});
test('money accepts Russian Excel numbers, negatives and exact decimals without executing formulas', () => {
  for (const [raw, expected] of [['1 234,50', '1234.5'], ['1\u00a0234\u202f567,89', '1234567.89'], ['-123,5', '-123.5'], ['−12,3', '-12.3'], ['(1 234,50)', '-1234.5'], ['1,234.56', '1234.56'], ['1.234,56', '1234.56'], ['1,234,567', '1234567'], ['1.2e6', '1200000'], ['.5', '.5'], ['0', '0']]) assert.ok(parseAmount(raw).value.eq(expected), raw);
  for (const raw of ['', '—', '–', '-']) assert.equal(parseAmount(raw).value, null);
  for (const raw of ['=1+2', 'NaN', 'Infinity', '1 23', '12%', '1.2.3', '1e99', '0x10', '123 руб.']) assert.ok(parseAmount(raw).error, raw);
  assert.equal(parseAmount('123456789012345678901,125').value.toFixed(3), '123456789012345678901.125');
});
test('version changes reload the catalog and never mix CPI generations', async t => {
  let catalogs = 0, reports = 0;
  t.mock.method(globalThis, 'fetch', async url => {
    const isCatalog = String(url).endsWith('/catalog'), value = isCatalog ? fixtures.catalog() : fixtures.report();
    if (isCatalog) value.version = ++catalogs === 1 ? 'old' : 'new'; else { reports++; value.version = 'new'; }
    return Response.json(value);
  });
  const data = await loadPriceData(new AbortController().signal);
  assert.equal(data.report.version, 'new'); assert.equal(catalogs, 2); assert.equal(reports, 2);
});
test('abort and empty region catalogs fail explicitly rather than producing invented indices', async t => {
  const controller = new AbortController();
  t.mock.method(globalThis, 'fetch', (_url, { signal }) => new Promise((_, reject) => signal.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')), { once: true })));
  const promise = loadPriceData(controller.signal); controller.abort();
  await assert.rejects(promise, { name: 'AbortError' });
  const report = fixtures.report(); report.selection.regions = []; report.charts = [];
  assert.throws(() => readPriceData(report), /Регионы/);
  report.selection.regions = ['Москва'];
  assert.equal(readPriceData(report).series[0].nodes.size, 0);
});
test('rounding is half-up only at output and thousands separators never group fractional digits', () => {
  assert.equal(resultText(parseAmount('1,005').value, 2), '1,01');
  assert.equal(resultText(parseAmount('-1,005').value, 2), '-1,01');
  assert.equal(displayResult(parseAmount('1234,123456').value, 6), '1\u202f234,123456');
  assert.equal(resultText(new Decimal('10'), 0), '10');
});
test('Excel parser preserves blank positions, Russian decimal commas and quoted fields', () => {
  assert.deepEqual(parseExcel('"1,25"\t2\r\n\t3\r\n'), [['1,25', '2'], ['', '3']]);
  assert.deepEqual(parseExcel('1;2\n3;4'), [['1', '2'], ['3', '4']]);
  assert.throws(() => parseExcel(''), /пуст/);
  assert.throws(() => parseExcel('"unterminated'), /Некорректный/);
});
test('paste is atomic, expands rows/columns and keeps existing neighboring values', () => {
  const source = { columns: ['A'], rows: [{ period: '2024-12', values: ['5'] }] }, before = structuredClone(source);
  const result = pasteValues(source, 'month', 0, 0, [['10', '20'], ['30', '40']]);
  assert.deepEqual(result.rows, [{ period: '2024-12', values: ['10', '20'] }, { period: '2025-01', values: ['30', '40'] }]);
  assert.deepEqual(source, before);
  assert.throws(() => pasteValues(source, 'month', 0, 0, [['10'], ['oops']]), /Строка 2/);
  assert.throws(() => pasteValues(source, 'month', 0, 12, [['1']]), /Не больше/);
});
test('imports support named columns, explicit dates, horizontal ranges and more than 12 horizontal periods', () => {
  const result = importSheet('Год\tВыручка\tРасходы\n2023\t1 000,50\t(200)\n2025\t0\t', 'year', '2023', false, true, true);
  assert.deepEqual(result.columns, ['Выручка', 'Расходы']); assert.deepEqual(result.rows.map(row => row.period), ['2023', '2025']);
  const horizontal = importSheet(Array.from({ length: 20 }, (_, i) => i).join('\t'), 'month', '2023-01', true, false, false);
  assert.equal(horizontal.rows.length, 20); assert.equal(horizontal.rows.at(-1).period, '2024-08');
  assert.throws(() => importSheet('Дата\tA\nnot-date\t123', 'month', '2023-01', false, true, true), /период/);
  const named = importSheet('Показатель\t2023\t2024\nВыручка\t1\t2\nЗатраты\t3\t4', 'year', '2023', true, true, true);
  assert.deepEqual(named.columns, ['Выручка', 'Затраты']);
  assert.deepEqual(named.rows, [{ period: '2023', values: ['1', '3'] }, { period: '2024', values: ['2', '4'] }]);
});
test('explicit imported periods accept year, Russian month labels, dates and quarters', () => {
  for (const [raw, kind, expected] of [['2024 г.', 'year', '2024'], ['05.2024', 'month', '2024-05'], ['01.05.2024', 'month', '2024-05'], ['Май 2024', 'month', '2024-05'], ['2024-Q2', 'quarter', '2024-Q2'], ['II кв. 2024', 'quarter', '2024-Q2']]) assert.equal(importedPeriod(raw, kind), expected);
  assert.equal(importedPeriod('2024-13', 'month'), null);
});
test('copied results are Excel TSV with decimal commas and safe user-provided headings', () => {
  const sheet = { columns: ['=cmd', 'B'], rows: [{ period: '2024', values: ['-1', '0'] }] };
  assert.equal(copyResults(sheet, 'year', [[new Decimal('-1.125'), new Decimal(0)]], 2, false), '-1,13\t0,00');
  assert.equal(copyResults(sheet, 'year', [[new Decimal('-1.125'), null]], 2, true), "Период\t'=cmd\tB\r\n2024\t-1,13\t");
  const sparse = { columns: ['A'], rows: ['2023', '2024', '2025', '2026'].map(period => ({ period, values: [''] })) };
  assert.equal(copyResults(sparse, 'year', [[new Decimal(1)], [null], [new Decimal(2)], [null]], 0, false), '1\r\n\r\n2');
});
test('CSV preserves exact input precision and negative numeric values, neutralizing only text headings', () => {
  const sheet = { columns: [' =cmd'], rows: [{ period: '2024', values: ['-1,125'] }] };
  const csv = calculationCsv(sheet, 'year', [[parseAmount('-1.125').value]], [new Decimal(1)], 2, '2024');
  assert.ok(csv.startsWith('\uFEFFПериод;\' =cmd'));
  assert.ok(csv.endsWith('2024;-1,125;1;-1,13'));
});
test('service navigation and route point to a lazy calculator, not the developer profile', () => {
  for (const file of ['App.tsx', 'Sidebar.tsx', 'ServicePages.tsx']) assert.match(readFileSync(new URL(file, import.meta.url), 'utf8'), /\/service\/constant-prices/);
  assert.match(readFileSync(new URL('App.tsx', import.meta.url), 'utf8'), /lazy\(\(\) => import\('\.\/ConstantPrices'\)\)/);
});
