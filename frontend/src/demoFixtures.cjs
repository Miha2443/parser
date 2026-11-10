// Normalized test responses only; production demonstration never imports fixtures.
const fs = require('node:fs');
const path = require('node:path');
const annual = require('./annualConstruction.browser.cjs');
const commissioning = require('./commissioning.browser.cjs');
const economics = require('./economics.browser.cjs');
const apartments = require('./apartmentFixtures.cjs');
const sales = require('./sales.browser.cjs');
const { permitPageFixture } = require('./permitPageFixtures.mjs');
const snapshot = JSON.parse(fs.readFileSync(path.join(__dirname, '../public/profile-snapshot.json'), 'utf8'));
const meta = { schemaVersion: 1, version: 'demo-test-v1', generatedAt: '2026-10-28T10:00:00Z', source: { date: '2026-10-20', files: ['test-only.json'], issues: [] } };
const monthOptions = Array.from({ length: 12 }, (_, i) => ({ id: i + 1, label: `Месяц ${i + 1}` }));
const mortgageMeta = { ...meta, source: { ...meta.source, url: 'https://наш.дом.рф/аналитика/ипотечное_кредитование', collectedAt: meta.generatedAt } };
const preferentialMeta = { ...mortgageMeta, source: { ...mortgageMeta.source, url: 'https://дом.рф/programmy-gosudarstvennoj-podderzhki/operational-reporting/' } };
const catalog = metadata => ({ ...metadata, defaultRegion: 'msk', regions: [{ id: 'msk', label: 'Москва', periods: ['2026-05'] }], months: monthOptions });
function profile(url) {
  const live = { ...snapshot, version: 'profile-test', controls: { ...snapshot.controls, frozen: false } };
  return { ...live, profiles: url.pathname === '/api/v1/catalog' ? [] : live.profiles.filter(row => row.developerKey === url.searchParams.get('developer') && row.region === url.searchParams.get('region')) };
}
function domrf() {
  const metrics = { sold_pct: 40, sold_thousand_m2: 4000, unsold_pct: 0, unsold_thousand_m2: 0, sales_not_open_pct: null, sales_not_open_thousand_m2: null };
  return { ...meta, region: { id: 'msk', label: 'Москва' }, construction: { period: { id: '2026-05', label: 'Май 2026', reportDate: '2026-05-31' }, metrics: { living: 10000, total: 15000 }, history: [{ period: '2026-05', label: 'Май 2026', reportDate: '2026-05-31', living: 10000, total: 15000, sourceFile: 'test-only.json' }] }, sales: { period: { id: '2026-05', label: 'Май 2026', reportDate: '2026-05-31' }, metrics, history: [{ period: '2026-05', label: 'Май 2026', reportDate: '2026-05-31', sourceFile: 'test-only.json', metrics }] }, notes: [] };
}
function linear() {
  return { ...meta, currentYear: 2026, sources: ['dsti', 'monitoring', 'forecast'].map(id => ({ id, file: null, date: null, sheet: null })), notes: [], metrics: ['roads', 'bridges', 'crossings', 'metro_stations', 'metro_tunnels', 'depots'].map((id, index) => ({ id, label: id, unit: ['roads', 'metro_tunnels'].includes(id) ? 'км' : 'ед.', fact: null, factPeriod: null, factSource: null, annualPlan: null, forecast: null, target: null, targetKind: null, percent: null, remaining: null, years: [2025, 2026].map(year => ({ year, fact: year === 2025 ? index + 1 : null, factKind: year === 2025 ? 'annual' : null, factPeriod: year === 2025 ? '2025' : null, factSource: year === 2025 ? 'monitoring' : null, plan: null, forecast: null })) })) };
}
function mortgage() {
  const values = { annual: 100, selected: 50, actualThroughMonth: 5, annualYoYPct: null, selectedYoYPct: null };
  const cell = { volumeRub: 0, count: 100, ratePct: 8, termMonths: null, averageAmountRub: null };
  return { ...mortgageMeta, selection: { region: 'msk', month: 5 }, regionLabel: 'Москва', annual: [{ year: 2025, volume: values, count: values }], monthly: [{ period: '2026-05', all: cell, primary: cell, secondary: { ...cell, volumeRub: null } }], notes: [] };
}
const programs = ['preferential', 'family', 'it'];
const characteristics = ['averageAmountMln', 'ratePct', 'termMonths', 'ownFundsPct', 'areaM2', 'priceThousandM2'];
function preferential() {
  const flow = { year: 2025, throughMonth: 5, programs: Object.fromEntries(programs.map(id => [id, { volumeMln: 10, count: 5 }])), total: { volumeMln: 30, count: 15 } };
  const months = Object.fromEntries(characteristics.map(id => [id, 5]));
  const characteristic = { year: 2025, throughMonth: 5, throughMonths: months, programThroughMonths: Object.fromEntries(programs.map(id => [id, months])), programs: Object.fromEntries(programs.map(id => [id, Object.fromEntries(characteristics.map(metric => [metric, 5]))])) };
  return { ...preferentialMeta, selection: { region: 'msk', month: 5, characteristicMonth: 5 }, regionLabel: 'Москва', latestPeriod: '2026-05', latestPeriodPartial: null, annual: [flow], cumulative: [flow], characteristicsAnnual: [characteristic], characteristicsCumulative: [characteristic], notes: [] };
}
function response(input) {
  const url = new URL(input, 'http://localhost'), p = url.pathname, query = url.searchParams;
  if (p === '/api/v1/auth/session') return { enabled: false, user: null, csrfToken: null };
  if (p === '/api/v1/catalog' || p === '/api/v1/profile') return profile(url);
  if (p === '/api/v1/construction/permits') return permitPageFixture('total');
  if (p === '/api/v1/construction/domrf') return domrf();
  if (p === '/api/v1/sales/catalog') return sales.catalog;
  if (p === '/api/v1/sales') return sales.detail(query.get('region'), query.get('period'));
  if (p === '/api/v1/apartments/catalog') return apartments.catalog;
  if (p === '/api/v1/apartments') return apartments.overview(query.get('region'));
  if (p === '/api/v1/apartments/developer') return apartments.detail(query.get('region'), query.get('developer'));
  if (p === '/api/v1/commissioning/annual/catalog') return annual.annualCatalog;
  if (p === '/api/v1/commissioning/annual') return annual.annualForecastReport({ region: query.get('region') });
  if (p === '/api/v1/commissioning/operational/catalog') return commissioning.operationalCatalog;
  if (p === '/api/v1/commissioning/operational') return commissioning.operationalReport({ month: Number(query.get('month')), year: Number(query.get('year')), quarter: Number(query.get('quarter')), cumulative: query.get('cumulative') === 'true', excludeMkd: query.get('exclude_mkd') === 'true' });
  if (p === '/api/v1/linear/dashboard') return linear();
  const economic = p.match(/^\/api\/v1\/economics\/(accounts|salary|ipc)\/(catalog|report)$/);
  if (economic) {
    const family = economic[1]; if (economic[2] === 'catalog') return economics.catalog(family);
    const selection = Object.fromEntries(Object.entries(economics.catalog(family).defaults).map(([key, value]) => [key, Array.isArray(value) ? query.getAll(key).filter(Boolean).map(item => typeof value[0] === 'number' ? Number(item) : item) : typeof value === 'boolean' ? query.get(key) === 'true' : query.get(key)]));
    return economics.report(family, selection);
  }
  if (p === '/api/v1/mortgage/catalog') return catalog(mortgageMeta);
  if (p === '/api/v1/mortgage') return mortgage();
  if (p === '/api/v1/preferential-mortgage/catalog') return catalog(preferentialMeta);
  if (p === '/api/v1/preferential-mortgage') return preferential();
  throw new Error(`Unhandled demo fixture: ${url.pathname}${url.search}`);
}
module.exports = { response, mortgage, preferential, domrf, linear, meta };
