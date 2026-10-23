import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { mortgageAnnualOption, mortgageAnnualRows, mortgageCumulativeLabel, mortgageHistory, mortgageMetricPeriods, mortgageMonthlyOption, mortgageMonthlyRows, mortgagePeriodLabel, mortgageRange, mortgageRatesOption, mortgageSelection, mortgageSourceUrl, mortgageUrl, readMortgageCatalog, readMortgageReport } from './mortgageData.ts';

const metadata = () => ({ schemaVersion: 1, version: 'test-mortgage-generation', generatedAt: '2026-10-21T10:00:00Z', source: { url: mortgageSourceUrl, date: '2026-10-20', collectedAt: '2026-10-21T09:00:00Z', files: ['data/raw/realty/nashdom/mortgage_history.json'] } });
const empty = () => ({ volumeRub: null, count: null, ratePct: null, termMonths: null, averageAmountRub: null });
const values = (value = {}) => ({ ...empty(), ...value });
const month = (period, all = {}, primary = {}, secondary = {}) => ({ period, all: values(all), primary: values(primary), secondary: values(secondary) });
const annualValue = (value = {}) => ({ annual: null, selected: null, actualThroughMonth: null, annualYoYPct: null, selectedYoYPct: null, ...value });
const annual = (year, volume = {}, count = {}) => ({ year, volume: annualValue(volume), count: annualValue(count) });
const selection = { region: 'msk', month: 5 };
const catalog = () => ({ ...metadata(), regions: [{ id: 'msk', label: 'Москва', periods: ['2026-01', '2026-02'] }, { id: 'rf', label: 'Российская Федерация', periods: ['2025-12', '2026-01'] }], defaultRegion: 'msk', months: Array.from({ length: 12 }, (_, index) => ({ id: index + 1, label: `По месяц ${index + 1}` })) });
const report = () => ({ ...metadata(), selection: { ...selection }, regionLabel: 'Москва', annual: [annual(2025, { annual: 550e9, selected: 168e9, actualThroughMonth: 5, annualYoYPct: -7.4, selectedYoYPct: -35.1 }, { annual: 71900, selected: 22900, actualThroughMonth: 5, annualYoYPct: -16, selectedYoYPct: -38 }), annual(2026, { selected: 219e9, actualThroughMonth: 5, selectedYoYPct: 31 }, { selected: 28400, actualThroughMonth: 5, selectedYoYPct: 24 })], monthly: [month('2026-01', { ratePct: 9.7, termMonths: 280, averageAmountRub: 7e6 }, { volumeRub: 8.5e9, count: 1700, ratePct: 6.7, termMonths: 300, averageAmountRub: 8e6 }, { volumeRub: 12e9, count: 2500, ratePct: 15.2, termMonths: 260, averageAmountRub: 5e6 }), month('2026-03', { ratePct: 9.6 }, { volumeRub: 9e9, count: 1900, ratePct: 6.5 }, { volumeRub: 11e9, count: 2400, ratePct: 15 })], notes: [] });

test('mortgage catalog validates regions, twelve months and source provenance', () => {
  assert.equal(readMortgageCatalog(catalog()).defaultRegion, 'msk');
  for (const mutate of [raw => { raw.schemaVersion = 2; }, raw => { raw.source.url = 'https://other.example/mortgage'; }, raw => { raw.source.url += '?other=1'; }, raw => { raw.source.collectedAt = 'today'; }, raw => { raw.months.pop(); }, raw => { raw.months[1].id = 1; }, raw => { raw.regions.push(raw.regions[0]); }, raw => { raw.regions[0].periods.push('2026-13'); }, raw => { raw.regions[0].periods.push('2026-01'); }, raw => { raw.defaultRegion = 'spb'; }]) {
    const raw = catalog(); mutate(raw); assert.throws(() => readMortgageCatalog(raw));
  }
});

test('mortgage source accepts only intended HTTPS page including its encoded URL', () => {
  const raw = catalog(); raw.source.url = new URL(mortgageSourceUrl).href;
  assert.equal(readMortgageCatalog(raw).source.url, raw.source.url);
  for (const url of [mortgageSourceUrl.replace('https:', 'http:'), mortgageSourceUrl + '/unrelated', mortgageSourceUrl + '#x', 'https://user:password@' + new URL(mortgageSourceUrl).hostname + new URL(mortgageSourceUrl).pathname]) {
    raw.source.url = url; assert.throws(() => readMortgageCatalog(raw));
  }
});

