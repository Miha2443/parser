import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { registerHooks } from 'node:module';
import { runInNewContext } from 'node:vm';
import { after, test } from 'node:test';
import ts from 'typescript';
import { createElement as h } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const root = new URL('./', import.meta.url).href;
const hooks = registerHooks({
  resolve(specifier, context, nextResolve) {
    if (context.parentURL?.startsWith(root) && /^\.\.?\/[^.]+$/.test(specifier)) {
      const extension = ['.ts', '.tsx'].find(extension => existsSync(new URL(`${specifier}${extension}`, context.parentURL)));
      if (extension) return nextResolve(`${specifier}${extension}`, context);
    }
    if (/^echarts\/(core|charts|components|renderers)$/.test(specifier)) return nextResolve(`${specifier}.js`, context);
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.endsWith('.css')) return { format: 'module', shortCircuit: true, source: '' };
    if (url.endsWith('.tsx') || url === new URL('./useDashboardData.ts', import.meta.url).href) return { format: 'module', shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX }, fileName: url, transformers: { before: [context => source => ts.visitNode(source, function visit(node) {
      if (ts.isPropertyAccessExpression(node) && node.name.text === 'env' && ts.isMetaProperty(node.expression)) return ts.factory.createObjectLiteralExpression();
      return ts.visitEachChild(node, visit, context);
    })] } }).outputText };
    return nextLoad(url, context);
  },
});
after(() => hooks.deregister());
const { AnalystOnly, AuthAccount, AuthContext, AuthGate, AuthProvider, analystRoute, readAuthSession, useAccess } = await import('./auth.tsx');
const { AUTH_EXPIRED_EVENT, ApiError, loadCatalog, request, setRequestAccess } = await import('./data.ts');
const access = role => ({ enabled: true, user: { username: 'test-user', role }, canAnalyze: role === 'analyst', logout: async () => {}, csrfToken: 'fixture-csrf' });
const renderAs = (role, node) => renderToStaticMarkup(h(AuthContext.Provider, { value: access(role) }, node));
function mockGlobal(t, key, value) {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, key);
  Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  t.after(() => { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; });
}

test('unwrapped legacy components keep analyst access without an authenticated user', () => {
  let value;
  function Probe() { value = useAccess(); return h(AnalystOnly, null, 'legacy controls'); }
  assert.equal(renderToStaticMarkup(h(Probe)), 'legacy controls');
  assert.equal(value.enabled, false); assert.equal(value.user, null); assert.equal(value.canAnalyze, true);
  assert.equal(renderToStaticMarkup(h(AuthGate, null, 'legacy content')), 'legacy content');
});

test('viewer SSR actually omits analyst children, home links and analyst service pages', async () => {
  const { Home, Tdm, Updates } = await import('./ServicePages.tsx');
  function ForbiddenChild() { throw new Error('Viewer mounted analyst content'); }
  assert.equal(renderAs('viewer', h(AnalystOnly, null, h(ForbiddenChild))), '');
  assert.equal(renderAs('viewer', h(Tdm)), '');
  assert.equal(renderAs('viewer', h(Updates, { query: '', change() {} })), '');
  const viewer = renderAs('viewer', h(Home, { navigate() {} }));
  assert.match(viewer, /href="\/demo"/); assert.match(viewer, /href="\/construction\/permits"/);
  for (const path of ['/updates', '/tdm', '/service/constant-prices']) assert.ok(!viewer.includes(`href="${path}"`));
  const analyst = renderAs('analyst', h(Home, { navigate() {} }));
  for (const path of ['/updates', '/tdm', '/service/constant-prices']) assert.ok(analyst.includes(`href="${path}"`));
  assert.match(renderAs('viewer', h(AuthAccount)), /test-user/);
  assert.match(renderAs('viewer', h(AuthAccount)), /aria-label="Выйти"/);
});

test('sidebar omits restricted viewer links even with the service group expanded', async t => {
  mockGlobal(t, 'localStorage', { getItem: () => '{"service":true,"market":true}' });
  const { default: Sidebar } = await import('./Sidebar.tsx');
  const props = { open: true, onClose() {}, compact: false, onCompact() {}, path: '/home', onNavigate() {} };
  const html = renderAs('viewer', h(Sidebar, props));
  assert.match(html, /href="\/demo"/);
  for (const path of ['/updates', '/tdm', '/service/constant-prices']) assert.ok(!html.includes(`href="${path}"`));
});

test('AuthGate does not mount or fetch protected content before session discovery, including App SSR', async t => {
  let mounted = 0, calls = 0;
  t.mock.method(globalThis, 'fetch', async () => { calls++; throw new Error('Unexpected fetch in SSR'); });
  function Protected() { mounted++; return h('div', null, 'private data'); }
  const html = renderToStaticMarkup(h(AuthProvider, null, h(AuthGate, null, h(Protected))));
  assert.match(html, /Проверка сессии/); assert.doesNotMatch(html, /private data/);
  const { default: App } = await import('./App.tsx');
  assert.match(renderToStaticMarkup(h(App)), /Проверка сессии/);
  assert.equal(mounted, 0); assert.equal(calls, 0);
});

