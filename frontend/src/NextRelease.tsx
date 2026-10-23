import { CalendarClock } from 'lucide-react';
import { nextReleaseDate, releaseDateLabel } from './releaseSchedule';
import type { ReleasePeriod, ReleaseRule } from './releaseSchedule';
import './next-release.css';

export function NextRelease({ rule, period, regionLabel, schedule }: { rule: ReleaseRule; period: ReleasePeriod | null | undefined; regionLabel?: string; schedule?: string }) {
  if (!period && !schedule) return null;
  const next = period ? nextReleaseDate(rule, period) : null;
  return <p className="next-release" data-release-rule={rule} title="Плановая дата публикации по графику показателя; не время запуска загрузчика.">
    <CalendarClock size={14} aria-hidden="true" />
    <span>Следующее обновление данных{regionLabel ? ` (${regionLabel})` : ''}: {next
      ? <time dateTime={next}>{releaseDateLabel(next)}</time>
      : <span>{schedule ?? 'дата уточняется'}</span>}</span>
  </p>;
}
