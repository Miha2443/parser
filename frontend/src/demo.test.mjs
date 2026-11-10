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
const { demoPanelContext, demoPanelTitle, demoSlides, demoSourceTitle } = await import('./demoSlides.ts');

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
  for (const id of ['mortgage/annual', 'mortgage/monthly', 'preferential/flows/annual', 'preferential/flows/cumulative', 'preferential/characteristics/annual']) assert.equal(slides.find(slide => slide.id === id).charts.length, 2);
  assert.match(demoSourceTitle('permits/housing'), /РС/);
  assert.match(demoSourceTitle('annual/total'), /РВ/);
  assert.match(demoSourceTitle('operational/housing'), /РВ/);
  assert.equal(demoSlides([charts[0]])[0].layout, 'single');
  assert.equal(demoSlides([{ ...charts[0], empty: true }]).length, 0);
  const scoped = demoSlides([chart('profile/a/annual', 'profile/a'), chart('profile/a/structure/0', 'profile/a'), chart('profile/b/annual', 'profile/b')]);
  assert.deepEqual(scoped.map(slide => slide.charts.length), [2, 1], 'different companies must never share a composite screen');
});

test('renovation fund has no geography slides while other developer charts remain available', async () => {
  const { normalizeSnapshot } = await import('./snapshot.ts');
  const { demoProfileCharts } = await import('./DemoProfileCharts.tsx');
  const source = JSON.parse(readFileSync(new URL('../public/profile-snapshot.json', import.meta.url), 'utf8'));
  const base = source.profiles[0], ids = [];
  for (const key of ['фонд реновации', 'другой инвестор']) {
    const payload = { ...source, profiles: [{ ...base, developerKey: key, developer: key,
      housingComparison: [{ title: 'Geography', segments: [{ key: 'msk', valueM2: 100 }, { key: 'otherRf', valueM2: 200 }] }] }] };
    const snapshot = normalizeSnapshot(payload), developer = snapshot.developers[0], profile = developer.regions[0];
    const charts = demoProfileCharts(profile, { id: developer.id, name: developer.name, regions: [profile.id] }, snapshot);
    ids.push(charts.map(chart => chart.id));
    assert.ok(charts.some(chart => chart.id === 'profile/annual'));
    assert.equal(charts.filter(chart => chart.id.includes('/geography/')).length, key === 'фонд реновации' ? 0 : 1);
  }
  assert.deepEqual(ids[0], ids[1].filter(id => !id.includes('/geography/')));
});
const groupedDemoSections = [
  { source: 'sales', title: 'Распроданность', ids: ['forecast', 'monthly_percent'] },
  { source: 'apartments', title: 'Квартирография', ids: ['distribution', 'rooms'] },
  { source: 'linear', title: 'Годовой ввод линейных объектов', ids: ['roads', 'bridges', 'crossings', 'metro_stations', 'metro_tunnels', 'depots'] },
];
const groupedDemoChart = (id, overrides = {}) => ({ id, title: id, context: 'Москва · 2026-11-10', href: '/', empty: false, rows: [['n'], [0]], option: () => ({ series: [] }), ...overrides });

test('all six preferential characteristics share one annual screen and a separate cumulative screen', () => {
  const metrics = ['averageAmountMln', 'ratePct', 'termMonths', 'ownFundsPct', 'areaM2', 'priceThousandM2'];
  const charts = ['annual', 'cumulative'].flatMap(period => metrics.map(metric => groupedDemoChart(`preferential/${metric}/${period}`)));
  const slides = demoSlides(charts);
  assert.deepEqual(slides.map(slide => slide.id), ['preferential/characteristics/annual', 'preferential/characteristics/cumulative']);
  assert.ok(slides.every(slide => slide.layout === 'grid' && slide.charts.length === 6));
  assert.deepEqual(slides.flatMap(slide => slide.charts), charts);
  for (const period of ['annual', 'cumulative']) for (let mask = 1; mask < 64; mask++) {
    const selected = charts.filter(chart => chart.id.endsWith(`/${period}`)).filter((_, index) => mask & 1 << index);
    const [slide] = demoSlides(selected);
    assert.equal(slide.id, `preferential/characteristics/${period}`);
    assert.equal(slide.layout, selected.length === 1 ? 'single' : 'grid');
    assert.deepEqual(slide.charts, selected);
  }
});

