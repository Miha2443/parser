type Calendar = { daysOff: readonly string[]; workingSaturdays: readonly string[]; source: string };

// Federal five-day calendar. Dates outside verified years stay unknown.
export const ruWorkingCalendars: Readonly<Record<number, Calendar>> = {
  2026: {
    daysOff: ['01-01', '01-02', '01-03', '01-04', '01-05', '01-06', '01-07', '01-08', '01-09',
      '02-23', '03-08', '03-09', '05-01', '05-09', '05-11', '06-12', '11-04', '12-31'],
    workingSaturdays: [],
    source: 'https://publication.pravo.gov.ru/document/0001202509240023',
  },
  2027: {
    daysOff: ['01-01', '01-02', '01-03', '01-04', '01-05', '01-06', '01-07', '01-08',
      '02-22', '02-23', '03-08', '05-01', '05-03', '05-09', '05-10', '06-12', '06-14', '11-04', '11-05', '12-31'],
    workingSaturdays: ['02-20'],
    source: 'https://publication.pravo.gov.ru/document/0001202609180037',
  },
};

export function russianWorkingDay(date: Date): boolean | null {
  if (!Number.isFinite(date.getTime())) return null;
  const calendar = ruWorkingCalendars[date.getUTCFullYear()];
  if (!calendar) return null;
  const day = date.toISOString().slice(5, 10);
  if (calendar.workingSaturdays.includes(day)) return true;
  return !calendar.daysOff.includes(day) && ![0, 6].includes(date.getUTCDay());
}

export function nthRussianWorkingDay(year: number, month: number, count: number): string | null {
  if (!Number.isInteger(year) || year < 1900 || year > 9999 || !Number.isInteger(month)
      || month < 1 || month > 12 || !Number.isInteger(count) || count < 1 || count > 31) return null;
  const day = new Date(Date.UTC(year, month - 1, 1));
  for (let seen = 0, elapsed = 0; elapsed < 100; elapsed++, day.setUTCDate(day.getUTCDate() + 1)) {
    const working = russianWorkingDay(day);
    if (working === null) return null;
    if (working && ++seen === count) return day.toISOString().slice(0, 10);
  }
  return null;
}