test('session reader validates the exact role contract and fails closed for malformed payloads', () => {
  const session = { enabled: true, user: { username: 'reader', role: 'viewer' }, csrfToken: 'token' };
  assert.deepEqual(readAuthSession(session), session);
  for (const secureCookie of [true, false]) assert.deepEqual(readAuthSession({ ...session, secureCookie }), { ...session, secureCookie });
  assert.equal(Object.hasOwn(readAuthSession(session), 'secureCookie'), false);
  assert.deepEqual(readAuthSession({ enabled: false, user: null, csrfToken: null }), { enabled: false, user: null, csrfToken: null });
  for (const value of [null, {}, { ...session, enabled: 0 }, { ...session, csrfToken: undefined }, { ...session, secureCookie: 'false' }, { ...session, user: { username: 'x', role: 'admin' } }, { ...session, user: { username: '', role: 'analyst' } }]) assert.throws(() => readAuthSession(value));
  for (const path of ['/tdm', '/tdm/', '/updates', '/service/constant-prices']) assert.equal(analystRoute(path), true);
  for (const path of ['/', '/home', '/demo', '/construction/permits']) assert.equal(analystRoute(path), false);
});

test('central request sends same-origin cookies and current CSRF on every unsafe private method only', async t => {
  let token = 'first';
  const restore = setRequestAccess(() => ({ enabled: true, csrfToken: token })), calls = [], controller = new AbortController();
  t.mock.method(globalThis, 'fetch', async (url, init) => { calls.push([url, init]); return new Response('{}'); });
  try {
    for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) await request('/api/v1/tdm/send', controller.signal, { method, body: 'fixture', headers: { 'X-TDM-Token': 'tdm' } });
    token = 'rotated'; await request('/api/v1/tdm/upload', controller.signal, { method: 'POST' });
    for (const url of ['/api/v1/profile', '/api/v1/auth/login', '/public.json', 'https://example.invalid/api/v1/private']) await request(url, controller.signal, { method: url === '/api/v1/profile' ? 'GET' : 'POST' });
    for (const [, init] of calls) { assert.equal(init.credentials, 'same-origin'); assert.equal(init.signal, controller.signal); assert.equal(init.cache, 'no-store'); }
    for (const [, init] of calls.slice(0, 4)) { assert.equal(init.headers.get('X-CSRF-Token'), 'first'); assert.equal(init.headers.get('X-TDM-Token'), 'tdm'); assert.equal(init.body, 'fixture'); }
    assert.equal(calls[4][1].headers.get('X-CSRF-Token'), 'rotated');
    assert.ok(calls.slice(5).every(([, init]) => !init.headers.has('X-CSRF-Token')));
  } finally { restore(); }
});

test('private 401/403 emits auth-expired once per failed request without auth endpoint recursion or retries', async t => {
  const target = new EventTarget(), events = [], calls = [];
  mockGlobal(t, 'window', target);
  target.addEventListener(AUTH_EXPIRED_EVENT, event => events.push(event.detail));
  let status = 401;
  t.mock.method(globalThis, 'fetch', async url => { calls.push(url); return new Response('{}', { status }); });
  const signal = new AbortController().signal;
  for (const code of [401, 403]) { status = code; await assert.rejects(request('/api/v1/tdm/send', signal, { method: 'POST' }), ApiError); }
  for (const url of ['/api/v1/auth/session', '/api/v1/auth/login', '/api/v1/auth/logout', '/profile-snapshot.json', 'https://example.invalid/api/v1/profile']) await assert.rejects(request(url, signal), ApiError);
  const aborted = new AbortController(); aborted.abort(); await assert.rejects(request('/api/v1/profile', aborted.signal), ApiError);
  assert.deepEqual(events, [{ url: '/api/v1/tdm/send', status: 401 }, { url: '/api/v1/tdm/send', status: 403 }]);
  assert.equal(calls.length, 8, 'unsafe requests must never be replayed');
});

test('authenticated catalog never requests the public snapshot even in legacy snapshot mode', async t => {
  const { default: fixtures } = await import('./demoFixtures.cjs'), calls = [];
  const restore = setRequestAccess(() => ({ enabled: true, csrfToken: 'token' }));
  t.mock.method(globalThis, 'fetch', async url => { calls.push(url); return Response.json(fixtures.response(url)); });
  try { const catalog = await loadCatalog('snapshot', new AbortController().signal); assert.ok(catalog.developers.length); assert.deepEqual(calls, ['/api/v1/catalog']); assert.equal(catalog.demo, undefined); }
  finally { restore(); }
});

test('legacy browser mock handlers answer session discovery before private API accounting', async () => {
  for (const file of ['annualConstruction.browser.cjs', 'constant-prices.browser.cjs', 'live-api.browser.cjs']) {
    const source = readFileSync(new URL(file, import.meta.url), 'utf8'), tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
    let callback;
    function visit(node) {
      if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression) && node.expression.name.text === 'route' && ts.isStringLiteral(node.arguments[0]) && ['**/api/**', '**/api/v1/**'].includes(node.arguments[0].text)) callback ??= node.arguments[1];
      ts.forEachChild(node, visit);
    }
    visit(tree); assert.ok(callback, file);
    const handler = runInNewContext(`(${callback.getText(tree)})`, { URL });
    let response;
    await handler({ request: () => ({ method: () => 'GET', url: () => 'http://fixture.test/api/v1/auth/session' }), fulfill: value => { response = value; return Promise.resolve(); } });
    assert.deepEqual(JSON.parse(JSON.stringify(response)), { json: { enabled: false, user: null, csrfToken: null } }, file);
  }
});
