const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');
const fixtures = require('./constantPricesFixtures.cjs');

const root = path.resolve(__dirname, '../..'), children = [], output = path.join(os.tmpdir(), 'dashboard-constant-prices');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function port() { const server = net.createServer(); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); const port = server.address().port; await new Promise(resolve => server.close(resolve)); return port; }
function start(executable, args, options = {}) {
  const child = spawn(executable, args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], ...options }); child.messages = '';
  for (const stream of [child.stdout, child.stderr]) stream.on('data', chunk => child.messages = (child.messages + chunk).slice(-10000));
  children.push(child); return child;
}
async function ready(url, child) {
  for (let i = 0; i < 300; i++) {
    if (child.exitCode !== null) throw new Error(child.messages);
    try { if ((await fetch(url, { signal: AbortSignal.timeout(2000) })).ok) return; } catch {}
    await pause(200);
  }
  throw new Error(`Not ready: ${url}\n${child.messages}`);
}
const numeric = text => Number(text.replace(/[\s\u202f]/g, '').replace(',', '.'));
const cell = (page, row, column = 0) => page.locator(`[data-result-row="${row}"][data-result-column="${column}"]`);
async function paste(page, locator, text) { await locator.evaluate((element, text) => { const clipboardData = new DataTransfer(); clipboardData.setData('text/plain', text); element.dispatchEvent(new ClipboardEvent('paste', { clipboardData, bubbles: true, cancelable: true })); }, text); }
async function layout(page, name) {
  const state = await page.evaluate(() => ({ overflow: document.documentElement.scrollWidth > innerWidth + 1, rows: document.querySelectorAll('.constant-table tbody tr').length,
    bad: [...document.querySelectorAll('.constant-page button, .constant-page h1, .constant-controls label, .constant-base')].filter(node => { const box = node.getBoundingClientRect(); return !node.closest('.constant-table-wrap') && box.width && (box.x < -1 || box.right > innerWidth + 1); }).map(node => node.textContent) }));
  await page.screenshot({ path: path.join(output, `${name}.png`), fullPage: true });
  assert.ok(!state.overflow, JSON.stringify(state)); assert.deepEqual(state.bad, []);
  return state;
}
async function main() {
  let browser, backend, api;
  const errors = [], checks = [], sourceFile = path.join(root, 'data/processed/ipc.pkl'), sourceBytes = fs.readFileSync(sourceFile);
  fs.mkdirSync(output, { recursive: true });
  try {
    if (process.env.CONSTANT_PRICES_REAL === '1') {
      api = `http://127.0.0.1:${await port()}`;
      backend = start(process.env.DASHBOARD_TEST_PYTHON || 'python', ['-X', 'utf8', '-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', new URL(api).port], { cwd: root, env: { ...process.env, PARSER_ROOT: root, PARSER_USE_REALTY_MARTS: '0', PARSER_REQUIRE_REALTY_MARTS: '0' } });
      await ready(api + '/api/v1/economics/ipc/catalog', backend);
    }
    const web = `http://127.0.0.1:${await port()}`, vite = start(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', new URL(web).port, '--strictPort'], { cwd: path.join(root, 'frontend'), env: { ...process.env, ...(api ? { DASHBOARD_API_TARGET: api } : {}) } });
    await ready(web, vite);
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce', acceptDownloads: true });
    await context.addInitScript(() => { localStorage.setItem('dashboard.navigation', JSON.stringify({ service: true })); Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async text => { window.testClipboard = text; } } }); });
    let unavailable = false, broken = false, gap = false, calls = [];
    await context.route('**/api/**', async route => {
      const url = new URL(route.request().url()); calls.push({ url: url.href, method: route.request().method() });
      assert.ok(url.pathname.startsWith('/api/v1/economics/ipc/'), `Unexpected API: ${url}`);
      if (unavailable) return route.fulfill({ status: 503, json: { detail: { code: 'source_unavailable', reason: 'ИПЦ недоступен для проверки' } } });
      const value = url.pathname.endsWith('/catalog') ? fixtures.catalog() : fixtures.report();
      if (gap && value.charts) value.charts[0].rows.filter(row => row.year === 2024 && row.month === 5).forEach(row => { row['ипц_%'] = null; });
      if (broken && value.charts) delete value.charts[0].rows[0].year;
      return route.fulfill({ json: value });
    });
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
    await page.goto(web + '/service/constant-prices');
    await page.locator('.constant-table').waitFor();
    assert.equal(await page.getByLabel('Период цен', { exact: true }).inputValue(), '2026-08');
    assert.equal(await page.getByLabel('Регион ИПЦ', { exact: true }).inputValue(), 'Москва');
    assert.equal(await page.getByRole('button', { name: 'Копировать результат', exact: true }).isDisabled(), true);
    const initialCalls = calls.length;
    assert.ok(initialCalls >= 2 && initialCalls <= 4, 'StrictMode may abort the first catalog request');
    await page.getByRole('group', { name: 'Периодичность исходных данных' }).getByRole('button', { name: 'Месяцы', exact: true }).click();
    await page.getByLabel('Первый период таблицы').selectOption('2023-01');
    await page.getByLabel('Период цен', { exact: true }).selectOption('2023-03');
    await paste(page, page.getByLabel('Исходное значение, строка 1, показатель 1', { exact: true }), '100\t200\r\n0\t-100\r\n');
    assert.equal(numeric(await cell(page, 0).innerText()), 102.01);
    assert.equal(numeric(await cell(page, 0, 1).innerText()), 204.02);
    assert.equal(numeric(await cell(page, 1).innerText()), 0);
    assert.equal(numeric(await cell(page, 1, 1).innerText()), -101);
    assert.equal(calls.length, initialCalls, 'Typed/pasted values must never be sent to the API');
    await page.getByRole('button', { name: 'Копировать результат', exact: true }).click();
    assert.match(await page.evaluate(() => window.testClipboard), /102,01\t204,02\r\nФевраль 2023\t0,00\t-101,00/);
    await page.getByLabel('С периодами и заголовками').uncheck();
    await page.getByRole('button', { name: 'Копировать результат', exact: true }).click();
    assert.ok((await page.evaluate(() => window.testClipboard)).startsWith('102,01\t204,02'));
    await page.evaluate(() => { navigator.clipboard.writeText = async () => { throw new Error('Denied for QA'); }; });
    await page.getByRole('button', { name: /Копировать результат|Скопировано/ }).click();
    await page.getByRole('dialog').waitFor();
    assert.ok((await page.getByLabel('Результат для копирования').inputValue()).startsWith('102,01'));
    assert.equal(await page.getByLabel('Результат для копирования').evaluate(node => node.selectionEnd === node.value.length), true);
    await page.getByRole('button', { name: 'Закрыть результат', exact: true }).click();
    checks.push('monthly forward conversion, rectangular Excel paste, zero/negative, two copy modes and denied-clipboard fallback');

    await page.getByRole('button', { name: 'Импорт из Excel', exact: true }).click();
    await page.getByLabel('Диапазон Excel').fill('100\t200\t300');
    await page.getByRole('button', { name: 'Периоды в столбцах', exact: true }).click();
    await page.getByLabel('Первый период импорта').selectOption('2023-01');
    await page.getByRole('button', { name: 'Заменить таблицу', exact: true }).click();
    assert.equal(await page.locator('.constant-table tbody tr').count(), 3);
    assert.equal(numeric(await cell(page, 1).innerText()), 202);
    assert.equal(numeric(await cell(page, 2).innerText()), 300);
    await page.getByRole('group', { name: 'Периодичность исходных данных' }).getByRole('button', { name: 'Годы', exact: true }).click();
    await page.getByRole('button', { name: 'Импорт из Excel', exact: true }).click();
    await page.getByLabel('Диапазон Excel').fill('Год\tВыручка\tЗатраты\n2024\t1 000,50\t(200)\n2025\t2000\t0');
    await page.getByLabel('Первая строка — заголовки').check(); await page.getByLabel('Первый столбец — период').check();
    await page.getByRole('button', { name: 'Заменить таблицу', exact: true }).click();
    const mean = Array.from({ length: 12 }, (_, i) => 1.01 ** i).reduce((a, b) => a + b, 0) / 12;
    assert.equal(numeric(await cell(page, 0).innerText()), Number((1000.5 * 1.01 ** -10 / mean).toFixed(2)));
    await page.getByLabel('Уровень цен', { exact: true }).selectOption('end');
    assert.equal(numeric(await cell(page, 0).innerText()), Number((1000.5 * 1.01 ** -21).toFixed(2)));
    await page.getByLabel('Периодичность цен', { exact: true }).selectOption('year');
    assert.equal(await page.getByLabel('Период цен', { exact: true }).locator('option[value="2026"]').count(), 0);
    await page.getByLabel('Период цен', { exact: true }).selectOption('2024');
    assert.equal(numeric(await cell(page, 0).innerText()), 1000.5);
    const download = page.waitForEvent('download'); await page.getByRole('button', { name: 'Скачать расчёт CSV', exact: true }).click();
    assert.match(fs.readFileSync(await (await download).path(), 'utf8'), /1000,5/);
    await page.getByLabel('Исходное значение, строка 1, показатель 1', { exact: true }).fill('not-a-number');
    assert.equal(await page.getByLabel('Исходное значение, строка 1, показатель 1', { exact: true }).getAttribute('aria-invalid'), 'true');
    assert.equal(await page.getByRole('button', { name: 'Копировать результат', exact: true }).isDisabled(), true);
    await page.getByLabel('Исходное значение, строка 1, показатель 1', { exact: true }).fill('1 000,50');
    checks.push('horizontal import, dated headings, annual mean/end methods, incomplete-year exclusion, CSV and invalid-input guard');

    for (const [width, height] of [[320, 800], [390, 844], [768, 1000], [1440, 1000], [1920, 1080]]) { await page.setViewportSize({ width, height }); checks.push({ viewport: [width, height], ...(await layout(page, `fixture-${width}`)) }); }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole('group', { name: 'Периодичность исходных данных' }).getByRole('button', { name: 'Кварталы', exact: true }).click();
    await page.getByLabel('Первый период таблицы').selectOption('2023-Q1');
    await page.getByLabel('Периодичность цен', { exact: true }).selectOption('quarter');
    await page.getByLabel('Период цен', { exact: true }).selectOption('2023-Q1');
    await page.getByLabel('Исходное значение, строка 1, показатель 1', { exact: true }).fill('450');
    assert.equal(numeric(await cell(page, 0).innerText()), 450);
    await page.getByRole('group', { name: 'Периодичность исходных данных' }).getByRole('button', { name: 'Годы', exact: true }).click();
    await page.getByLabel('Периодичность цен', { exact: true }).selectOption('year');
    await page.getByLabel('Период цен', { exact: true }).selectOption('2024');
    const beforeRegion = numeric(await cell(page, 1).innerText());
    await page.getByLabel('Регион ИПЦ', { exact: true }).selectOption('Российская Федерация');
    assert.notEqual(numeric(await cell(page, 1).innerText()), beforeRegion); assert.equal(calls.length, initialCalls);
    await page.reload(); await page.locator('.constant-table').waitFor();
    assert.equal(await page.getByLabel('Регион ИПЦ', { exact: true }).inputValue(), 'Российская Федерация');
    assert.equal(await page.getByLabel('Исходное значение, строка 1, показатель 1', { exact: true }).inputValue(), '1 000,50');
    await page.getByRole('group', { name: 'Периодичность исходных данных' }).getByRole('button', { name: 'Месяцы', exact: true }).click();
    assert.equal(await page.locator('.constant-table tbody tr').count(), 3);
    assert.equal(await page.getByLabel('Исходное значение, строка 1, показатель 1', { exact: true }).inputValue(), '100');
    await page.getByRole('group', { name: 'Периодичность исходных данных' }).getByRole('button', { name: 'Годы', exact: true }).click();
    unavailable = true; await page.getByRole('button', { name: 'Обновить ИПЦ', exact: true }).click();
    await page.getByRole('alert').waitFor(); assert.equal(await page.locator('.constant-table').count(), 0);
    unavailable = false; await page.getByRole('button', { name: 'Повторить', exact: true }).click(); await page.locator('.constant-table').waitFor();
    assert.equal(await page.getByLabel('Исходное значение, строка 1, показатель 1', { exact: true }).inputValue(), '1 000,50');
    gap = true; await page.getByRole('button', { name: 'Обновить ИПЦ', exact: true }).click(); await page.locator('.constant-table').waitFor();
    await page.getByLabel('Уровень цен', { exact: true }).selectOption('average');
    assert.equal(await cell(page, 0).innerText(), '—'); assert.equal(await page.getByRole('button', { name: 'Копировать результат', exact: true }).isDisabled(), true);
    broken = true; await page.getByRole('button', { name: 'Обновить ИПЦ', exact: true }).click(); await page.getByRole('alert').waitFor();
    checks.push('local drafts, independent frequency sheets, regional recomputation, source errors/retry, gap and malformed-source fail-closed');
    assert.ok(calls.every(call => call.method === 'GET'));
    await context.close();

    if (backend) {
      const live = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' }), page = await live.newPage();
      page.on('pageerror', error => errors.push(error.message));
      const response = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/economics/ipc/report');
      await page.goto(web + '/service/constant-prices'); await page.locator('.constant-table').waitFor({ timeout: 120000 });
      const report = await (await response).json(), region = await page.getByLabel('Регион ИПЦ', { exact: true }).inputValue();
      const rows = report.charts[0].rows.filter(row => row.region === region).sort((a, b) => a.year - b.year || a.month - b.month), latest = rows.at(-1), base = `${latest.year}-${String(latest.month).padStart(2, '0')}`;
      assert.equal(await page.getByLabel('Период цен', { exact: true }).inputValue(), base);
      const year = Number(await page.getByLabel('Период строки 1', { exact: true }).inputValue());
      let level = 1; const selected = [];
      for (const row of rows.filter(row => row.year >= year)) { if (selected.length || row.month !== 1 || row.year !== year) level *= row['ипц_%'] / 100; if (row.year === year) selected.push(level); }
      const expected = 1000 * level / (selected.reduce((a, b) => a + b, 0) / selected.length);
      await page.getByLabel('Исходное значение, строка 1, показатель 1', { exact: true }).fill('1000');
      assert.equal(numeric(await cell(page, 0).innerText()), Number(expected.toFixed(2)));
      for (const width of [390, 1440, 1920]) { await page.setViewportSize({ width, height: 1000 }); checks.push({ live: true, base, ...(await layout(page, `live-${width}`)) }); }
      assert.deepEqual(fs.readFileSync(sourceFile), sourceBytes, 'The calculator must not change source files');
      await live.close();
    }
    assert.deepEqual(errors, []); fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ checks, errors }, null, 2));
    console.log(JSON.stringify({ passed: true, output, checks, errors }));
  } finally {
    await browser?.close();
    for (const child of children.reverse()) if (child.exitCode === null) { child.kill(); await new Promise(resolve => child.once('exit', resolve)); }
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
