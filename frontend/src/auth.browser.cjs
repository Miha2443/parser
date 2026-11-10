const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');
const fixtures = require('./demoFixtures.cjs');

const root = path.resolve(__dirname, '..'), output = path.join(os.tmpdir(), 'dashboard-auth-checks');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function freePort() {
  const server = net.createServer(); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port; await new Promise(resolve => server.close(resolve)); return port;
}
async function main() {
  let server, browser;
  fs.mkdirSync(output, { recursive: true });
  try {
    let base = process.env.DASHBOARD_TEST_URL;
    if (!base) {
      const port = await freePort(); base = `http://127.0.0.1:${port}`;
      server = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], { cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
      let messages = '';
      for (const stream of [server.stdout, server.stderr]) stream.on('data', chunk => { messages = (messages + chunk).slice(-10000); });
      let ready = false;
      for (let attempt = 0; attempt < 120; attempt++) {
        if (server.exitCode !== null) throw new Error(messages);
        try { if ((await fetch(base)).ok) { ready = true; break; } } catch {}
        await pause(250);
      }
      if (!ready) throw new Error(`Private Vite did not start: ${messages}`);
    }
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await context.addCookies([{ name: 'auth-fixture', value: 'same-origin', url: base, httpOnly: true, sameSite: 'Lax' }]);
    const page = await context.newPage(), errors = [], privateCalls = [], loginBodies = [], tdmPosts = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.clock.install();
    const anonymous = () => ({ enabled: true, user: null, csrfToken: null });
    let session = { ...anonymous(), secureCookie: true }, sessionGets = 0, sessionStatus = 200, logoutCalls = 0, logoutFails = false;
    let releaseSession, holdSession = new Promise(resolve => { releaseSession = resolve; }), releaseLogin, holdLogin, onSession;
    let tdmDenied = false, privateExpired = false, snapshots = 0, httpsOnly = false;
    await page.route('**/profile-snapshot.json', async route => { snapshots++; await route.abort(); });
    await page.route('**/api/**', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      if (p === '/api/v1/auth/session') {
        sessionGets++;
        onSession?.(); onSession = undefined;
        const result = structuredClone(session), status = sessionStatus, pending = holdSession;
        if (pending) await pending;
        return route.fulfill({ status, json: result });
      }
      if (p === '/api/v1/auth/login') {
        assert.equal(req.headers().origin, new URL(base).origin);
        assert.match(req.headers().cookie ?? '', /auth-fixture=same-origin/);
        const body = req.postDataJSON(); loginBodies.push(body);
        if (httpsOnly) return route.fulfill({ status: 426, json: { detail: 'https_required' } });
        if (holdLogin) await holdLogin;
        if (body.password !== 'fixture-password' || !['analyst', 'viewer'].includes(body.username)) return route.fulfill({ status: 401, json: { detail: 'invalid_credentials' } });
        sessionStatus = 200;
        session = { enabled: true, user: { username: body.username, role: body.username }, csrfToken: `csrf-${body.username}-${loginBodies.length}` };
        return route.fulfill({ json: session });
      }
      if (p === '/api/v1/auth/logout') {
        logoutCalls++;
        assert.equal(req.headers().origin, new URL(base).origin);
        assert.equal(req.headers()['x-csrf-token'], session.csrfToken);
        if (logoutFails) return route.fulfill({ status: 500, json: { detail: 'fixture failure' } });
        session = anonymous(); return route.fulfill({ status: 204 });
      }
      privateCalls.push([req.method(), p]);
      if (privateExpired) { privateExpired = false; session = anonymous(); sessionStatus = 401; }
      if (session.enabled && !session.user) return route.fulfill({ status: 401, json: { detail: 'expired' } });
      if (p.startsWith('/api/v1/tdm/')) {
        if (req.method() === 'GET') return route.fulfill({ json: { actionToken: 'tdm-fixture', maxUploadBytes: 52428800, status: { tokenReady: true, workspaceReady: true, groupReady: true, disabled: false, ready: true }, datasets: [{ key: 'fixture', title: 'Тест', section: 'Мониторинг', files: ['private.xlsx'], size: 10 }], files: [] } });
        tdmPosts.push(req.postDataJSON());
        assert.equal(req.headers()['x-csrf-token'], session.csrfToken);
        assert.equal(req.headers()['x-tdm-token'], 'tdm-fixture');
        assert.ok(req.headers()['idempotency-key']);
        if (tdmDenied) { session = { ...session, user: { username: 'analyst', role: 'viewer' } }; return route.fulfill({ status: 403, json: { detail: 'role_changed' } }); }
        return route.fulfill({ json: { message: 'Отправлено' } });
      }
      try { return route.fulfill({ json: fixtures.response(req.url()) }); }
      catch (error) { return route.fulfill({ status: 500, json: { detail: String(error) } }); }
    });
    async function login(role) {
      await page.getByLabel('Имя пользователя', { exact: true }).fill(role);
      await page.getByLabel('Пароль', { exact: true }).fill('fixture-password');
      await page.getByRole('button', { name: 'Войти', exact: true }).click();
      await page.locator('.app-shell').waitFor();
    }
    async function assertFits(selector) {
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      const bad = await page.locator(selector).evaluateAll(elements => elements.filter(element => {
        const rect = element.getBoundingClientRect(); return rect.left < 0 || rect.right > innerWidth + 1 || element.scrollWidth > element.clientWidth + 1;
      }).map(element => element.outerHTML));
      assert.deepEqual(bad, []);
    }

    await page.goto(`${base}/home`);
    await page.getByRole('status').filter({ hasText: 'Проверка сессии' }).waitFor();
    assert.equal(privateCalls.length, 0, 'protected content fetched before session discovery');
    assert.equal(snapshots, 0);
    holdSession = undefined; releaseSession();
    await page.getByRole('alert').filter({ hasText: 'Для входа необходимо защищённое подключение HTTPS.' }).waitFor();
    assert.equal(await page.locator('input').count(), 0, 'secure-cookie HTTP login exposed credential inputs');
    assert.equal(loginBodies.length, 0);
    assert.equal(privateCalls.length, 0);
    assert.equal(await page.getByRole('link', { name: 'Открыть HTTPS', exact: true }).getAttribute('href'), `${base.replace('http:', 'https:')}/home`);
    session = { ...anonymous(), secureCookie: false };
    await page.getByRole('button', { name: 'Повторить проверку', exact: true }).click();
    await page.getByRole('heading', { name: 'Вход', exact: true }).waitFor();
    for (const width of [320, 375, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await page.waitForTimeout(200);
      await assertFits('.auth-form input, .auth-form button');
      await page.screenshot({ path: path.join(output, `login-${width}.png`), fullPage: true });
    }
    await page.getByLabel('Имя пользователя', { exact: true }).fill('viewer');
    await page.getByLabel('Имя пользователя', { exact: true }).focus();
    await page.keyboard.press('Tab');
    assert.equal(await page.getByLabel('Пароль', { exact: true }).evaluate(element => element === document.activeElement), true);
    await page.getByLabel('Пароль', { exact: true }).fill('wrong');
    await page.keyboard.press('Tab');
    const visibility = page.getByRole('button', { name: 'Показать пароль', exact: true });
    assert.equal(await visibility.evaluate(element => element === document.activeElement && element.matches(':focus-visible')), true);
    await page.keyboard.press('Space');
    assert.equal(await page.getByLabel('Пароль', { exact: true }).getAttribute('type'), 'text');
    await page.keyboard.press('Space');
    await page.keyboard.press('Tab');
    const beforeBadLogin = sessionGets;
    await page.keyboard.press('Enter');
    await page.getByRole('alert').filter({ hasText: 'Неверное имя пользователя или пароль' }).waitFor();
    assert.equal(await page.getByLabel('Пароль', { exact: true }).inputValue(), '');
    assert.equal(sessionGets, beforeBadLogin, 'login 401 recursively revalidated auth');
    httpsOnly = true;
    await page.getByLabel('Пароль', { exact: true }).fill('fixture-password');
    await page.getByRole('button', { name: 'Войти', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'Для входа необходимо защищённое подключение HTTPS.' }).waitFor();
    assert.equal(privateCalls.length, 0);
    httpsOnly = false;
    holdLogin = new Promise(resolve => { releaseLogin = resolve; });
    await page.getByLabel('Пароль', { exact: true }).fill('fixture-password');
    await page.getByRole('button', { name: 'Войти', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('form')?.getAttribute('aria-busy') === 'true');
    assert.equal(await page.getByLabel('Имя пользователя', { exact: true }).isDisabled(), true);
    assert.equal(await page.getByLabel('Пароль', { exact: true }).isDisabled(), true);
    assert.equal(privateCalls.length, 0);
    holdLogin = undefined; releaseLogin();
    await page.locator('.app-shell').waitFor();
    assert.equal(loginBodies.length, 3);
    assert.deepEqual(loginBodies.at(-1), { username: 'viewer', password: 'fixture-password' });
    assert.equal(await page.locator('.auth-account').innerText(), 'viewer\nПросмотр');
    await page.getByRole('button', { name: 'Сервис', exact: true }).click();
    for (const href of ['/updates', '/tdm', '/service/constant-prices']) assert.equal(await page.locator(`a[href="${href}"]`).count(), 0);
    assert.ok(await page.locator('a[href="/demo"]').count() > 0);
    for (const width of [320, 375, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await page.waitForTimeout(200);
      await assertFits('.auth-account');
      await page.screenshot({ path: path.join(output, `viewer-home-${width}.png`), fullPage: true });
    }
    const beforeRestricted = privateCalls.length;
    for (const route of ['/updates', '/tdm', '/service/constant-prices']) {
      await page.goto(base + route);
      await page.locator('.home-page').waitFor();
      await page.waitForURL(url => url.pathname === '/home');
    }
    assert.equal(privateCalls.length, beforeRestricted, 'viewer direct routes mounted restricted pages');
    await page.goto(`${base}/`);
    await page.locator('#profile .chart-canvas canvas').first().waitFor();
    assert.equal(await page.locator('#profile .source-line, #profile #sources, #profile .source-anchor, #profile .profile-actions').count(), 0);
    assert.equal(await page.getByRole('button', { name: 'Скачать квартирографию CSV' }).count(), 0);
    assert.equal(await page.getByRole('button', { name: 'Скачать переносы сроков CSV' }).count(), 0);
    assert.ok((await page.locator('#profile .kpi-band').innerText()).includes('тыс. м²'));
    assert.equal(snapshots, 0);
    await page.evaluate(() => { window.__authCanvas = document.querySelector('#profile canvas'); });
    const focusResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/auth/session');
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await focusResponse;
    await page.waitForTimeout(50);
    assert.equal(await page.evaluate(() => window.__authCanvas.isConnected), true, 'same-user validation remounted protected content');
    session = { ...session, user: { username: 'viewer', role: 'analyst' }, csrfToken: 'role-promoted' };
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.locator('#profile .source-anchor').waitFor();
    assert.equal(await page.evaluate(() => window.__authCanvas.isConnected), false, 'role transition retained protected state');
    session = anonymous();
    await page.clock.fastForward(60010);
    await page.getByRole('heading', { name: 'Вход', exact: true }).waitFor();
    assert.equal(await page.locator('.app-shell').count(), 0, '60-second validation retained logged-out content');
    await login('analyst');
    await page.goto(`${base}/demo`);
    await page.getByLabel('Полный экран браузера', { exact: true }).uncheck();
    await page.locator('[data-demo-source="profile"]').getByLabel('Ввод по годам', { exact: true }).check();
    await page.getByRole('button', { name: 'Начать показ', exact: true }).click();
    await page.getByRole('dialog').waitFor();
    session = anonymous();
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.getByRole('heading', { name: 'Вход', exact: true }).waitFor();
    assert.equal(await page.getByRole('dialog').count(), 0, 'expired session retained demonstration portal');
    assert.equal(await page.evaluate(() => document.body.style.overflow), '');
    assert.equal(await page.locator('#root').evaluate(element => element.inert), false);
    await login('analyst');
    await page.goto(`${base}/tdm`);
    await page.getByLabel('Текст TDM', { exact: true }).fill('fixture message');
    await page.getByRole('button', { name: 'Отправить текст', exact: true }).click();
    await page.getByRole('status').filter({ hasText: 'Отправлено' }).waitFor();
    assert.equal(tdmPosts.length, 1);
    tdmDenied = true;
    await page.getByRole('button', { name: 'Отправить текст', exact: true }).click();
    await page.locator('.home-page').waitFor();
    assert.equal(tdmPosts.length, 2, 'TDM action was automatically retried');
    assert.equal(await page.locator('.auth-account').innerText(), 'analyst\nПросмотр');
    logoutFails = true;
    await page.getByRole('button', { name: 'Выйти', exact: true }).click();
    await page.getByRole('button', { name: 'Повторить выход', exact: true }).waitFor();
    assert.equal(await page.locator('.app-shell').count(), 0, 'failed logout revealed private content');
    assert.equal(await page.getByRole('button', { name: 'Войти', exact: true }).count(), 0, 'login raced unfinished logout');
    logoutFails = false;
    await page.getByRole('button', { name: 'Повторить выход', exact: true }).click();
    await page.getByRole('heading', { name: 'Вход', exact: true }).waitFor();
    assert.equal(logoutCalls, 2);
    await login('analyst');
    privateExpired = true;
    await page.goto(`${base}/`);
    await page.getByRole('heading', { name: 'Вход', exact: true }).waitFor();
    assert.equal(await page.locator('.app-shell').count(), 0, 'private/session 401 retained protected content');
    sessionStatus = 500;
    await page.goto(`${base}/home`);
    await page.getByRole('button', { name: 'Повторить проверку', exact: true }).waitFor();
    assert.equal(await page.locator('.app-shell').count(), 0);
    sessionStatus = 200; session = { enabled: true, user: { username: 'viewer', role: 'viewer' }, csrfToken: 'timeout-fixture' };
    await page.getByRole('button', { name: 'Повторить проверку', exact: true }).click();
    await page.locator('.home-page').waitFor();
    holdSession = new Promise(resolve => { releaseSession = resolve; });
    const beforeTimeout = sessionGets;
    const pendingSession = new Promise(resolve => { onSession = resolve; });
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await pendingSession;
    assert.ok(sessionGets > beforeTimeout);
    await page.clock.fastForward(15010);
    await page.getByRole('button', { name: 'Повторить проверку', exact: true }).waitFor();
    assert.equal(await page.locator('.app-shell').count(), 0, 'timed-out validation left stale viewer content');
    holdSession = undefined; releaseSession();
    sessionStatus = 200; session = { enabled: false, user: null, csrfToken: null };
    await page.getByRole('button', { name: 'Повторить проверку', exact: true }).click();
    await page.locator('.home-page').waitFor();
    assert.ok(await page.locator('a[href="/tdm"]').count() > 0, 'auth-disabled local mode lost analyst compatibility');
    assert.equal(await page.locator('.auth-account').count(), 0);
    assert.equal(snapshots, 0);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ output, sessionGets, loginRequests: loginBodies.length, logoutCalls, tdmPosts: tdmPosts.length, checks: 'session-before-mount, secure-cookie HTTP blocks inputs before typing, keyboard/password/error/pending/HTTPS426, mobile 320/375/768/1440, viewer nav/direct routes/profile, cookies/Origin/CSRF, same-user validation, role remount, 60s+focus expiry, demonstration portal cleanup, TDM no replay, logout failure/retry, private/session 401, session error/retry/timeout, disabled legacy mode' }));
  } finally {
    if (browser) await browser.close();
    if (server && server.exitCode === null && server.signalCode === null) { const stopped = once(server, 'exit'); server.kill(); await stopped; console.log(`Private Vite stopped: ${server.pid}`); }
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
