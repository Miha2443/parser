import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { registerHooks } from 'node:module';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import ts from 'typescript';
import * as echarts from 'echarts/core.js';
import { BarChart, LineChart, PieChart } from 'echarts/charts.js';
import { GridComponent, TooltipComponent, LegendComponent, GraphicComponent, MarkPointComponent } from 'echarts/components.js';
import { SVGRenderer } from 'echarts/renderers.js';
import fixtures from './demoFixtures.cjs';
import annual from './annualConstruction.browser.cjs';
import commissioning from './commissioning.browser.cjs';

// Use the same TS/TSX resolver as the existing demonstration contract tests.
const root = new URL('./', import.meta.url).href;
const hooks = registerHooks({
  resolve(specifier, context, next) {
    if (context.parentURL?.startsWith(root) && /^\.\.?\/[^.]+$/.test(specifier)) {
      const extension = ['.ts', '.tsx'].find(ext => existsSync(new URL(`${specifier}${ext}`, context.parentURL)));
      if (extension) return next(`${specifier}${extension}`, context);
    }
    if (/^echarts\/(core|charts|components|renderers)$/.test(specifier)) return next(`${specifier}.js`, context);
    return next(specifier, context);
  },
  load(url, context, next) {
    if (url.endsWith('.css')) return { format: 'module', shortCircuit: true, source: '' };
    if (url.endsWith('.tsx')) return { format: 'module', shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX }, fileName: url,
    }).outputText };
    return next(url, context);
  },
});
after(() => hooks.deregister());
echarts.use([BarChart, LineChart, PieChart, GridComponent, TooltipComponent, LegendComponent, GraphicComponent, MarkPointComponent, SVGRenderer]);
const { demoSources } = await import('./demoData.ts');
const { readConstructionTree } = await import('./constructionTreeData.ts');
const { normalizeMapReport } = await import('./mapData.ts');
const { readConstructionReport } = await import('./annualConstructionData.ts');
const { readLinearReport } = await import('./commissioningData.ts');

const cwd = fileURLToPath(new URL('../../', import.meta.url));
const python = process.env.PYTHON || 'python';
function project(path, payload) {
  const before = structuredClone(payload);
  const result = JSON.parse(execFileSync(python, ['-X', 'utf8', '-c',
    'import json,sys; from backend.viewer_payload import viewer_payload; v=json.load(sys.stdin); original=json.dumps(v["payload"],sort_keys=True); result=viewer_payload(v["path"],v["payload"]); assert json.dumps(v["payload"],sort_keys=True)==original; print(json.dumps(result,ensure_ascii=False,allow_nan=False))'],
    { cwd, input: JSON.stringify({ path, payload }), encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 }));
  assert.deepEqual(payload, before);
  return result;
}
const privateValue = 'postgresql://dbuser:VIEWER_SECRET@db/private';
function poisoned(raw) {
  const copy = structuredClone(raw);
  copy.futureMetadata = { databasePassword: privateValue };
  copy.notes = [privateValue];
  if (copy.source) copy.source.futureMetadata = { databasePassword: privateValue };
  return copy;
}

test('all 13 real report loaders decode viewer JSON and render identical graph data', async () => {
  const originalFetch = globalThis.fetch;
  try {
    const load = async projected => {
      globalThis.fetch = async input => {
        const raw = poisoned(fixtures.response(String(input)));
        const result = projected ? project(String(input), raw) : raw;
        if (projected) {
          const json = JSON.stringify(result);
          assert.ok(!json.includes('VIEWER_SECRET'));
          assert.ok(!json.includes('data/raw/'));
          assert.ok(!json.includes('data/processed/'));
          assert.ok(!json.includes('futureMetadata'));
          if (Array.isArray(result.notes)) assert.deepEqual(result.notes, []);
        }
        return new Response(JSON.stringify(result), { headers: { 'Content-Type': 'application/json' } });
      };
      const reports = new Map();
      for (const source of demoSources) reports.set(source.id, await source.load(new AbortController().signal));
      return reports;
    };
    const analyst = await load(false), viewer = await load(true);
    assert.equal(viewer.size, 13);
    let rendered = 0;
    for (const [id, charts] of viewer) {
      assert.ok(charts.length > 0, id);
      const graphRows = charts => charts.map(chart => chart.rows.map(row => row.map(value =>
        typeof value === 'string' && /\.(?:json|xlsx?|pkl|csv)\b/.test(value) ? '' : value)));
      assert.deepEqual(graphRows(charts), graphRows(analyst.get(id)), `${id}: numbers/nulls/periods`);
      for (const chart of charts) {
        const option = chart.option('light', 1000, [], 600), original = analyst.get(id).find(row => row.id === chart.id).option('light', 1000, [], 600);
        const seriesData = option => JSON.parse(JSON.stringify(option.series.map(series => series.data)));
        assert.deepEqual(seriesData(option), seriesData(original), chart.id);
        const renderer = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 1000, height: 600 });
        try {
          renderer.setOption(option);
          assert.match(renderer.renderToSVGString(), /<svg[\s\S]*<path/);
          rendered++;
        } finally { renderer.dispose(); }
      }
    }
    assert.ok(rendered >= 40, `Rendered ${rendered} graphs`);
  } finally { globalThis.fetch = originalFetch; }
});

