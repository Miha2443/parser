const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');
const fixtures = require('./demoFixtures.cjs');

const output = path.join(os.tmpdir(), 'dashboard-demo-sticky');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function startServer() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  const child = spawn(process.execPath, [path.resolve(__dirname, '../node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    cwd: path.resolve(__dirname, '..'), windowsHide: true, stdio: 'ignore',
  });
  const base = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 120; i++) {
    try { if ((await fetch(base)).ok) return { child, base }; } catch {}
    if (child.exitCode !== null) break;
    await pause(250);
  }
  if (child.exitCode === null) {
    child.kill();
    await new Promise(resolve => child.once('exit', resolve));
  }
  throw new Error('Private Vite did not start');
}

async function assertToolbar(page, expectedTop = 0) {
  const state = await page.locator('.demo-settings').evaluate(toolbar => {
    const rect = toolbar.getBoundingClientRect();
    const controls = [...toolbar.querySelectorAll('input, button')].map(node => {
      const box = node.getBoundingClientRect();
      return { left: box.left, right: box.right, top: box.top, bottom: box.bottom,
        reachable: node.contains(document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2)) };
    });
    return { top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right,
      position: getComputedStyle(toolbar).position, background: getComputedStyle(toolbar).backgroundColor,
      width: innerWidth, height: innerHeight, documentWidth: document.documentElement.scrollWidth, controls };
  });
  assert.equal(state.position, 'sticky');
  assert.ok(Math.abs(state.top - expectedTop) <= 1, JSON.stringify(state));
  assert.ok(state.bottom < state.height && state.documentWidth <= state.width + 1, JSON.stringify(state));
  assert.ok(!state.background.startsWith('rgba(') && state.background !== 'transparent', 'Toolbar must be opaque');
  assert.equal(state.controls.length, 3);
  for (const control of state.controls) {
    assert.ok(control.left >= state.left && control.right <= state.right + 1 && control.top >= state.top && control.bottom <= state.bottom, JSON.stringify(control));
    assert.ok(control.reachable, 'Toolbar controls must not be covered');
  }
  for (let i = 0; i < state.controls.length; i++) for (let j = i + 1; j < state.controls.length; j++) {
    const a = state.controls[i], b = state.controls[j];
    assert.ok(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top, 'Toolbar controls overlap');
  }
}

async function assertGrid(page, columns, panelCount = 6) {
  await page.locator('.demo-stage').evaluate(stage => { stage.scrollTop = 0; });
  const state = await page.locator('.demo-stage').evaluate(stage => {
    const panels = [...stage.children].map(node => {
      const rect = node.getBoundingClientRect(), chart = node.querySelector('.chart-canvas').getBoundingClientRect();
      return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom,
        chartTop: chart.top, chartBottom: chart.bottom, chartHeight: chart.height };
    });
    return { columns: getComputedStyle(stage).gridTemplateColumns.split(' ').length,
      width: stage.clientWidth, scrollWidth: stage.scrollWidth, height: stage.clientHeight, scrollHeight: stage.scrollHeight, panels };
  });
  assert.equal(state.columns, columns);
  assert.equal(state.panels.length, panelCount);
  assert.ok(state.scrollWidth <= state.width + 1, JSON.stringify(state));
  const viewport = page.viewportSize();
  if (viewport.width >= 1280 && viewport.height >= 800) assert.ok(state.scrollHeight <= state.height + 1, `Desktop grid must fit without scrolling: ${JSON.stringify(state)}`);
  for (const panel of state.panels) {
    assert.ok(panel.left >= 0 && panel.right <= (await page.viewportSize()).width + 1, JSON.stringify(panel));
    assert.ok(panel.chartHeight >= 140 && panel.chartTop >= panel.top && panel.chartBottom <= panel.bottom + 1, JSON.stringify(panel));
  }
  for (let i = 0; i < state.panels.length; i++) for (let j = i + 1; j < state.panels.length; j++) {
    const a = state.panels[i], b = state.panels[j];
    assert.ok(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top, 'Grid panels overlap');
  }
  await page.locator('.demo-stage').evaluate(stage => { stage.scrollTop = stage.scrollHeight; });
  assert.ok(await page.locator('.demo-panel').last().evaluate(panel => {
    const chart = panel.querySelector('.chart-canvas').getBoundingClientRect();
    const controls = document.querySelector('.demo-viewer-controls').getBoundingClientRect();
    return chart.bottom <= controls.top + 1 && chart.bottom > 0;
  }), 'Last chart must scroll above playback controls');
}