test('report validates expected filters and detaches the input with chronological observations', () => {
  const raw = report(); raw.monthly.reverse(); raw.annual.reverse();
  const data = readMortgageReport(raw, selection);
  assert.deepEqual(data.monthly.map(row => row.period), ['2026-01', '2026-03']);
  assert.deepEqual(data.annual.map(row => row.year), [2025, 2026]);
  data.monthly[0].all.ratePct = 0;
  assert.equal(raw.monthly[1].all.ratePct, 9.7);
  assert.throws(() => readMortgageReport(raw, { ...selection, region: 'rf' }));
  assert.throws(() => readMortgageReport(raw, { ...selection, month: 6 }));
});

test('non-finite, missing, negative and duplicated mortgage measurements fail closed', () => {
  for (const mutate of [raw => { raw.monthly[0].primary.count = -1; }, raw => { raw.monthly[0].all.ratePct = Number.NaN; }, raw => { raw.monthly[0].all.ratePct = Infinity; }, raw => { delete raw.monthly[0].all.termMonths; }, raw => { raw.monthly[0].secondary = null; }, raw => { raw.monthly.push(raw.monthly[0]); }, raw => { raw.annual.push(raw.annual[0]); }, raw => { raw.monthly[0].period = '2026-00'; }, raw => { raw.annual[0].volume.actualThroughMonth = 6; }, raw => { raw.annual[0].volume.selected = 600e9; }, raw => { raw.annual[1].volume.annualYoYPct = 10; }]) {
    const raw = report(); mutate(raw); assert.throws(() => readMortgageReport(raw, selection));
  }
});

test('null and zero remain distinct; source average sizes and terms are not filled', () => {
  const raw = report(); raw.monthly[0].all = empty(); raw.monthly[0].primary = values({ ratePct: 0, count: 0 });
  const data = readMortgageReport(raw, selection);
  assert.equal(data.monthly[0].primary.count, 0);
  assert.equal(data.monthly[0].all.count, null);
  assert.equal(data.monthly[0].primary.averageAmountRub, null);
  assert.equal(data.monthly[0].primary.termMonths, null);
});

test('global region and annual month do not consume monthly range or metrics controls', () => {
  const c = readMortgageCatalog(catalog());
  assert.equal(mortgageSelection(null, new URLSearchParams()), null);
  assert.deepEqual(mortgageSelection(c, new URLSearchParams()), { region: 'msk', month: 2 });
  assert.deepEqual(mortgageSelection(c, new URLSearchParams('region=rf&annualMonth=4&mortgage_volume_from=2026-01&mortgage_rates_to=2026-03&mortgage_terms=2026-01')), { region: 'rf', month: 4 });
  assert.deepEqual(mortgageSelection(c, new URLSearchParams('region=spb&annualMonth=13&month=5')), { region: 'msk', month: 2 });
});

test('cumulative mortgage defaults follow the latest regional source year, not old December or collection date', () => {
  const raw = catalog();
  raw.regions[0].periods = ['2026-09', '2025-12', '2026-01'];
  raw.regions[1].periods = ['2026-12', '2027-01'];
  raw.source.date = '2030-12-31';
  const c = readMortgageCatalog(raw), before = structuredClone(c);
  assert.deepEqual(mortgageSelection(c, new URLSearchParams()), { region: 'msk', month: 9 });
  assert.deepEqual(mortgageSelection(c, new URLSearchParams('region=rf')), { region: 'rf', month: 1 });
  for (let month = 1; month <= 12; month++) {
    assert.deepEqual(mortgageSelection(c, new URLSearchParams(`region=rf&annualMonth=${month}`)), { region: 'rf', month });
  }
  assert.deepEqual(c, before);
  raw.regions[0].periods = [];
  assert.throws(() => readMortgageCatalog(raw));
});

test('mortgage exports pin generation and canonical filters', () => {
  assert.equal(mortgageUrl(selection), '/api/v1/mortgage?region=msk&month=5');
  const url = new URL(mortgageUrl(selection, 'version&one'), 'https://dashboard.example');
  assert.equal(url.pathname, '/api/v1/mortgage/export');
  assert.equal(url.searchParams.get('required_version'), 'version&one');
  assert.equal(url.searchParams.get('month'), '5');
});

test('local observed ranges default to full history, never invert and preserve missing months', () => {
  const data = report(), before = structuredClone(data);
  assert.deepEqual(mortgageRange(data.monthly, null, null).rows, data.monthly);
  assert.deepEqual(mortgageRange(data.monthly, '2026-03', '2026-01').rows.map(row => row.period), ['2026-03']);
  assert.equal(mortgageRange(data.monthly, 'invalid', 'invalid').from, '2026-01');
  assert.deepEqual(mortgageRange([], null, null), { from: '', to: '', rows: [] });
  assert.deepEqual(data, before);
});

