import { nthRussianWorkingDay } from './ruWorkingCalendar.ts';

export type ReleaseRule = 'accounts' | 'salary' | 'ipc' | 'readiness' | 'realization' | 'mortgage' | 'preferentialMortgage';
export type ReleasePeriod = { year: number; month: number | null };

export function periodFromId(id: string | null | undefined): ReleasePeriod | null {
  if (!id || !/^\d{4}-(0[1-9]|1[0-2])$/.test(id)) return null;
  const year = Number(id.slice(0, 4));
  return year >= 1900 && year <= 9999 ? { year, month: Number(id.slice(5)) } : null;
}

const validPeriod = (period: ReleasePeriod) => Number.isInteger(period.year) && period.year >= 1900
  && period.year <= 9996 && (period.month === null || Number.isInteger(period.month) && period.month >= 1 && period.month <= 12);

export function nextReleaseDate(rule: ReleaseRule, latest: ReleasePeriod | null | undefined): string | null {
  if (!latest || !validPeriod(latest)) return null;
  // The next reporting period follows the latest published measurement.
  if (rule === 'accounts') return `${latest.year + 3}-04-10`;
  if (latest.month === null) return null;
  const lag = rule === 'salary' || rule === 'preferentialMortgage' ? 3 : 2;
  const releaseMonth = new Date(Date.UTC(latest.year, latest.month - 1 + lag, 1));
  const year = releaseMonth.getUTCFullYear(), month = releaseMonth.getUTCMonth() + 1;
  if (rule === 'salary') return nthRussianWorkingDay(year, month, 23);
  const day = rule === 'ipc' ? 15 : rule === 'mortgage' ? 30 : rule === 'preferentialMortgage' ? 10 : 11;
  // February has no 30th: use its last calendar day rather than March 2nd.
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return new Date(Date.UTC(year, month - 1, Math.min(day, lastDay))).toISOString().slice(0, 10);
}

export function releaseDateLabel(iso: string): string {
  if (!/^\d{4}-(0[1-9]|1[0-2])-\d{2}$/.test(iso)) return '';
  const date = new Date(`${iso}T00:00:00Z`);
  if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== iso) return '';
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })
    .format(date).replace(/\s*г\.$/, ' года');
}
