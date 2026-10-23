export const operationalStructureYears = [2026, 2025, 2024, 2023];

export function operationalScopeParams(params: URLSearchParams, scope: 'housing' | 'nonres' | 'structure') {
  const selected = new URLSearchParams();
  if (scope === 'structure') {
    if (operationalStructureYears.includes(Number(params.get('year')))) selected.set('year', params.get('year')!);
    for (const key of ['quarter', 'cumulative']) if (params.has(key)) selected.set(key, params.get(key)!);
    if (params.has('structureMonth')) selected.set('month', params.get('structureMonth')!);
  } else {
    const month = params.get(`${scope}Month`) ?? params.get('month');
    if (month) selected.set('month', month);
    if (scope === 'nonres') {
      const exclude = params.get('nonresExcludeMkd') ?? params.get('exclude_mkd');
      if (exclude) selected.set('exclude_mkd', exclude);
    }
  }
  return selected;
}
