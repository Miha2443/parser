import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { constructionTreeArea, constructionTreeCsv, constructionTreeDate, constructionTreeExact, constructionTreeFilename, constructionTreeIds, constructionTreeLabels, loadConstructionTree, readConstructionTree, watchConstructionTree } from './constructionTreeData.ts';

const fixture = () => ({
  schemaVersion: 1,
  version: 'ready-2026-10-16',
  generatedAt: '2026-10-16T14:00:00Z',
  unit: 'million_m2',
  source: { file: 'ready/Мониторинг.xlsx', sheet: 'Дерево текущ стр во', column: 'Итог', downloadedAt: '2026-10-16T16:45:00+03:00', fileDate: '2026-10-15' },
  metrics: [
    ['total', 'Общая площадь всего', 51.65728716, 'L5'],
    ['housing', 'Жилье', 37.92555967, 'L6'],
    ['embedded', 'Встроенный нежилой', 5.09855928, 'L7'],
    ['mop', 'МОП', 10.68700039, 'L8'],
    ['living', 'Жилая', 22.14, 'L9'],
    ['investors', 'Инвесторы', 15.74, 'L10'],
    ['renovation', 'Реновация', 6.4, 'L11'],
    ['nonresidential', 'Нежилье', 13.73172749, 'L12'],
    ['budget', 'Бюджет', 7.123456789, 'L13'],
    ['nonbudget', 'Внебюджет', null, 'L14'],
  ].map(([id, label, value, cell]) => ({ id, label, value, cell })),
});

test('READY tree preserves all ten original values, source labels, cell addresses and metadata', () => {
  const raw = fixture(), before = structuredClone(raw), parsed = readConstructionTree(raw);
  assert.deepEqual(parsed, raw);
  assert.deepEqual(raw, before);
  assert.deepEqual(parsed.metrics.map(metric => metric.id), constructionTreeIds);
  assert.equal(parsed.metrics.find(metric => metric.id === 'embedded').value, 5.09855928);
  assert.equal(parsed.metrics.find(metric => metric.id === 'nonresidential').value, 13.73172749);
  assert.equal(parsed.metrics.find(metric => metric.id === 'investors').label, 'Инвесторы');
  assert.equal(constructionTreeLabels.investors, 'Инвесторы с ДДУ');
});

test('only supplied totals are used, including inconsistent totals, missing values and real zeros', () => {
  const raw = fixture();
  raw.metrics.find(metric => metric.id === 'total').value = 987.654321;
  raw.metrics.find(metric => metric.id === 'housing').value = null;
  raw.metrics.find(metric => metric.id === 'mop').value = 0;
  const parsed = readConstructionTree(raw);
  assert.equal(parsed.metrics[0].value, 987.654321);
  assert.equal(parsed.metrics[1].value, null);
  assert.equal(parsed.metrics[3].value, 0);
  raw.metrics[0].value = null;
  assert.equal(readConstructionTree(raw).metrics[0].value, null);
  raw.metrics.reverse();
  assert.deepEqual(readConstructionTree(raw).metrics, raw.metrics);
  assert.equal(constructionTreeArea(null), '—');
  assert.equal(constructionTreeArea(0), '0,00');
  assert.equal(constructionTreeArea(51.65728716), '51,66');
  assert.equal(constructionTreeExact(51.65728716), '51.65728716');
  assert.equal(constructionTreeExact(null), '—');
  assert.equal(constructionTreeExact(0), '0');
});

