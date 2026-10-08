import test from 'node:test';
import assert from 'node:assert/strict';
import { mapBounds, mapCsvRows, mapQuery, mapRepairs, normalizeMapCatalog, normalizeMapReport } from './mapData.ts';

test('map URL produces typed repeated filters without table state', () => {
  const params = mapQuery(new URLSearchParams('status=Введено|Строится&okrug=ЦАО&okrug=ЗАО&year_from=2011&quality=approximate&page=2&object=rv:1'));
  assert.deepEqual(params.getAll('status'), ['Введено', 'Строится']);
  assert.deepEqual(params.getAll('okrug'), ['ЦАО', 'ЗАО']);
  assert.equal(params.get('year_from'), '2011');
  assert.equal(params.has('page'), false);
  assert.equal(params.has('object'), false);
});
test('map bounds retain longitude latitude order and exclude missing positions', () => {
  assert.deepEqual(mapBounds([{ has_coords: true, lon: 37.4, lat: 55.7 }, { has_coords: true, lon: 38, lat: 55.8 }, { has_coords: false, lon: 0, lat: 0 }]), [[37.4, 55.7], [38, 55.8]]);
  assert.equal(mapBounds([]), null);
});
test('map schema rejects incompatible replies rather than inventing rows', () => {
  assert.throws(() => normalizeMapCatalog({ schemaVersion: 2 }));
  assert.throws(() => normalizeMapReport({ schemaVersion: 1, version: 'one', source: { files: [], issues: [] } }));
  const catalog = { schemaVersion: 1, version: 'one', source: { files: [], issues: [] }, developers: ['A'], statuses: [], okrugs: [], years: {}, scope: {} };
  assert.equal(normalizeMapCatalog(catalog), catalog);
});

test('map CSV keeps every supplied row and distinguishes missing coordinates from zero', () => {
  const rows = [{ id: 'rv:1', has_coords: true, lat: 55.7, lon: 37.4, area: 0 },
    { id: 'oks:2', has_coords: false, lat: null, lon: null, area: null }];
  assert.deepEqual(mapCsvRows(rows), [['id', 'has_coords', 'lat', 'lon', 'area'],
    ['rv:1', 'true', 55.7, 37.4, 0], ['oks:2', 'false', null, null, null]]);
  assert.deepEqual(mapCsvRows([]), [[]]);
});

test('refreshed map catalogs remove disappeared selections and stale object/page state', () => {
  const catalog = { developers: ['B'], statuses: ['Введено'], okrugs: ['ЦАО'] };
  const params = new URLSearchParams('developer=A&status=Введено|Строится&okrug=ЗАО&object=rv:1&page=2');
  assert.deepEqual(mapRepairs(catalog, params), { developer: null, status: 'Введено', okrug: null, object: null, page: null });
  assert.deepEqual(mapRepairs(catalog, new URLSearchParams('developer=B&status=Введено&year_from=2011&year_to=2030')), {});
  assert.deepEqual(mapRepairs(catalog, new URLSearchParams('quality=exact&year_from=2030&year_to=2011')), { quality: null, year_from: null, year_to: null, object: null, page: null });
});