test('credit metrics picker includes overall source terms or sizes only, and keeps zero', () => {
  const rows = [month('2025-11', { termMonths: 0 }), month('2025-12', {}, {}, { termMonths: 0 }), month('2026-01', { ratePct: 10 }), month('2026-02', { averageAmountRub: 0 })];
  assert.deepEqual(mortgageMetricPeriods(rows), ['2025-11', '2026-02']);
  assert.deepEqual(mortgageMetricPeriods([month('2026-03', { volumeRub: 100, count: 10 })]), []);
  assert.equal(mortgagePeriodLabel('2026-02'), 'Февраль 2026');
  assert.equal(mortgageCumulativeLabel(10), 'янв–окт');
});

test('each monthly chart starts at its own first observed indicator without trimming internal gaps', () => {
  const rows = [month('2025-12', { ratePct: 10 }), month('2026-01', {}, { volumeRub: 0 }), month('2026-02'), month('2026-03', {}, { count: 100 }, { volumeRub: 500 })];
  assert.deepEqual(mortgageHistory(rows, 'volume').map(row => row.period), ['2026-01', '2026-02', '2026-03']);
  assert.deepEqual(mortgageHistory(rows, 'count').map(row => row.period), ['2026-03']);
  assert.deepEqual(mortgageHistory(rows, 'rates').map(row => row.period), ['2025-12']);
  assert.deepEqual(mortgageHistory([month('2026-01')], 'rates'), []);
});

for (const theme of ['light', 'dark']) {
  for (const metric of ['volume', 'count']) {
    test(`annual ${metric} (${theme}) has separate percentage axis and no fake partial annual`, () => {
      const rows = report().annual, before = structuredClone(rows), option = mortgageAnnualOption(rows, metric, theme, 900), divisor = metric === 'volume' ? 1e9 : 1000;
      assert.deepEqual(option.xAxis[1].data, ['2025', '2026']);
      assert.equal(option.xAxis[1].axisLabel.formatter('2026', 1), '2026\nянв–май');
      assert.equal(option.series[0].data[1], rows[1][metric].selected / divisor);
      assert.equal(option.series[1].data[0], (rows[0][metric].annual - rows[0][metric].selected) / divisor);
      assert.equal(option.series[1].data[1], null);
      assert.equal(option.series[2].yAxisIndex, 0);
      assert.equal(option.series[0].yAxisIndex, 1);
      assert.equal(option.series[2].lineStyle.type, 'dashed');
      assert.equal(option.series[2].connectNulls, false);
      assert.deepEqual(option.series[2].data, rows.map(row => row[metric].annualYoYPct));
      assert.match(option.tooltip.formatter({ dataIndex: 1, seriesIndex: 0 }), /янв–май/);
      assert.ok(!option.tooltip.formatter({ dataIndex: 0, seriesIndex: 1 }).includes('382'));
      assert.match(option.tooltip.formatter({ dataIndex: 0, seriesIndex: 1 }), /Годовое значение/);
      assert.equal(option.series[0].label.show, true);
      assert.equal(mortgageAnnualOption(rows, metric, theme, 100).series[0].label.show, true);
      assert.deepEqual(rows, before);
    });
    test(`monthly ${metric} (${theme}) stacks only observed primary/secondary and retains calendar gaps`, () => {
      const rows = report().monthly, before = structuredClone(rows), option = mortgageMonthlyOption(rows, metric, theme);
      assert.equal(option.series.length, 2);
      assert.deepEqual(option.series.map(series => series.name), ['Первичный рынок', 'Вторичный рынок']);
      assert.deepEqual(option.xAxis.data, ['2026-01', '2026-02', '2026-03']);
      assert.equal(option.series[0].data[1], null);
      assert.equal(option.series[1].data[1], null);
      assert.equal(option.series[0].label.show, false);
      assert.match(option.tooltip.formatter({ dataIndex: 0, seriesIndex: 1 }), /Вторичный рынок/);
      assert.match(option.tooltip.formatter({ dataIndex: 0, seriesIndex: 1 }), metric === 'volume' ? /12 млрд руб\./ : /2,5 тыс\. шт\./);
      assert.deepEqual(rows, before);
    });
  }
  test(`rates (${theme}) have three source-only monthly lines with gaps and value-scale`, () => {
    const rows = report().monthly, before = structuredClone(rows), option = mortgageRatesOption(rows, theme);
    assert.equal(option.series.length, 3);
    assert.deepEqual(option.series[0].data, [9.7, null, 9.6]);
    assert.deepEqual(option.series[1].data, [6.7, null, 6.5]);
    assert.deepEqual(option.series[2].data, [15.2, null, 15]);
    assert.ok(option.series.every(series => series.type === 'line' && series.connectNulls === false));
    assert.equal(option.yAxis.scale, true);
    assert.ok(option.yAxis.min({ min: 40, max: 42 }) > 0);
    assert.match(option.tooltip.formatter([{ dataIndex: 0 }]), /В целом: 9,7 %/);
    assert.match(option.tooltip.formatter([{ dataIndex: 0 }]), /Первичный рынок: 6,7 %/);
    assert.deepEqual(rows, before);
  });
}

