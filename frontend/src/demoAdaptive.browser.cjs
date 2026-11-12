const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require(process.env.DEMO_ADAPTIVE_PLAYWRIGHT || process.env.DEMO_LABELS_PLAYWRIGHT || 'playwright');
const fixtures = require('./demoFixtures.cjs');

// Run with the same bundled NODE_PATH as demo-labels.browser.cjs; no backend is needed.
const output = process.env.DEMO_ADAPTIVE_OUTPUT || path.join(os.tmpdir(), 'dashboard-demo-adaptive');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const overlaps = (a, b) => Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x) > .5
  && Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y) > .5;

async function freePort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

function response(input) {
  const value = structuredClone(fixtures.response(input));
  if (new URL(input, 'http://localhost').pathname !== '/api/v1/linear/dashboard') return value;
  const labels = [
    '\u0410\u0432\u0442\u043e\u043c\u043e\u0431\u0438\u043b\u044c\u043d\u044b\u0435 \u0434\u043e\u0440\u043e\u0433\u0438',
    '\u041c\u043e\u0441\u0442\u044b \u0438 \u044d\u0441\u0442\u0430\u043a\u0430\u0434\u044b',
    '\u041f\u0435\u0448\u0435\u0445\u043e\u0434\u043d\u044b\u0435 \u043f\u0435\u0440\u0435\u0445\u043e\u0434\u044b',
    '\u0421\u0442\u0430\u043d\u0446\u0438\u0438 \u043c\u0435\u0442\u0440\u043e',
    '\u0422\u043e\u043d\u043d\u0435\u043b\u0438 \u043c\u0435\u0442\u0440\u043e',
    '\u042d\u043b\u0435\u043a\u0442\u0440\u043e\u0434\u0435\u043f\u043e \u043c\u0435\u0442\u0440\u043e',
  ];
  value.currentYear = 2026;
  value.metrics.forEach((metric, index) => {
    const measure = n => Number(n.toFixed(['roads', 'metro_tunnels'].includes(metric.id) ? 1 : 0));
    const annual = i => [40, 3, 12, 4, 6, 1][index] + i * [4.6, 1, 2, 1, 1.4, 0][index]
      + i % 4 * [10.3, 3, 5, 2, 2.7, 1][index];
    metric.label = labels[index];
    metric.years = Array.from({ length: 20 }, (_, i) => {
      const year = 2011 + i, observed = year <= value.currentYear, current = year === value.currentYear;
      return { year, fact: observed ? measure(annual(i) * (current ? .45 : 1)) : null,
        factKind: observed ? current ? 'partial' : 'annual' : null,
        factPeriod: current ? '\u044f\u043d\u0432\u0430\u0440\u044c-\u043e\u043a\u0442\u044f\u0431\u0440\u044c 2026' : observed ? String(year) : null,
        factSource: observed ? current ? 'dsti' : 'monitoring' : null,
        plan: year >= value.currentYear ? measure(annual(i) * 1.1) : null,
        forecast: year >= value.currentYear ? measure(annual(i) * 1.2) : null };
    });
    const current = metric.years.find(row => row.year === value.currentYear);
    Object.assign(metric, { fact: current.fact, factPeriod: current.factPeriod, factSource: current.factSource,
      annualPlan: current.plan, forecast: current.forecast, target: current.forecast, targetKind: 'forecast',
      percent: current.fact / current.forecast * 100, remaining: Math.max(0, current.forecast - current.fact) });
  });
  return value;
}

function permitFixture() {
  const value = structuredClone(fixtures.response('/api/v1/construction/permits'));
  for (const permits of [value.permits, ...Object.values(value.areaVariants).map(variant => variant.permits)]) {
    let selected = 10;
    permits.presentation.rows.forEach((row, index) => {
      const growth = index ? 2 + (index - 1) * 1.5 : null;
      if (index) selected *= 1 + growth / 100;
      Object.assign(row, { selected, annualActual: selected * 1.8, annualForecast: null,
        difference: selected * .8, remainder: selected * .8, periodComplete: true, annualComplete: true, growth });
    });
  }
  return value;
}

