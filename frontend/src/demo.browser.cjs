const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');
const fixtures = require('./demoFixtures.cjs');
const base = (process.env.DEMO_TEST_URL || 'http://127.0.0.1:5173').replace(/\/$/, '');
const output = path.join(os.tmpdir(), 'dashboard-demo-checks');
fs.mkdirSync(output, { recursive: true });

async function painted(page) {
  const canvas = page.locator('.demo-stage .demo-full-chart > .chart-block canvas');
  await canvas.waitFor();
  await page.waitForFunction(() => {
    const canvas = document.querySelector('.demo-stage .demo-full-chart > .chart-block canvas');
    if (!canvas || !canvas.width || !canvas.height) return false;
    const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    let colored = 0;
    for (let i = 0; i < pixels.length; i += 16) if (pixels[i + 3] && Math.max(pixels[i], pixels[i + 1], pixels[i + 2]) - Math.min(pixels[i], pixels[i + 1], pixels[i + 2]) > 25) colored++;
    return colored > 50;
  });
  return canvas.evaluate(node => ({ width: node.width, cssWidth: node.getBoundingClientRect().width, height: node.height, cssHeight: node.getBoundingClientRect().height }));
}
async function noOverflow(page) { assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1 && (!document.querySelector('.demo-viewer') || document.querySelector('.demo-viewer').scrollWidth <= innerWidth + 1))); }
async function main() {
  const browser = await chromium.launch({ channel: 'chrome', headless: true }), checks = [], errors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce', acceptDownloads: true, deviceScaleFactor: 2 });
    const page = await context.newPage();
    let failIpc = false, emptyLinear = false, emptySalary = false, apiCalls = [], releasePermits;
    const permitsGate = new Promise(resolve => { releasePermits = resolve; });
    page.on('pageerror', error => errors.push(error.message));
    await context.addInitScript(() => {
      localStorage.setItem('dashboard.navigation', JSON.stringify({ market: true, construction: true, commissioning: true, profile: true, statistics: true, cbr: true, service: true }));
      const request = Element.prototype.requestFullscreen;
      window.demoFullscreenMode = 'native';
      Element.prototype.requestFullscreen = function () { return window.demoFullscreenMode === 'reject' ? Promise.reject(new DOMException('Denied for QA', 'NotAllowedError')) : request.call(this); };
    });
    await context.route('**/api/**', async route => {
      const url = route.request().url(); apiCalls.push(url);
      if (url.includes('/construction/permits')) await permitsGate;
      if (failIpc && url.includes('/economics/ipc/')) return route.fulfill({ status: 503, json: { detail: { code: 'source_unavailable', reason: 'Проверка недоступного источника' } } });
      try {
        const value = structuredClone(fixtures.response(url));
        if (emptyLinear && url.includes('/linear/dashboard')) value.metrics.forEach(metric => metric.years.forEach(row => Object.assign(row, { fact: null, factKind: null, factPeriod: null, factSource: null })));
        if (emptySalary && url.includes('/economics/salary/report')) { value.charts = []; value.tables = []; }
        return route.fulfill({ json: value });
      } catch (error) { errors.push(error.message); return route.fulfill({ status: 500, json: { detail: error.message } }); }
    });
    await page.clock.install();
    await page.goto(`${base}/demo`);
    await page.locator('[data-demo-source="permits"] [role="status"]').waitFor();
    assert.equal(await page.locator('[data-demo-source="permits"]').getAttribute('aria-busy'), 'true');
    releasePermits();
    await page.waitForFunction(() => document.querySelectorAll('.demo-source[aria-busy="false"]').length === 13, { timeout: 30000 });
    assert.equal(await page.locator('.demo-error').count(), 0);
    assert.equal(await page.getByRole('slider', { name: 'Интервал показа' }).inputValue(), '120');
    assert.equal(await page.locator('.demo-page iframe').count(), 0);
    assert.ok(await page.locator('.demo-page canvas').count() < await page.locator('.demo-chart-choice').count());
    await noOverflow(page);
    await page.screenshot({ path: path.join(output, 'configuration-desktop.png') });
    checks.push('13 API sources, real lazy live previews, default interval, no iframes');

    // Home is outside the only scrollable navigation region, including compact/mobile.
    const pinned = page.locator('.navigation-home'), market = page.getByRole('button', { name: 'Рынок недвижимости', exact: true });
    const pinTop = (await pinned.boundingBox()).y;
    assert.ok(await pinned.locator('a').evaluate((node, market) => parseFloat(getComputedStyle(node).fontSize) >= parseFloat(getComputedStyle(market).fontSize), await market.elementHandle()));
    await page.locator('.navigation-sections').evaluate(node => { node.scrollTop = node.scrollHeight; });
    assert.equal((await pinned.boundingBox()).y, pinTop);
    await page.getByRole('button', { name: 'Свернуть боковую панель' }).click();
    assert.ok(await pinned.getByRole('link', { name: 'Главная' }).isVisible());
    await page.getByRole('button', { name: 'Развернуть боковую панель' }).click();
    const newTab = context.waitForEvent('page');
    await page.locator('.navigation-brand > a').click({ modifiers: ['Control'] });
    const homeTab = await newTab; await homeTab.waitForLoadState(); assert.ok(homeTab.url().endsWith('/home')); await homeTab.close(); assert.ok(page.url().endsWith('/demo'));
    checks.push('pinned accessible Home, compact mode and modified brand click');

    const all = page.getByRole('checkbox', { name: 'Все графики', exact: true });
    await all.uncheck(); assert.equal(await page.getByRole('button', { name: 'Начать показ', exact: true }).isEnabled(), false);
    const choices = page.locator('.demo-chart-choice input:not(:disabled)');
    await choices.nth(0).check(); await choices.nth(1).check();
    assert.match(await page.locator('.demo-selection-count').innerText(), /2 \/ /);
    const slider = page.getByRole('slider', { name: 'Интервал показа' });
    await slider.focus(); await slider.press('End'); assert.equal(await slider.inputValue(), '1800');
    await slider.press('Home'); assert.equal(await slider.inputValue(), '15');
    await page.getByRole('checkbox', { name: 'Полный экран браузера', exact: true }).uncheck();
    await page.getByRole('button', { name: 'Начать показ', exact: true }).click();
    await page.getByRole('dialog').waitFor(); await painted(page);
    assert.equal(await page.locator('.demo-page').count(), 0); assert.equal(await page.locator('.demo-stage canvas').count(), 1);
    assert.equal(await page.evaluate(() => document.getElementById('root').inert), true);
    assert.equal(await page.getByRole('button', { name: 'Завершить демонстрацию', exact: true }).evaluate(node => node === document.activeElement), true);
    await page.keyboard.press('Shift+Tab'); assert.equal(await page.getByRole('button', { name: 'Полный экран браузера', exact: true }).evaluate(node => node === document.activeElement), true);
    await page.keyboard.press('Tab'); assert.equal(await page.getByRole('button', { name: 'Завершить демонстрацию', exact: true }).evaluate(node => node === document.activeElement), true);
    const firstTitle = await page.locator('#demo-slide-title').innerText();
    await page.clock.runFor(16000); const secondTitle = await page.locator('#demo-slide-title').innerText(); assert.notEqual(secondTitle, firstTitle);
    await page.clock.runFor(15000); assert.equal(await page.locator('#demo-slide-title').innerText(), firstTitle);
    await page.clock.runFor(7000);
    await page.getByRole('button', { name: 'Приостановить показ', exact: true }).click();
    await page.clock.runFor(30000); assert.equal(await page.locator('#demo-slide-title').innerText(), firstTitle);
    await page.getByRole('button', { name: 'Продолжить показ', exact: true }).click();
    await page.clock.runFor(8000); assert.equal(await page.locator('#demo-slide-title').innerText(), secondTitle);
    await page.keyboard.press('ArrowLeft'); assert.equal(await page.locator('#demo-slide-title').innerText(), firstTitle);
    await page.keyboard.press('ArrowRight'); assert.equal(await page.locator('#demo-slide-title').innerText(), secondTitle);
    await painted(page); await page.screenshot({ path: path.join(output, 'viewer-desktop-dpr2.png') });
    await page.keyboard.press('Escape'); await page.locator('.demo-page').waitFor();
    assert.equal(await page.evaluate(() => document.getElementById('root').inert), false);
    assert.equal(await page.getByRole('button', { name: 'Начать показ', exact: true }).evaluate(node => node === document.activeElement), true);
    assert.equal(await slider.inputValue(), '15'); assert.match(await page.locator('.demo-selection-count').innerText(), /2 \/ /);
    checks.push('subset, empty selection, range endpoints, timed cycle, pause/resume, arrows, focus trap, Escape restoration');

    await page.evaluate(() => { window.demoFullscreenMode = 'reject'; });
    await page.getByRole('checkbox', { name: 'Полный экран браузера', exact: true }).check();
    await page.getByRole('button', { name: 'Начать показ', exact: true }).click();
    await page.getByRole('status').filter({ hasText: 'Полный экран браузера недоступен' }).waitFor();
    assert.equal(await page.getByRole('dialog').isVisible(), true); await painted(page); await noOverflow(page);
    await page.getByRole('button', { name: 'Завершить демонстрацию', exact: true }).click();
    await page.evaluate(() => { window.demoFullscreenMode = 'native'; });
    await page.getByRole('button', { name: 'Начать показ', exact: true }).click();
    await page.waitForFunction(() => document.fullscreenElement?.classList.contains('demo-viewer'));
    await painted(page); await page.screenshot({ path: path.join(output, 'viewer-browser-fullscreen.png') });
    await page.evaluate(() => document.exitFullscreen()); await page.locator('.demo-page').waitFor();
    checks.push('fullscreen denial fallback and native fullscreen enter/exit');

    // A single chart loops correctly, and exports are the original data, not raster slides.
    await all.check(); await all.uncheck(); await choices.first().check();
    await page.getByRole('checkbox', { name: 'Полный экран браузера', exact: true }).uncheck();
    await page.getByRole('button', { name: 'Начать показ', exact: true }).click();
    await page.clock.runFor(31000); assert.equal(await page.locator('.demo-slide-count').innerText(), '1 / 1');
    await page.getByRole('button', { name: 'Данные и скачивание', exact: true }).click();
    const download = page.waitForEvent('download'); await page.getByRole('button', { name: 'CSV', exact: true }).click(); assert.ok((await download).suggestedFilename().endsWith('.csv'));
    await page.keyboard.press('Escape'); checks.push('one-chart loop and original CSV download');

    await all.check();
    await page.getByRole('button', { name: 'Начать показ', exact: true }).click();
    const count = Number((await page.locator('.demo-slide-count').innerText()).split('/')[1]), seen = new Set();
    for (let index = 0; index < count; index++) {
      const chart = page.locator('.demo-full-chart'), id = await chart.getAttribute('data-demo-chart');
      seen.add(id);
      await chart.locator(':scope > .chart-block canvas').waitFor();
      assert.ok(await chart.locator(':scope > .chart-block canvas').evaluate(node => node.width > 0 && node.height > 0));
      assert.equal(await page.locator('.demo-page').count(), 0);
      if (id === 'permits/main') {
        assert.equal(await page.locator('.demo-growth-chart canvas').count(), 1);
        await painted(page); await page.screenshot({ path: path.join(output, 'viewer-permits-live-yoy.png') });
      }
      if (id === 'preferential/volumeMln/annual') {
        const legend = page.locator('.demo-full-chart > .chart-block .chart-legend button').first();
        await legend.click(); assert.equal(await legend.getAttribute('aria-pressed'), 'false');
        await legend.click(); assert.equal(await legend.getAttribute('aria-pressed'), 'true');
        await page.screenshot({ path: path.join(output, 'viewer-preferential.png') });
      }
      await page.getByRole('button', { name: 'Следующий график', exact: true }).click();
    }
    assert.equal(seen.size, count); assert.equal(new Set([...seen].map(id => id.split('/')[0])).size, 13);
    await page.keyboard.press('Escape');
    await all.uncheck(); await choices.first().check();
    checks.push(`all ${count} available charts render sequentially, permits reuse live YoY, preferential legend restores`);

    for (const width of [320, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 800 });
      await noOverflow(page);
      await page.getByRole('button', { name: 'Начать показ', exact: true }).click();
      const pixels = await painted(page); assert.ok(pixels.width >= pixels.cssWidth * 1.9);
      const bounds = await page.locator('.demo-viewer').boundingBox(); assert.ok(bounds.x >= 0 && bounds.width <= width + 1);
      assert.equal(await page.locator('.demo-viewer .institutional-brand').isVisible(), true);
      await noOverflow(page); await page.screenshot({ path: path.join(output, `viewer-${width}.png`) });
      await page.keyboard.press('Escape');
      if (width === 390) {
        await page.getByRole('button', { name: 'Открыть навигацию', exact: true }).click();
        const before = (await pinned.boundingBox()).y;
        await page.locator('.navigation-sections').evaluate(node => { node.scrollTop = node.scrollHeight; });
        assert.equal((await pinned.boundingBox()).y, before);
        await page.locator('.navigation-home a').click(); await page.locator('.home-page').waitFor(); assert.ok(page.url().endsWith('/home'));
        assert.equal(await page.getByText('Последнее обновление', { exact: true }).count(), 0);
        await page.locator('.home-service a[href="/demo"]').click(); await page.locator('.demo-page').waitFor();
        await page.waitForFunction(() => document.querySelectorAll('.demo-source[aria-busy="false"]').length === 13);
        await all.check(); await all.uncheck(); await choices.first().check(); await page.getByRole('checkbox', { name: 'Полный экран браузера', exact: true }).uncheck();
      }
    }
    checks.push('320/390/768/1440 responsive live canvases at DPR2, persistent institutional brand, mobile Home');

    failIpc = true; emptyLinear = true; emptySalary = true;
    await page.reload(); await page.waitForFunction(() => document.querySelectorAll('.demo-source[aria-busy="false"]').length === 13);
    await page.locator('[data-demo-source="ipc"] [role="alert"]').waitFor();
    assert.equal(await page.locator('[data-demo-source="linear"] input:disabled').count(), 6);
    assert.match(await page.locator('[data-demo-source="salary"]').innerText(), /Нет доступных графиков/);
    failIpc = false;
    await page.getByRole('button', { name: 'Повторить: ИПЦ', exact: true }).click();
    await page.locator('[data-demo-source="ipc"] .demo-chart-choice').first().waitFor();
    checks.push('explicit error, retry recovery, empty source and disabled empty charts');
    const prior = apiCalls.length;
    await page.locator('.navigation-home a').click(); await page.locator('.home-page').waitFor();
    assert.equal(apiCalls.length, prior, 'Home must not request update status');
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ checks, errors, requests: apiCalls.length }, null, 2));
    console.log(JSON.stringify({ passed: true, checks, errors, output }));
    await context.close();
  } finally { await browser.close(); }
}
module.exports = { main };
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