for (const theme of ['light', 'dark']) for (const metric of ['volume', 'count']) {
  test(`annual ${metric} (${theme}) labels every bar above and inside, even without a gray remainder`, () => {
    const divisor = metric === 'volume' ? 1e9 : 1000;
    const rows = [annual(2023), annual(2024), annual(2025), annual(2026)];
    rows[0][metric] = annualValue({ annual: 10 * divisor, selected: 10 * divisor, actualThroughMonth: 12 });
    rows[1][metric] = annualValue({ annual: 15 * divisor, selected: 5 * divisor, actualThroughMonth: 5 });
    rows[2][metric] = annualValue({ selected: 0, actualThroughMonth: 5 });
    rows[3][metric] = annualValue({ selected: 7 * divisor, actualThroughMonth: 5 });
    const before = structuredClone(rows);
    for (const width of [240, 360, 700, 1500]) {
      const option = mortgageAnnualOption(rows, metric, theme, width);
      assert.equal(option.xAxis[1].axisLabel.interval, 0);
      assert.equal(option.xAxis[1].axisLabel.hideOverlap, false);
      assert.equal(option.series[0].label.show, true);
      assert.equal(option.series[0].labelLayout.hideOverlap, false);
      assert.equal(option.series[0].label.position, 'insideBottom');
      assert.deepEqual(option.graphic.map(label => label.children[0].style.text), ['10', '15', '0', '7']);
      assert.deepEqual(option.series[0].data.map((value, dataIndex) => option.series[0].label.formatter({ value, dataIndex }).replace(/\{small\|([^}]*)\}/g, '$1')), ['10', '5', '0', '7']);
      assert.deepEqual(option.series[1].data, [null, 10, null, null]);
      assert.equal(option.series[1].label.show, false);
      assert.ok(option.graphic.every(label => label.x > 0 && label.x < width && label.y > 90 && label.y < 400));
    }
    assert.deepEqual(rows, before);
    const narrow = mortgageAnnualOption(Array.from({ length: 18 }, (_, index) => ({ ...rows[3], year: 2009 + index })), metric, theme, 270);
    assert.equal(narrow.graphic.length, 18);
    assert.equal(narrow.xAxis[1].axisLabel.rotate, 90);
    assert.equal(narrow.xAxis[1].axisLabel.formatter('2026', 17), '2026 янв–май');
    assert.equal(narrow.series[0].label.rotate, 90);
    const missing = mortgageAnnualOption([annual(2026)], metric, theme, 270);
    assert.deepEqual(missing.graphic, []);
    assert.equal(missing.series[0].label.formatter({ dataIndex: 0, value: null }), '');
  });
}

test('monthly charts divide calendar years independently of selected range, missing months and theme', () => {
  const rows = [month('2016-11', { ratePct: 9 }, { volumeRub: 10, count: 2 }), month('2017-02', { ratePct: 10 }, {}, { volumeRub: 15, count: 3 }), month('2018-03', { ratePct: 11 })];
  const before = structuredClone(rows);
  for (const theme of ['light', 'dark']) {
    for (const option of [mortgageMonthlyOption(rows, 'volume', theme), mortgageMonthlyOption(rows, 'count', theme), mortgageRatesOption(rows, theme)]) {
      assert.equal(option.xAxis.splitLine.show, true);
      assert.equal(option.xAxis.splitLine.showMinLine, false);
      assert.equal(option.xAxis.splitLine.showMaxLine, false);
      const divisions = option.xAxis.data.filter((period, index) => index > 0 && option.xAxis.splitLine.interval(index, period));
      assert.deepEqual(divisions, ['2017-01', '2018-01']);
      assert.equal(option.xAxis.splitLine.interval(0, '2016-11'), true);
      assert.equal(option.xAxis.splitLine.interval(3, '2017-02'), false);
      assert.equal(option.xAxis.splitLine.lineStyle.width, 1);
      assert.equal(option.xAxis.splitLine.lineStyle.color, theme === 'light' ? '#c5d0d7' : '#64717c');
    }
    const oneYear = mortgageMonthlyOption([rows[0]], 'volume', theme);
    assert.deepEqual(oneYear.xAxis.data.filter((period, index) => index > 0 && oneYear.xAxis.splitLine.interval(index, period)), []);
  }
  assert.deepEqual(rows, before);
});

