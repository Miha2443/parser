const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const base = process.env.DASHBOARD_TEST_URL || 'http://127.0.0.1:5180';
const output = path.join(process.env.TEMP || '.', 'dashboard-integration-checks');
const routes = [
  ['/', 'Профиль застройщика', true],
  ['/apartments', 'Квартирография', true],
  ['/apartments/developer', 'Квартирография по девелоперу', true],
  ['/sales', 'Распроданность и стройготовность', true],
  ['/commissioning/annual', 'Годовой ввод', true],
  ['/commissioning/operational', 'Ввод недвижимости · оперативные данные', true],
  ['/commissioning/linear', 'Ввод линейных объектов', true],
  ['/construction', 'Текущее строительство', true],
  ['/economics/salary', 'Среднемесячная заработная плата', true],
  ['/economics/ipc', 'Индексы потребительских цен', true],
  ['/economics/accounts', 'ВРП и ВВП', true],
  ['/home', 'Аналитика Москвы', false],
  ['/updates', 'Журнал обновлений', false],
  ['/tdm', 'Отправка в TDM', false],
];

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage();
    const errors = [], checks = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/v1/tdm/**', route => {
      assert.equal(route.request().method(), 'GET', 'No action may run merely by visiting TDM');
      return route.fulfill({ json: { actionToken: 'fixture', maxUploadBytes: 52428800,
        status: { tokenReady: false, workspaceReady: false, groupReady: false, ready: false, disabled: true },
        datasets: [], files: [] } });
    });
    for (const theme of ['dark', 'light']) for (const width of [360, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript(theme => localStorage.setItem('dashboard.theme', theme), theme);
      for (const [route, heading, chartExpected] of routes) {
        await page.goto(base + route);
        await page.locator('main h1').waitFor({ timeout: 120000 });
        assert.ok((await page.locator('main h1').innerText()).includes(heading), route);
        await page.waitForFunction(() => !document.querySelector('main .load-state'), { timeout: 120000 });
        assert.equal(await page.locator('main .load-state[role="alert"]').count(), 0, route);
        if (chartExpected) await page.locator('.chart-canvas canvas').first().waitFor();
        await page.waitForTimeout(350);
        const state = await page.evaluate(() => ({
          overflow: document.documentElement.scrollWidth > innerWidth + 1,
          charts: [...document.querySelectorAll('.chart-canvas')].map(element => {
            const rect = element.getBoundingClientRect(), canvas = element.querySelector('canvas');
            const colors = new Set();
            if (canvas?.width && canvas?.height) {
              const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
              const step = Math.max(4, Math.floor(pixels.length / 16000 / 4) * 4);
              for (let i = 0; i < pixels.length; i += step) if (pixels[i + 3]) colors.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]}`);
            }
            return { colors: colors.size, outside: rect.left < -1 || rect.right > innerWidth + 1,
              scrolling: element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1 };
          }),
        }));
        assert.equal(state.overflow, false, `${route}/${theme}/${width}: horizontal overflow`);
        for (const chart of state.charts) {
          assert.ok(chart.colors > 3, `${route}: blank chart`);
          assert.equal(chart.outside, false, `${route}: chart outside viewport`);
          assert.equal(chart.scrolling, false, `${route}: internal chart scrolling`);
        }
        checks.push({ route, theme, width, charts: state.charts.length });
        await page.screenshot({ path: path.join(output, `${route.replaceAll('/', '-') || 'profile'}-${theme}-${width}.png`), fullPage: true });
      }
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto(base + '/home');
    await page.locator('main h1').waitFor();
    for (const heading of ['Мосстат / Росстат', 'Ввод недвижимости', 'Сервис']) {
      const button = page.getByRole('button', { name: heading, exact: true });
      if ((await button.getAttribute('aria-expanded')) !== 'true') await button.click();
    }
    for (const route of ['/economics/salary', '/economics/ipc', '/economics/accounts', '/commissioning/annual', '/commissioning/operational', '/commissioning/linear', '/updates', '/tdm']) {
      const link = page.locator(`.sidebar nav a[href="${route}"]`);
      assert.equal(await link.count(), 1, `Missing local navigation ${route}`);
      assert.equal(await link.getAttribute('target'), null, `Unexpected new tab ${route}`);
      await link.click();
      await page.waitForURL(url => url.pathname === route);
      assert.equal(await link.getAttribute('aria-current'), 'page', `Wrong active navigation ${route}`);
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ checks, errors }, null, 2));
    console.log(JSON.stringify({ layouts: checks.length, errors, output }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
