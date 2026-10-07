import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { normalizeSnapshot } from '../src/snapshot.ts';
import { sumAreas } from '../src/format.ts';

const raw = JSON.parse(readFileSync(new URL('../public/profile-snapshot.json', import.meta.url), 'utf8'));
const snapshot = normalizeSnapshot(raw);

test('rejects unsupported schema without guessing fields', () => {
  assert.throws(() => normalizeSnapshot({ profiles: [] }), /версии 1/);
  assert.throws(() => normalizeSnapshot({ schemaVersion: 2, profiles: [] }), /версии 1/);
});

test('area totals never include numeric years or other metadata', () => {
  const row = raw.profiles[0].annual.rows[0];
  const adapted = snapshot.developers[0].regions[0].annual.find(r => r.year === row.year);
  assert.ok(adapted);
  assert.ok(Math.abs(sumAreas(adapted) - row.totalM2) < 0.000001);
  assert.equal(sumAreas({ year: row.year }), null);
  assert.equal(sumAreas({ housing: null }), null);
  assert.equal(sumAreas({ housing: 0 }), 0);
});

test('only snapshot developers and apartment regions appear', () => {
  assert.deepEqual(snapshot.developers.map(d => d.name), raw.controls.developers.map(d => d.developer));
  assert.equal(snapshot.developers.flatMap(d => d.regions).length, raw.profiles.length);
  for (const developer of snapshot.developers) {
    const control = raw.controls.developers.find(d => d.developerKey === developer.id);
    assert.deepEqual(developer.regions.map(r => r.id), control.regions);
  }
});

test('apartment region does not change Moscow metrics or RF/Moscow sales', () => {
  for (const d of snapshot.developers) {
    for (const r of d.regions.slice(1)) {
      assert.deepEqual(r.construction, d.regions[0].construction);
      assert.deepEqual(r.annual, d.regions[0].annual);
      assert.deepEqual(r.ratings, d.regions[0].ratings);
      assert.deepEqual(r.escrow, d.regions[0].escrow);
      assert.deepEqual(r.salesByRegion, d.regions[0].salesByRegion);
    }
  }
});

test('preserves all six delay displays, null comparisons, and complete object records', () => {
  for (const d of snapshot.developers) for (const r of d.regions) {
    const p = raw.profiles.find(p => p.developerKey === d.id && p.region === r.id);
    assert.equal(r.delays.length, 0);
    assert.equal(r.delayMetrics.length, p.delays.cards.length);
    assert.deepEqual(r.delayMetrics.map(m => m.displayValue), p.delays.cards.map(m => m.displayValue));
    assert.deepEqual(r.geography.map(g => g.moscow), p.housingComparison.map(g => g.segments?.find(s => s.key === 'msk').valueM2 ?? null));
    assert.deepEqual(r.objects[0].rows, p.objects.commissioned.rows);
    assert.deepEqual(r.objects[1].rows, p.objects.permitted.rows);
    assert.equal(r.objects[0].rows.length, p.objects.commissioned.rowCount);
    assert.equal(r.objects[1].rows.length, p.objects.permitted.rowCount);
  }
});

test('apartment area conversion keeps source units and source dates unchanged', () => {
  for (const d of snapshot.developers) for (const r of d.regions) {
    const p = raw.profiles.find(p => p.developerKey === d.id && p.region === r.id);
    for (const [i, a] of r.apartments.entries()) assert.equal(a.area, p.apartments.rows[i].areaThousandM2 == null ? null : p.apartments.rows[i].areaThousandM2 * 1000);
  }
  assert.deepEqual(snapshot.sources.map(s => s.date), Object.values(raw.sourceDates));
});

test('period captions disclose real coverage and missing paired years', () => {
  const capital = snapshot.developers.find(d => d.id === 'capital group').regions[0];
  assert.ok(capital.structures[0].title.includes('2017'));
  assert.ok(!capital.structures[0].title.includes('2016'));
  const historical = capital.delayMetrics.find(d => d.label.includes('регионах РФ за 2022'));
  assert.ok(historical.note.includes('2023'));
  assert.ok(historical.note.includes('2022, 2024, 2025'));
  const empty = structuredClone(raw);
  empty.profiles[0].delays.inputs.pairedYears = [];
  const adapted = normalizeSnapshot(empty).developers[0].regions[0];
  assert.ok(adapted.delayMetrics.find(d => d.label.includes('регионах РФ за 2022')).note.includes('Нет подтверждённых'));
});
