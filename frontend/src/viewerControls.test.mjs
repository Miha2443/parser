import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { registerHooks } from 'node:module';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const root = new URL('./', import.meta.url).href;
const privateComponents = {
  'Apartments.tsx': ['VolumeTable', 'Evidence'],
  'Economics.tsx': ['ExcelExport', 'Evidence'],
  'Linear.tsx': ['DashboardExport'],
  'PermitsPage.tsx': ['PermitChart'],
  'ConstructionTree.tsx': ['ReadyTree'],
  'Sales.tsx': ['Evidence'],
  'DomRfPage.tsx': ['LegacySource'],
};
const hooks = registerHooks({
  resolve(specifier, context, nextResolve) {
    if (context.parentURL?.startsWith(root)) {
      if (specifier.includes('?worker&url')) return { url: 'viewer-test:worker', shortCircuit: true };
      if (specifier === 'maplibre-gl') return { url: 'viewer-test:maplibre', shortCircuit: true };
      if (/^\.\.?\/[^.]+$/.test(specifier)) {
        const extension = ['.ts', '.tsx'].find(ext => existsSync(new URL(`${specifier}${ext}`, context.parentURL)));
        if (extension) return nextResolve(`${specifier}${extension}`, context);
      }
    }
    if (/^echarts\/(core|charts|components|renderers)$/.test(specifier)) return nextResolve(`${specifier}.js`, context);
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url === 'viewer-test:worker') return { format: 'module', shortCircuit: true, source: 'export default "";' };
    if (url === 'viewer-test:maplibre') return { format: 'module', shortCircuit: true, source: 'export function setWorkerUrl() {}' };
    if (url.endsWith('.css')) return { format: 'module', shortCircuit: true, source: '' };
    if (url.endsWith('.tsx')) {
      const names = privateComponents[url.split('/').at(-1)] ?? [];
      const source = readFileSync(new URL(url), 'utf8') + (names.length ? `\nexport { ${names.join(', ')} };\n` : '');
      return { format: 'module', shortCircuit: true, source: ts.transpileModule(source, {
        compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX }, fileName: url,
      }).outputText };
    }
    return nextLoad(url, context);
  },
});
const { AuthContext } = await import('./auth.tsx');
const { Chart } = await import('./Chart.tsx');
const { default: ProfileExport } = await import('./ProfileExport.tsx');
const { default: ApartmentExport } = await import('./ApartmentExport.tsx');
const { default: Objects } = await import('./Objects.tsx');
const apartments = await import('./Apartments.tsx');
const operational = await import('./Operational.tsx');
const permits = await import('./PermitsPage.tsx');
const { MarketExport } = await import('./Annual.tsx');
const { DashboardExport } = await import('./Linear.tsx');
const economics = await import('./Economics.tsx');
const sales = await import('./Sales.tsx');
const { ReadyTree } = await import('./ConstructionTree.tsx');
const { LegacySource } = await import('./DomRfPage.tsx');
const { MapObjectDetails } = await import('./MapPage.tsx');
hooks.deregister();

const noop = () => {};
function render(Component, props = {}, role) {
  const element = createElement(Component, props);
  if (!role) return renderToStaticMarkup(element);
  return renderToStaticMarkup(createElement(AuthContext.Provider, { value: {
    enabled: true, user: { username: role, role }, canAnalyze: role === 'analyst', logout: async () => {},
  } }, element));
}
const table = { id: 'raw', title: 'Private Raw Table', columns: [{ id: 'value', label: 'Value' }], rows: [{ value: 'PRIVATE_RECORD' }] };
const metadata = { schemaVersion: 1, version: 'secret-version', generatedAt: '2026-11-01', reportDate: '2026-10-31', source: {
  date: '2026-10-31', files: ['PRIVATE_FILE.xlsx'], issues: [], dateEvidence: 'PRIVATE_EVIDENCE',
  downloadSummary: 'PRIVATE_DOWNLOAD_SUMMARY', recordedSources: ['PRIVATE_RECORD_SOURCE'], recordedSourceEvidence: 'PRIVATE_RECORDED_EVIDENCE',
  provenance: table,
} };

