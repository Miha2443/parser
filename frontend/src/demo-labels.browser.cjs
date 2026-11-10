const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require(process.env.DEMO_LABELS_PLAYWRIGHT || 'playwright');

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function freePort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function main() {
  let child, browser;
  const output = process.env.DEMO_LABELS_OUTPUT || path.join(os.tmpdir(), 'dashboard-demo-labels');
  const errors = [], checks = [];
  fs.mkdirSync(output, { recursive: true });
  try {
    const port = await freePort(), base = `http://127.0.0.1:${port}`, cwd = path.resolve(__dirname, '..');
    child = spawn(process.execPath, [path.join(cwd, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'], { cwd, windowsHide: true, stdio: 'ignore' });
    let ready = false;
    for (let i = 0; i < 120; i++) {
      try { if ((await fetch(base)).ok) { ready = true; break; } } catch {}
      await pause(250);
    }
    assert.ok(ready, 'Private Vite did not start');
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/demo-labels-harness', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;background:white"><div id="chart"></div></body></html>' }));
    await page.goto(base + '/demo-labels-harness');
    await page.evaluate(async () => {
      const echarts = await import('/node_modules/echarts/dist/echarts.esm.js');
      const { demoPresentationOption } = await import('/src/demoPresentation.ts');
      const mortgage = await import('/src/mortgageData.ts');
      const { flowChartOption } = await import('/src/preferentialMortgageData.ts');
      const { economicsChartOption } = await import('/src/economicsData.ts');
      const categories = n => Array.from({ length: n }, (_, i) => String(2000 + i));
      const basic = series => ({ animation: false, grid: { left: 48, right: 24, top: 40, bottom: 48 }, xAxis: { type: 'category', data: categories(series[0].data.length) }, yAxis: { type: 'value', axisLabel: { color: '#374151' } }, series });
      const months = Array.from({ length: 132 }, (_, i) => {
        const cell = factor => ({ volumeRub: (20 + 12 * Math.sin(i / 8) + i / 4) * factor * 1e9, count: (10000 + i * 100) * factor, ratePct: 8 + factor + Math.sin(i / 5), termMonths: null, averageAmountRub: null });
        return { period: `${2015 + Math.floor(i / 12)}-${String(i % 12 + 1).padStart(2, '0')}`, all: cell(1), primary: cell(.4), secondary: cell(.6) };
      });
      months[25].primary.volumeRub = null;
      const annual = Array.from({ length: 18 }, (_, i) => {
        const value = { annual: i === 17 ? null : (100 + i * 20) * 1e9, selected: (50 + i * 10) * 1e9, actualThroughMonth: 5, annualYoYPct: i === 0 ? null : i % 3 === 0 ? -20 : 25, selectedYoYPct: 10 };
        return { year: 2009 + i, volume: value, count: value };
      });
      const cpi = Array.from({ length: 90 }, (_, i) => 100 + Math.sin(i / 8) * 6 + Math.sin(i * 1.7) * .08);
      cpi[20] = 120.24; cpi[60] = 85.24; cpi[40] = null;
      const flowRows = [[0, .9, 0], [0, 5.5, 0], [41.8, 9.1, 0], [36.9, 15.6, 0], [34.2, 22.3, 1.3], [41.8, 43.1, 11.2], [11.1, 43.4, 9], [0, 56.8, 0], [0, 25.4, 0], [0, 0, 0]].map((parts, i) => ({
        year: 2018 + i, throughMonth: 12,
        programs: Object.fromEntries(['preferential', 'family', 'it'].map((id, j) => [id, { count: parts[j] * 1000, volumeMln: parts[j] * 9800 }])),
        total: { count: parts.reduce((sum, v) => sum + v, 0) * 1000, volumeMln: parts.reduce((sum, v) => sum + v, 0) * 9800 },
      }));
      window.cases = {
        monthly: () => mortgage.mortgageMonthlyOption(months, 'volume', 'light'),
        annual: (w, h) => mortgage.mortgageAnnualOption(annual, 'volume', 'light', w, h),
        preferential: w => flowChartOption(flowRows, 'count', 'light', w),
        'preferential-volume': w => flowChartOption(flowRows, 'volumeMln', 'light', w),
        grouped: () => basic([{ type: 'bar', name: 'a', data: [10.24, 20.24, 30.24, 40.24] }, { type: 'bar', name: 'b', data: [12.24, 22.24, 32.24, 42.24] }, { type: 'bar', name: 'c', data: [15.24, 25.24, 35.24, 45.24] }]),
        profile: () => ({ ...basic(Array.from({ length: 4 }, (_, j) => ({ type: 'bar', name: `area-${j}`, stack: 'area', data: Array.from({ length: 18 }, (_, i) => (i + 1) * (j + 1) * 1.24) }))), grid: { left: 8, right: 12, top: 32, bottom: 8, containLabel: true } }),
        signed: () => basic([{ type: 'bar', stack: 's', data: [10.24, -20.24, 30.24, -40.24] }, { type: 'bar', stack: 's', data: [5.24, -5.24, -10.24, 10.24] }]),
        cpi: w => economicsChartOption({ id: 'ipc-demo', kind: 'line', unit: '%', rows: [], series: [
          { id: 'msk', name: 'Moscow', unit: '%', points: cpi.map((y, i) => ({ x: String(i), y })) },
          { id: 'rf', name: 'Russia', unit: '%', points: cpi.map((y, i) => ({ x: String(i), y: y === null ? null : y + 1.24 })) },
        ] }, 'light', { width: w }),
      };
      window.render = (id, width, height, reuse = false) => {
        if (!reuse) window.labelChart?.dispose();
        const element = document.querySelector('#chart');
        element.style.width = `${width}px`; element.style.height = `${height}px`;
        const option = { ...window.cases[id](width, height), animation: true, animationDuration: 0, animationDurationUpdate: 300 }, before = JSON.stringify(option);
        const presentation = demoPresentationOption(option, width, height);
        if (JSON.stringify(option) !== before) throw new Error('Input option mutated');
        if (presentation.animation !== false) throw new Error('Demo animation is not disabled');
        if (reuse) window.labelChart.resize();
        else window.labelChart = echarts.init(element, null, { renderer: 'canvas', devicePixelRatio: 1 });
        window.labelChart.setOption(presentation, true);
        const display = window.labelChart.getZr().storage.getDisplayList();
        const rect = node => { const r = node.getBoundingRect().clone(); r.applyTransform(node.getComputedTransform()); return { x: r.x, y: r.y, width: r.width, height: r.height }; };
        const labels = display.filter(node => node.type === 'tspan' && !node.ignore && /600/.test(node.style.font) && /^[\d\s.,+\u2212-]+%?$/.test(String(node.style.text))).map(node => ({ text: node.style.text, font: node.style.font, ...rect(node) }));
        const bars = [], segments = [], configured = [];
        window.labelChart.getModel().eachSeries(series => {
          if (series.subType !== 'bar') return;
          const data = series.getData();
          configured.push({ show: series.get(['label', 'show']), texts: Array.from({ length: data.count() }, (_, dataIndex) => series.getFormattedLabel(dataIndex)).filter(Boolean), sample: Array.from({ length: data.count() }, (_, i) => { const label = data.getItemGraphicEl(i)?.getTextContent(); return label?.style.text ? { i, ignore: label.ignore, text: label.style.text, rect: rect(label), x: label.x, y: label.y } : null; }).filter(Boolean).slice(0, 4) });
          for (let i = 0; i < data.count(); i++) {
            const el = data.getItemGraphicEl(i), label = el?.getTextContent();
            const bar = data.getItemLayout(i), group = { index: i, stack: series.get('stack'), xAxisIndex: series.get('xAxisIndex'), yAxisIndex: series.get('yAxisIndex') };
            if (bar && Number.isFinite(bar.y) && Number.isFinite(bar.height)) segments.push({ ...group, bar });
            if (label && !label.ignore && label.style.text) bars.push({ ...group, bar, label: { text: label.style.text, ...rect(label) } });
          }
        });
        const canvas = element.querySelector('canvas'), pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data, colors = new Set();
        for (let i = 0; i < pixels.length; i += Math.max(4, Math.floor(pixels.length / 32000 / 4) * 4)) if (pixels[i + 3]) colors.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]}`);
        const points = presentation.series.flatMap((series, seriesIndex) => (series.markPoint?.data ?? []).filter(p => p.name?.startsWith('demo-extremum-')).map(point => ({ seriesIndex, ...point, label: { ...point.label, formatter: point.label.formatter() } })));
        return { labels, bars, segments, points, configured, texts: display.filter(node => node.type === 'tspan').map(node => ({ text: node.style.text, font: node.style.font })), colors: colors.size, measurements: presentation.series.map(s => s.data), sourceMeasurements: option.series.map(s => s.data), width: element.clientWidth, height: element.clientHeight };
      };
    });
    for (const [width, height] of [[600, 500], [1200, 300], [1824, 280], [1920, 900], [3750, 1740], [390, 350]]) {
      await page.setViewportSize({ width: Math.max(1440, width), height: Math.max(900, height) });
      for (const id of ['monthly', 'annual', 'preferential', 'preferential-volume', 'grouped', 'signed', 'cpi']) {
        const state = await page.evaluate(({ id, width, height }) => window.render(id, width, height), { id, width, height });
        const tag = `${id} ${width}x${height}`;
        await page.screenshot({ path: path.join(output, `${id}-${width}-${height}.png`), clip: { x: 0, y: 0, width, height } });
        assert.ok(state.colors > 5, `${tag}: blank canvas`);
        assert.deepEqual(state.measurements, state.sourceMeasurements, `${tag}: data changed`);
        assert.ok(state.labels.length >= 2, `${tag}: no readable labels ${JSON.stringify({ configured: state.configured, bars: state.bars, texts: state.texts })}`);
        assert.ok(state.labels.every(r => r.x >= -1 && r.y >= -1 && r.x + r.width <= width + 1 && r.y + r.height <= height + 1), `${tag}: clipped labels ${JSON.stringify(state.labels)}`);
        for (let i = 0; i < state.labels.length; i++) for (const b of state.labels.slice(i + 1)) {
          const a = state.labels[i], overlapX = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x), overlapY = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
          assert.ok(overlapX <= .5 || overlapY <= .5, `${tag}: overlapping labels ${JSON.stringify([a, b])}`);
        }
        for (const { bar, label } of state.bars) {
          const top = Math.min(bar.y, bar.y + bar.height), bottom = Math.max(bar.y, bar.y + bar.height);
          assert.ok(label.y + label.height <= top + 2 || label.y >= bottom - 2, `${tag}: label inside bar ${JSON.stringify({ bar, label })}`);
        }
        if (id.startsWith('preferential')) {
          assert.equal(state.labels.length, state.bars.length, `${tag}: duplicate floating totals`);
          if (width >= 600) {
            assert.equal(state.bars.length, 10, `${tag}: a yearly total is missing or duplicated`);
            assert.equal(state.bars.filter(b => b.label.text === '0').length, 1, `${tag}: spurious baseline zeros`);
          }
          for (const total of state.bars) {
            const parts = state.segments.filter(s => s.index === total.index && s.stack === total.stack && s.xAxisIndex === total.xAxisIndex && s.yAxisIndex === total.yAxisIndex);
            const top = Math.min(...parts.map(s => Math.min(s.bar.y, s.bar.y + s.bar.height)));
            assert.ok(total.label.y + total.label.height <= top + 1, `${tag}: total is not above the actual stack ${JSON.stringify({ total, top })}`);
            const value = state.measurements.reduce((sum, data) => sum + (data[total.index] ?? 0), 0);
            assert.equal(total.label.text, new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(value), `${tag}: incorrect sum`);
          }
        }
        if (id === 'monthly') assert.ok(state.labels.length < 60, `${tag}: dense monthly labels`);
        if (id === 'annual') assert.ok(state.labels.some(p => String(p.text).includes('%')), `${tag}: growth units missing`);
        if (width >= 1000 && height === 280) assert.ok(state.labels.some(p => Number(p.font.match(/([\d.]+)px/)[1]) >= 12), `${tag}: small grouped labels`);
        if (id === 'cpi') {
          assert.ok(state.points.some(p => p.value === 120.24 || p.value === 121.47999999999999), `${tag}: CPI peak missing`);
          assert.ok(state.points.some(p => p.value === 85.24 || p.value === 86.47999999999999), `${tag}: CPI trough missing`);
          for (const p of state.points) assert.equal(p.value, state.measurements[p.seriesIndex][p.coord[0]], `${tag}: fabricated extremum`);
        }
        checks.push({ id, width, height, labels: state.labels.length, extrema: state.points.length });
      }
    }
    for (const id of ['profile', 'annual', 'preferential']) {
      await page.setViewportSize({ width: 1920, height: 900 });
      await page.evaluate(id => window.render(id, 1920, 800), id);
      for (const [width, height] of [[1920, 800], [3840, 1740], [350, 300]]) {
        await page.setViewportSize({ width, height: Math.max(900, height) });
        await page.evaluate(({ id, width, height }) => window.render(id, width, height, true), { id, width, height });
        await page.waitForTimeout(180);
        const state = await page.evaluate(() => ({ animation: window.labelChart.getOption().animation,
          labels: window.labelChart.getZr().storage.getDisplayList().filter(node => node.type === 'tspan' && !node.ignore && /600/.test(node.style.font) && /^[\d\s.,+\u2212-]+%?$/.test(String(node.style.text))).map(node => {
            const r = node.getBoundingRect().clone(); r.applyTransform(node.getComputedTransform());
            return { text: node.style.text, x: r.x, y: r.y, width: r.width, height: r.height };
          }) }));
        const tag = `${id} resize ${width}x${height} after 180ms`;
        assert.equal(state.animation, false, tag);
        assert.ok(state.labels.length >= 2, `${tag}: labels missing`);
        assert.ok(state.labels.every(r => r.x >= -1 && r.y >= -1 && r.x + r.width <= width + 1 && r.y + r.height <= height + 1), `${tag}: stale/clipped geometry ${JSON.stringify(state.labels)}`);
        await page.screenshot({ path: path.join(output, `${id}-resize-${width}-${height}.png`), clip: { x: 0, y: 0, width, height } });
        checks.push({ id, width, height, reuse: true, afterMs: 180, labels: state.labels.length });
      }
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ checks, errors }, null, 2));
    console.log(JSON.stringify({ passed: checks.length, output, errors }));
  } finally {
    await browser?.close();
    if (child && child.exitCode === null) {
      child.kill();
      await new Promise(resolve => child.once('exit', resolve));
    }
  }
}

if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { main };