async function installHarness(page, base) {
  await page.route('**/demo-adaptive-harness', route => route.fulfill({ contentType: 'text/html',
    body: '<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;background:white"><div id="chart"></div></body></html>' }));
  await page.goto(base + '/demo-adaptive-harness');
  await page.evaluate(async permits => {
    const echarts = await import('/node_modules/echarts/dist/echarts.esm.js');
    const { demoPresentationOption, demoPresentationScale, layoutDemoLineLabels } = await import('/src/demoPresentation.ts');
    const { economicsChartOption } = await import('/src/economicsData.ts');
    const { permitsMainOption } = await import('/src/permitPresentation.ts');
    const { chartNumberFormatter } = await import('/src/format.ts');
    if (typeof layoutDemoLineLabels !== 'function') throw new Error('layoutDemoLineLabels export is required');
    await document.fonts.ready;
    const cpi = Array.from({ length: 90 }, (_, i) => 98 + i * .2);
    cpi[20] = 125.2; cpi[60] = 85.2; cpi[40] = null;
    const economic = (data, width) => economicsChartOption({ id: 'ipc-adaptive', title: 'IPC', kind: 'line', stack: false,
      unit: '%', columns: [], rows: [], series: data.map((values, i) => ({ id: String(i), name: ['Moscow', 'Russia'][i], unit: '%',
        points: values.map((y, index) => ({ x: `m-${index + 1}`, y })) })) }, 'light', { width });
    const cases = {
      dense: width => {
        const option = economic([cpi, cpi.map(y => y === null ? null : y + .1)], width);
        option.yAxis.min = 85; option.yAxis.max = 126;
        return option;
      },
      roomy: width => economic([[98, 99.2, 100.4, null, 102.8, 104, 105.2, 106.4, 107.6, 108.8, 110, 111.2],
        [98.1, 99.3, 100.5, null, 102.9, 104.1, 105.3, 106.5, 107.7, 108.9, 110.1, 111.3]], width),
      paired: () => ({ animation: false, grid: { left: 48, right: 24, top: 40, bottom: 48 },
        xAxis: { type: 'category', data: ['A', 'B', 'C', 'D'], boundaryGap: true },
        yAxis: { type: 'value', name: '%', min: 90, max: 120 },
        series: [[100, 104, 108, 112], [99.9, 103.9, 107.9, 111.9]].map((data, i) => ({
          type: 'line', name: `paired-${i}`, data, symbolSize: 4, lineStyle: { color: ['#2473af', '#b9404c'][i] } })) }),
      permits: (width, height) => permitsMainOption(permits, 'light', { width, height, includeGrowth: true,
        presentation: true, fontScale: demoPresentationScale(width, height) }),
    };
    const normalized = text => String(text).replace(/\s/g, '').replace(/\u2212/g, '-').replace(/^\+/, '');
    const rect = node => {
      const r = node.getBoundingRect().clone(); r.applyTransform(node.getComputedTransform());
      return { x: r.x, y: r.y, width: r.width, height: r.height };
    };
    const visible = node => {
      for (let current = node; current; current = current.parent) {
        if (current.ignore || current.invisible || current.style?.opacity === 0) return false;
      }
      return true;
    };
    window.renderAdaptive = async (id, width, height, reuse = false) => {
      const element = document.querySelector('#chart');
      element.style.width = `${width}px`; element.style.height = `${height}px`;
      if (reuse) {
        window.adaptiveChart.resize({ width, height });
        layoutDemoLineLabels(window.adaptiveChart);
      } else {
        window.adaptiveChart?.dispose();
        window.adaptiveChart = echarts.init(element, null, { renderer: 'canvas', devicePixelRatio: 1 });
      }
      const chart = window.adaptiveChart, option = cases[id](width, height), before = JSON.stringify(option);
      const presentation = demoPresentationOption(option, width, height);
      chart.setOption(presentation, true);
      layoutDemoLineLabels(chart);
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      chart.getZr().refreshImmediately();
      const actual = chart.getOption(), format = chartNumberFormatter(option.series.flatMap(series => series.data));
      const expected = option.series.flatMap((series, seriesIndex) => series.type !== 'line' ? [] : series.data.flatMap((value, index) => {
        if (!Number.isFinite(value)) return [];
        const point = chart.convertToPixel({ xAxisIndex: series.xAxisIndex || 0, yAxisIndex: series.yAxisIndex || 0 }, [index, value]);
        const area = chart.getModel().getSeriesByIndex(seriesIndex).coordinateSystem.master.getRect();
        const mark = actual.series[seriesIndex].markPoint?.data?.find(p => /^demo-(extremum|value)-/.test(p.name)
          && p.coord[0] === index && p.coord[1] === value);
        return [{ key: `${seriesIndex}/${index}`, seriesIndex, index, value, text: normalized(`${format(value)}%`),
          point, plot: { x: area.x, y: area.y, width: area.width, height: area.height }, kind: mark?.name?.split('-')[1] }];
      }));
      const texts = chart.getZr().storage.getDisplayList(true).filter(node => node.type === 'tspan' && visible(node) && node.style.text)
        .map(node => ({ text: String(node.style.text), font: node.style.font, ...rect(node) }));
      // Axis ticks have no percent suffix. Match rendered captions, never configured show flags.
      const labels = texts.filter(node => /^[+\u2212-]?[\d\s.,]+\s*%$/.test(node.text)).map(label => ({ ...label,
        key: expected.find(point => point.text === normalized(label.text))?.key }));
      const colors = new Set();
      for (const canvas of element.querySelectorAll('canvas')) {
        const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        for (let i = 0; i < pixels.length; i += Math.max(4, Math.floor(pixels.length / 32000 / 4) * 4)) {
          if (pixels[i + 3]) colors.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]}`);
        }
      }
      return { width, height, expected, labels, otherTexts: texts.filter(node => !/^[+\u2212-]?[\d\s.,]+\s*%$/.test(node.text)),
        colors: colors.size, unchanged: before === JSON.stringify(option),
        sourceData: option.series.map(series => series.data), renderedData: actual.series.map(series => series.data),
        categories: actual.xAxis.map(axis => axis.data), animation: actual.animation };
    };
  }, permitFixture());
}

function assertLabels(state, tag, allFinite = false) {
  assert.ok(state.colors > 5, `${tag}: blank canvas`);
  assert.equal(state.animation, false, `${tag}: animation was re-enabled`);
  assert.ok(state.unchanged, `${tag}: source option mutated`);
  assert.deepEqual(state.renderedData, state.sourceData, `${tag}: measurements mutated`);
  assert.equal(new Set(state.expected.map(point => point.text)).size, state.expected.length, `${tag}: ambiguous test values`);
  assert.ok(state.labels.length, `${tag}: no visible percent captions`);
  assert.ok(state.labels.every(label => label.key), `${tag}: fabricated caption ${JSON.stringify(state.labels.filter(label => !label.key))}`);
  assert.equal(new Set(state.labels.map(label => label.key)).size, state.labels.length, `${tag}: duplicate captions`);
  for (const [index, label] of state.labels.entries()) {
    assert.ok(label.x >= -1 && label.y >= -1 && label.x + label.width <= state.width + 1
      && label.y + label.height <= state.height + 1, `${tag}: clipped caption ${JSON.stringify(label)}`);
    for (const other of state.labels.slice(index + 1)) {
      assert.ok(!overlaps(label, other), `${tag}: overlapping captions ${JSON.stringify([label, other])}`);
    }
    for (const other of state.otherTexts) {
      assert.ok(!overlaps(label, other), `${tag}: caption overlaps axis/graphic ${JSON.stringify([label, other])}`);
    }
  }
  if (allFinite) assert.deepEqual(state.labels.map(label => label.key).sort(), state.expected.map(point => point.key).sort(),
    `${tag}: every finite point must have an actual visible percent caption`);
}

async function checkLabels(page, base, check) {
  await installHarness(page, base);
  for (const [id, sizes] of [['dense', [[900, 300], [1440, 360]]], ['roomy', [[1920, 900], [3750, 1740]]],
    ['paired', [[1200, 650]]], ['permits', [[1920, 900], [3750, 1740], [1920, 900]]]]) {
    for (const [step, [width, height]] of sizes.entries()) {
      await check(`${id}-${width}-${height}-${step}`, async name => {
        await page.setViewportSize({ width: Math.max(1440, width), height: Math.max(900, height) });
        const state = await page.evaluate(({ id, width, height, reuse }) => window.renderAdaptive(id, width, height, reuse),
          { id, width, height, reuse: step > 0 });
        await page.screenshot({ path: path.join(output, `${name}.png`), clip: { x: 0, y: 0, width, height } });
        fs.writeFileSync(path.join(output, `${name}.json`), JSON.stringify(state, null, 2));
        assertLabels(state, name, id !== 'dense');
        if (id === 'dense') {
          assert.equal(state.expected.length, 178, `${name}: dense fixture lost finite points`);
          assert.ok(state.labels.length < state.expected.length, `${name}: fixture must exercise real collisions`);
          assert.ok(state.labels.some(label => state.expected.find(point => point.key === label.key).kind === 'value'),
            `${name}: only extrema are labelled despite available gaps`);
          for (const [a, b] of [[125.2, 125.3], [85.2, 85.3]]) {
            const point = state.expected.find(point => Math.abs(point.value - a) < 1e-8);
            assert.ok(state.labels.some(label => {
              const value = state.expected.find(point => point.key === label.key).value;
              return Math.abs(value - a) < 1e-8 || Math.abs(value - b) < 1e-8;
            }), `${name}: prioritized extremum ${a} missing`);
            if (a < 90) assert.ok(point.point[1] > point.plot.y + point.plot.height - 4,
              `${name}: trough no longer exercises the plot bottom`);
          }
        }
        if (id === 'paired') {
          for (let index = 0; index < 4; index++) {
            const pair = state.expected.filter(point => point.index === index);
            const labels = pair.map(point => ({ ...state.labels.find(label => label.key === point.key), point: point.point }));
            assert.ok(labels.some(label => label.y >= label.point[1] + 1), `${name}: colliding pair ${index} never moved below`);
            assert.ok(labels.some(label => label.y + label.height <= label.point[1] - 1), `${name}: pair ${index} lost its upper caption`);
          }
        }
        if (id === 'permits') {
          assert.equal(state.categories[0].length, 16, `${name}: fixture must span sixteen years`);
          assert.equal(state.expected.length, 15, `${name}: only the first year's growth may be unavailable`);
          assert.deepEqual(state.expected.map(point => point.index), Array.from({ length: 15 }, (_, i) => i + 1));
        }
        return { width, height, finite: state.expected.length, visible: state.labels.length, reuse: step > 0 };
      });
    }
  }
}

