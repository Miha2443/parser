const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { spawn } = require('node:child_process');
const { chromium } = require(process.argv[2] || 'playwright');
const fixtures = require('./demoFixtures.cjs');

const root = path.resolve(__dirname, '../..');
const base = process.env.DASHBOARD_TEST_URL || 'http://127.0.0.1:5180';
const output = path.join(root, 'outputs/excel-export-browser');
const pause = ms => new Promise(r => setTimeout(r, ms));
async function freePort() {
  const s = net.createServer(); await new Promise(r => s.listen(0, '127.0.0.1', r));
  const port = s.address().port; await new Promise(r => s.close(r)); return port;
}
async function main() {
  fs.mkdirSync(output, { recursive: true });
  const port = await freePort(), api = `http://127.0.0.1:${port}`;
  const server = spawn(process.argv[3] || 'python', ['-X', 'utf8', '-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', String(port)], { cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let logs = '', browser, failures = 0, role = 'analyst';
  const checks = [], requests = [], errors = [];
  for (const stream of [server.stdout, server.stderr]) stream.on('data', b => { logs = (logs+b).slice(-12000); });
  try {
    for(let i=0;i<100;i++) {
      if(server.exitCode!==null)throw new Error(logs);
      try {if((await fetch(api+'/docs')).ok)break;} catch {}
      if(i===99)throw new Error('API startup timeout '+logs);
      await pause(200);
    }
    browser = await chromium.launch({ headless: true, channel: 'chrome' });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true, reducedMotion: 'reduce' });
    page.on('pageerror', e=>errors.push(String(e)));
    await page.route('**/api/**', async route=> {
      const req = route.request(), u = new URL(req.url());
      if(u.pathname==='/api/v1/auth/session')return route.fulfill({json:{enabled:true,user:{username:role,role},csrfToken:'export-browser-test'}});
      if(u.pathname==='/api/v1/exports/table') {
        const rows = req.postDataJSON().rows; requests.push(rows);
        assert.equal(req.headers()['x-csrf-token'],'export-browser-test');
        if(failures) {failures--;return route.fulfill({status:413,json:{detail:'Table too large'}});}
        const response=await page.request.post(api+u.pathname,{data:{rows}});
        assert.equal(response.status(),200,await response.text());
        return route.fulfill({status:200,body:await response.body(),headers:{'content-type':response.headers()['content-type']}});
      }
      if(u.pathname.startsWith('/api/v1/construction/permits')) {
        const response=await page.request.get(api+u.pathname+u.search);
        return route.fulfill({status:response.status(),body:await response.body(),headers:{'content-type':response.headers()['content-type']}});
      }
      try{return route.fulfill({json:fixtures.response(req.url())});}
      catch(e){return route.fulfill({status:500,json:{detail:String(e)}});}
    });
    async function fits(label) {
      const values=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth+1,
        buttons:[...document.querySelectorAll('.export-actions button')].filter(e=>{const r=e.getBoundingClientRect();return r.width&&r.height;}).map(e=>{const r=e.getBoundingClientRect();return {text:e.textContent,left:r.left,right:r.right,clipped:e.scrollWidth>e.clientWidth+2};})}));
      assert.equal(values.overflow,false,label+' page overflow');
      assert.ok(values.buttons.every(b=>b.left>=0&&b.right<=page.viewportSize().width+1&&!b.clipped),JSON.stringify(values));
      await page.screenshot({path:path.join(output,label+'.png'),fullPage:false});
      checks.push(label);
    }
    async function download(button, expected, name) {
      const pending=page.waitForEvent('download');await button.click();const result=await pending;
      assert.ok(result.suggestedFilename().endsWith(expected),result.suggestedFilename());
      const target=path.join(output,name+expected);await result.saveAs(target);
      const bytes=fs.readFileSync(target);
      if(expected==='.xlsx')assert.equal(bytes.subarray(0,2).toString(),'PK');
      if(expected==='.png')assert.equal(bytes.subarray(1,4).toString(),'PNG');
      return target;
    }
    for(const [route,id] of [['/economics/ipc','ipc'],['/economics/mortgage','mortgage'],['/construction/permits','permits']]) {
      await page.goto(base+route);
      await page.locator('.chart-canvas canvas').first().waitFor({timeout:120000});
      await page.locator('.data-toggle').first().click();
      const panel=page.locator('.chart-data-panel').first(),actions=panel.locator('.export-actions');
      assert.equal(await actions.getByRole('button',{name:'CSV',exact:true}).count(),1);
      assert.equal(await actions.getByRole('button',{name:'PNG',exact:true}).count(),1);
      assert.equal(await actions.getByRole('button',{name:/Скачать Excel/}).count(),1);
      for(const width of [1440,375]) {
        await page.setViewportSize({width,height:1000});
        if(width<960) {
          if(await page.locator('.sidebar').evaluate(el=>el.classList.contains('is-open')))await page.locator('.mobile-close').click();
          await page.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right<=1);
        }
        await actions.scrollIntoViewIfNeeded();await fits(id+'-'+width);
      }
      await download(actions.getByRole('button',{name:/Скачать Excel/}),'.xlsx',id);
      if(id==='ipc') {
        await download(actions.getByRole('button',{name:'CSV',exact:true}),'.csv','ipc');
        await download(actions.getByRole('button',{name:'PNG',exact:true}),'.png','ipc');
        failures=1;await actions.getByRole('button',{name:/Скачать Excel/}).click();
        await actions.getByRole('alert').waitFor();await fits('ipc-error-375');
        await download(actions.getByRole('button',{name:/Скачать Excel/}),'.xlsx','ipc-retry');
      }
    }
    role='viewer';await page.reload();await page.locator('.chart-canvas canvas').first().waitFor({timeout:120000});
    assert.equal(await page.locator('.data-toggle').count(),0);
    assert.equal(await page.getByRole('button',{name:/Скачать.*Excel/}).count(),0);
    checks.push('viewer-no-downloads');
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,'requests.json'),JSON.stringify(requests));
    fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({checks,errors},null,2));
    console.log(JSON.stringify({checks,output},null,2));
  } finally {
    await browser?.close();server.kill();await new Promise(r=>server.exitCode===null?server.once('exit',r):r());
  }
}
main().catch(e=>{console.error(e);process.exitCode=1;});
