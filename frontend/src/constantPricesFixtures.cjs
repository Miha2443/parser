const economics = require('./economics.browser.cjs');
const regions = ['Москва', 'Российская Федерация'];
const selection = { period: 'month', regions, index_base: 'month_to_month', months: Array.from({ length: 12 }, (_, i) => i + 1), quarters: [1, 2, 3, 4] };
function catalog() { return economics.catalog('ipc'); }
function report() {
  const result = economics.report('ipc', selection), rows = regions.flatMap((region, index) => Array.from({ length: 44 }, (_, i) => ({ year: 2023 + Math.floor(i / 12), month: i % 12 + 1, region, 'ипц_%': 101 + index, период: `${2023 + Math.floor(i / 12)}-${String(i % 12 + 1).padStart(2, '0')}` })));
  result.charts = [{ id: 'ipc_month', title: 'ИПЦ к предыдущему месяцу', unit: '%', kind: 'line', stack: false, columns: ['year', 'month', 'region', 'ипц_%', 'период'].map(id => ({ id, label: id })), rows,
    series: regions.map(region => ({ id: region, name: region, unit: '%', points: rows.filter(row => row.region === region).map(row => ({ x: row.период, y: row['ипц_%'] })) })) }];
  result.tables = [];
  return result;
}
module.exports = { catalog, report, selection };
