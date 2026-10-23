import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import fixture from './economics.browser.cjs';
import { economicsReleasePeriods, readEconomicsCatalog, readEconomicsReport } from './economicsData.ts';
import { nextReleaseDate } from './releaseSchedule.ts';

const families = ['salary', 'ipc', 'accounts'];
const period = (region, metric, year, month = null) => ({ region, metric, year, month });
const source = name => readFileSync(new URL(name, import.meta.url), 'utf8');

test('legacy economics v1 omits optional releasePeriods without changing the parsed shape', () => {
  for (const family of families) {
    const rawCatalog = fixture.catalog(family), rawReport = fixture.report(family);
    const catalog = readEconomicsCatalog(rawCatalog, family), report = readEconomicsReport(rawReport, family);
    assert.deepEqual(catalog, rawCatalog);
    assert.deepEqual(report, rawReport);
    assert.equal(Object.hasOwn(catalog, 'releasePeriods'), false);
    assert.equal(Object.hasOwn(report, 'releasePeriods'), false);
    assert.deepEqual(economicsReleasePeriods(catalog, catalog.defaults, 'na_block1'), []);
  }
});

test('catalogs and reports validate and retain monthly, annual and explicitly empty releasePeriods', () => {
  for (const family of families) {
    const releases = family === 'accounts' ? [period(fixture.MSK, 'vrp_total', 2024), period(fixture.RF, 'gdp_total', 2025)]
      : [period(fixture.MSK, family, 2026, 9), period(fixture.RF, family, 2026, 10)];
    for (const values of [[], releases]) {
      const rawCatalog = { ...fixture.catalog(family), releasePeriods: values }, rawReport = { ...fixture.report(family), releasePeriods: values };
      assert.deepEqual(readEconomicsCatalog(rawCatalog, family), rawCatalog);
      assert.deepEqual(readEconomicsReport(rawReport, family), rawReport);
    }
  }
});

test('present non-array releasePeriods rejects in both catalogs and reports', () => {
  for (const family of families) for (const releasePeriods of [null, {}, '2026-09', 2026, false]) {
    assert.throws(() => readEconomicsCatalog({ ...fixture.catalog(family), releasePeriods }, family), /версии 1/);
    assert.throws(() => readEconomicsReport({ ...fixture.report(family), releasePeriods }, family), /версии 1/);
  }
});

test('release period entries reject malformed regions, metrics, years and months', () => {
  const valid = period(fixture.MSK, 'salary', 2026, 9);
  const malformed = [null, [], {}, true,
    ...['', ' ', null, 1, undefined].map(region => ({ ...valid, region })),
    ...['', ' ', null, 1, undefined].map(metric => ({ ...valid, metric })),
    ...[0, -1, 10000, 2026.5, '2026', NaN, Infinity, null, undefined].map(year => ({ ...valid, year })),
    ...[0, -1, 13, 9.5, '09', false, NaN, Infinity, undefined].map(month => ({ ...valid, month })),
  ];
  for (const entry of malformed) {
    assert.throws(() => readEconomicsCatalog({ ...fixture.catalog('salary'), releasePeriods: [entry] }, 'salary'), /версии 1/);
    assert.throws(() => readEconomicsReport({ ...fixture.report('salary'), releasePeriods: [entry] }, 'salary'), /версии 1/);
  }
});

test('salary uses the latest monthly catalog evidence for the selected region, not old chart filters', () => {
  const releasePeriods = [period(fixture.MSK, 'salary', 2026, 9), period(fixture.RF, 'salary', 2026, 10),
    period(fixture.MSK, 'salary', 2025, 12), period(fixture.MSK, 'salary', 2026, 1),
    period(fixture.MSK, 'ipc', 2099, 12), period(fixture.MSK, 'salary', 2099)];
  const catalog = readEconomicsCatalog({ ...fixture.catalog('salary'), releasePeriods }, 'salary');
  const selected = { ...catalog.defaults, period: 'year', months: [1], quarters: [1], views: [] };
  assert.deepEqual(economicsReleasePeriods(catalog, selected), [releasePeriods[0]]);
  assert.deepEqual(economicsReleasePeriods(catalog, { ...selected, region: fixture.RF }), [releasePeriods[1]]);
  assert.deepEqual(economicsReleasePeriods(catalog, { ...selected, region: 'unknown' }), []);
  assert.deepEqual(catalog.releasePeriods, releasePeriods);
});

