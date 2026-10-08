const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');

const base = process.env.DASHBOARD_TEST_URL || 'http://localhost:5173';
const output = path.join(process.env.TEMP || '.', 'dashboard-map-checks');
fs.mkdirSync(output, { recursive: true });
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const checks = [];
  try {
    const context = await browser.newContext({ acceptDownloads: true });
    // Never drive the public OSM service with automated pan/zoom requests.
    const tilePage = await context.newPage(); await tilePage.setViewportSize({ width: 256, height: 256 });
    await tilePage.setContent('<style>body{margin:0}</style><svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><rect width="256" height="256" fill="#e5e9ed"/><path d="M0 64H256M0 160H256M60 0V256M180 0V256" stroke="#ffffff" stroke-width="8"/><path d="M0 230L256 80" stroke="#96bbc9" stroke-width="12"/></svg>');
    const tile = await tilePage.screenshot(); await tilePage.close();
    await context.route('https://tile.openstreetmap.org/**', route => route.fulfill({ contentType: 'image/png', body: tile }));
    const page = await context.newPage();
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    for (const theme of ['dark', 'light']) for (const width of [360, 768, 1280, 1920]) {
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript(theme => localStorage.setItem('dashboard.theme', theme), theme);
      await page.goto(`${base}/map`);
      await page.waitForSelector('.map-canvas canvas', { timeout: 60000 });
      await page.waitForSelector('.map-cluster-label', { timeout: 30000 });
      await page.waitForTimeout(300);
      const metrics = await page.evaluate(() => {
        const canvas = document.querySelector('.map-canvas canvas'), box = canvas.getBoundingClientRect();
        return { width: box.width, height: box.height, overflow: document.documentElement.scrollWidth - innerWidth,
                 clusterCount: document.querySelectorAll('.map-cluster-label').length,
                 clipped: [...document.querySelectorAll('.map-canvas')].some(el => el.scrollHeight > el.clientHeight + 1),
                 attribution: document.querySelector('.maplibregl-ctrl-attrib').textContent };
      });
      assert.ok(metrics.width > 250 && metrics.height >= 360); assert.ok(metrics.overflow <= 1); assert.equal(metrics.clipped, false); assert.ok(metrics.attribution.includes('OpenStreetMap'));
      assert.equal(await page.locator('.map-warning').count(), 0);
      checks.push({ theme, width, ...metrics });
      await page.screenshot({ path: path.join(output, `map-${theme}-${width}.png`), fullPage: true });
    }
    await page.getByRole('button', { name: 'Показать объект', exact: false }).first().click();
    await page.waitForSelector('.map-object'); assert.ok(page.url().includes('object='));
    await page.getByRole('button', { name: 'Zoom in', exact: true }).click();
    await page.getByRole('button', { name: 'Показать все объекты', exact: true }).click();
    const filtered = page.waitForResponse(response => response.url().includes('/api/v1/map?') && response.url().includes('quality=approximate') && response.status() === 200);
    await page.getByLabel('Качество координат').selectOption('approximate');
    await filtered;
    await page.waitForSelector('.map-canvas canvas');
    const download = page.waitForEvent('download'); await page.getByRole('button', { name: 'Скачать реестр Excel', exact: true }).click();
    assert.ok((await download).suggestedFilename().endsWith('.xlsx'));
    const sourceCatalog = await (await page.request.get(`${base}/api/v1/map/catalog`)).json();
    const sourceReport = await (await page.request.get(`${base}/api/v1/map`)).json();
    let catalogRequests = 0; const recoveryRequests = [];
    await page.route('**/api/v1/map/catalog', route => route.fulfill({ json: {
      ...sourceCatalog, developers: [++catalogRequests === 1 ? 'A' : 'B'],
    } }));
    await page.route(/\/api\/v1\/map(?:\?|$)/, route => {
      const developer = new URL(route.request().url()).searchParams.get('developer'); recoveryRequests.push(developer);
      return developer === 'A' ? route.fulfill({ status: 404, json: { detail: 'Developer unavailable' } }) : route.fulfill({ json: sourceReport });
    });
    await page.goto(`${base}/map?developer=A&object=rv:gone&page=2`);
    await page.locator('.load-state[role="alert"]').waitFor();
    await page.getByRole('button', { name: 'Повторить', exact: true }).click();
    await page.waitForURL(url => !url.searchParams.has('developer') && !url.searchParams.has('object') && !url.searchParams.has('page'));
    await page.waitForSelector('.map-canvas canvas');
    assert.equal(await page.getByLabel('Застройщик карты').inputValue(), '');
    assert.deepEqual(recoveryRequests, ['A', null]);
    assert.equal(catalogRequests, 2);
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ checks, errors, recoveryRequests }, null, 2));
    console.log(JSON.stringify({ layouts: checks.length, errors, output }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
