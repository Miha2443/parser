import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { loadCatalog, loadProfile, normalizeCatalog, profileUrl, request, selectFilters } from './data.ts';

const fixture = JSON.parse(readFileSync(new URL('../public/profile-snapshot.json', import.meta.url), 'utf8'));
const live = { ...fixture, version: 'live-test', controls: { ...fixture.controls, frozen: false } };
const catalogRaw = { ...live, profiles: undefined, controls: { frozen: false, developers: [...live.controls.developers, { developer: 'NEW COMPANY', developerKey: 'new company', regions: [] }] } };

test('catalog includes developers without snapshot profiles and defaults empty apartment regions to Moscow', () => {
  const catalog = normalizeCatalog(catalogRaw);
  assert.equal(catalog.developers.length, fixture.controls.developers.length + 1);
  assert.deepEqual(catalog.developers.at(-1), { id: 'new company', name: 'NEW COMPANY', regions: ['msk'] });
  assert.deepEqual(catalog.metadata.sources.map(s => s.files), Object.values(fixture.provenance.sources).map(s => s.candidateRawFiles));
  assert.ok(catalog.metadata.sources.every(s => s.note.includes('не журнал чтений')));
  assert.equal(catalog.metadata.version, 'live-test');
  assert.equal(catalog.metadata.frozen, false);
});

test('catalog rejects unsupported, frozen, invalid and duplicate API selectors', () => {
  for (const raw of [{ ...catalogRaw, schemaVersion: 2 }, { ...catalogRaw, version: null }, { ...catalogRaw, generatedAt: 'invalid' }, { ...catalogRaw, controls: { ...catalogRaw.controls, frozen: true } }, { ...catalogRaw, controls: { ...catalogRaw.controls, developers: [...catalogRaw.controls.developers, catalogRaw.controls.developers[0]] } }]) assert.throws(() => normalizeCatalog(raw));
});

test('URL filter selection uses normalized keys and available regions only', () => {
  const catalog = normalizeCatalog(catalogRaw);
  const selected = selectFilters(catalog, new URLSearchParams({ developer: 'new company', region: 'rf' }));
  assert.equal(selected.developer.id, 'new company');
  assert.equal(selected.region, 'msk');
  assert.equal(selectFilters(catalog, new URLSearchParams({ developer: 'unknown', region: 'unknown' })).developer.id, catalog.developers[0].id);
});

test('profile and full Excel URLs encode keys and never include local year filters', () => {
  assert.equal(profileUrl('a & b/гк', 'rf'), '/api/v1/profile?developer=a+%26+b%2F%D0%B3%D0%BA&region=rf');
  assert.equal(profileUrl('a b', 'msk', true), '/api/v1/profile/export?developer=a+b&region=msk');
  assert.equal(profileUrl('a b', 'msk', true, 'v1'), '/api/v1/profile/export?developer=a+b&region=msk&required_version=v1');
});

test('API loads catalog then one profile retaining all years, units, nulls and source metadata', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options });
    return Response.json(url === '/api/v1/catalog' ? catalogRaw : { ...live, profiles: [live.profiles[0]] });
  });
  const controller = new AbortController();
  const catalog = await loadCatalog('api', controller.signal);
  const p = live.profiles[0];
  const { snapshot, catalog: profileCatalog } = await loadProfile(catalog, p.developerKey, p.region, controller.signal);
  assert.equal(profileCatalog.metadata.version, live.version);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].url, profileUrl(p.developerKey, p.region));
  assert.equal(calls[1].options.signal, controller.signal);
  assert.equal(calls[1].options.cache, 'no-store');
  assert.deepEqual(snapshot.developers[0].regions[0], (await import('./snapshot.ts')).normalizeSnapshot({ ...fixture, profiles: [p] }).developers[0].regions[0]);
  assert.deepEqual(snapshot.sources, catalog.metadata.sources);
  assert.equal(snapshot.version, live.version);
  assert.equal(snapshot.frozen, false);
});

test('profile rejects mismatched company/region or multiple profiles', async t => {
  const catalog = normalizeCatalog(catalogRaw);
  t.mock.method(globalThis, 'fetch', async () => Response.json({ ...live, profiles: [live.profiles[0]] }));
  await assert.rejects(loadProfile(catalog, 'wrong', 'msk', new AbortController().signal), /фильтрам/);
  await assert.rejects(loadProfile(catalog, live.profiles[0].developerKey, 'rf', new AbortController().signal), /фильтрам/);
  globalThis.fetch = async () => Response.json(live);
  await assert.rejects(loadProfile(catalog, live.profiles[0].developerKey, 'msk', new AbortController().signal), /схеме/);
});

test('HTTP and network errors remain visible with no snapshot fallback', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async url => { calls.push(url); return new Response('', { status: 503 }); });
  await assert.rejects(loadCatalog('api', new AbortController().signal), /HTTP 503/);
  assert.deepEqual(calls, ['/api/v1/catalog']);
  globalThis.fetch = async () => { throw new TypeError('network'); };
  await assert.rejects(request('/api/v1/catalog', new AbortController().signal), /подключиться/);
});

test('snapshot mode is explicit, frozen, and never requests a live profile', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async url => { calls.push(url); return Response.json(fixture); });
  const controller = new AbortController();
  const catalog = await loadCatalog('snapshot', controller.signal);
  const p = fixture.profiles[0];
  const { snapshot } = await loadProfile(catalog, p.developerKey, p.region, controller.signal);
  assert.deepEqual(calls, ['/profile-snapshot.json']);
  assert.equal(snapshot.frozen, true);
});

test('profile response carries current catalog generation and nonfatal provenance diagnostics', async t => {
  const catalog = normalizeCatalog(catalogRaw), p = live.profiles[0];
  const current = { ...live, version: 'changed', controls: catalogRaw.controls, profiles: [p], provenance: { ...live.provenance, issues: [['warning', 'Fallback mart selected']] } };
  t.mock.method(globalThis, 'fetch', async () => Response.json(current));
  const response = await loadProfile(catalog, p.developerKey, p.region, new AbortController().signal);
  assert.equal(response.catalog.metadata.version, 'changed');
  assert.equal(response.catalog.developers.at(-1).id, 'new company');
  assert.ok(response.snapshot.notes.includes('Диагностика источника (warning): Fallback mart selected'));
});