test('IPC keeps independent latest regional releases, even with historical chart selections', () => {
  const releasePeriods = [period(fixture.MSK, 'ipc', 2026, 9), period(fixture.RF, 'ipc', 2026, 10),
    period(fixture.MSK, 'ipc', 2025, 12), period(fixture.RF, 'ipc', 2026, 1)];
  const catalog = readEconomicsCatalog({ ...fixture.catalog('ipc'), releasePeriods }, 'ipc');
  const selected = { ...catalog.defaults, regions: [fixture.MSK, fixture.RF], months: [1], period: 'year' };
  const releases = economicsReleasePeriods(catalog, selected);
  assert.deepEqual(releases, releasePeriods.slice(0, 2));
  assert.deepEqual(releases.map(p => nextReleaseDate('ipc', p)), ['2026-11-15', '2026-12-15']);
  assert.deepEqual(economicsReleasePeriods(catalog, { ...selected, regions: [] }), []);
});

test('accounts blocks map exact regional metrics and use annual evidence only', () => {
  const pairs = [['vrp_total', 'gdp_total'], ['vrp_per_capita', 'gdp_pc_total'],
    ['vrp_index', 'gdp_index'], ['vrp_per_capita_index', 'gdp_pc_index']];
  const releasePeriods = pairs.flatMap(([msk, rf], i) => [period(fixture.MSK, msk, 2021 + i), period(fixture.RF, rf, 2022 + i)]);
  releasePeriods.push(period(fixture.MSK, 'gdp_total', 2099), period(fixture.RF, 'vrp_total', 2099),
    period(fixture.MSK, 'vrp_total', 2099, 12));
  const catalog = readEconomicsCatalog({ ...fixture.catalog('accounts'), releasePeriods }, 'accounts');
  const selected = { ...catalog.defaults, ...Object.fromEntries(pairs.map((_, i) => [`block${i + 1}_regions`, [fixture.MSK, fixture.RF]])) };
  pairs.forEach((_, i) => assert.deepEqual(economicsReleasePeriods(catalog, selected, `na_block${i + 1}`), releasePeriods.slice(i * 2, i * 2 + 2)));
  assert.equal(nextReleaseDate('accounts', economicsReleasePeriods(catalog, selected, 'na_block4')[0]), '2027-04-10');
  assert.deepEqual(economicsReleasePeriods(catalog, { ...selected, block1_regions: [] }, 'na_block1'), []);
  assert.deepEqual(economicsReleasePeriods(catalog, selected, 'unknown_block'), []);
});

test('accounts structure mode and industry index use only their own metric and region evidence', () => {
  const releasePeriods = [period(fixture.MSK, 'vds_value', 2022), period(fixture.MSK, 'vds_structure', 2023),
    period(fixture.MSK, 'vds_index', 2024), period(fixture.RF, 'vds_value', 2025)];
  const catalog = readEconomicsCatalog({ ...fixture.catalog('accounts'), releasePeriods }, 'accounts'), selected = catalog.defaults;
  assert.deepEqual(economicsReleasePeriods(catalog, selected, 'na_block5'), [releasePeriods[0]]);
  assert.deepEqual(economicsReleasePeriods(catalog, { ...selected, structure_mode: 'share' }, 'na_block5'), [releasePeriods[1]]);
  assert.deepEqual(economicsReleasePeriods(catalog, { ...selected, structure_region: fixture.RF }, 'na_block5'), [releasePeriods[3]]);
  assert.deepEqual(economicsReleasePeriods(catalog, { ...selected, structure_region: fixture.RF, structure_mode: 'share' }, 'na_block5'), []);
  assert.deepEqual(economicsReleasePeriods(catalog, selected, 'na_block6'), [releasePeriods[2]]);
  assert.deepEqual(economicsReleasePeriods(catalog, { ...selected, index_region: fixture.RF }, 'na_block6'), []);
  assert.deepEqual(economicsReleasePeriods(catalog, selected, 'na_block1'), []);
});

