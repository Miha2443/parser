export function operationalScopeParams(params: URLSearchParams, scope: 'housing' | 'nonres' | 'structure') {
  const selected = new URLSearchParams();
  if (scope === 'structure') {
    for (const key of ['year', 'quarter', 'cumulative']) if (params.has(key)) selected.set(key, params.get(key)!);
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