test('wrong schema, source scope, units, duplicate/missing/unknown IDs and nonnumeric values reject', () => {
  const mutations = [
    raw => raw.schemaVersion = 2, raw => raw.schemaVersion = '1',
    raw => raw.unit = 'm2', raw => raw.unit = 'thousand_m2',
    raw => raw.version = '', raw => raw.version = ' ', raw => raw.version = 123,
    raw => raw.source = null, raw => raw.source = [],
    raw => raw.source.file = '', raw => delete raw.source.file,
    raw => raw.source.sheet = 'ОКС', raw => raw.source.sheet = 'Дерево текущ стр во ',
    raw => raw.source.column = 'Бюджет', raw => delete raw.source.column,
    raw => raw.metrics = {}, raw => raw.metrics = [], raw => raw.metrics.pop(),
    raw => raw.metrics.push({ ...raw.metrics[0] }),
    raw => raw.metrics[1].id = raw.metrics[0].id,
    raw => raw.metrics[0].id = 'computed_total', raw => delete raw.metrics[0].id,
    raw => raw.metrics[0] = null, raw => raw.metrics[0].label = ' ',
    raw => raw.metrics[0].value = '51.65728716', raw => raw.metrics[0].value = '51,65',
    raw => raw.metrics[0].value = false, raw => raw.metrics[0].value = {},
    raw => raw.metrics[0].value = NaN, raw => raw.metrics[0].value = Infinity,
    raw => raw.metrics[0].value = -1, raw => delete raw.metrics[0].value,
    raw => raw.metrics[0].cell = '', raw => raw.metrics[0].cell = 'L0',
    raw => raw.metrics[0].cell = 'XFE1', raw => raw.metrics[0].cell = 'A1048577',
    raw => raw.metrics[0].cell = 'L5:L6',
  ];
  for (const mutate of mutations) {
    const raw = fixture(); mutate(raw);
    assert.throws(() => readConstructionTree(raw), /схеме API версии 1/, mutate.toString());
  }
  for (const value of [null, undefined, [], false, 5, 'payload']) assert.throws(() => readConstructionTree(value));
  const raw = fixture(); raw.metrics[0].cell = '$XFD$1048576';
  assert.equal(readConstructionTree(raw).metrics[0].cell, '$XFD$1048576');
});

test('ISO timestamps and calendar dates are strict; download, file date and reporting period stay separate', () => {
  for (const path of ['generatedAt', 'downloadedAt']) {
    for (const value of ['', null, '2026-10-16', '2026-02-30T12:00:00Z', '2026-10-16T25:00:00Z', '2026-10-16T00:00:00', '16.10.2026', 2026]) {
      const raw = fixture(); (path === 'generatedAt' ? raw : raw.source)[path] = value;
      assert.throws(() => readConstructionTree(raw), undefined, `${path}: ${value}`);
    }
  }
  for (const value of ['2026-02-29', '2026-13-01', '2026-04-31', '16.10.2026', '', undefined]) {
    const raw = fixture(); raw.source.fileDate = value;
    assert.throws(() => readConstructionTree(raw), undefined, String(value));
  }
  const raw = fixture(); raw.source.fileDate = null;
  assert.equal(readConstructionTree(raw).source.fileDate, null);
  raw.source.fileDate = '2024-02-29'; raw.generatedAt = '2026-10-16T14:00:00.123456Z';
  assert.equal(readConstructionTree(raw).source.fileDate, '2024-02-29');
  assert.equal(constructionTreeDate(raw.source.downloadedAt), '16.10.2026');
  assert.equal(constructionTreeDate(raw.source.fileDate), '29.02.2024');
  assert.equal(constructionTreeDate(null), 'не указана');
});

test('CSV exports full scope once with exact precision, native labels, provenance and nulls, not cross-reference totals', () => {
  const data = readConstructionTree(fixture()), csv = constructionTreeCsv(data), rows = csv.slice(1).split('\r\n');
  assert.equal(csv[0], '\uFEFF');
  assert.equal(rows.length, 11);
  for (const metric of data.metrics) {
    const row = rows.find(row => row.startsWith(`"${metric.id}";`));
    assert.ok(row.includes(`"${metric.label}"`));
    assert.ok(row.includes(`"${metric.value === null ? '' : metric.value}";"${metric.cell}"`));
    for (const value of [data.source.file, data.source.sheet, data.source.column, data.version, data.source.downloadedAt, data.source.fileDate, data.generatedAt]) assert.ok(row.includes(`"${value}"`));
  }
  assert.equal(rows.filter(row => row.startsWith('"embedded";')).length, 1);
  assert.equal(csv.includes('18.83028677'), false);
  assert.equal(constructionTreeFilename(data), 'construction-tree-ready-2026-10-16.csv');
  data.version = '../folder/<unsafe>?';
  assert.match(constructionTreeFilename(data), /^construction-tree-[a-zA-Z0-9._-]+\.csv$/);
  assert.equal(constructionTreeFilename(data).includes('/'), false);
});