test('economics heading integration uses catalog latest and collapses equal next dates only', () => {
  const ui = source('./Economics.tsx'), start = ui.indexOf('function ReleaseDates('), end = ui.indexOf('\nfunction MultiSelect(', start);
  assert.ok(start >= 0 && end > start);
  const integration = ui.slice(start, end);
  assert.match(integration, /economicsReleasePeriods\(catalog, selected, blockId\)/);
  assert.match(integration, /new Set\(releases\.map\(period => nextReleaseDate\(rule, period\)\)\)\.size > 1/);
  assert.match(integration, /if \(!differentDates\) return <NextRelease rule=\{rule\} period=\{releases\[0\]\}/);
  assert.match(integration, /releases\.map\(period => <NextRelease[^\n]+regionLabel=/);
  assert.doesNotMatch(integration, /data\.|charts|rows|points|months|quarters|\bDate\(/);
  assert.match(ui, /<h1>\{titles\[family\]\}<\/h1>[^\n]+family !== 'accounts'[^\n]+<ReleaseDates catalog=\{catalog\} selected=\{selection\}/);
  assert.match(ui, /<h2>\{i \+ 1\}\. \{block\.title\}<\/h2><ReleaseDates catalog=\{catalog\} selected=\{selection\} blockId=\{block\.id\}/);
  const catalog = readEconomicsCatalog({ ...fixture.catalog('ipc'), releasePeriods: [period(fixture.MSK, 'ipc', 2026, 9), period(fixture.RF, 'ipc', 2026, 9)] }, 'ipc');
  const dates = economicsReleasePeriods(catalog, { ...catalog.defaults, regions: [fixture.MSK, fixture.RF] }).map(p => nextReleaseDate('ipc', p));
  assert.equal(new Set(dates).size, 1);
});

test('mortgage heading latest comes from selected catalog region independently of annualMonth and ranges', () => {
  const ui = source('./Mortgage.tsx'), latest = ui.split('\n').find(line => line.includes('const latestCatalogPeriod ='));
  assert.ok(latest);
  assert.match(latest, /catalog\?\.regions\.find\(region => region\.id === selection\?\.region\)\?\.periods/);
  assert.match(latest, /\.sort\(\)\.at\(-1\)/);
  assert.doesNotMatch(latest, /annualMonth|selection\.month|monthly|annual\b|params|range|data\./);
  assert.match(ui, /<\/h1><NextRelease rule="mortgage" period=\{periodFromId\(latestCatalogPeriod\)\}/);
});

test('readiness heading latest comes from region catalog, not selected readiness period or range', () => {
  const ui = source('./DomRfPage.tsx'), latest = ui.split('\n').find(line => line.includes('const latestReadyPeriod ='));
  assert.match(ui, /readyPeriods = readiness\.catalog\?\.regions\.find\(r => r\.id === region\)\?\.periods/);
  assert.ok(latest);
  assert.match(latest, /\[\.\.\.readyPeriods\]\.sort\(\(a, b\) => a\.id\.localeCompare\(b\.id\)\)\.at\(-1\)\?\.id/);
  assert.doesNotMatch(latest, /readyData|readiness\.period|params|range|forecast/);
  assert.match(ui, /<h2 id="domrf-readiness-title">[^<]+<\/h2><NextRelease rule="readiness" period=\{periodFromId\(latestReadyPeriod\)\}/);
});

test('realization heading compares full history with current period and never schedules construction', () => {
  const ui = source('./DomRfPage.tsx'), latest = ui.split('\n').find(line => line.includes('const latestRealizationPeriod ='));
  assert.ok(latest);
  assert.match(latest, /s\.history\.map\(h => h\.period\)/);
  assert.match(latest, /s\.period\?\.id \? \[s\.period\.id\] : \[\]/);
  assert.match(latest, /\.sort\(\)\.at\(-1\)/);
  assert.doesNotMatch(latest, /params|selected|chosen|range|reportDate/);
  assert.match(ui, /<h2 id="domrf-realization-title">[^<]+<\/h2><NextRelease rule="realization" period=\{periodFromId\(latestRealizationPeriod\)\}/);
  assert.doesNotMatch(ui, /<NextRelease[^>]+rule="(?:construction|preferentialMortgage)"/);
});
