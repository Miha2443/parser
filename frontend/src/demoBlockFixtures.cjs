const fixtures = require('./demoFixtures.cjs');

function response(input) {
  const value = structuredClone(fixtures.response(input));
  if (new URL(input, 'http://localhost').pathname !== '/api/v1/construction/permits') return value;
  const housing = value.breakdowns[0];
  housing.rows.forEach(row => { for (const field of ['living', 'mop_embedded', 'housing_total']) row[field] *= 25; });
  housing.series.forEach(series => series.points.forEach(point => point.y *= 25));
  const block = (id, title, fields, aggregateField, rows) => ({ id, title, unit: 'тыс. м²', periodLabel: housing.periodLabel,
    aggregateField, rows, columns: ['year', ...fields, aggregateField].map(id => ({ id, label: id })), notes: [],
    series: fields.map(id => ({ id, name: id, points: rows.map(row => ({ x: String(row.year), y: row[id] })) })),
  });
  const nonres = block('nonresidential_components', 'Структура площади нежилья', ['office', 'trade', 'other'], 'nonres_total',
    housing.rows.map(row => ({ year: row.year, office: 700, trade: 500, other: 300, nonres_total: 1500, periodComplete: true, annualComplete: true })));
  const area = block('area_composition', 'Общая площадь и состав', ['living', 'mop_embedded', 'nonresidential'], 'total_area',
    housing.rows.map(row => ({ year: row.year, living: row.living, mop_embedded: row.mop_embedded, nonresidential: 1500, total_area: row.housing_total + 1500, periodComplete: true, annualComplete: true })));
  value.breakdowns = [area, housing, nonres];
  return value;
}
module.exports = { response };