test('preferential characteristics preserve scopes, periods, empty filtering and independent flow screens', () => {
  const charts = [groupedDemoChart('preferential/averageAmountMln/annual', { scope: 'msk' }),
    groupedDemoChart('preferential/ratePct/annual', { scope: 'rf' }),
    groupedDemoChart('preferential/ratePct/annual', { scope: 'msk' }),
    groupedDemoChart('preferential/termMonths/cumulative', { scope: 'msk' }),
    groupedDemoChart('preferential/ownFundsPct/annual', { scope: 'msk', empty: true }),
    groupedDemoChart('preferential/volumeMln/annual'), groupedDemoChart('preferential/count/annual')];
  const slides = demoSlides(charts);
  assert.deepEqual(slides.map(slide => [slide.id, slide.charts.length]), [
    ['preferential/characteristics/annual/msk', 2], ['preferential/characteristics/annual/rf', 1],
    ['preferential/characteristics/cumulative/msk', 1], ['preferential/flows/annual', 2],
  ]);
  assert.ok(slides.every(slide => slide.sourceTitle === demoSourceTitle('preferential/')));
});

test('characteristic panels do not repeat the shared annual or cumulative heading', () => {
  for (const [period, suffix] of [['annual', 'по годам'], ['cumulative', 'накопленный период']]) {
    const chart = groupedDemoChart(`preferential/ownFundsPct/${period}`, { title: `Собственные средства заёмщика · ${suffix}` });
    const [slide] = demoSlides([chart, groupedDemoChart(`preferential/ratePct/${period}`)]);
    assert.equal(demoPanelTitle(chart, slide), 'Собственные средства заёмщика');
    assert.equal(chart.title, `Собственные средства заёмщика · ${suffix}`);
  }
  const chart = groupedDemoChart('preferential/count/annual', { title: 'Количество кредитов · по годам' });
  assert.equal(demoPanelTitle(chart, demoSlides([chart])[0]), chart.title);
});

for (const { source, title, ids } of groupedDemoSections) test(`${source} groups every multi-chart selection on one grid and leaves a lone chart single`, () => {
  const charts = ids.map(id => groupedDemoChart(`${source}/${id}`));
  const before = JSON.stringify(charts);
  for (let mask = 1; mask < 2 ** charts.length; mask++) {
    const selection = new Set(charts.filter((_chart, index) => mask & (1 << index)).map(chart => chart.id));
    const selected = demoSelection(charts, selection), slides = demoSlides(selected);
    assert.equal(slides.length, 1, `${source}: selection ${mask}`);
    const [slide] = slides;
    assert.equal(slide.id, `${source}/overview`);
    assert.equal(slide.layout, selected.length > 1 ? 'grid' : 'single');
    assert.equal(slide.title, selected.length > 1 ? title : selected[0].title);
    assert.equal(slide.sourceTitle, demoSourceTitle(selected[0].id));
    assert.equal(slide.context, charts[0].context);
    assert.deepEqual(slide.charts, selected);
    slide.charts.forEach((chart, index) => assert.equal(chart, selected[index]));
  }
  assert.equal(JSON.stringify(charts), before, 'grouping must not mutate charts or source data');
});

test('new section grids preserve explicit scopes and never combine sources or developers', () => {
  const charts = groupedDemoSections.flatMap(({ source, ids }) => [
    groupedDemoChart(`${source}/${ids[0]}`, { scope: 'developer/a' }),
    groupedDemoChart(`${source}/${ids[0]}`, { scope: 'developer/b' }),
    groupedDemoChart(`${source}/${ids[1]}`, { scope: 'developer/a' }),
    groupedDemoChart(`${source}/${ids[0]}`),
    groupedDemoChart(`${source}/${ids[1]}`, { scope: 'developer/b', empty: true }),
  ]);
  const slides = demoSlides(charts);
  assert.equal(slides.length, 9);
  assert.equal(new Set(slides.map(slide => slide.id)).size, slides.length);
  for (const { source } of groupedDemoSections) {
    const section = slides.filter(slide => slide.id.startsWith(`${source}/`));
    assert.deepEqual(section.map(slide => slide.layout), ['grid', 'single', 'single']);
    assert.deepEqual(section.map(slide => slide.charts.map(chart => chart.scope)), [['developer/a', 'developer/a'], ['developer/b'], [undefined]]);
    assert.ok(section.every(slide => slide.sourceTitle === demoSourceTitle(`${source}/`)));
  }
});