async function checkSticky(page, check) {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    await check(`sticky-${viewport.width}`, async name => {
      await page.setViewportSize(viewport);
      const samples = [];
      for (const fraction of [0, .25, .65, 1]) {
        const sample = await page.evaluate(async fraction => {
          window.scrollTo(0, (document.scrollingElement.scrollHeight - innerHeight) * fraction);
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const toolbar = document.querySelector('.demo-settings');
          const controls = [...toolbar.querySelectorAll('input, button')].map(element => {
            const r = element.getBoundingClientRect(), hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
            return { name: element.getAttribute('aria-label') || element.textContent || element.type,
              x: r.x, y: r.y, width: r.width, height: r.height, disabled: element.disabled,
              reachable: hit === element || element.contains(hit) };
          });
          return { scrollY, top: toolbar.getBoundingClientRect().top, controls,
            overflow: document.documentElement.scrollWidth > innerWidth + 1 };
        }, fraction);
        assert.ok(sample.controls.length >= 3, `${name}: setup controls missing`);
        assert.ok(!sample.overflow, `${name}: horizontal overflow`);
        for (const control of sample.controls) {
          assert.ok(control.width > 0 && control.height > 0 && control.x >= -1 && control.y >= -1
            && control.x + control.width <= viewport.width + 1 && control.y + control.height <= viewport.height + 1,
          `${name} at ${fraction}: control left viewport ${JSON.stringify(control)}`);
          assert.ok(control.reachable && !control.disabled, `${name} at ${fraction}: control obscured/disabled ${JSON.stringify(control)}`);
        }
        samples.push(sample);
      }
      assert.ok(samples.at(-1).scrollY - samples[0].scrollY > viewport.height, `${name}: page did not actually scroll`);
      assert.ok(Math.max(...samples.slice(1).map(sample => sample.top)) - Math.min(...samples.slice(1).map(sample => sample.top)) <= 2,
        `${name}: toolbar does not stay pinned`);
      await page.screenshot({ path: path.join(output, `${name}.png`) });
      return { viewport, samples };
    });
  }
}