test('every reusable Excel exporter is absent for viewer and remains available to analyst and legacy callers', () => {
  const exporters = [
    [ProfileExport, { developer: 'dev', region: 'msk', version: 'v1', frozen: false }],
    [ApartmentExport, { region: 'msk', version: 'v1' }],
    [operational.CommissioningExport, { kind: 'operational', selection: { year: 2026, quarter: 1 }, version: 'v1' }],
    [MarketExport, { kind: 'annual', selection: { region: 'msk' }, version: 'v1' }],
    [DashboardExport, { version: 'v1' }],
    [permits.ConstructionExport, { url: '/api/export', filename: 'report.xlsx', label: 'Download Excel', refresh: noop }],
    [sales.SalesExport, { region: 'msk', period: '2026-10', version: 'v1' }],
    [economics.ExcelExport, { family: 'ipc', data: { version: 'v1', selection: {} }, retry: noop }],
  ];
  for (const [Component, props] of exporters) {
    assert.equal(render(Component, props, 'viewer'), '', Component.name);
    assert.match(render(Component, props, 'analyst'), /<button/);
    assert.equal(render(Component, props), render(Component, props, 'analyst'), `${Component.name}: legacy default`);
  }
});

test('viewer keeps chart canvas, legend controls and forecast semantics but not data/download controls', () => {
  const props = { theme: 'light', label: 'Annual forecast, million m2', filename: 'annual', rows: [['Year', 'Value'], ['2026', 1.2]],
    option: { series: [{ type: 'bar', name: 'Forecast', data: [1.2], itemStyle: { color: '#246380', borderType: 'dashed' } }] },
    legendHints: { Forecast: 'Forecast, not actual' },
  };
  const viewer = render(Chart, props, 'viewer'), analyst = render(Chart, props, 'analyst');
  assert.match(viewer, /chart-canvas|role="img"/);
  assert.match(viewer, /Forecast, not actual/);
  assert.match(viewer, /aria-pressed="true"/);
  assert.doesNotMatch(viewer, /data-toggle|chart-data-panel|CSV|PNG|Данные и скачивание/);
  assert.match(analyst, /data-toggle/);
});

test('custom permit chart keeps accessible growth values and legend controls without CSV/PNG panels', () => {
  const props = { theme: 'light', label: 'Permits', filename: 'permits', rows: [['Year', 'Raw'], [2026, 100]], growthLabel: 'January-October 2026',
    growth: [{ year: 2026, value: 25 }], buildOption: () => ({ series: [
      { type: 'bar', name: 'Actual', data: [100], itemStyle: { color: '#338080' } },
      { type: 'line', name: 'Growth, %', data: [25], itemStyle: { color: '#285d70' } },
    ] }),
  };
  const viewer = render(permits.PermitChart, props, 'viewer');
  assert.match(viewer, /chart-canvas/); assert.match(viewer, /Growth, %/); assert.match(viewer, /January-October 2026/);
  assert.match(viewer, /25 %/);
  assert.doesNotMatch(viewer, /data-toggle|chart-data-panel|CSV|PNG/);
  assert.match(render(permits.PermitChart, props, 'analyst'), /data-toggle/);
});

test('object registries and raw table disclosures do not render for viewer', () => {
  const components = [
    [Objects, { tables: [{ title: 'Registry', columns: ['value'], rows: [{ value: 'PRIVATE_RECORD' }] }], developerId: 'dev' }],
    [operational.CommissioningTable, { table, params: new URLSearchParams(), change: noop, scope: 'raw', filename: 'raw' }],
    [sales.SegmentTable, { table, params: new URLSearchParams(), change: noop, filename: 'raw' }],
  ];
  for (const [Component, props] of components) {
    assert.equal(render(Component, props, 'viewer'), '', Component.name);
    assert.match(render(Component, props, 'analyst'), /PRIVATE_RECORD/);
  }
});