async function main() {
  let child, browser;
  const checks = [], errors = [];
  fs.mkdirSync(output, { recursive: true });
  try {
    let base = process.env.DEMO_TEST_URL?.replace(/\/$/, '');
    if (!base) ({ child, base } = await startServer());
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
    await context.route('**/api/**', async route => {
      try {
        const url = route.request().url();
        const json = new URL(url).pathname === '/api/v1/auth/session'
          ? { enabled: true, user: { username: 'sticky-layout-analyst-with-long-name', role: 'analyst' }, csrfToken: 'test' }
          : fixtures.response(url);
        await route.fulfill({ json });
      } catch (error) { errors.push(error.message); await route.fulfill({ status: 500, json: { detail: error.message } }); }
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/demo');
    await page.waitForFunction(() => document.querySelectorAll('.demo-source[aria-busy="false"]').length === 13);
    assert.deepEqual(await page.locator('.demo-error').allTextContents(), []);
    await page.locator('[data-demo-source="linear"] .demo-source-selection input').check();
    for (const viewport of [{ width: 1440, height: 900 }, { width: 1024, height: 768 }, { width: 820, height: 1180 }, { width: 390, height: 844 }, { width: 320, height: 568 }, { width: 844, height: 390 }]) {
      await page.setViewportSize(viewport);
      for (const top of [900, 2400]) {
        await page.evaluate(top => window.scrollTo(0, top), top);
        await assertToolbar(page);
      }
      await page.locator('.demo-interval input').focus();
      await page.keyboard.press('End');
      assert.equal(await page.locator('.demo-interval input').inputValue(), '1800');
      await page.locator('.demo-fullscreen-choice input').uncheck();
      await assertToolbar(page);
      await page.screenshot({ path: path.join(output, `toolbar-${viewport.width}.png`) });
      checks.push({ toolbar: viewport });
    }
    // Exercise the opt-in offset with a wrapped sticky shell header, without changing shell sources.
    await page.setViewportSize({ width: 320, height: 568 });
    await page.evaluate(() => {
      const header = document.querySelector('.topbar');
      header.style.cssText = 'position: sticky; top: 0; z-index: 24';
      document.documentElement.style.setProperty('--app-sticky-header-height', `${header.getBoundingClientRect().height}px`);
    });
    await page.evaluate(() => window.scrollTo(0, 900));
    await assertToolbar(page, await page.locator('.topbar').evaluate(header => header.getBoundingClientRect().bottom));
    await page.evaluate(() => {
      document.querySelector('.topbar').removeAttribute('style');
      document.documentElement.style.removeProperty('--app-sticky-header-height');
    });
    await page.locator('.demo-start').click();
    await page.locator('.demo-viewer').waitFor();
    await page.locator('.demo-pin-button').click();
    await page.locator('.demo-stage canvas').first().waitFor();
    assert.equal(await page.locator('.demo-layout-grid').getAttribute('data-demo-panels'), '6');
    for (const [width, height, columns] of [[1920, 1080, 3], [1440, 900, 3], [1280, 800, 3], [1280, 900, 3], [1920, 800, 3], [1280, 720, 3], [1080, 1920, 2], [1000, 560, 2], [390, 844, 1], [320, 568, 1], [844, 390, 2]]) {
      await page.setViewportSize({ width, height });
      await page.waitForFunction(() => [...document.querySelectorAll('.demo-stage canvas')].length === 6 && [...document.querySelectorAll('.demo-stage canvas')].every(canvas => {
        const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        for (let i = 0; i < pixels.length; i += 16) if (pixels[i + 3]) return true;
        return false;
      }));
      await assertGrid(page, columns);
      await page.locator('.demo-stage').evaluate(stage => { stage.scrollTop = 0; });
      await page.screenshot({ path: path.join(output, `grid-${width}-${height}.png`) });
      checks.push({ grid: { width, height, columns } });
    }
    await page.keyboard.press('Escape');
    await page.locator('.demo-page').waitFor();
    await page.setViewportSize({ width: 1440, height: 900 });
    const group = page.locator('[data-demo-source="linear"] .demo-source-selection input');
    const choices = page.locator('[data-demo-source="linear"] .demo-chart-choice input:enabled');
    for (const count of [1, 2, 3, 4, 5]) {
      await group.uncheck();
      for (let i = 0; i < count; i++) await choices.nth(i).check();
      await page.locator('.demo-start').click();
      await page.locator('.demo-stage canvas').first().waitFor();
      await page.locator('.demo-pin-button').click();
      assert.equal(await page.locator('.demo-stage').getAttribute('data-demo-panels'), String(count));
      await assertGrid(page, count === 1 ? 1 : count < 5 ? 2 : 3, count);
      await page.keyboard.press('Escape');
      await page.locator('.demo-page').waitFor();
      checks.push({ panelCount: count });
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: checks.length, checks, output, errors }));
  } finally {
    await browser?.close();
    if (child && child.exitCode === null) { child.kill(); await new Promise(resolve => child.once('exit', resolve)); }
  }
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { main };
