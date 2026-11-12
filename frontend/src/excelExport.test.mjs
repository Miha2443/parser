import assert from 'node:assert/strict';
import test from 'node:test';
import { excelBlob, excelFilename, EXCEL_MIME } from './excelTable.ts';
import { setRequestAccess } from './data.ts';

test('Excel filename replaces CSV extension, keeps Unicode and sanitizes paths', () => {
  assert.equal(excelFilename('РС-янв-сентябрь.csv'), 'РС-янв-сентябрь.xlsx');
  assert.equal(excelFilename('chart'), 'chart.xlsx');
  assert.equal(excelFilename('table.XLSX'), 'table.xlsx');
  assert.equal(excelFilename(''), 'data.xlsx');
  assert.equal(excelFilename('a/b:c.csv'), 'a_b_c.xlsx');
});

test('Excel request preserves raw numeric values, zero, null, boolean and leading-zero strings with CSRF', async t => {
  const rows = [['Период', 'Значение'], ['2026-09', 1234567.891234], ['00123', 0], [null, true]];
  const restore = setRequestAccess(() => ({ enabled: true, csrfToken: 'test-csrf' })); t.after(restore);
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls++; assert.equal(url, '/api/v1/exports/table'); assert.equal(options.method, 'POST');
    assert.equal(options.headers.get('X-CSRF-Token'), 'test-csrf');
    assert.equal(options.credentials, 'same-origin'); assert.equal(options.cache, 'no-store');
    assert.deepEqual(JSON.parse(options.body), { rows });
    return new Response(new Blob(['test'], { type: EXCEL_MIME }), { headers: { 'Content-Type': EXCEL_MIME } });
  });
  const blob = await excelBlob(rows, new AbortController().signal);
  assert.equal(blob.type, EXCEL_MIME); assert.equal(calls, 1);
});

test('non-finite values and unsupported cells never become plausible nulls during JSON serialization', async t => {
  let calls = 0; t.mock.method(globalThis, 'fetch', async () => { calls++; throw new Error('must not send'); });
  for (const rows of [[], [[NaN]], [[Infinity]], [[undefined]], [[{}]]]) {
    await assert.rejects(excelBlob(rows, new AbortController().signal), /некорректные/);
  }
  assert.equal(calls, 0);
});

test('Excel export reports unavailable endpoint, size limit and wrong response content', async t => {
  for (const [status, message] of [[404, /backend/], [413, /слишком большая/]]) {
    t.mock.method(globalThis, 'fetch', async () => new Response('', { status }));
    await assert.rejects(excelBlob([['data']], new AbortController().signal), message);
  }
  t.mock.method(globalThis, 'fetch', async () => new Response('<html/>', { headers: { 'Content-Type': 'text/html' } }));
  await assert.rejects(excelBlob([['data']], new AbortController().signal), /не вернул файл Excel/);
});
