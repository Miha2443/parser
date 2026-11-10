const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const { randomBytes } = require('node:crypto');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require(process.env.AUTH_INTEGRATION_PLAYWRIGHT || 'playwright');
const fixtures = require('./demoFixtures.cjs');

const repo = path.resolve(__dirname, '../..'), frontend = path.join(repo, 'frontend');
const PASSWORD = 'auth-browser-fixture-only-2026';
const redact = value => String(value).replaceAll(PASSWORD, '[fixture-password-redacted]');
const PRIVATE_MARKER = 'AUTH_BROWSER_PRIVATE_SOURCE.xlsx';
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const authPath = pathname => pathname.startsWith('/api/v1/auth/');
const reportPath = pathname => pathname.startsWith('/api/') && !authPath(pathname) && !pathname.endsWith('/export') && !pathname.startsWith('/api/v1/tdm/') && !['/api/v1/health', '/api/v1/updates'].includes(pathname);

async function freePort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}
function cleanEnv(extra) {
  const keep = new Set(['path', 'systemroot', 'windir', 'comspec', 'pathext', 'systemdrive', 'temp', 'tmp', 'home', 'userprofile', 'appdata', 'localappdata', 'node_path']);
  return { ...Object.fromEntries(Object.entries(process.env).filter(([key]) => keep.has(key.toLowerCase()))), ...extra };
}
function launch(command, args, cwd, env) {
  const child = spawn(command, args, { cwd, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  child.messages = '';
  child.startError = null;
  child.on('error', error => { child.startError = error; });
  for (const stream of [child.stdout, child.stderr]) stream.on('data', data => { child.messages = (child.messages + data).slice(-8000); });
  return child;
}
async function ready(url, child) {
  for (let i = 0; i < 160; i++) {
    if (child.startError) throw child.startError;
    if (child.exitCode !== null) throw new Error(`Fixture process exited: ${child.messages}`);
    try { if ((await fetch(url, { signal: AbortSignal.timeout(1000) })).ok) return; } catch {}
    await pause(250);
  }
  throw new Error(`Private server did not start: ${child.messages}`);
}
async function stop(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null || !child.pid) return;
  const ended = once(child, 'exit');
  child.kill();
  await ended;
}
function fixtureResponse(input) {
  const url = new URL(input), payload = structuredClone(fixtures.response(input));
  payload.__privateFixtureEvidence = PRIVATE_MARKER;
  if (payload.source?.files) payload.source.files = [PRIVATE_MARKER];
  if (url.pathname === '/api/v1/construction/permits') payload.selection.permitKind = url.searchParams.get('permit_kind') || 'housing';
  if (url.pathname === '/api/v1/mortgage') {
    payload.selection.month = Number(url.searchParams.get('month') || 5);
    const annual = (year, scale) => ({ annual: 100 * scale, selected: 50 * scale, actualThroughMonth: 5, annualYoYPct: year === 2024 ? null : 25, selectedYoYPct: 10 });
    payload.annual = [2024, 2025, 2026].map(year => ({ year, volume: annual(year, 1e9), count: annual(year, 1000) }));
    payload.monthly = [3, 4, 5].map((month, i) => {
      const cell = factor => ({ volumeRub: (20 + i * 5) * 1e9 * factor, count: (10000 + i * 1000) * factor,
        ratePct: 8 + factor + i / 10, termMonths: 240 + i, averageAmountRub: 5e6 + i * 1e5 });
      return { period: `2026-0${month}`, all: cell(1), primary: cell(.4), secondary: cell(.6) };
    });
  }
  return payload;
}
async function pixels(page, selector = '.chart-canvas canvas') {
  return page.locator(selector).first().evaluate(canvas => {
    const bytes = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data, colors = new Set();
    for (let i = 0; i < bytes.length; i += Math.max(4, Math.floor(bytes.length / 32000 / 4) * 4)) if (bytes[i + 3]) colors.add(`${bytes[i]},${bytes[i + 1]},${bytes[i + 2]}`);
    return { width: canvas.width, height: canvas.height, colors: colors.size };
  });
}
async function viewerControls(page) {
  assert.equal(await page.locator('.chart-data, .chart-data-panel, .sources-section, .source-anchor, .cp-source, .ct-source, .cp-raw-record, .cp-segments-disclosure, .objects-section, .linear-sources-link').count(), 0, 'Viewer received protected report UI');
  const downloads = await page.locator('button').evaluateAll(buttons => buttons.filter(button => /скачать|download|\bCSV\b|\bExcel\b|\bPNG\b/i.test(`${button.textContent} ${button.title} ${button.getAttribute('aria-label')}`)).map(button => button.outerHTML));
  assert.deepEqual(downloads, [], 'Viewer download control remained in the DOM');
  for (const href of ['/updates', '/tdm', '/service/constant-prices']) assert.equal(await page.locator(`a[href="${href}"]`).count(), 0, href);
  assert.ok(!(await page.locator('body').innerText()).includes(PRIVATE_MARKER));
}

async function main() {
  let api, vite, browser, currentPage, readState;
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-auth-integration-'));
  const output = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-auth-integration-artifacts-'));
  const setupToken = randomBytes(24).toString('hex'), checks = [], errors = [], registrations = new Set(), skipped = [];
  const record = (name, detail = {}) => { checks.push({ name, ...detail }); console.log(`Passed: ${name}${detail.route ? ` ${detail.route}` : ''}`); };
  try {
    const apiPort = await freePort(), apiBase = `http://127.0.0.1:${apiPort}`;
    api = launch(process.env.DASHBOARD_TEST_PYTHON || 'python', ['-X', 'utf8', '-B', '-m', 'backend.tests.auth_browser_fixture', '--db', path.join(scratch, 'auth.sqlite3'), '--port', String(apiPort)], repo,
      cleanEnv({ PYTHONDONTWRITEBYTECODE: '1', PYTHONIOENCODING: 'utf-8', AUTH_BROWSER_FIXTURE_TOKEN: setupToken }));
    await ready(apiBase + '/health', api);
    const port = await freePort(), base = `http://127.0.0.1:${port}`;
    const bootstrap = "import('vite').then(async ({createServer}) => { const server = await createServer({cacheDir:process.env.AUTH_BROWSER_VITE_CACHE,server:{host:'127.0.0.1',port:Number(process.env.AUTH_BROWSER_VITE_PORT),strictPort:true}}); await server.listen(); }).catch(error => { console.error(error); process.exitCode=1; });";
    vite = launch(process.execPath, ['-e', bootstrap], frontend, cleanEnv({ DASHBOARD_API_TARGET: apiBase, VITE_DATA_MODE: 'api',
      AUTH_BROWSER_VITE_PORT: String(port), AUTH_BROWSER_VITE_CACHE: path.join(scratch, 'vite-cache') }));
    await ready(base, vite);
    async function control(suffix = '') {
      const response = await fetch(apiBase + '/__fixture' + suffix, { headers: { 'X-Fixture-Token': setupToken } });
      assert.equal(response.status, 200);
      return response.json();
    }
    readState = () => control('/state');
    async function register(input) {
      const url = new URL(input), key = url.pathname + url.search;
      if (registrations.has(key)) return;
      const payload = fixtureResponse(input);
      const response = await fetch(apiBase + '/__fixture', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Fixture-Token': setupToken }, body: JSON.stringify({ key, payload }) });
      assert.equal(response.status, 200, `Fixture setup failed: ${key}`);
      registrations.add(key);
    }
    const catalog = fixtures.response(base + '/api/v1/catalog'), developer = catalog.controls.developers[0];
    assert.ok(developer?.developerKey, 'No first fixture developer');
    const profileQuery = new URLSearchParams({ developer: developer.developerKey, region: developer.regions.includes('msk') ? 'msk' : developer.regions[0] });
    const profileUrl = base + '/api/v1/profile?' + profileQuery, profilePage = base + '/?' + profileQuery;
    await register(profileUrl);
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    let snapshotRequests = 0;
    async function context() {
      const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce', acceptDownloads: true });
      await context.route('https://**/*', route => route.abort());
      await context.route('**/profile-snapshot.json', route => { snapshotRequests++; return route.abort(); });
      await context.route('**/api/**', async route => {
        const request = route.request(), url = new URL(request.url());
        if (request.method() === 'GET' && reportPath(url.pathname)) await register(request.url());
        // No mocked auth or report response: every browser byte comes from FastAPI.
        await route.continue();
      });
      const page = await context.newPage();
      page.setDefaultTimeout(30000);
      page.on('pageerror', error => errors.push(error.message));
      return { context, page };
    }
    const a = await context(), b = await context();
    currentPage = a.page;
    async function session(context) {
      const response = await context.request.get(base + '/api/v1/auth/session');
      assert.equal(response.status(), 200, `Real auth session failed: ${await response.text()}`);
      return response.json();
    }
    async function login(page, username) {
      await page.getByLabel('Имя пользователя', { exact: true }).fill(username);
      await page.getByLabel('Пароль', { exact: true }).fill(PASSWORD);
      const received = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/auth/login');
      await page.getByRole('button', { name: 'Войти', exact: true }).click();
      const response = await received;
      assert.equal(response.status(), 200, `Login/Origin proxy mismatch: ${await response.text()}`);
      await page.locator('.app-shell').waitFor();
      assert.ok((await page.locator('.auth-account').innerText()).includes(username));
    }
    async function graph(page, url, rootSelector) {
      currentPage = page;
      await page.goto(url);
      await page.locator(`${rootSelector} .chart-canvas canvas`).first().waitFor();
      await page.waitForLoadState('networkidle');
      const image = await pixels(page, `${rootSelector} .chart-canvas canvas`);
      assert.ok(image.width > 0 && image.height > 0 && image.colors > 5, `Blank real-backend graph: ${url}`);
      await viewerControls(page);
      return image;
    }

    await a.page.goto(base + '/home');
    await a.page.getByRole('heading', { name: 'Вход', exact: true }).waitFor();
    assert.equal(await a.page.locator('.app-shell').count(), 0);
    const beforeLogin = await control('/state');
    assert.equal(beforeLogin.reports, 0);
    assert.equal(beforeLogin.requests.filter(r => reportPath(r.path)).length, 0, 'Browser fetched reports before login');
    const anonymous = await a.context.request.get(profileUrl);
    assert.equal(anonymous.status(), 401);
    assert.equal((await control('/state')).reports, 0, 'Unauthenticated request reached report handler');
    const localSession = await session(a.context);
    assert.equal(localSession.secureCookie, false);
    await a.page.setViewportSize({ width: 320, height: 800 });
    assert.ok(await a.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await a.page.screenshot({ path: path.join(output, 'login-320.png'), fullPage: true });
    await a.page.setViewportSize({ width: 1440, height: 900 });
    record('no-reports-before-login-and-anonymous-401');

    await login(a.page, 'viewer');
    await b.page.goto(base + '/home');
    await login(b.page, 'viewer');
    const firstCookie = (await a.context.cookies(base)).find(cookie => cookie.name === 'dashboard_session');
    const secondCookie = (await b.context.cookies(base)).find(cookie => cookie.name === 'dashboard_session');
    assert.ok(firstCookie && secondCookie);
    assert.ok(firstCookie.value !== secondCookie.value, 'Shared account reused the same browser session');
    for (const cookie of [firstCookie, secondCookie]) {
      assert.equal(cookie.httpOnly, true); assert.equal(cookie.sameSite, 'Lax'); assert.equal(cookie.secure, false); assert.equal(cookie.path, '/');
    }
    assert.ok(!(await a.page.evaluate(() => document.cookie)).includes('dashboard_session'));
    const viewerSession = await session(a.context);
    assert.equal(viewerSession.user.role, 'viewer');
    const projectedResponse = await a.context.request.get(profileUrl), projectedText = await projectedResponse.text();
    assert.equal(projectedResponse.status(), 200);
    assert.ok(!projectedText.includes(PRIVATE_MARKER), 'Backend bytes leaked private provenance');
    const raw = fixtureResponse(profileUrl), projected = JSON.parse(projectedText);
    const measurements = payload => payload.profiles[0].annual.rows.map(row => ({ year: row.year, valuesM2: row.valuesM2, totalM2: row.totalM2 }));
    assert.deepEqual(measurements(projected), measurements(raw), 'Backend projection changed chart measurements');
    assert.equal(projectedResponse.headers()['cache-control'], 'no-store');
    assert.equal(projectedResponse.headers().vary, 'Cookie');
    record('real-cookies-two-independent-shared-account-sessions-and-backend-projection');

    await graph(a.page, profilePage, '#profile');
    const legend = a.page.locator('#profile .chart-legend button').first();
    await legend.click(); assert.equal(await legend.getAttribute('aria-pressed'), 'false');
    await legend.click(); assert.equal(await legend.getAttribute('aria-pressed'), 'true');
    assert.match(await a.page.locator('#profile .kpi-band').first().innerText(), /тыс\.|м²/);
    await a.page.screenshot({ path: path.join(output, 'viewer-profile-desktop.png'), fullPage: true });
    record('viewer-profile-graphs-kpis-and-legend-interaction');

    const pages = [
      ['/apartments', '#apartments-page'], ['/apartments/developer', '#apartments-page'],
      ['/construction/domrf', '#domrf-page'], ['/construction/permits', '#permits-page'],
      ['/commissioning/operational', '#operational-page'], ['/commissioning/annual', '#annual-page'],
      ['/commissioning/linear', '#linear-page'], ['/economics/accounts', '#economics-page'],
      ['/economics/ipc', '#economics-page'], ['/economics/salary', '#economics-page'],
      ['/economics/mortgage', '#mortgage-page'], ['/economics/preferential-mortgage', '#preferential-mortgage-page'],
    ];
    for (const [route, selector] of pages) {
      const image = await graph(a.page, base + route, selector);
      record('viewer-report-ui-and-canvas', { route, ...image });
    }
    skipped.push({ routes: ['/map', '/construction/tree'], reason: 'No map/tree responses in demoFixtures; no unrelated real providers are started.' });
    await graph(a.page, base + '/economics/mortgage', '#mortgage-page');
    const month = a.page.getByRole('combobox', { name: 'Накопленный период ипотеки', exact: true });
    await month.selectOption('6');
    await a.page.waitForURL(url => url.searchParams.get('annualMonth') === '6');
    await a.page.locator('#mortgage-page .chart-canvas canvas').first().waitFor();
    assert.equal(await month.inputValue(), '6');
    await viewerControls(a.page);
    record('viewer-mortgage-period-selector');
    for (const width of [320, 390]) {
      await a.page.setViewportSize({ width, height: 844 });
      await a.page.waitForTimeout(200);
      assert.ok(await a.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Viewer mobile overflow at ${width}`);
      assert.ok((await pixels(a.page)).colors > 5);
      await a.page.screenshot({ path: path.join(output, `viewer-mortgage-${width}.png`), fullPage: true });
      record('mobile-overflow-and-nonblank-canvas', { width });
    }
    await a.page.setViewportSize({ width: 1440, height: 900 });
    for (const route of ['/updates', '/tdm', '/service/constant-prices']) {
      await a.page.goto(base + route); await a.page.locator('.home-page').waitFor();
      await a.page.waitForURL(url => url.pathname === '/home');
    }
    const exportsBefore = (await control('/state')).exports;
    assert.equal((await a.context.request.get(base + '/api/v1/profile/export?' + profileQuery)).status(), 403);
    assert.equal((await a.context.request.get(base + '/api/v1/tdm/catalog')).status(), 403);
    assert.equal((await a.context.request.post(base + '/api/v1/tdm/send', { headers: { Origin: base, 'X-CSRF-Token': viewerSession.csrfToken }, data: { text: 'fixture only' } })).status(), 403);
    assert.equal((await control('/state')).exports, exportsBefore);
    assert.equal((await control('/state')).tdmActions, 0);
    record('viewer-services-direct-export-and-tdm-denied-by-server');

    await a.page.goto(base + '/demo');
    await a.page.waitForFunction(() => document.querySelectorAll('.demo-source[aria-busy="false"]').length === 13, undefined, { timeout: 90000 });
    assert.deepEqual(await a.page.locator('.demo-error').allTextContents(), []);
    await a.page.getByRole('checkbox', { name: 'Все графики', exact: true }).uncheck();
    const card = a.page.locator('.demo-chart-choice').filter({ has: a.page.locator('[data-demo-chart="profile/annual"]') });
    await card.getByRole('checkbox').check();
    await a.page.getByRole('checkbox', { name: 'Полный экран браузера', exact: true }).uncheck();
    await a.page.getByRole('button', { name: 'Начать показ', exact: true }).click();
    await a.page.getByRole('dialog').waitFor();
    await a.page.locator('.demo-stage .chart-canvas canvas').first().waitFor();
    assert.ok((await pixels(a.page, '.demo-stage .chart-canvas canvas')).colors > 5);
    await a.page.screenshot({ path: path.join(output, 'viewer-demo.png') });
    await a.page.keyboard.press('Escape');
    record('viewer-demo-all-13-sources-and-real-canvas-playback');

    await a.page.getByRole('button', { name: 'Выйти', exact: true }).click();
    await a.page.getByRole('heading', { name: 'Вход', exact: true }).waitFor();
    assert.equal((await session(a.context)).user, null);
    assert.equal((await a.context.request.get(profileUrl)).status(), 401);
    assert.equal((await session(b.context)).user.role, 'viewer');
    await graph(b.page, profilePage, '#profile');
    assert.ok((await b.context.cookies(base)).find(cookie => cookie.name === 'dashboard_session').value === secondCookie.value, 'Other browser session cookie changed');
    record('logout-revokes-only-the-current-browser');

    currentPage = a.page;
    await login(a.page, 'analyst');
    await a.page.goto(profilePage);
    await a.page.locator('#profile .chart-canvas canvas').first().waitFor();
    const excel = a.page.getByRole('button', { name: 'Скачать полный профиль Excel', exact: true });
    await excel.waitFor();
    assert.ok(await a.page.locator('#profile .source-anchor').count());
    assert.ok(await a.page.locator('#profile .data-toggle').count());
    const unprojected = await a.context.request.get(profileUrl);
    assert.equal(unprojected.status(), 200); assert.ok((await unprojected.text()).includes(PRIVATE_MARKER));
    const downloading = a.page.waitForEvent('download');
    await excel.click();
    const downloaded = await downloading;
    assert.match(downloaded.suggestedFilename(), /\.xlsx$/);
    assert.ok(fs.statSync(await downloaded.path()).size > 0);
    assert.equal((await a.context.request.get(base + '/api/v1/profile/export?' + profileQuery)).status(), 200);
    record('analyst-controls-and-real-authorized-export-download');

    const analystSession = await session(a.context), tdmBefore = (await control('/state')).tdmActions;
    for (const headers of [{ Origin: base, 'X-CSRF-Token': 'invalid-fixture-token' }, { Origin: 'http://invalid.test', 'X-CSRF-Token': analystSession.csrfToken }]) {
      assert.equal((await a.context.request.post(base + '/api/v1/tdm/send', { headers, data: { text: 'fixture only' } })).status(), 403);
    }
    assert.equal((await control('/state')).tdmActions, tdmBefore, 'Invalid CSRF/Origin reached a mutation handler');
    const accepted = await a.context.request.post(base + '/api/v1/tdm/send', { headers: { Origin: base, 'X-CSRF-Token': analystSession.csrfToken }, data: { text: 'fixture only' } });
    assert.equal(accepted.status(), 200); assert.equal((await accepted.json()).providerCalled, false);
    assert.equal((await control('/state')).tdmActions, tdmBefore + 1);
    record('real-origin-and-csrf-rejection-without-tdm-side-effects');

    const analystCookie = (await a.context.cookies(base)).find(cookie => cookie.name === 'dashboard_session');
    const switched = await a.context.request.post(base + '/api/v1/auth/login', { headers: { Origin: base, 'X-CSRF-Token': analystSession.csrfToken }, data: { username: 'viewer', password: PASSWORD } });
    assert.equal(switched.status(), 200); assert.equal((await switched.json()).user.role, 'viewer');
    assert.ok((await a.context.cookies(base)).find(cookie => cookie.name === 'dashboard_session').value !== analystCookie.value, 'Role switch did not rotate the session cookie');
    const revoked = await fetch(base + '/api/v1/auth/session', { headers: { Cookie: `dashboard_session=${analystCookie.value}` } });
    assert.equal(revoked.status, 200); assert.equal((await revoked.json()).user, null, 'Previous analyst session remained valid');
    await graph(a.page, profilePage, '#profile');
    assert.equal((await session(a.context)).user.role, 'viewer');
    assert.equal((await session(b.context)).user.role, 'viewer');
    record('login-role-switch-rotates-only-current-session');

    const state = await control('/state');
    assert.deepEqual(state.missingFixtures, []);
    assert.equal(snapshotRequests, 0, 'Auth integration fetched the frozen snapshot');
    const logins = state.requests.filter(r => r.path === '/api/v1/auth/login' && r.status === 200);
    assert.ok(logins.length >= 4);
    assert.ok(logins.every(r => r.host === new URL(base).host && r.origin === base), 'Vite proxy changed Host/Origin semantics');
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ checks, skipped, requests: state.requests, reports: state.reports,
      exports: state.exports, tdmActions: state.tdmActions, errors }, null, 2));
    console.log(JSON.stringify({ passed: checks.length, output, skipped, reports: state.reports, exports: state.exports, tdmActions: state.tdmActions }));
  } catch (error) {
    if (currentPage && !currentPage.isClosed()) {
      await currentPage.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {});
      fs.writeFileSync(path.join(output, 'failure-view.txt'), redact(await currentPage.locator('body').innerText().catch(() => 'Page unavailable')));
    }
    if (readState) fs.writeFileSync(path.join(output, 'failure-server.json'), JSON.stringify(await readState().catch(() => ({})), null, 2));
    fs.writeFileSync(path.join(output, 'failure.json'), JSON.stringify({ message: redact(error.message), checks, errors: errors.map(redact), skipped }, null, 2));
    if (api?.messages) console.error(redact(api.messages));
    console.error(`Auth integration artifacts: ${output}`);
    const safeError = new Error(redact(error.message)); safeError.name = error.name;
    throw safeError;
  } finally {
    const cleanup = await Promise.allSettled([browser?.close(), stop(vite), stop(api)]);
    const real = fs.realpathSync(scratch), parent = fs.realpathSync(os.tmpdir());
    assert.equal(path.dirname(real), parent, 'Refusing cleanup outside the temporary root');
    assert.ok(path.basename(real).startsWith('dashboard-auth-integration-'));
    fs.rmSync(real, { recursive: true, force: true, maxRetries: 30, retryDelay: 100 });
    const failed = cleanup.find(result => result.status === 'rejected');
    if (failed) throw new Error(`Fixture cleanup failed: ${redact(failed.reason.message)}`);
  }
}

if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { main };