test('shared source/evidence components omit provenance, files, versions and raw records for viewer', () => {
  const components = [
    [operational.CommissioningEvidence, { metadata, anchor: 'sources' }],
    [permits.ConstructionSource, { metadata, label: 'Sources', children: createElement('p', null, 'PRIVATE_CHILD_RECORD') }],
    [apartments.Evidence, { metadata }],
    [sales.Evidence, { metadata }],
    [economics.Evidence, { metadata, params: new URLSearchParams(), change: noop }],
    [LegacySource, { data: { ...metadata, period: { label: 'October 2026' }, region: { label: 'Moscow' } }, label: 'Sources' }],
  ];
  for (const [Component, props] of components) {
    assert.equal(render(Component, props, 'viewer'), '', Component.name);
    assert.match(render(Component, props, 'analyst'), /PRIVATE_FILE.xlsx/);
  }
});

test('useful aggregate comparison tables stay visible without their download button', () => {
  const props = { rows: [{ id: 'dev', name: 'Developer A', place: 1, apartmentThousandCount: 12.3, areaThousandM2: 45.6, rooms: [] }],
    title: 'Aggregate developer comparison', scope: 'developers', region: 'msk', params: new URLSearchParams(), change: noop };
  const viewer = render(apartments.VolumeTable, props, 'viewer');
  assert.match(viewer, /Aggregate developer comparison/); assert.match(viewer, /Developer A/); assert.match(viewer, /12,3/);
  assert.match(viewer, /<table/); assert.doesNotMatch(viewer, /Скачать|CSV/);
  assert.match(render(apartments.VolumeTable, props, 'analyst'), /Скачать/);
});

test('construction tree retains the visual business metrics but hides raw cells, metadata and CSV', () => {
  const data = { ...metadata, source: { ...metadata.source, file: 'PRIVATE_FILE.xlsx', sheet: 'PRIVATE_SHEET', column: 'PRIVATE_COLUMN', downloadedAt: '2026-11-01', fileDate: '2026-10-31' },
    metrics: ['total', 'housing', 'living', 'investors', 'renovation', 'embedded', 'mop', 'nonresidential'].map((id, i) => ({ id, label: id, value: i + 1, cell: 'PRIVATE_CELL' })) };
  const viewer = render(ReadyTree, { data }, 'viewer');
  assert.match(viewer, /ct-diagram/); assert.match(viewer, /Общая площадь/); assert.match(viewer, /млн м²/);
  assert.doesNotMatch(viewer, /PRIVATE_|ct-source|Скачать CSV/);
  assert.match(render(ReadyTree, { data }, 'analyst'), /PRIVATE_FILE.xlsx/);
});

test('map object summary remains useful while source record fields are analyst-only', () => {
  const object = { id: 'o1', object_name: 'Residential block', address: 'Example address', developer: 'Developer A', builder: 'Builder A',
    source_sheet: 'PRIVATE_SHEET', permit: 'PRIVATE_PERMIT', coord_source: 'PRIVATE_COORD_SOURCE', quality: 'approximate',
    status: 'Building', year: 2026, okrug: 'District A', district: 'Area A', area_total: 1234, area_living: 1000, apartments: 25, lat: 55.7, lon: 37.6 };
  const viewer = render(MapObjectDetails, { object, close: noop }, 'viewer');
  assert.match(viewer, /Residential block/); assert.match(viewer, /Example address/); assert.match(viewer, /Developer A/);
  assert.match(viewer, /Приблизительно/); assert.match(viewer, /2026/);
  assert.doesNotMatch(viewer, /PRIVATE_|Реестр|Разрешение/);
  assert.match(render(MapObjectDetails, { object, close: noop }, 'analyst'), /PRIVATE_SHEET|PRIVATE_COORD_SOURCE/);
});