async function checkViewer(page, base, check) {
  await page.route('**/api/**', async route => route.fulfill({ json: response(route.request().url()) }));
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto(base + '/demo');
  await page.waitForFunction(() => document.querySelectorAll('.demo-source').length > 0
    && [...document.querySelectorAll('.demo-source')].every(source => source.getAttribute('aria-busy') === 'false'),
  undefined, { timeout: 180000 });
  assert.deepEqual(await page.locator('.demo-error').allTextContents(), []);
  const groups = {
    sales: ['sales/monthly_percent', 'apartments/distribution'],
    linear: ['roads', 'bridges', 'crossings', 'metro_stations', 'metro_tunnels', 'depots'].map(id => `linear/${id}`),
  };
  for (const [source, ids] of Object.entries(groups)) {
    const section = page.locator(`.demo-source[data-demo-source="${source}"]`);
    assert.equal(await section.locator('.demo-block-choice').count(), 1, `${source}: expected one atomic block`);
    await section.locator('.demo-source-selection input').check();
  }
  assert.equal(await page.locator('.demo-chart-choice.is-selected').count(), Object.keys(groups).length);
  await page.getByRole('checkbox', { name: '\u041f\u043e\u043b\u043d\u044b\u0439 \u044d\u043a\u0440\u0430\u043d \u0431\u0440\u0430\u0443\u0437\u0435\u0440\u0430', exact: true }).uncheck();
  await checkSticky(page, check);
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.getByRole('button', { name: '\u041d\u0430\u0447\u0430\u0442\u044c \u043f\u043e\u043a\u0430\u0437', exact: true }).click();
  await page.getByRole('dialog').waitFor();
  await page.getByRole('button', { name: '\u041f\u0440\u0438\u043e\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u044c \u043f\u043e\u043a\u0430\u0437', exact: true }).click();
  const slideCount = Number((await page.locator('.demo-slide-count').innerText()).split('/')[1]);
  await check('grouped-screen-count', async () => {
    assert.equal(slideCount, Object.keys(groups).length, 'Selected sales/apartments/linear charts must each share one source screen');
    return { slideCount };
  });
  for (const viewport of [{ width: 1920, height: 1080 }, { width: 3840, height: 2160 }, { width: 1440, height: 900 }]) {
    await page.setViewportSize(viewport);
    const seen = [];
    for (let index = 0; index < slideCount; index++) {
      const slideId = await page.locator('.demo-stage').getAttribute('data-demo-slide');
      await check(`group-${viewport.width}-${index}`, async name => {
        await page.waitForFunction(() => [...document.querySelectorAll('.demo-stage .demo-full-chart')]
          .every(chart => chart.querySelector('.chart-canvas canvas')));
        await page.waitForTimeout(200);
        const state = await page.evaluate(async () => {
          const stage = document.querySelector('.demo-stage'), controls = document.querySelector('.demo-viewer-controls');
          const hasLinear = !!stage.querySelector('[data-demo-chart^="linear/"]');
          const resource = hasLinear && performance.getEntriesByType('resource').find(entry => /echarts_core\.js/.test(entry.name));
          if (hasLinear && !resource) throw new Error('ECharts core module not found for linear inspection');
          const echarts = resource ? await import(resource.name) : null;
          return { viewport: { width: innerWidth, height: innerHeight }, controlsTop: controls.hidden ? innerHeight : controls.getBoundingClientRect().top,
            scrolling: stage.scrollHeight > stage.clientHeight + 1 || stage.scrollWidth > stage.clientWidth + 1,
            panels: [...stage.querySelectorAll('.demo-full-chart')].map(element => {
              const canvas = element.querySelector('.chart-canvas canvas'), r = canvas.getBoundingClientRect();
              const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data, colors = new Set();
              for (let i = 0; i < pixels.length; i += Math.max(4, Math.floor(pixels.length / 32000 / 4) * 4)) {
                if (pixels[i + 3]) colors.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]}`);
              }
              const chart = element.dataset.demoChart.startsWith('linear/') ? echarts.getInstanceByDom(element.querySelector('.chart-canvas')) : null;
              const option = chart?.getOption();
              const texts = chart?.getZr().storage.getDisplayList(true).filter(node => {
                if (node.type !== 'tspan' || !node.style.text) return false;
                for (let current = node; current; current = current.parent) {
                  if (current.ignore || current.invisible || current.style?.opacity === 0) return false;
                }
                return true;
              }).map(node => {
                const rect = node.getBoundingRect().clone(); rect.applyTransform(node.getComputedTransform());
                return { text: String(node.style.text), font: node.style.font, x: rect.x, y: rect.y, width: rect.width, height: rect.height };
              });
              return { id: element.dataset.demoChart, x: r.x, y: r.y, width: r.width, height: r.height, colors: colors.size,
                ...(chart ? { linear: { width: chart.getWidth(), height: chart.getHeight(), categories: option.xAxis[0].data,
                  data: option.series.map(series => series.data), texts } } : {}) };
            }) };
        });
        await page.screenshot({ path: path.join(output, `${name}.png`) });
        fs.writeFileSync(path.join(output, `${name}.json`), JSON.stringify(state, null, 2));
        const source = state.panels[0]?.id.split('/')[0];
        assert.ok(Object.hasOwn(groups, source), `${name}: unexpected source ${source}`);
        seen.push(source);
        assert.deepEqual(state.panels.map(panel => panel.id).sort(), [...groups[source]].sort(), `${name}: selected charts split across screens`);
        assert.ok(!state.scrolling, `${name}: grouped screen requires scrolling`);
        for (const [i, panel] of state.panels.entries()) {
          assert.ok(panel.colors > 5 && panel.width > 100 && panel.height >= 140, `${name}: blank/tiny canvas ${JSON.stringify(panel)}`);
          assert.ok(panel.x >= -1 && panel.y >= -1 && panel.x + panel.width <= viewport.width + 1
            && panel.y + panel.height <= Math.min(viewport.height, state.controlsTop) + 1, `${name}: canvas outside visible screen ${JSON.stringify(panel)}`);
          for (const other of state.panels.slice(i + 1)) assert.ok(!overlaps(panel, other), `${name}: overlapping chart canvases`);
          if (panel.linear) {
            const metric = response('/api/v1/linear/dashboard').metrics.find(metric => panel.id === `linear/${metric.id}`);
            const slots = [...metric.years.filter(row => row.year <= 2026), ...metric.years.filter(row => row.year >= 2026)];
            assert.deepEqual(panel.linear.categories.map(category => Number.parseInt(category, 10)), slots.map(row => row.year),
              `${name}: ${panel.id} must include 2011-2030, with separate 2026 fact and forecast slots`);
            assert.deepEqual(panel.linear.data, [slots.map((row, i) => i < 16 ? row.fact : null),
              slots.map((row, i) => i >= 16 ? row.forecast : null)], `${name}: ${panel.id} history measurements changed`);
            assert.ok(panel.linear.texts.some(text => /600/.test(text.font) && /^\d/.test(text.text)),
              `${name}: ${panel.id} has no rendered value labels`);
            for (const text of panel.linear.texts) assert.ok(text.x >= -1 && text.y >= -1
              && text.x + text.width <= panel.linear.width + 1 && text.y + text.height <= panel.linear.height + 1,
            `${name}: ${panel.id} clipped text ${JSON.stringify(text)}`);
          }
        }
        return { slideId, ...state };
      });
      await page.keyboard.press('ArrowRight');
    }
    await check(`group-coverage-${viewport.width}`, async () => {
      assert.deepEqual(seen.sort(), Object.keys(groups).sort(), 'Every selected source must occur exactly once per cycle');
      return { seen };
    });
  }
  await page.keyboard.press('Escape');
}

async function main() {
  let browser, child;
  const checks = [], errors = [], failures = [];
  fs.mkdirSync(output, { recursive: true });
  const check = async (name, run) => {
    try {
      checks.push({ name, passed: true, ...await run(name) });
      console.log(`PASS ${name}`);
    } catch (error) {
      failures.push({ name, error: error.stack || String(error) });
      console.error(`FAIL ${name}: ${error.message}`);
    }
  };
  try {
    const port = await freePort(), base = `http://127.0.0.1:${port}`, cwd = path.resolve(__dirname, '..');
    child = spawn(process.execPath, [path.join(cwd, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
      { cwd, windowsHide: true, stdio: 'ignore', env: { ...process.env, VITE_DATA_MODE: 'api' } });
    let ready = false;
    for (let i = 0; i < 120; i++) {
      try { if ((await fetch(base)).ok) { ready = true; break; } } catch {}
      if (child.exitCode !== null) break;
      await pause(250);
    }
    assert.ok(ready, 'Private Vite did not start');
    browser = await chromium.launch({ channel: process.env.DASHBOARD_BROWSER_CHANNEL || 'chrome', headless: true });
    const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, reducedMotion: 'reduce', deviceScaleFactor: 1 });
    context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    const labels = await context.newPage();
    await check('label-harness', async () => checkLabels(labels, base, check));
    await labels.close();
    const viewer = await context.newPage();
    await check('viewer-harness', async () => checkViewer(viewer, base, check));
    await check('browser-errors', async () => assert.deepEqual(errors, []));
    console.log(JSON.stringify({ passed: checks.length, failed: failures.length, output, errors }));
    assert.equal(failures.length, 0, `${failures.length} adaptive regression check(s) failed; see ${path.join(output, 'checks.json')}`);
  } finally {
    fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ checks, failures, errors }, null, 2));
    await browser?.close();
    if (child && child.exitCode === null) {
      child.kill();
      await new Promise(resolve => child.once('exit', resolve));
    }
  }
}

if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { main };