test('large annual and monthly measurements remove decimals consistently across each chart', () => {
  const rows = [annual(2024, { annual: 1020.2e9, selected: 300.2e9, actualThroughMonth: 5, selectedYoYPct: 13.2 }), annual(2026, { selected: 399.2e9, actualThroughMonth: 5 })];
  const option = mortgageAnnualOption(rows, 'volume', 'light', 900);
  assert.equal(option.yAxis[1].axisLabel.formatter(300.2), '300');
  assert.deepEqual(option.graphic.map(label => label.children[0].style.text), [new Intl.NumberFormat('ru-RU').format(1020), '399']);
  assert.match(option.series[0].label.formatter({ dataIndex: 0, value: 300.2 }), /^300\n\{up\|\+13%\}$/);
  const months = [month('2026-01', {}, { volumeRub: 600.2e9 }, { volumeRub: 500.2e9 }), month('2026-02', {}, { volumeRub: 300.2e9 }, { volumeRub: 399.2e9 })];
  const monthly = mortgageMonthlyOption(months, 'volume', 'light');
  assert.equal(monthly.yAxis.axisLabel.formatter(399.2), '399');
  assert.ok(!monthly.tooltip.formatter({ dataIndex: 0, seriesIndex: 0 }).includes('600,2'));
});

test('missing historical years and null source fields are not fabricated as zero annual observations', () => {
  const raw = report(); raw.annual = [annual(2020, { selected: 0, actualThroughMonth: 5 }), annual(2026)];
  const data = readMortgageReport(raw, selection), option = mortgageAnnualOption(data.annual, 'volume', 'light');
  assert.deepEqual(option.xAxis[1].data, ['2020', '2026']);
  assert.deepEqual(option.series[0].data, [0, null]);
  assert.deepEqual(option.series[1].data, [null, null]);
  assert.equal(mortgageAnnualRows(data.annual, 'volume')[1][2], 0);
  assert.equal(mortgageAnnualRows(data.annual, 'volume')[2][2], null);
});

test('download rows retain canonical source precision, units and observed nulls', () => {
  const rows = [month('2026-01', { ratePct: 8.12345 }, { volumeRub: 1234567890.123, count: 0, ratePct: null }, { volumeRub: null, count: 100, ratePct: 15.12345 })];
  assert.deepEqual(mortgageMonthlyRows(rows, 'volume')[1], ['2026-01', 1234567890.123, null]);
  assert.deepEqual(mortgageMonthlyRows(rows, 'count')[1], ['2026-01', 0, 100]);
  assert.deepEqual(mortgageMonthlyRows(rows, 'rates')[1], ['2026-01', 8.12345, null, 15.12345]);
});

test('route, home and sidebar lead to a dedicated mortgage page, not the profile fallback', () => {
  const app = readFileSync(new URL('./App.tsx', import.meta.url), 'utf8'), sidebar = readFileSync(new URL('./Sidebar.tsx', import.meta.url), 'utf8'), home = readFileSync(new URL('./ServicePages.tsx', import.meta.url), 'utf8'), mortgage = readFileSync(new URL('./Mortgage.tsx', import.meta.url), 'utf8');
  assert.match(app, /const mortgage = path === '\/economics\/mortgage'/);
  assert.match(app, /mortgage \? <Mortgage/);
  assert.match(sidebar, /group\('cbr', 'ЦБ РФ'.*Ипотечное кредитование.*\/economics\/mortgage/);
  assert.match(home, /homeLink\('\/economics\/mortgage', 'Ипотечное кредитование'\)/);
  assert.match(mortgage, /echarts\.use\(\[LineChart, DataZoomComponent\]\)/);
  assert.match(mortgage, /ДОМ\.РФ/);
  assert.ok(!mortgage.includes('profile-snapshot'));
});
