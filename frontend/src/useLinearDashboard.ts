import { useEffect, useState } from 'react';
import { loadLinearDashboard } from './linearDashboardData';
import type { LinearDashboard } from './linearDashboardData';

export function useLinearDashboard() {
  const [data, setData] = useState<LinearDashboard | null>(null);
  const [error, setError] = useState(''), [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setError('');
    loadLinearDashboard(controller.signal).then(value => {
      if (!controller.signal.aborted) setData(value);
    }).catch(error => {
      if (!controller.signal.aborted) setError(error instanceof Error ? error.message : 'Не удалось загрузить линейные объекты.');
    });
    return () => controller.abort();
  }, [attempt]);
  return { data, error, retry: () => setAttempt(value => value + 1) };
}
