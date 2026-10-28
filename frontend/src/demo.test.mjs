import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { registerHooks } from 'node:module';
import { after, test } from 'node:test';
import ts from 'typescript';
import fixtures from './demoFixtures.cjs';

const root = new URL('./', import.meta.url).href;
const hooks = registerHooks({
  resolve(specifier, context, nextResolve) {
    if (context.parentURL?.startsWith(root) && /^\.\.?\/[^.]+$/.test(specifier)) {
      const extension = ['.ts', '.tsx'].find(extension => existsSync(new URL(`${specifier}${extension}`, context.parentURL)));
      if (extension) return nextResolve(`${specifier}${extension}`, context);
    }
    if (/^echarts\/(core|charts|components|renderers)$/.test(specifier)) return nextResolve(`${specifier}.js`, context);
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.endsWith('.css')) return { format: 'module', shortCircuit: true, source: '' };
    if (url.endsWith('.tsx')) return { format: 'module', shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX }, fileName: url }).outputText };
    return nextLoad(url, context);
  },
});
after(() => hooks.deregister());
const { DEMO_MIN_SECONDS, DEMO_MAX_SECONDS, DEMO_DEFAULT_SECONDS, demoHasValues, demoIndex, demoInterval, demoIntervalLabel, demoSelection, demoSources, demoVersioned } = await import('./demoData.ts');
const { ApiError } = await import('./data.ts');

test('interval is bounded to 15 seconds through 30 minutes with a 120-second default', () => {
  assert.deepEqual([DEMO_MIN_SECONDS, DEMO_MAX_SECONDS, DEMO_DEFAULT_SECONDS], [15, 1800, 120]);
  assert.equal(demoInterval(-1), 15); assert.equal(demoInterval(9999), 1800); assert.equal(demoInterval(NaN), 120); assert.equal(demoInterval(Infinity), 120);
  assert.deepEqual([15, 60, 75, 120, 1800].map(demoIntervalLabel), ['15 с', '1 мин', '1 мин 15 с', '2 мин', '30 мин']);
});
test('subset selection follows registry order and excludes empty or obsolete IDs without treating zero as empty', () => {
  const charts = [{ id: 'a', empty: false }, { id: 'b', empty: true }, { id: 'c', empty: false }];
  assert.deepEqual(demoSelection(charts, null).map(chart => chart.id), ['a', 'c']);
  assert.deepEqual(demoSelection(charts, new Set(['missing', 'c', 'a', 'b'])).map(chart => chart.id), ['a', 'c']);
  assert.deepEqual(demoSelection(charts, new Set()), []);
  assert.equal(demoHasValues([null, undefined]), false); assert.equal(demoHasValues([0]), true); assert.equal(demoHasValues([NaN, Infinity]), false);
});
test('cycle wraps in both directions, including a one-chart playlist', () => {
  assert.deepEqual([-4, -1, 0, 1, 2, 3, 4].map(index => demoIndex(index, 3)), [2, 2, 0, 1, 2, 0, 1]);
  assert.equal(demoIndex(100, 1), 0); assert.equal(demoIndex(-1, 1), 0); assert.equal(demoIndex(5, 0), 0);
});
test('catalog reconciliation retries once for changed versions and 404, but never hides permanent errors', async () => {
  let calls = 0;
  const changed = await demoVersioned(async () => ({ version: `v${++calls}` }), async () => ({ version: 'v2' }));
  assert.equal(changed.version, 'v2'); assert.equal(calls, 2);
  calls = 0;
  await demoVersioned(async () => ({ version: 'v1' }), async () => { if (!calls++) throw new ApiError(404); return { version: 'v1' }; }); assert.equal(calls, 2);
  calls = 0;
  await assert.rejects(demoVersioned(async () => ({ version: 'v1' }), async () => { calls++; throw new ApiError(503); }), /503/); assert.equal(calls, 1);
  calls = 0;
  await assert.rejects(demoVersioned(async () => ({ version: 'v1' }), async () => { calls++; return { version: 'other' }; }), /Источник изменился/); assert.equal(calls, 2);
});
test('every chart page has an explicit source, without whole-page mounts, iframes or static image slides', () => {
  assert.equal(demoSources.length, 13); assert.equal(new Set(demoSources.map(source => source.id)).size, 13);
  const source = readFileSync(new URL('./DemoPage.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /<iframe|html2canvas|toDataURL|<img[^>]+(?:\.png|\.jpe?g)/);
  assert.match(source, /<Chart /); assert.match(source, /IntersectionObserver/); assert.match(source, /createPortal/);
});
for (const source of demoSources) test(`real ${source.id} adapter shares one normalized report, preserves data, and builds responsive live options`, async t => {
  const calls = [], signal = new AbortController().signal;
  t.mock.method(globalThis, 'fetch', async (url, options) => { calls.push(url); assert.equal(options.signal, signal); return Response.json(fixtures.response(url)); });
  const charts = await source.load(signal);
  assert.ok(charts.length > 0, source.id); assert.equal(new Set(charts.map(chart => chart.id)).size, charts.length);
  const before = JSON.stringify(charts.map(chart => chart.rows));
  for (const chart of charts) {
    assert.ok(chart.context); assert.ok(chart.href.startsWith('/')); assert.ok(chart.rows.length);
    for (const width of [280, 1200]) for (const theme of ['light', 'dark']) {
      const option = chart.option(theme, width, []); assert.ok(Array.isArray(option.series));
      const names = option.series.map(series => series.name), hidden = chart.option(theme, width, names.slice(0, 1));
      assert.deepEqual(hidden.series.map(series => series.name), names, `${chart.id}: hidden legend must remain restorable`);
    }
  }
  assert.equal(JSON.stringify(charts.map(chart => chart.rows)), before);
  assert.equal(new Set(calls).size, calls.length, `${source.id} must not refetch per thumbnail`);
  if (source.id === 'preferential') assert.equal(charts.length, 16);
  if (source.id === 'annual') { assert.equal(charts.length, 7); assert.equal(charts[0].option('light', 1000, []).series.at(-1).label.show, true); }
  if (source.id === 'permits') { assert.ok(charts[0].growth.length); assert.ok(charts[0].growth.some(point => point.value !== null)); }
});
test('source errors and cancellations propagate instead of silently returning empty charts', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response('', { status: 503 }));
  await assert.rejects(demoSources.find(source => source.id === 'linear').load(new AbortController().signal), /503/);
  const controller = new AbortController(); controller.abort();
  globalThis.fetch = async (_url, options) => { options.signal.throwIfAborted(); };
  await assert.rejects(demoSources.find(source => source.id === 'linear').load(controller.signal), { name: 'AbortError' });
});
test('home has no update status or update request; brand and pinned Home use real accessible links', async () => {
  const { createElement } = await import('react'), { renderToStaticMarkup } = await import('react-dom/server');
  const { Home } = await import('./ServicePages.tsx'), { default: Sidebar } = await import('./Sidebar.tsx');
  const home = renderToStaticMarkup(createElement(Home, { navigate() {} }));
  assert.doesNotMatch(home, /Последнее обновление|Свежесть источников|Обновить сводку/); assert.match(home, /href="\/demo"/);
  const sidebar = renderToStaticMarkup(createElement(Sidebar, { open: false, onClose() {}, compact: false, onCompact() {}, path: '/home', onNavigate() {} }));
  assert.match(sidebar, /<a class="brand" href="\/home" aria-label=/); assert.match(sidebar, /class="navigation-home"/); assert.match(sidebar, /aria-current="page"/);
});