test('CSV quotes separators, quotes and newlines and neutralizes spreadsheet formulas', () => {
  const data = readConstructionTree(fixture());
  data.metrics[0].label = '=HYPERLINK("https://example.test");\r\nlabel';
  data.source.file = '  +source.xlsx';
  data.version = '@unsafe';
  const csv = constructionTreeCsv(data);
  assert.ok(csv.includes('"\'=HYPERLINK(""https://example.test"");\r\nlabel"'));
  assert.ok(csv.includes('"\'  +source.xlsx"'));
  assert.ok(csv.includes('"\'@unsafe"'));
  assert.equal(csv.includes('"51.65728716"'), true);
});

test('loader uses the single live API endpoint, request/no-store, and the caller abort signal', async t => {
  const calls = [], controller = new AbortController();
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify(fixture()));
  });
  assert.deepEqual(await loadConstructionTree(controller.signal), fixture());
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/v1/construction/tree');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[0].options.signal, controller.signal);
  t.mock.method(globalThis, 'fetch', async () => new Response('unavailable', { status: 503 }));
  await assert.rejects(loadConstructionTree(controller.signal), /HTTP 503/);
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ ...fixture(), unit: 'm2' })));
  await assert.rejects(loadConstructionTree(controller.signal), /схеме API/);
  t.mock.method(globalThis, 'fetch', async () => new Response('{invalid json'));
  await assert.rejects(loadConstructionTree(controller.signal));
});

function environment(t, initialVisibility = 'visible') {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'document');
  const feeds = [];
  const document = new EventTarget();
  document.visibilityState = initialVisibility;
  Object.defineProperty(globalThis, 'document', { configurable: true, value: document });
  let interval, cleared = false;
  t.mock.method(globalThis, 'setInterval', (callback, delay) => { assert.equal(delay, 60_000); interval = callback; return 123; });
  t.mock.method(globalThis, 'clearInterval', id => { assert.equal(id, 123); cleared = true; });
  t.after(() => {
    feeds.forEach(feed => feed.stop());
    if (previous) Object.defineProperty(globalThis, 'document', previous); else delete globalThis.document;
  });
  return {
    watch(update) { const feed = watchConstructionTree(update); feeds.push(feed); return feed; },
    tick: () => interval(),
    visibility(state) { document.visibilityState = state; document.dispatchEvent(new Event('visibilitychange')); },
    cleared: () => cleared,
  };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('visible 60-second polling deduplicates requests, refreshes on visibility, and never polls hidden pages', async t => {
  const env = environment(t), states = [], pending = [];
  t.mock.method(globalThis, 'fetch', (url, options) => new Promise(resolve => pending.push({ url, options, resolve })));
  const feed = env.watch(state => states.push(state));
  assert.equal(pending.length, 1);
  assert.equal(states.at(-1).loading, true);
  env.tick(); env.visibility('visible'); await feed.refresh();
  assert.equal(pending.length, 1, 'one request even across timer, visibility and manual refresh');
  pending[0].resolve(new Response(JSON.stringify(fixture()))); await settle();
  assert.equal(states.at(-1).data.version, fixture().version);
  env.visibility('hidden'); env.tick(); await feed.refresh();
  assert.equal(pending.length, 1);
  env.visibility('visible');
  assert.equal(pending.length, 2);
  assert.equal(states.at(-1).loading, true);
  assert.equal(states.at(-1).data.version, fixture().version, 'background loading preserves the rendered tree');
  const updated = fixture(); updated.version = 'parser-published-new-version'; updated.metrics[0].value = 54.321;
  pending[1].resolve(new Response(JSON.stringify(updated))); await settle();
  assert.equal(states.at(-1).data.version, updated.version);
  assert.equal(states.at(-1).data.metrics[0].value, 54.321);
  env.tick();
  assert.equal(pending.length, 3);
  assert.equal(pending[2].options.cache, 'no-store');
});

