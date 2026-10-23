import assert from 'node:assert/strict';
import test from 'node:test';
import { nextReleaseDate, periodFromId, releaseDateLabel } from './releaseSchedule.ts';
import { nthRussianWorkingDay, russianWorkingDay } from './ruWorkingCalendar.ts';

test('monthly releases follow the next reporting period, not mtime or the computer clock', () => {
  assert.equal(nextReleaseDate('ipc', { year: 2026, month: 8 }), '2026-10-15');
  assert.equal(nextReleaseDate('ipc', { year: 2026, month: 9 }), '2026-11-15');
  for (const rule of ['readiness', 'realization']) {
    assert.equal(nextReleaseDate(rule, { year: 2026, month: 9 }), '2026-11-11');
    assert.equal(nextReleaseDate(rule, { year: 2026, month: 11 }), '2027-01-11');
  }
  assert.equal(nextReleaseDate('mortgage', { year: 2026, month: 8 }), '2026-10-30');
  assert.equal(nextReleaseDate('mortgage', { year: 2026, month: 12 }), '2027-02-28');
  assert.equal(nextReleaseDate('mortgage', { year: 2027, month: 12 }), '2028-02-29');
  assert.equal(nextReleaseDate('preferentialMortgage', { year: 2026, month: 8 }), '2026-11-10');
});

test('annual accounts stay fixed until a new reporting year is available', () => {
  assert.equal(nextReleaseDate('accounts', { year: 2024, month: null }), '2027-04-10');
  assert.equal(nextReleaseDate('accounts', { year: 2025, month: null }), '2028-04-10');
});

test('salary counts working days through holidays and rolls into the next month when necessary', () => {
  assert.equal(nthRussianWorkingDay(2026, 11, 23), '2026-12-03');
  assert.equal(nextReleaseDate('salary', { year: 2026, month: 8 }), '2026-12-03');
  assert.equal(nthRussianWorkingDay(2026, 1, 1), '2026-01-12');
  assert.equal(nthRussianWorkingDay(2026, 12, 23), '2027-01-11');
  assert.equal(nthRussianWorkingDay(2027, 2, 23), '2027-03-04');
  assert.equal(russianWorkingDay(new Date('2027-02-20T00:00:00Z')), true);
  for (const day of ['2026-03-09', '2026-05-11', '2026-11-04', '2026-12-31',
    '2027-02-22', '2027-05-03', '2027-05-10', '2027-06-14', '2027-11-05', '2027-12-31']) {
    assert.equal(russianWorkingDay(new Date(`${day}T00:00:00Z`)), false, day);
  }
});

test('verified calendars have the published monthly workday counts', () => {
  const counts = year => Array.from({ length: 12 }, (_, month) => {
    let total = 0;
    for (let day = 1; day <= new Date(Date.UTC(year, month + 1, 0)).getUTCDate(); day++) {
      if (russianWorkingDay(new Date(Date.UTC(year, month, day)))) total++;
    }
    return total;
  });
  assert.deepEqual(counts(2026), [15, 19, 21, 22, 19, 21, 23, 21, 22, 22, 20, 22]);
  assert.deepEqual(counts(2027), [15, 19, 22, 22, 19, 21, 22, 22, 22, 21, 20, 22]);
});

test('unknown calendars and missing or invalid observed periods do not invent dates', () => {
  assert.equal(nthRussianWorkingDay(2028, 1, 23), null);
  assert.equal(nthRussianWorkingDay(2027, 12, 23), null);
  assert.equal(russianWorkingDay(new Date('invalid')), null);
  for (const latest of [null, undefined, { year: 2026, month: 0 }, { year: 2026, month: 13 }, { year: 2026.5, month: 9 }]) {
    assert.equal(nextReleaseDate('ipc', latest), null);
  }
  assert.equal(nextReleaseDate('ipc', { year: 2026, month: null }), null);
  assert.equal(nthRussianWorkingDay(2026, 0, 23), null);
  assert.equal(nthRussianWorkingDay(2026, 1, 0), null);
});

test('periods and long Russian dates are validated without local timezone shifts', () => {
  assert.deepEqual(periodFromId('2026-09'), { year: 2026, month: 9 });
  for (const invalid of ['', '2026-9', '2026-13', '2026-09-15', '2026-00', null]) assert.equal(periodFromId(invalid), null);
  assert.equal(releaseDateLabel('2027-04-10'), '10 апреля 2027 года');
  assert.equal(releaseDateLabel('2026-11-15'), '15 ноября 2026 года');
  assert.equal(releaseDateLabel('2027-02-30'), '');
});
