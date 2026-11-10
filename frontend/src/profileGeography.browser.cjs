const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');
const fixtures = require('./demoFixtures.cjs');

const fundKey = '\u0444\u043e\u043d\u0434 \u0440\u0435\u043d\u043e\u0432\u0430\u0446\u0438\u0438';
const fundName = fundKey.toLocaleUpperCase('ru-RU');
const geographyTitle = '\u0413\u0435\u043e\u0433\u0440\u0430\u0444\u0438\u044f \u0432\u0432\u043e\u0434\u0430 \u0436\u0438\u043b\u043e\u0439 \u043f\u043b\u043e\u0449\u0430\u0434\u0438';
const companies = [{ developerKey: fundKey, developer: fundName, regions: ['msk'] },
  { developerKey: 'other-developer', developer: 'Other Developer', regions: ['msk'] }];
const source = JSON.parse(fs.readFileSync(path.join(__dirname, '../public/profile-snapshot.json'), 'utf8'));
const metadata = { ...source, version: 'profile-geography-test-v1', controls: { ...source.controls, frozen: false, developers: companies }, profiles: [] };
function response(address, role) {
  const url = new URL(address);
  if (url.pathname === '/api/v1/auth/session') return { enabled: true, user: { username: `test-${role}`, role }, csrfToken: 'test-csrf', secureCookie: false };
  if (url.pathname === '/api/v1/catalog') return metadata;
  if (url.pathname === '/api/v1/profile') {
    const company = companies.find(company => company.developerKey === url.searchParams.get('developer'));
    assert.ok(company, 'Unknown fixture company');
    // Deliberately include an old regional comparison to verify the compatibility guard.
    const profile = { ...structuredClone(source.profiles[0]), ...company, region: 'msk',
      housingComparison: [{ id: 'all', title: 'Regional comparison', segments: [
        { key: 'msk', valueM2: 100 }, { key: 'otherRf', valueM2: 200 }], totalM2: 300 }] };
    return { ...metadata, profiles: [profile] };
  }
  return fixtures.response(address);
}
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function main() {
  let child, browser;
  const output = path.join(os.tmpdir(), 'dashboard-profile-geography'), errors = [], checks = [];
  fs.mkdirSync(output, { recursive: true });
  try {
    const socket = net.createServer();
    await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
    const port = socket.address().port;
    await new Promise(resolve => socket.close(resolve));
    const base = `http://127.0.0.1:${port}`, cwd = path.resolve(__dirname, '..');
    child = spawn(process.execPath, [path.join(cwd, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'], { cwd, windowsHide: true, stdio: 'ignore' });
    let ready = false;
    for (let i = 0; i < 120; i++) {
      try { if ((await fetch(base)).ok) { ready = true; break; } } catch {}
      if (child.exitCode !== null) break;
      await pause(250);
    }
    assert.ok(ready, 'Private Vite did not start');
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    for (const role of ['analyst', 'viewer']) {
      const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
      await context.route('**/api/**', route => route.fulfill({ json: response(route.request().url(), role) }));
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      let commissioning;
      for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
        await page.setViewportSize(viewport);
        for (const company of companies) {
          await page.goto(`${base}/?${new URLSearchParams({ developer: company.developerKey, region: 'msk' })}`);
          await page.locator('.developer-heading h2').filter({ hasText: company.developer }).waitFor();
          assert.equal(await page.getByRole('heading', { name: geographyTitle, exact: true }).count(), company.developerKey === fundKey ? 0 : 1);
          const canvas = page.locator('#dynamics .chart-canvas').first();
          await canvas.locator('canvas').waitFor();
          const values = await canvas.evaluate(async element => {
            const resource = performance.getEntriesByType('resource').find(entry => /echarts_core\.js/.test(entry.name));
            const echarts = await import(resource.name), chart = echarts.getInstanceByDom(element);
            return chart.getOption().series.map(series => series.data);
          });
          assert.ok(values.some(series => series.some(value => typeof value === 'number' && value > 0)));
          if (commissioning) assert.deepEqual(values, commissioning, 'Geography exclusion changed the annual chart');
          else commissioning = values;
          await page.screenshot({ path: path.join(output, `${role}-${company.developerKey === fundKey ? 'fund' : 'other'}-${viewport.width}.png`) });
          checks.push({ role, developer: company.developerKey, width: viewport.width });
        }
      }
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(base + '/demo');
      const section = page.locator('[data-demo-source="profile"]');
      await section.locator('[data-demo-chart="profile/annual"]').waitFor();
      assert.equal(await section.locator('[data-demo-chart^="profile/geography/"]').count(), 0);
      const selector = section.getByRole('combobox');
      await selector.fill('Other');
      await section.getByRole('option', { name: 'Other Developer', exact: true }).click();
      await section.locator('[data-demo-chart^="profile/geography/"]').waitFor();
      await section.locator('.demo-source-selection input').check();
      await selector.fill(fundName);
      await section.getByRole('option', { name: fundName, exact: true }).click();
      await page.waitForFunction(name => {
        const node = document.querySelector('[data-demo-source="profile"]');
        return node.getAttribute('aria-busy') === 'false' && node.querySelector('.demo-context')?.textContent.includes(name);
      }, fundName);
      assert.equal(await section.locator('[data-demo-chart^="profile/geography/"]').count(), 0);
      assert.ok(await section.locator('[data-demo-chart="profile/annual"]').count());
      checks.push({ role, demoProfileSwitch: true });
      await context.close();
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ checks, errors }, null, 2));
    console.log(JSON.stringify({ passed: checks.length, output, errors }));
  } finally {
    await browser?.close();
    if (child && child.exitCode === null) { child.kill(); await new Promise(resolve => child.once('exit', resolve)); }
  }
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { main };
