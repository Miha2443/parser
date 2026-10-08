import assert from 'node:assert/strict';
import test from 'node:test';
import fixtures from './apartmentFixtures.cjs';
import { apartmentFilters, apartmentRows, apartmentUrl, loadApartmentData, readApartmentCatalog, readApartmentDetail, readApartmentOverview, roomWidths, tablePage } from './apartmentData.ts';

test('catalog preserves exact raw names and region-specific lists', () => {
  const catalog = readApartmentCatalog(fixtures.catalog);
  assert.equal(catalog.developersByRegion.msk.at(-1).id, fixtures.developers.at(-1).id);
  assert.equal(apartmentFilters(catalog, new URLSearchParams()).region.id, 'msk');
  assert.equal(apartmentFilters(catalog, new URLSearchParams({ region: 'rf', developer: fixtures.developers[0].id })).developer.id, 'РФ Компания');
  const rfOnly = readApartmentCatalog({ ...fixtures.catalog, regions: [fixtures.catalog.regions[1]] });
  assert.equal(apartmentFilters(rfOnly, new URLSearchParams({ region: 'msk' })).region.id, 'rf');
});
test('overview retains units, independent nulls, all rows and API Moscow-first order', () => {
  const raw = fixtures.overview(), data = readApartmentOverview(raw, 'msk');
  assert.deepEqual(data, JSON.parse(JSON.stringify(raw)));
  assert.equal(data.apartments[0].count, 12345);
  assert.equal(data.apartments[0].areaThousandM2, 654.3);
  assert.equal(data.apartments[1].count, null);
  assert.equal(data.apartments[1].areaThousandM2, 12.34);
  assert.equal(data.apartments[2].areaThousandM2, null);
  assert.equal(data.regions[0].name, 'Город Москва');
  assert.equal(data.developers.length, 73);
  assert.equal(data.developers[0].apartmentThousandCount, null);
  assert.deepEqual(data.source, fixtures.metadata.source);
  assert.equal(data.reportDate, '02.07.2026');
});
test('detail uses backend summary without recomputing average or shares', () => {
  const id = fixtures.developers.at(-1).id, raw = fixtures.detail('msk', id), data = readApartmentDetail(raw, 'msk', id);
  assert.deepEqual(data, raw);
  assert.equal(data.summary.averageAreaM2, 58.9);
  assert.equal(data.summary.marketSharePercent, 3.25);
  assert.equal(data.referenceAverages[0].averageAreaM2, null);
  assert.equal(data.comparison.length, 11);
  assert.equal(data.comparison.at(-1).id, id);
});
test('room normalization changes widths only; null, zero and negative source values survive', () => {
  const before = structuredClone(fixtures.rooms), widths = roomWidths(before);
  assert.deepEqual(before, fixtures.rooms);
  assert.deepEqual(widths.map(r => r.sharePercent), [30, 20, null, 10]);
  assert.ok(Math.abs(widths.reduce((sum, r) => sum + r.width, 0) - 100) < 1e-10);
  assert.equal(widths[0].width, 50);
  assert.equal(widths[2].width, 0);
  assert.deepEqual(roomWidths([{ type: '1 комн', sharePercent: -3 }, { type: '2 комн', sharePercent: 0 }]).map(r => [r.sharePercent, r.width]), [[-3, 0], [0, 0]]);
});
test('pagination defaults to 50, searches all source rows and reconciles invalid bounds', () => {
  assert.equal(tablePage(fixtures.developers, '', null, null).shown.length, 50);
  assert.equal(tablePage(fixtures.developers, '', '2', null).shown.length, 23);
  assert.equal(tablePage(fixtures.developers, 'А+Б', '999', '7').shown[0].id, fixtures.developers.at(-1).id);
  assert.equal(tablePage(fixtures.developers, '', '-4', '20').current, 1);
  assert.equal(tablePage(fixtures.developers, '', 'x', '20').pageSize, 20);
  assert.equal(tablePage([], '', '4', null).current, 1);
});
test('full exports preserve source shares and omit search/page filters', () => {
  const rows = apartmentRows(fixtures.developers);
  assert.equal(rows.length, 74);
  assert.deepEqual(rows[1].slice(4), [30, 20, null, 10]);
  assert.equal(rows[1][2], null);
  const id = fixtures.developers.at(-1).id, url = new URL(apartmentUrl('msk', id, 'v 1'), 'http://localhost');
  assert.equal(url.pathname, '/api/v1/apartments/export');
  assert.deepEqual([...url.searchParams.keys()], ['region', 'developer', 'required_version']);
  assert.equal(url.searchParams.get('developer'), id);
  assert.equal(url.searchParams.get('required_version'), 'v 1');
  assert.equal(new URL(apartmentUrl('rf', undefined, 'v1'), 'http://localhost').searchParams.has('developer'), false);
});
test('schema and filter mismatch reject invalid responses without changing numeric data', () => {
  for (const raw of [{ ...fixtures.catalog, schemaVersion: 2 }, { ...fixtures.catalog, version: '' }, { ...fixtures.catalog, generatedAt: 'bad' }]) assert.throws(() => readApartmentCatalog(raw));
  assert.throws(() => readApartmentOverview(fixtures.overview('rf'), 'msk'));
  assert.throws(() => readApartmentOverview({ ...fixtures.overview(), apartments: [{ type: 'x', count: '4', areaThousandM2: null }] }, 'msk'));
  assert.throws(() => readApartmentDetail(fixtures.detail(), 'msk', 'wrong name'));
  assert.throws(() => readApartmentDetail({ ...fixtures.detail(), summary: { ...fixtures.detail().summary, averageAreaM2: Infinity } }, 'msk', fixtures.developers[0].id));
});
test('duplicate selector names keep first match while overview duplicate rows survive', () => {
  const first = fixtures.catalog.developersByRegion.msk[0];
  const catalog = readApartmentCatalog({ ...fixtures.catalog, developersByRegion: { ...fixtures.catalog.developersByRegion, msk: [first, { ...first, place: 99 }] } });
  assert.deepEqual(catalog.developersByRegion.msk, [first]);
  const raw = structuredClone(fixtures.overview()); raw.developers.push({ ...raw.developers[0] });
  assert.equal(readApartmentOverview(raw, 'msk').developers.length, 74);
});
test('partial availability and empty tables remain valid rather than invented data', () => {
  const catalog = readApartmentCatalog({ ...fixtures.catalog, developersByRegion: { msk: [], rf: fixtures.catalog.developersByRegion.rf } });
  const filters = apartmentFilters(catalog, new URLSearchParams({ region: 'msk' }));
  assert.equal(filters.region.id, 'msk'); assert.equal(filters.developer, undefined);
  const raw = { ...fixtures.overview(), apartments: [], distribution: [], developers: [], regions: [], developerCount: 0, regionCount: 0 };
  assert.deepEqual(readApartmentOverview(raw, 'msk'), raw);
  const detail = fixtures.detail();
  detail.rooms = fixtureRoomsNull();
  detail.summary = { ...detail.summary, countThousand: null, areaThousandM2: null, averageAreaM2: null, marketSharePercent: null };
  assert.equal(readApartmentDetail(detail, 'msk', detail.developer.id).summary.averageAreaM2, null);
});
function fixtureRoomsNull() { return fixtures.rooms.map(room => ({ ...room, sharePercent: null })); }
test('new pages use apartments API only, including when requests fail', async () => {
  const original = globalThis.fetch, requests = [];
  try {
    globalThis.fetch = async url => { requests.push(String(url)); return new Response('{}', { status: 503 }); };
    await assert.rejects(loadApartmentData('msk', undefined, new AbortController().signal), /503/);
    assert.deepEqual(requests, ['/api/v1/apartments?region=msk']);
    globalThis.fetch = async url => { requests.push(String(url)); return Response.json(fixtures.detail()); };
    const data = await loadApartmentData('msk', fixtures.developers[0].id, new AbortController().signal);
    assert.equal(data.summary.averageAreaM2, 58.9);
    assert.ok(requests.every(url => url.startsWith('/api/v1/apartments')));
  } finally { globalThis.fetch = original; }
});
