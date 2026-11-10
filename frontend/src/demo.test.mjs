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
const { demoSlides, demoSourceTitle } = await import('./demoSlides.ts');

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

test('grouped screens keep section identity, selected-only panels, paired units and input data intact', () => {
  const chart = (id, scope) => ({ id, scope, title: id, context: 'Москва · 2026-11-10', empty: false, rows: [['n'], [0]], option: () => ({ series: [] }) });
  const charts = ['profile/annual', 'profile/structure/0', 'profile/structure/1', 'profile/construction',
    'domrf/construction', 'domrf/realization', 'domrf/sold', 'domrf/unsold', 'domrf/sales_not_open',
    'mortgage/volume/annual', 'mortgage/volume/monthly', 'mortgage/count/annual', 'mortgage/count/monthly',
    'preferential/volumeMln/annual', 'preferential/count/annual', 'preferential/volumeMln/cumulative', 'preferential/count/cumulative',
    'preferential/averageAmountMln/annual', 'preferential/ratePct/annual', 'operational/housing', 'permits/housing'].map(id => chart(id));
  const before = JSON.stringify(charts), slides = demoSlides(charts);
  assert.equal(JSON.stringify(charts), before);
  assert.deepEqual(slides.flatMap(slide => slide.charts.map(chart => chart.id)).sort(), charts.map(chart => chart.id).sort());
  const profile = slides.find(slide => slide.id === 'profile/commissioning');
  assert.equal(profile.layout, 'overview');
  assert.deepEqual(profile.charts.map(chart => chart.id), ['profile/structure/0', 'profile/structure/1', 'profile/annual']);
  assert.equal(slides.find(slide => slide.id === 'domrf/realization-structure').charts.length, 3);
  assert.equal(slides.find(slide => slide.id === 'domrf/realization').layout, 'single');
  for (const id of ['mortgage/annual', 'mortgage/monthly', 'preferential/flows/annual', 'preferential/flows/cumulative', 'preferential/characteristics/0/annual']) assert.equal(slides.find(slide => slide.id === id).charts.length, 2);
  assert.match(demoSourceTitle('permits/housing'), /РС/);
  assert.match(demoSourceTitle('annual/total'), /РВ/);
  assert.match(demoSourceTitle('operational/housing'), /РВ/);
  assert.equal(demoSlides([charts[0]])[0].layout, 'single');
  assert.equal(demoSlides([{ ...charts[0], empty: true }]).length, 0);
  const scoped = demoSlides([chart('profile/a/annual', 'profile/a'), chart('profile/a/structure/0', 'profile/a'), chart('profile/b/annual', 'profile/b')]);
  assert.deepEqual(scoped.map(slide => slide.charts.length), [2, 1], 'different companies must never share a composite screen');
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

test('demo starts with no selection, a disabled start action, and named section checkboxes', async () => {
  const { createElement } = await import('react'), { renderToStaticMarkup } = await import('react-dom/server');
  const { default: DemoPage } = await import('./DemoPage.tsx');
  const html = renderToStaticMarkup(createElement(DemoPage, { theme: 'light' }));
  assert.match(html, /Выбрано: 0 \/ 0/);
  assert.match(html, /<button[^>]*class="demo-start"[^>]*disabled=""/);
  assert.equal((html.match(/checked=""/g) ?? []).length, 1, 'only browser fullscreen is checked by default');
  for (const source of demoSources) assert.ok(html.includes(`aria-label="Все графики раздела: ${source.title}"`));
});

test('presentation adapts the measured canvas only in the viewer, without extra growth canvases or progress UI', () => {
  const source = readFileSync(new URL('./DemoPage.tsx', import.meta.url), 'utf8');
  assert.match(source, /canvas \?\? element/);
  assert.match(source, /width = Math\.round\(bounds\.width\), height = Math\.round\(bounds\.height\)/);
  assert.match(source, /entry\.option\(theme, Math\.max\(240, width\), hidden, height\)/);
  assert.match(source, /preview \? built : demoPresentationOption\(built, width, height, hidden\)/);
  assert.match(source, /useSlideClock\(step, seconds, paused, next\)/);
  assert.doesNotMatch(source, /demo-growth-chart|permitGrowthOption|demo-progress|progressbar/);
  const css = readFileSync(new URL('./demo.css', import.meta.url), 'utf8');
  assert.match(css, /\.demo-viewer-controls\[hidden\]\s*\{\s*display: none/);
  assert.match(css, /\.demo-full-chart \.chart-data, \.demo-full-chart \.chart-data-panel\s*\{\s*display: none/);
  assert.doesNotMatch(css, /font-size:[^;}]*\bvw\b/);
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
  if (source.id === 'permits') {
    const option = charts[0].option('light', 1000, [], 700);
    assert.equal(option.grid.length, 2, 'bars and growth share one canvas');
    assert.ok(option.series.some(series => series.type === 'line' && series.yAxisIndex === 0));
    assert.ok(option.series.some(series => series.type === 'bar' && series.yAxisIndex === 1));
    assert.equal('growth' in charts[0], false);
  }
});
test('source errors and cancellations propagate instead of silently returning empty charts', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response('', { status: 503 }));
  await assert.rejects(demoSources.find(source => source.id === 'linear').load(new AbortController().signal), /503/);
  const controller = new AbortController(); controller.abort();
  globalThis.fetch = async (_url, options) => { options.signal.throwIfAborted(); };
  await assert.rejects(demoSources.find(source => source.id === 'linear').load(controller.signal), { name: 'AbortError' });
});

test('explicit developer and period options reach only their adapter and preserve truthful profile scope', async t => {
  const calls = [], signal = new AbortController().signal;
  t.mock.method(globalThis, 'fetch', async url => {
    calls.push(String(url));
    const result = structuredClone(fixtures.response(url)), query = new URL(url, 'http://localhost').searchParams;
    if (new URL(url, 'http://localhost').pathname === '/api/v1/mortgage') {
      result.selection.month = Number(query.get('month'));
      for (const row of result.annual) for (const metric of ['volume', 'count']) row[metric].actualThroughMonth = result.selection.month;
    }
    if (new URL(url, 'http://localhost').pathname === '/api/v1/preferential-mortgage') {
      result.selection.month = Number(query.get('month'));
      result.selection.characteristicMonth = Number(query.get('characteristicMonth'));
      for (const row of result.cumulative) row.throughMonth = result.selection.month;
      for (const row of result.characteristicsCumulative) {
        row.throughMonth = result.selection.characteristicMonth;
        for (const metric of Object.keys(row.throughMonths)) row.throughMonths[metric] = row.throughMonth;
        for (const metrics of Object.values(row.programThroughMonths)) for (const metric of Object.keys(metrics)) metrics[metric] = row.throughMonth;
      }
    }
    return Response.json(result);
  });
  const profile = await demoSources.find(source => source.id === 'profile').load(signal, { developer: 'самолет', region: 'rf' });
  assert.ok(profile.length);
  assert.ok(profile.every(chart => chart.context.startsWith('САМОЛЕТ · Москва')));
  assert.ok(profile.every(chart => chart.scope === `profile/${encodeURIComponent('самолет')}`));
  assert.ok(calls.some(url => new URL(url, 'http://localhost').searchParams.get('developer') === 'самолет'));
  calls.length = 0;
  await demoSources.find(source => source.id === 'mortgage').load(signal, { region: 'msk', month: 2 });
  assert.equal(new URL(calls.at(-1), 'http://localhost').searchParams.get('month'), '2');
  calls.length = 0;
  await demoSources.find(source => source.id === 'preferential').load(signal, { region: 'msk', month: 3 });
  const query = new URL(calls.at(-1), 'http://localhost').searchParams;
  assert.equal(query.get('month'), '3');
  assert.equal(query.get('characteristicMonth'), '3');
  await assert.rejects(demoSources.find(source => source.id === 'mortgage').load(signal, { region: 'rf' }), /регион/);
});
test('missing Moscow never silently falls back to RF while the default region control still says Moscow', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async url => {
    calls.push(String(url));
    assert.ok(String(url).includes('/catalog'), 'no report should load for an unavailable region');
    const result = structuredClone(fixtures.response(url));
    result.regions = result.regions.some(region => region.id === 'rf') ? result.regions.filter(region => region.id === 'rf') : [{ ...result.regions[0], id: 'rf', label: 'Российская Федерация' }];
    if ('defaultRegion' in result) result.defaultRegion = 'rf';
    return Response.json(result);
  });
  for (const id of ['apartments', 'sales', 'annual', 'mortgage', 'preferential']) for (const options of [undefined, { region: 'msk' }]) {
    calls.length = 0;
    await assert.rejects(demoSources.find(source => source.id === id).load(new AbortController().signal, options), /регион/);
    assert.equal(calls.length, 1);
  }
});

test('home has no update status or update request; brand and pinned Home use real accessible links', async () => {
  const { createElement } = await import('react'), { renderToStaticMarkup } = await import('react-dom/server');
  const { Home } = await import('./ServicePages.tsx'), { default: Sidebar } = await import('./Sidebar.tsx');
  const home = renderToStaticMarkup(createElement(Home, { navigate() {} }));
  assert.doesNotMatch(home, /Последнее обновление|Свежесть источников|Обновить сводку/); assert.match(home, /href="\/demo"/);
  const sidebar = renderToStaticMarkup(createElement(Sidebar, { open: false, onClose() {}, compact: false, onCompact() {}, path: '/home', onNavigate() {} }));
  assert.match(sidebar, /<a class="brand" href="\/home" aria-label=/); assert.match(sidebar, /class="navigation-home"/); assert.match(sidebar, /aria-current="page"/);
});
