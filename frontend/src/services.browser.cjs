const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const base = process.env.DASHBOARD_TEST_URL || 'http://localhost:5173';
const output = path.join(process.env.TEMP || '.', 'dashboard-service-checks');
fs.mkdirSync(output, { recursive: true });
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ acceptDownloads: true });
    const errors = [], checks = []; page.on('pageerror', error => errors.push(error.message));
    // Prevent any real TDM request during browser verification.
    let sends = 0;
    await page.route('**/api/v1/tdm/**', async route => {
      if (route.request().method() === 'GET') return route.fulfill({ json: {
        actionToken: 'fixture-token', maxUploadBytes: 52428800,
        status: { tokenReady: true, workspaceReady: true, groupReady: true, disabled: false, ready: true },
        datasets: [{ key: 'fixture', title: 'Мониторинг', section: 'Недвижимость', files: ['data/raw/fixture.xlsx'], size: 100 }],
        files: [{ key: 'fixture.xlsx', label: 'fixture.xlsx', size: 100 }],
      } });
      if (route.request().url().includes('/groups')) return route.fulfill({ json: { groups: [{ groupId: '1', workspaceId: '2', title: 'Тестовая группа' }] } });
      sends++; assert.equal(route.request().headers()['x-tdm-token'], 'fixture-token');
      assert.ok(route.request().headers()['idempotency-key']); return route.fulfill({ json: { ok: true, message: 'Отправлено' } });
    });
    for (const route of ['home', 'updates', 'tdm']) for (const theme of ['dark', 'light']) for (const width of [360, 768, 1280, 1920]) {
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript(theme => localStorage.setItem('dashboard.theme', theme), theme);
      await page.goto(`${base}/${route}`);
      await page.waitForSelector(route === 'tdm' ? '.tdm-modes' : '.service-page table', { timeout: 60000 });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      checks.push({ route, theme, width }); await page.screenshot({ path: path.join(output, `${route}-${theme}-${width}.png`), fullPage: true });
    }
    assert.equal(sends, 0);
    await page.getByRole('button', { name: 'Получить группы бота' }).click(); await page.getByText('Тестовая группа').waitFor();
    await page.getByRole('button', { name: 'Отправить файл', exact: true }).click(); await page.getByRole('status').filter({ hasText: 'Отправлено' }).waitFor();
    await page.getByLabel('Текст TDM').fill('Fixture'); await page.getByRole('button', { name: 'Отправить текст', exact: true }).click();
    await page.waitForTimeout(100); assert.equal(sends, 2);
    await page.getByLabel('С компьютера', { exact: false }).check();
    await page.getByLabel('Загрузить файл TDM').setInputFiles({ name: 'test.xlsx', mimeType: 'application/octet-stream', buffer: Buffer.from('fixture') });
    await page.getByRole('button', { name: 'Отправить файл', exact: true }).click(); await page.waitForTimeout(100); assert.equal(sends, 3);
    await page.goto(`${base}/updates`); await page.waitForSelector('.service-page table');
    await page.getByLabel('Период событий').selectOption('90'); await page.getByLabel('История мониторинга').selectOption('7'); await page.getByLabel('Только ошибки').check();
    await page.waitForSelector('.service-page table'); assert.ok(page.url().includes('days=90') && page.url().includes('monitoring_days=7') && page.url().includes('only_errors=true'));
    const download = page.waitForEvent('download'); await page.getByRole('button', { name: 'Скачать CSV: Журнал событий', exact: true }).click(); assert.ok((await download).suggestedFilename().endsWith('.csv'));
    assert.deepEqual(errors, []); fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ checks, errors, fixtureSends: sends }, null, 2));
    console.log(JSON.stringify({ layouts: checks.length, errors, fixtureSends: sends, output }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