test('legacy construction and quarterly readers retain normalized tables', () => {
  const construction = annual.constructionReport();
  const projected = project('/api/v1/construction', construction);
  assert.deepEqual(readConstructionReport(projected, construction.selection).permits.chart, construction.permits.chart);
  const quarterly = commissioning.linearReport();
  assert.deepEqual(readLinearReport(project('/api/v1/linear', quarterly), quarterly.selection).allPeriods, quarterly.allPeriods);
});

test('strict tree decoder accepts synthetic evidence without actual source addresses', () => {
  const tree = { schemaVersion: 1, version: 'tree', generatedAt: '2026-11-10T10:00:00Z', unit: 'million_m2',
    source: { file: '/runtime/releases/private/book.xlsx', sheet: 'Дерево текущ стр во', column: 'Итог', downloadedAt: '2026-11-10T09:00:00Z', fileDate: '2026-11-10' },
    metrics: ['total', 'housing', 'embedded', 'mop', 'living', 'investors', 'renovation', 'nonresidential', 'budget', 'nonbudget'].map(id => ({ id, label: id, value: id === 'budget' ? null : id === 'mop' ? 0 : 10, cell: 'L532' })),
    nonresidentialSectors: { status: 'available', sheet: 'Реестр ОКС', areaColumn: 'Q', sectorColumn: 'AR', canonicalValue: 10, registryValue: 10,
      filters: { 'Назначение': 'Нежилье', 'Статус объекта': 'Строящийся', 'Статус РС': 'Действует' },
      items: [{ id: 'sector', label: 'Образование', value: 10, rows: [983, 12345], count: 2 }] } };
  const projected = project('/api/v1/construction/tree', tree), parsed = readConstructionTree(projected);
  assert.deepEqual(parsed.metrics.map(metric => metric.value), tree.metrics.map(metric => metric.value));
  assert.equal(parsed.source.file, 'restricted');
  assert.ok(parsed.metrics.every(metric => metric.cell === 'A1'));
  assert.deepEqual(parsed.nonresidentialSectors.items[0].rows, [2, 3]);
});

test('map decoder keeps public marker coordinates without worksheet or geocoder evidence', () => {
  const row = { id: 'Реестр РВ:789', object_id: 'RAW-UIN', address: 'Москва', has_coords: true, lat: 55.75, lon: 37.61, area_total: 0,
    quality: 'approximate', source_row: 789, coord_source: privateValue, arbitraryRawColumn: privateValue };
  const counts = { total: 1, onMap: 1, located: 1, approximate: 0, missing: 0 };
  const raw = { schemaVersion: 1, version: 'map', source: { files: ['private.xlsx'], issues: [] }, notes: [], rows: [row], totals: counts, filteredTotals: counts,
    geojson: { type: 'FeatureCollection', features: [{ type: 'Feature', id: row.id, geometry: { type: 'Point', coordinates: [37.61, 55.75] }, properties: row }] } };
  const parsed = normalizeMapReport(project('/api/v1/map', raw));
  assert.deepEqual(parsed.geojson.features[0].geometry, raw.geojson.features[0].geometry);
  assert.equal(parsed.rows[0].id, parsed.geojson.features[0].properties.id);
  assert.equal(parsed.rows[0].id, parsed.geojson.features[0].id);
  assert.equal(parsed.geojson.features[0].properties.quality, 'approximate');
  assert.ok(!JSON.stringify(parsed).includes('RAW-UIN'));
  assert.ok(!JSON.stringify(parsed).includes('VIEWER_SECRET'));
});
