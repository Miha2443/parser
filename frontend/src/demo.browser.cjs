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
  const canvas = page.locator('.demo-stage .demo-full-chart > .chart-block canvas').first();
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
async function noOverflow(page) {
  const overflow = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth, viewer: document.querySelector('.demo-viewer')?.scrollWidth, nodes: [...document.querySelectorAll('.demo-page *, .demo-viewer *')].filter(node => { const rect = node.getBoundingClientRect(); return rect.width && (rect.x < -1 || rect.right > innerWidth + 1); }).slice(0, 10).map(node => ({ class: node.className, width: node.getBoundingClientRect().width, right: node.getBoundingClientRect().right })) }));
  if (overflow.document > overflow.width + 1 || overflow.viewer > overflow.width + 1) await page.screenshot({ path: path.join(output, 'overflow.png') });
  assert.ok(overflow.document <= overflow.width + 1 && !(overflow.viewer > overflow.width + 1), JSON.stringify(overflow));
}
async function revealControls(page) {
  if (await page.locator('.demo-viewer-controls').isHidden()) await page.getByRole('button', { name: 'Показать управление', exact: true }).click();
}
async function main() {
  const browser = await chromium.launch({ channel: 'chrome', headless: true }), checks = [], errors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce', hasTouch: true, deviceScaleFactor: 2 });
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
    assert.equal(await page.locator('[data-demo-source="permits"] .demo-source-selection input').isDisabled(), true);
    assert.equal(await page.locator('.demo-chart-choice input:checked').count(), 0, 'sources arriving asynchronously must not be selected');
    releasePermits();
    await page.waitForFunction(() => document.querySelectorAll('.demo-source[aria-busy="false"]').length === 13, { timeout: 30000 });
    assert.equal(await page.locator('.demo-error').count(), 0);
    assert.equal(await page.getByRole('slider', { name: 'Интервал показа' }).inputValue(), '120');
    assert.equal(await page.locator('.demo-page iframe').count(), 0);
    assert.ok(await page.locator('.demo-page canvas').count() < await page.locator('.demo-chart-choice').count());
    await noOverflow(page);
    await page.screenshot({ path: path.join(output, 'configuration-desktop.png') });
    checks.push('13 API sources, real lazy live previews, default interval, no iframes, asynchronously loaded charts unselected');

    const profileSection = page.locator('[data-demo-source="profile"]');
    const another = fixtures.response('/api/v1/catalog').controls.developers.find(row => row.developerKey === 'самолет');
    assert.ok(another);
    const otherRequests = apiCalls.filter(url => !/\/api\/v1\/(?:catalog|profile)(?:\?|$)/.test(url)).length;
    const company = profileSection.getByRole('combobox', { name: 'Группа компаний', exact: true });
    await company.fill('САМОЛ');
    await profileSection.getByRole('option', { name: 'САМОЛЕТ', exact: true }).click();
    await profileSection.locator('.demo-context').first().filter({ hasText: 'САМОЛЕТ' }).waitFor();
    assert.ok(apiCalls.some(url => new URL(url).pathname === '/api/v1/profile' && new URL(url).searchParams.get('developer') === another.developerKey));
    assert.equal(apiCalls.filter(url => !/\/api\/v1\/(?:catalog|profile)(?:\?|$)/.test(url)).length, otherRequests, 'changing the developer reloads only the profile section');
    assert.equal(await page.locator('.demo-chart-choice input:checked').count(), 0);
    checks.push('searchable alternative developer selection reloads only its source and leaves the playlist unselected');

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
    assert.equal(await all.isChecked(), false);
    assert.equal(await page.getByRole('button', { name: 'Начать показ', exact: true }).isEnabled(), false);
    assert.equal(await page.locator('.demo-chart-choice input:checked').count(), 0);
    const choices = page.locator('.demo-chart-choice input:not(:disabled)');
    const sections = page.locator('.demo-source').filter({ has: page.locator('.demo-chart-choice input:not(:disabled)') });
    const firstSection = sections.nth(0), secondSection = sections.nth(1), firstGroup = firstSection.locator('.demo-source-selection input'), secondGroup = secondSection.locator('.demo-source-selection input');
    const firstSectionCount = await firstSection.locator('.demo-chart-choice input:not(:disabled)').count();
    await firstSection.locator('.demo-chart-choice input:not(:disabled)').first().check();
    assert.equal(await firstGroup.evaluate(node => node.indeterminate), firstSectionCount > 1);
    assert.equal(await all.evaluate(node => node.indeterminate), true);
    await firstGroup.check(); assert.equal(await firstSection.locator('.demo-chart-choice input:checked').count(), firstSectionCount);
    await secondGroup.check(); const secondSectionCount = await secondSection.locator('.demo-chart-choice input:checked').count();
    await firstGroup.uncheck(); assert.equal(await firstSection.locator('.demo-chart-choice input:checked').count(), 0);
    assert.equal(await secondSection.locator('.demo-chart-choice input:checked').count(), secondSectionCount, 'section changes preserve other sections');
    await all.check(); assert.equal(await page.locator('.demo-source-selection input:enabled:not(:checked)').count(), 0);
    await firstGroup.uncheck(); assert.equal(await all.evaluate(node => node.indeterminate), true);
    assert.equal(await secondGroup.isChecked(), true, 'deselecting a section from global all preserves the rest');
    await all.check(); await all.uncheck();
    assert.equal(await page.locator('.demo-source-selection input:checked').count(), 0);
    await firstSection.locator('.demo-chart-choice input:not(:disabled)').first().check();
    await secondSection.locator('.demo-chart-choice input:not(:disabled)').first().check();
    assert.match(await page.locator('.demo-selection-count').innerText(), /2 \/ /);
    const slider = page.getByRole('slider', { name: 'Интервал показа' });
    await slider.focus(); await slider.press('End'); assert.equal(await slider.inputValue(), '1800');
    await slider.press('Home'); assert.equal(await slider.inputValue(), '15');
    await page.getByRole('checkbox', { name: 'Полный экран браузера', exact: true }).uncheck();
    await page.getByRole('button', { name: 'Начать показ', exact: true }).click();
    await page.getByRole('dialog').waitFor(); const initialCanvas = await painted(page);
    assert.equal(await page.locator('.demo-page').count(), 0); assert.equal(await page.locator('.demo-stage canvas').count(), 1);
    assert.equal(await page.evaluate(() => document.getElementById('root').inert), true);
    assert.equal(await page.getByRole('progressbar').count(), 0);
    assert.equal(await page.getByRole('button', { name: 'Данные и скачивание', exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: 'Завершить демонстрацию', exact: true }).evaluate(node => node === document.activeElement), true);
    await page.keyboard.press('Shift+Tab'); assert.equal(await page.getByRole('button', { name: 'Скрыть управление', exact: true }).evaluate(node => node === document.activeElement), true);
    await page.keyboard.press('Shift+Tab'); assert.equal(await page.getByRole('button', { name: 'Полный экран браузера', exact: true }).evaluate(node => node === document.activeElement), true);
    await page.keyboard.press('Tab'); assert.equal(await page.getByRole('button', { name: 'Скрыть управление', exact: true }).evaluate(node => node === document.activeElement), true);
    const exit = page.getByRole('button', { name: 'Завершить демонстрацию', exact: true });
    await exit.focus(); await page.mouse.move(100, 100);
    const beforeRevealRequests = apiCalls.length;
    await page.clock.runFor(3200);
    assert.equal(await page.locator('.demo-viewer-controls').isHidden(), true);
    assert.equal(await page.getByRole('button', { name: 'Следующий график', exact: true }).count(), 0, 'hidden controls leave the accessibility tree');
    assert.equal(await page.getByRole('button', { name: 'Полный экран браузера', exact: true }).count(), 0);
    assert.equal(await exit.isVisible(), true);
    assert.equal((await painted(page)).cssHeight, initialCanvas.cssHeight, 'hiding the footer must not resize the chart');
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.getByRole('button', { name: 'Показать управление', exact: true }).evaluate(node => node === document.activeElement), true);
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('.demo-viewer-controls').isVisible(), true);
    await page.getByRole('button', { name: 'Приостановить показ', exact: true }).focus();
    await page.clock.runFor(3200); assert.equal(await page.locator('.demo-viewer-controls').isVisible(), true, 'keyboard focus prevents hiding');
    await page.getByRole('button', { name: 'Закрепить управление', exact: true }).click();
    await exit.focus(); await page.clock.runFor(3200);
    assert.equal(await page.locator('.demo-viewer-controls').isVisible(), true, 'pinned controls remain visible');
    await page.getByRole('button', { name: 'Открепить управление', exact: true }).click();
    await exit.focus(); await page.clock.runFor(3200);
    assert.equal(await page.locator('.demo-viewer-controls').isHidden(), true);
    await page.mouse.move(400, 880); assert.equal(await page.locator('.demo-viewer-controls').isVisible(), true);
    await page.mouse.move(100, 100); await page.clock.runFor(3200);
    assert.equal(await page.locator('.demo-viewer-controls').isHidden(), true);
    await page.touchscreen.tap(300, 300); assert.equal(await page.locator('.demo-viewer-controls').isVisible(), true);
    await page.getByRole('button', { name: 'Скрыть управление', exact: true }).tap();
    assert.equal(await page.locator('.demo-viewer-controls').isHidden(), true, 'explicit touch hide does not reveal itself');
    await page.getByRole('button', { name: 'Показать управление', exact: true }).tap();
    assert.equal(await page.locator('.demo-viewer-controls').isVisible(), true);
    assert.equal(apiCalls.length, beforeRevealRequests, 'control visibility must not fetch data or rebuild the queue');
    // Reset to a known slide boundary after exercising the idle timer.
    await page.keyboard.press('ArrowRight'); await page.keyboard.press('ArrowLeft');
    const firstTitle = await page.locator('#demo-slide-title').innerText();
    await page.clock.runFor(16000);
    await page.waitForFunction(title => document.getElementById('demo-slide-title').textContent !== title, firstTitle);
    const secondTitle = await page.locator('#demo-slide-title').innerText(); assert.notEqual(secondTitle, firstTitle);
    await page.clock.runFor(15000);
    await page.waitForFunction(title => document.getElementById('demo-slide-title').textContent === title, firstTitle);
    await page.clock.runFor(7000);
    await revealControls(page);
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
    checks.push('section/global mixed selection, idle hiding, bottom-pointer/keyboard/touch reveal, focus hold, pin, no progress/exports, stable layout and queue');
    checks.push('subset, range endpoints, timed cycle without progress UI, pause/resume, arrows, focus trap, Escape restoration');

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

    // A single chart loops correctly even while all playback controls are hidden.
    await all.check(); await all.uncheck(); await choices.first().check();
    await page.getByRole('checkbox', { name: 'Полный экран браузера', exact: true }).uncheck();
    await page.getByRole('button', { name: 'Начать показ', exact: true }).click();
    await page.clock.runFor(31000); assert.equal(await page.locator('.demo-slide-count').innerText(), '1 / 1');
    assert.equal(await page.locator('.demo-viewer-controls').isHidden(), true);
    assert.equal(await page.getByRole('button', { name: 'Данные и скачивание', exact: true }).count(), 0);
    await page.keyboard.press('Escape'); checks.push('one-chart loop with hidden controls and no presentation export actions');

    await all.check();
    await page.getByRole('button', { name: 'Начать показ', exact: true }).click();
    const count = Number((await page.locator('.demo-slide-count').innerText()).split('/')[1]), seen = new Set();
    for (let index = 0; index < count; index++) {
      const chart = page.locator('.demo-full-chart').first(), id = await chart.getAttribute('data-demo-chart');
      seen.add(await page.locator('.demo-stage').getAttribute('data-demo-slide'));
      for (const panel of await page.locator('.demo-full-chart').all()) {
        await panel.locator(':scope > .chart-block canvas').waitFor();
        assert.ok(await panel.locator(':scope > .chart-block canvas').evaluate(node => node.width > 0 && node.height > 0));
      }
      assert.ok((await page.locator('.demo-slide-source').innerText()).length > 4);
      assert.equal(await page.locator('.demo-page').count(), 0);
      if (id === 'permits/main') {
        assert.equal(await page.locator('.demo-growth-chart canvas').count(), 0);
        assert.equal(await page.locator('.demo-stage canvas').count(), 1, 'permits and YoY share one live canvas');
        await painted(page); await page.screenshot({ path: path.join(output, 'viewer-permits-live-yoy.png') });
      }
      if (id === 'preferential/volumeMln/annual') {
        const legend = chart.locator(':scope > .chart-block .chart-legend button').first();
        await legend.click(); assert.equal(await legend.getAttribute('aria-pressed'), 'false');
        await legend.click(); assert.equal(await legend.getAttribute('aria-pressed'), 'true');
        await page.screenshot({ path: path.join(output, 'viewer-preferential.png') });
      }
      await page.keyboard.press('ArrowRight');
    }
    assert.equal(seen.size, count); assert.equal(new Set([...seen].map(id => id.split('/')[0])).size, 13);
    await page.keyboard.press('Escape');
    await all.uncheck(); await choices.first().check();
    checks.push(`all ${count} available charts render sequentially, permits use one live canvas, preferential legend restores`);

    for (const [width, height] of [[320, 800], [390, 800], [768, 800], [1440, 800], [1920, 1080], [3840, 2160], [844, 390]]) {
      await page.setViewportSize({ width, height });
      await noOverflow(page);
      await page.getByRole('button', { name: 'Начать показ', exact: true }).click();
      const pixels = await painted(page); assert.ok(pixels.width >= pixels.cssWidth * 1.9);
      const bounds = await page.locator('.demo-viewer').boundingBox(); assert.ok(bounds.x >= 0 && bounds.width <= width + 1);
      assert.equal(await page.locator('.demo-viewer .institutional-brand').isVisible(), true);
      const headingFont = await page.locator('#demo-slide-title').evaluate(node => parseFloat(getComputedStyle(node).fontSize));
      if (width === 1920) assert.equal(headingFont, 44);
      if (width === 3840) assert.equal(headingFont, 76);
      const heading = await page.locator('.demo-slide-heading').boundingBox(), stage = await page.locator('.demo-stage').boundingBox();
      assert.ok(heading.y + heading.height <= stage.y + 1, 'title and chart must not overlap');
      assert.ok(pixels.cssHeight >= 140);
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
    checks.push('320/390/768/1440, 1920x1080, 3840x2160 and mobile landscape DPR2 canvases, discrete title tiers, no overlaps, mobile Home');
    await page.setViewportSize({ width: 1440, height: 900 });

    failIpc = true; emptyLinear = true; emptySalary = true;
    await page.reload(); await page.waitForFunction(() => document.querySelectorAll('.demo-source[aria-busy="false"]').length === 13);
    await page.locator('[data-demo-source="ipc"] [role="alert"]').waitFor();
    assert.equal(await page.locator('[data-demo-source="linear"] .demo-chart-choice input:disabled').count(), 6);
    assert.equal(await page.locator('[data-demo-source="linear"] .demo-source-selection input').isDisabled(), true);
    assert.equal(await page.locator('[data-demo-source="salary"] .demo-source-selection input').isDisabled(), true);
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