test('new section grids retain first-seen order, empty filtering, panel contexts and unrelated groups', () => {
  const charts = [
    groupedDemoChart('sales/empty', { empty: true }),
    groupedDemoChart('permits/main'),
    groupedDemoChart('linear/roads', { context: 'Москва · км · 2026' }),
    groupedDemoChart('sales/monthly_percent'),
    groupedDemoChart('apartments/distribution'),
    groupedDemoChart('mortgage/count/annual'),
    groupedDemoChart('linear/bridges', { context: 'Москва · ед. · 2026' }),
    groupedDemoChart('apartments/rooms', { context: 'Застройщик А · Москва · 2026-11-10' }),
    groupedDemoChart('sales/forecast'),
    groupedDemoChart('mortgage/volume/annual'),
    groupedDemoChart('linear/empty', { empty: true }),
    groupedDemoChart('apartments/empty', { empty: true }),
    groupedDemoChart('profile/a/annual', { scope: 'profile/a' }),
    groupedDemoChart('profile/b/annual', { scope: 'profile/b' }),
    groupedDemoChart('profile/a/structure/0', { scope: 'profile/a' }),
  ];
  const slides = demoSlides(charts);
  assert.deepEqual(slides.map(slide => [slide.id, slide.layout]), [
    ['permits/main', 'single'], ['linear/overview', 'grid'], ['sales/overview', 'grid'],
    ['apartments/overview', 'grid'], ['mortgage/annual', 'pair'],
    ['profile/a/commissioning', 'overview'], ['profile/b/commissioning', 'single'],
  ]);
  assert.deepEqual(slides.flatMap(slide => slide.charts).map(chart => chart.id).sort(), charts.filter(chart => !chart.empty).map(chart => chart.id).sort());
  const linear = slides[1], apartments = slides[3];
  assert.equal(linear.context, 'Москва');
  assert.deepEqual(linear.charts.map(chart => demoPanelContext(chart, linear)), ['км · 2026', 'ед. · 2026']);
  assert.deepEqual(apartments.charts.map(chart => demoPanelContext(chart, apartments)), ['', 'Застройщик А · Москва · 2026-11-10']);
  for (const { source, ids } of groupedDemoSections) {
    const empty = groupedDemoChart(`${source}/empty`, { empty: true }), chart = groupedDemoChart(`${source}/${ids[0]}`);
    assert.deepEqual(demoSlides([empty]), []);
    const [single] = demoSlides([empty, chart]);
    assert.equal(single.layout, 'single');
    assert.equal(single.title, chart.title);
    assert.deepEqual(single.charts, [chart]);
    assert.deepEqual(demoSlides(demoSelection([empty, chart], new Set(['obsolete', empty.id]))), []);
  }
});

test('real sales, apartment and six linear adapters produce one logical screen per selected source', async t => {
  t.mock.method(globalThis, 'fetch', async url => Response.json(fixtures.response(url)));
  for (const { source, ids } of groupedDemoSections) {
    const charts = await demoSources.find(item => item.id === source).load(new AbortController().signal);
    const selected = demoSelection(charts, null), slides = demoSlides(selected);
    assert.equal(selected.length, ids.length, source);
    assert.equal(slides.length, 1, source);
    assert.equal(slides[0].layout, 'grid');
    assert.equal(slides[0].sourceTitle, demoSourceTitle(`${source}/`));
    assert.deepEqual(slides[0].charts, selected);
  }
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
  assert.match(source, /preview \? built : demoPresentationOption\(built, width, height, hidden, entry\.id\)/);
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
