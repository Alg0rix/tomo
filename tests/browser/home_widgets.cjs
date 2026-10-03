/* NODE_PATH=<Playwright installation>/node_modules node tests/browser/home_widgets.cjs */
const assert = require('node:assert/strict');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');
const html = execFileSync(path.join(root, '.venv/bin/python'), ['-c', `
from jinja2 import Environment, FileSystemLoader
from types import SimpleNamespace
from app.plugins.icons import ICONS
env = Environment(loader=FileSystemLoader('app/templates'), autoescape=True)
env.globals.update(plugin_icons=ICONS, url_for=lambda name, **kw: '/static/'+kw.get('path', ''), avatar_color=lambda *args: '#777', eval_ui_enabled=False)
print(env.get_template('index.html').render(brand='Tomo', page='home', static_ver='test', app_version='test', request=SimpleNamespace(session={'username':'test'}), plugin_nav=[], current_username='test', agents=[]))
`], {cwd: root, encoding: 'utf8'}).replace(/<script\b(?![^>]*type="application\/json")[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
(async () => {
 const browser = await chromium.launch({headless: true});
 try {
  for (const width of [390, 1280]) {
   const page = await browser.newPage({viewport: {width, height: 1000}});
   const errors = []; page.on('pageerror', e => errors.push(e.message));
   await page.route('http://home.test/**', route => route.fulfill({contentType: 'text/html', body: html}));
   async function load(layout = {order: [], hidden: []}, autoRefresh = false) {
    await page.goto('http://home.test/');
    await page.addStyleTag({path: path.join(root, 'app/static/css/tomo.css')});
    await page.addStyleTag({path: path.join(root, 'app/static/css/home.css')});
    if (autoRefresh) await page.clock.install();
    await page.evaluate(({layout, autoRefresh}) => {
     window.saved = layout; window.toasts = []; window.refreshRequests = []; window.refreshFail = false;
     window.Tomo = {escapeHtml: s => String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])), avatarColor: () => '#777', toast: msg => toasts.push(msg),
      api: async (url, options) => {
       if (url === '/api/home/layout') { window.saved = JSON.parse(options.body); return saved; }
       if (url.startsWith('/api/home/cards?')) {
        window.refreshRequests.push(new URLSearchParams(url.split('?')[1]).getAll('keys'));
        return {cards: [{key:'money:spending', ...(window.refreshFail ? {error:'Timed out'} : {data:{metric:{value:'Rp2jt'},actions:[{label:'Review',prompt:'Updated spending prompt'}]}})}]};
       }
       if (url.startsWith('/api/home?')) return {layout: saved, rooms: [
        {key:'money:spending', refresh_seconds:autoRefresh ? 5 : null, plugin:'money', plugin_name:'Money', title:'Spending', size:'s', default_visible:true, data:{status:{text:'On track',tone:'ok'},metric:{value:'Rp1.2jt',label:'spent this month'},caption:'14 transactions · October',chart:[{label:'Mon',value:120},{label:'Tue',value:80},{label:'Wed',value:190},{label:'Thu',value:100},{label:'Fri',value:160},{label:'Sat',value:240},{label:'Sun',value:140}],actions:[{label:'Review spending',prompt:'Break down my spending.'}]}},
        {key:'money:budgets', refresh_seconds:autoRefresh ? 5 : null, plugin:'money', plugin_name:'Money', title:'Budgets', size:'m', default_visible:false, data:{caption:'Budget breakdown'}},
        {key:'core:memory', core:'memory', title:'Memory', size:'s', data:{metric:{value:'+12',label:'facts this week'},caption:'A little more familiar every day',list:[{label:'Your morning routine',value:'Updated'},{label:'Current project',value:'Tomo'},{label:'Favourite workspace',value:'Home'}]}}]};
       return {};
      }};
    }, {layout, autoRefresh});
    await page.addScriptTag({path: path.join(root, 'app/static/js/home.js')});
    await page.waitForSelector('[data-key="money:spending"]');
   }
   await load();
   assert.equal(await page.locator('[data-key="money:budgets"]').count(), 0);
   await page.click('#homeAddTile');
   await page.click('[data-add-widget="money:budgets"]');
   await page.click('#homePickerClose');
   await page.click('#homeArrange');
   const spending = page.locator('[data-key="money:spending"]');
   if (width > 720) {
    await spending.locator('[data-width="l"]').click();
    await spending.locator('[data-height-cycle]').click();
   } else {
    for (const sel of ['[data-width="l"]', '[data-height-cycle]', '[data-resize]']) assert(!(await spending.locator(sel).first().isVisible()), sel + ' hidden on phone');
    await spending.locator('[data-move="1"]').click();
   }
   if (width > 720) await spending.locator('[data-move="1"]').click();
   await page.locator('[data-key="money:budgets"] [data-remove]').click();
   assert.equal(await page.locator('[data-key="money:budgets"]').count(), 0);
   await page.waitForTimeout(400);
   const saved = await page.evaluate(() => window.saved);
   if (width > 720) assert.deepEqual(saved.sizes['money:spending'], {width:'l', height:240});
   assert(!saved.selected.includes('money:budgets'));
   assert.match(await page.locator('#homeEditMsg').innerText(), /Removed Budgets/);
   await page.locator('#homeEditMsg .home-undo').click();
   assert.equal(await page.locator('[data-key="money:budgets"]').count(), 1);
   await page.locator('[data-key="money:budgets"] [data-remove]').click();
   assert.equal(saved.order[0], 'money:budgets');
   await load(saved);
   assert.equal(await page.locator('[data-key="money:budgets"]').count(), 0);
   if (width > 720) assert(await page.locator('[data-key="money:spending"]').evaluate(el => el.classList.contains('l')));
   await page.click('#homeArrange');
   if (width > 720) {
    const handle = page.locator('[data-key="money:spending"] [data-resize]');
    await handle.scrollIntoViewIfNeeded();
    const rect = await handle.boundingBox();
    await page.mouse.move(rect.x+10, rect.y+10); await page.mouse.down();
    await page.mouse.move(rect.x-100, rect.y+90); await page.mouse.up();
    await page.waitForTimeout(400);
    assert((await page.evaluate(() => window.saved.sizes['money:spending'].height)) > 240);
   }
   {
    const order = () => page.evaluate(() => [...document.querySelectorAll('#homeRooms .home-room[data-key]')].map(e => e.dataset.key));
    const before = await order();
    await page.locator(`[data-key="${before[0]}"]`).evaluate(el => el.scrollIntoView({block: 'start'})); await page.evaluate(() => scrollBy(0, -60));
    const grip = await page.locator(`[data-key="${before[0]}"] [data-drag]`).boundingBox();
    const target = await page.locator(`[data-key="${before[1]}"]`).boundingBox();
    await page.mouse.move(grip.x+8, grip.y+8); await page.mouse.down();
    await page.mouse.move(target.x+target.width/2, target.y+target.height*0.8, {steps: 8}); await page.mouse.up();
    await page.waitForTimeout(400);
    const after = await order();
    assert.notDeepEqual(after, before);
    assert.deepEqual(await page.evaluate(() => window.saved.order), after);
   }
   await page.locator('[data-key="money:spending"] [data-remove]').click();
   await page.click('#homeAddTile'); await page.click('[data-add-widget="money:spending"]');
   assert.match(await page.locator('#homePickerCount').innerText(), /widgets? on Home/);
   await page.click('#homePickerClose');
   if (width > 720) assert(await page.locator('[data-key="money:spending"]').evaluate(el => el.classList.contains('l')));
   await page.click('#homeResetLayout');
   assert.match(await page.locator('#homeResetLayout').innerText(), /Reset everything/);
   await page.waitForTimeout(100);
   assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
   if (process.env.SCREENSHOT_DIR && width > 720) {
    await page.click('#homeEditDone');
    await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR, 'desktop-full.png'), fullPage:true});
    await page.locator('section[aria-labelledby="roomsTitle"]').screenshot({path:path.join(process.env.SCREENSHOT_DIR, 'dashboard.png')});
    await page.click('#homeArrange');
    await page.locator('section[aria-labelledby="roomsTitle"]').screenshot({path:path.join(process.env.SCREENSHOT_DIR, 'dashboard-arrange.png')});
    await page.click('#homeAddTile');
    await page.locator('#homeWidgetPicker').screenshot({path:path.join(process.env.SCREENSHOT_DIR, 'widget-picker.png')});
    await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR, 'desktop-drawer.png')});
   }
   assert.deepEqual(errors, []); assert.deepEqual(await page.evaluate(() => toasts), []);
   if (width > 720) {
    await load({order:['core:memory','money:spending'], hidden:[], sizes:{'money:spending':{width:'l',height:320}}}, true);
    await page.evaluate(() => window.originalCard = document.querySelector('[data-key="money:spending"]'));
    await page.clock.runFor(6000);
    assert.deepEqual(await page.evaluate(() => refreshRequests), [['money:spending']]);
    assert.equal(await page.locator('[data-key="money:spending"] .home-metric').innerText(), 'Rp2jt');
    assert(await page.evaluate(() => originalCard === document.querySelector('[data-key="money:spending"]') && originalCard.style.height === '320px' && originalCard.classList.contains('l')));
    assert.equal(await page.locator('#homeRooms [data-key]').first().getAttribute('data-key'), 'core:memory');
    await page.locator('[data-key="money:spending"] [data-prompt-i]').click();
    assert.equal(await page.locator('#homeChatInput').inputValue(), 'Updated spending prompt');
    await page.evaluate(() => window.refreshFail = true);
    await page.clock.runFor(6000);
    assert.equal(await page.locator('[data-key="money:spending"] .home-metric').innerText(), 'Rp2jt');
    assert.equal(await page.locator('[data-key="money:spending"] .home-status').textContent(), 'Stale');
    await page.evaluate(() => { window.refreshFail = false; Object.defineProperty(document, 'hidden', {configurable:true, value:true}); });
    const paused = await page.evaluate(() => refreshRequests.length);
    await page.clock.runFor(10000);
    assert.equal(await page.evaluate(() => refreshRequests.length), paused);
    await page.evaluate(() => { Object.defineProperty(document, 'hidden', {configurable:true, value:false}); document.dispatchEvent(new Event('visibilitychange')); });
    await page.waitForFunction(() => !document.querySelector('[data-key="money:spending"] .home-status'));
    assert.equal(await page.evaluate(() => refreshRequests.length), paused + 1);
    await page.click('#homeArrange');
    const arranging = await page.evaluate(() => refreshRequests.length);
    await page.clock.runFor(6000);
    assert.equal(await page.evaluate(() => refreshRequests.length), arranging);
    await page.click('#homeEditDone');
    await page.waitForFunction(n => refreshRequests.length === n + 1, arranging);
    assert.deepEqual(errors, []);
   }
   await page.close();
  }
  console.log('Home widget selection, removal/restoration, ordering, resizing, reload, and periodic refresh passed at desktop and phone widths.');
 } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