test('HTTP, schema and network refresh failures remove all stale data; retry recovers', async t => {
  const env = environment(t);
  let mode = 'ok'; const states = [];
  t.mock.method(globalThis, 'fetch', async () => {
    if (mode === 'network') throw new TypeError('network');
    if (mode === 'http') return new Response('unavailable', { status: 503 });
    return new Response(JSON.stringify(mode === 'schema' ? { ...fixture(), metrics: [] } : fixture()));
  });
  const feed = env.watch(state => states.push(state));
  await settle();
  for (const failure of ['http', 'schema', 'network']) {
    assert.ok(states.at(-1).data);
    mode = failure; await feed.refresh();
    assert.equal(states.at(-1).data, null, failure);
    assert.equal(states.at(-1).loading, false);
    assert.ok(states.at(-1).error.length);
    mode = 'ok'; await feed.refresh();
    assert.deepEqual(states.at(-1).data, fixture());
    assert.equal(states.at(-1).error, '');
  }
});

test('unmount aborts in-flight requests, removes timers/listeners, and ignores late responses', async t => {
  const env = environment(t), states = [];
  let resolve, signal, calls = 0;
  t.mock.method(globalThis, 'fetch', (url, options) => { calls++; signal = options.signal; return new Promise(done => resolve = done); });
  const feed = env.watch(state => states.push(state));
  assert.equal(signal.aborted, false);
  feed.stop();
  assert.equal(signal.aborted, true);
  assert.equal(env.cleared(), true);
  const count = states.length;
  resolve(new Response(JSON.stringify(fixture()))); await settle();
  env.tick(); env.visibility('visible'); await feed.refresh();
  assert.equal(calls, 1);
  assert.equal(states.length, count);
});

test('initial hidden page waits until visible and abort rejection cannot publish after disposal', async t => {
  const env = environment(t, 'hidden'), states = [];
  let calls = 0, signal;
  t.mock.method(globalThis, 'fetch', (url, options) => {
    calls++; signal = options.signal;
    return new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))));
  });
  const feed = env.watch(state => states.push(state));
  assert.equal(calls, 0); env.tick(); assert.equal(calls, 0);
  env.visibility('visible'); assert.equal(calls, 1);
  const count = states.length; feed.stop(); await settle();
  assert.equal(signal.aborted, true);
  assert.equal(states.length, count);
});

test('page has stable QA hooks, no filters/collapses in the tree, and one default-closed source disclosure', () => {
  const page = readFileSync(new URL('./ConstructionTree.tsx', import.meta.url), 'utf8');
  const data = readFileSync(new URL('./constructionTreeData.ts', import.meta.url), 'utf8');
  const css = readFileSync(new URL('./construction-tree.css', import.meta.url), 'utf8');
  assert.match(page, /export default function ConstructionTree\(\)/);
  assert.match(page, /<main className="construction-tree-page"/);
  assert.match(page, /Дерево текущего строительства/);
  for (const id of ['total', 'housing', 'nonresidential']) assert.ok(page.includes(`data-metric="${id}"`));
  for (const id of ['embedded', 'mop', 'living', 'investors', 'renovation']) assert.match(page, new RegExp(`leaf\\('${id}'`));
  assert.match(page, /data-metric=\{id\}/);
  assert.match(page, /Нежильё в составе жилых объектов/);
  assert.match(page, /Не прибавляется к нежилым объектам или общей площади/);
  assert.match(page, /Повтор показателя/);
  assert.doesNotMatch(page.slice(page.indexOf('function ReadyTree')), /ct-provenance|Повтор показателя|Без пересчёта/);
  assert.match(page, /Отчётный период: не указан в источнике/);
  assert.equal((page.match(/<details\b/g) ?? []).length, 1);
  assert.match(page, /<details className="ct-source">/);
  assert.doesNotMatch(page, /<select\b|<input\b|\.filter\(|\.reduce\(|localStorage|sessionStorage|OKS|ОКС/);
  assert.doesNotMatch(page, /<svg\b|<path\b/);
  assert.doesNotMatch(data, /51\.65728716|37\.92555967|5\.09855928|13\.73172749|profile-snapshot|\/export/);
  assert.match(css, /grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(css, /@media \(max-width: 760px\)/);
  assert.doesNotMatch(css, /overflow(?:-x)?:\s*(?:auto|scroll)|100vw|url\(|gradient|letter-spacing:\s*-/);
});
