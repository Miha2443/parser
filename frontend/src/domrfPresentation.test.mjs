import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { domrfDonutOption, domrfFundsLabel, domrfLineOption, domrfPriceNote, domrfRealizationHistoryOption } from './domrfPresentation.ts';

const text = html => html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
const observation = (period, sold = 7440.2) => ({
  period, label: `Месяц ${period}`, reportDate: '2026-10-19', sourceFile: 'history.json',
  metrics: { sold_thousand_m2: sold, sold_pct: 48.2, unsold_thousand_m2: 7000, unsold_pct: 45.4, sales_not_open_thousand_m2: 1000, sales_not_open_pct: 6.4 },
});

test('DOMRF donut centers show area in million square meters, not percentage', () => {
  for (const theme of ['light', 'dark']) {
    const option = domrfDonutOption('Проданная площадь', 48.2, 7440.2, '#2c9869', theme);
    assert.equal(option.graphic[0].style.text, '7,4');
    assert.equal(option.graphic[1].style.text, 'млн м²');
    assert.equal(option.series[0].data[0].value, 48.2);
    assert.match(text(option.tooltip.formatter()), /Проданная площадь 7,4 млн м² · 48,2 %/);
    assert.ok(!option.graphic.some(item => item.style.text.includes('%')));
  }
});

test('DOMRF donuts preserve zero and missing values without deriving area from percentage', () => {
  assert.equal(domrfDonutOption('Непроданная площадь', 0, 0, '#398dcc', 'light').graphic[0].style.text, '0');
  const missing = domrfDonutOption('Проданная площадь', 48, null, '#2c9869', 'light');
  assert.equal(missing.graphic[0].style.text, '—');
  assert.equal(missing.series[0].data[0].value, 48);
  const invalid = domrfDonutOption('Проданная площадь', 104, 7440, '#2c9869', 'light');
  assert.deepEqual(invalid.series[0].data, []);
  assert.equal(invalid.graphic[0].style.text, '7,4');
});

test('dense monthly sales hide all segment labels but retain compact area and percentage hover', () => {
  const history = Array.from({ length: 82 }, (_, i) => observation(`${2020 + Math.floor(i / 12)}-${String(i % 12 + 1).padStart(2, '0')}`));
  const before = structuredClone(history), option = domrfRealizationHistoryOption(history, 'light');
  assert.equal(option.series.length, 3);
  for (const series of option.series) {
    assert.equal(series.stack, 'realization');
    assert.equal(series.label.show, false);
    assert.equal(series.emphasis.label.show, false);
  }
  assert.equal(text(option.tooltip.formatter({ dataIndex: 0, seriesIndex: 0 })), 'Месяц 2020-01 · Проданная площадь 7,4 млн м² · 48,2 %');
  assert.deepEqual(history, before);
});

test('DOMRF chart formatting uses one precision across all visible area values', () => {
  const history = [observation('2020-01', 1020200), observation('2020-02', 300200)];
  const option = domrfRealizationHistoryOption(history, 'dark');
  assert.match(text(option.tooltip.formatter({ dataIndex: 0, seriesIndex: 0 })), /1 020 млн м²/);
  assert.match(text(option.tooltip.formatter({ dataIndex: 1, seriesIndex: 0 })), /300 млн м²/);
  assert.equal(option.yAxis.axisLabel.formatter(399.2), '399');
});

test('DOMRF line history formats tooltips and axes consistently and preserves null gaps', () => {
  const chart = { id: 'domrf-construction', title: 'Строительство', kind: 'line', unit: 'млн м²', series: [{ id: 'living', name: 'Жилая площадь', unit: 'млн м²', points: [{ x: '2020-01', y: 1020.2 }, { x: '2020-02', y: null }, { x: '2020-03', y: 300.2 }] }] };
  const option = domrfLineOption(chart, 'light');
  assert.equal(option.yAxis.axisLabel.formatter(399.2), '399');
  assert.equal(text(option.tooltip.formatter([{ name: '2020-03', seriesName: 'Жилая площадь', seriesIndex: 0, value: 300.2 }])), '2020-03 · Жилая площадь 300 млн м²');
  assert.equal(option.series[0].connectNulls, false);
  assert.deepEqual(option.series[0].data, [1020.2, null, 300.2]);
});

test('DOMRF page keeps only million-area history, page-scoped CSS and literal source price', () => {
  const source = readFileSync(new URL('./DomRfPage.tsx', import.meta.url), 'utf8');
  assert.match(source, /import '\.\/domrf-page\.css'/);
  assert.match(source, /value=\{metrics\.price_per_m2_rub\}/);
  assert.match(source, /unit="руб\.\/м²" note=\{domrfPriceNote\}/);
  assert.doesNotMatch(source, /charts\.filter\(c => c\.id === 'monthly_volume'\)/);
  assert.doesNotMatch(source, /className="cp-donut-area"/);
  assert.match(domrfPriceNote, /млн руб\.\) \/ проданная площадь \(млн м²\) = руб\.\/м²/);
  assert.equal(domrfFundsLabel, 'Объём привлечённых средств по счетам эскроу');
  assert.equal(domrfPriceNote, `${domrfFundsLabel} (млн руб.) / проданная площадь (млн м²) = руб./м².`);
  assert.match(source, /label=\{domrfFundsLabel\} value=\{metrics\.funds_million_rub\}/);
  const legacy = readFileSync(new URL('./Construction.tsx', import.meta.url), 'utf8');
  assert.match(legacy, /label=\{domrfFundsLabel\} value=\{numeric\(sales\.funds_million_rub\)\}/);
  const css = readFileSync(new URL('./domrf-page.css', import.meta.url), 'utf8');
  for (const rule of css.split('\n').filter(line => line.includes('{') && !line.startsWith('@'))) assert.ok(rule.trimStart().startsWith('.domrf-page'), rule);
});
