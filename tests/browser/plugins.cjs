/* NODE_PATH=<Playwright installation>/node_modules node tests/browser/plugins.cjs */
const assert = require('node:assert/strict');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');
function render(admin = true, empty = false) {
  return execFileSync(process.env.PYTHON || path.join(root, '.venv/bin/python'), ['-c', `
from jinja2 import Environment, FileSystemLoader
from types import SimpleNamespace
from app.plugins.icons import ICONS
env=Environment(loader=FileSystemLoader('app/templates'), autoescape=True)
env.globals.update(plugin_icons=ICONS, url_for=lambda name, **kwargs: '/static/'+kwargs.get('path',''), avatar_color=lambda *args:'#777', eval_ui_enabled=False)
plugins=[dict(id='token_monitor', icon='chart-column', name='Token Monitor', description='Usage heatmap and recent agent activity.', version='0.3', running=True, error=None, source='git', pages=[dict(label='Usage',path='/plugins/token_monitor/')]), dict(id='kanban', icon='columns-3', name='Kanban', description='A board for your project.', version='0.1', running=False, error=None, source='git', pages=[])]
catalog=[dict(id='money', icon='wallet', name='Money', description='Track income and expenses with your agents.', version='0.1.0', author='Tomo', publisher='Tomo Official', marketplace='tomo-official', source=dict(url='https://github.com/Alg0rix/tomo-plugins', ref='main', subdirectory='plugins/money')),dict(id='token_monitor', icon='chart-column', name='Token Monitor', description='Usage heatmap and recent agent activity.', version='0.3', author='Tomo', publisher='Tomo Official', marketplace='tomo-official', source=dict(url='https://github.com/Alg0rix/tomo-plugins', ref='main', subdirectory='plugins/token_monitor')),dict(id='library', icon='library', name='Reading Library', description='Private notes and reading progress.', version='0.1', author='Test author', publisher='Community', marketplace='tomo-community', source=dict(url='https://github.com/example/library',ref='main',subdirectory=''))]
print(env.get_template('plugins.html').render(brand='Tomo', page='plugins', static_ver='test', app_version='test', request=SimpleNamespace(session={'username':'test'}), plugins=[] if ${empty ? 'True' : 'False'} else plugins, catalog=[] if ${empty ? 'True' : 'False'} else catalog, marketplaces=[dict(id='tomo-official',name='Tomo Official')], builder_agents=[dict(id='main',name='Main')], builder_workplaces=[dict(id='local',name='Plugin projects')], plugin_nav=[dict(id='plugins',label='Plugins',path='/extensions',page='plugins',icon='puzzle'),dict(id='token_monitor',label='Token Monitor',path='/plugins/token_monitor/',page='plugin-token_monitor',icon='chart-column')], can_manage_plugins=${admin ? 'True' : 'False'}))
`], {cwd:root, encoding:'utf8'}).replace(/<script\b(?![^>]*type="application\/json")[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
}
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
  for(const width of [390,1280]) {
   const page=await browser.newPage({viewport:{width,height:1000}});const errors=[];
   page.on('pageerror',e=>errors.push(e.message));
   let html=render();
   await page.route('http://plugins.test/**',route=>route.fulfill({contentType:'text/html',body:html}));
   async function load(hash='') {
    await page.goto('http://plugins.test/extensions'+hash);
    await page.addStyleTag({path:path.join(root,'app/static/css/tomo.css')});
    await page.addStyleTag({path:path.join(root,'app/static/css/plugins.css')});
    await page.evaluate(()=>{window.calls=[];window.toasts=[];window.Tomo={api:async(url,options)=>{calls.push({url,options});throw new Error('Test request failed');},toast:msg=>toasts.push(msg)};});
    await page.addScriptTag({path:path.join(root,'app/static/js/plugins.js')});
   }
   await load();
   assert.equal(await page.locator('#hub-discover [data-hub-card]:visible').count(),3);
   assert.equal(await page.locator('#hub-discover [data-plugin-icon=wallet]').count(),2);assert.equal(await page.locator('.app-rail-ico [data-plugin-icon=chart-column]').count(),1);assert.equal(await page.locator('.app-rail-ico [data-plugin-icon=chart-column]').evaluate(e=>getComputedStyle(e).width),'18px');
   assert.equal(await page.locator('#hub-discover [data-catalog-install="token_monitor@tomo-official"]').count(),0,'Already installed entry has no install action');
   await page.locator('#hub-search').fill('library');assert.equal(await page.locator('[data-hub-card]:visible').count(),1);
   await page.locator('#hub-filter').selectOption('tomo-official');assert.equal(await page.locator('[data-hub-empty]:visible').count(),1);
   await page.locator('#hub-search').fill('');await page.locator('#hub-filter').selectOption('all');
   await page.locator('#hub-refresh').click();assert.match(await page.locator('#hub-feedback').textContent(),/Tomo Official: Test request failed/);assert.equal(await page.locator('#hub-refresh').isDisabled(),false);await page.evaluate(()=>{calls=[];toasts=[]});
   await page.locator('#hub-sort').selectOption('desc');assert.equal(await page.locator('#hub-discover .hub-title').first().textContent(),'Token Monitor');await page.locator('#hub-sort').selectOption('asc');
   await page.locator('#hub-discover .hub-title').first().click();assert.equal(await page.locator('dialog[open]').count(),1);
   await page.keyboard.press('Escape');assert.equal(await page.locator('dialog[open]').count(),0);
   await page.getByRole('tab',{name:/Installed/}).click();await page.waitForFunction(()=>!document.getElementById('hub-installed').hidden);
   await page.locator('#hub-filter').selectOption('disabled');assert.equal(await page.locator('[data-hub-card]:visible').count(),1);
   await page.locator('#hub-installed .hub-card [data-action=enable]').click();assert.deepEqual(await page.evaluate(()=>calls.map(c=>c.url)),['/api/plugins/kanban/enable']);assert.equal(await page.locator('[data-action=enable]').first().isDisabled(),false);assert.equal(await page.evaluate(()=>toasts[0]),'Test request failed');
   await page.getByRole('button',{name:'New plugin',exact:true}).click();await page.waitForFunction(()=>!document.getElementById('hub-create').hidden);
   await page.getByRole('button',{name:/Money manager/}).click();assert.match(await page.locator('#plugin-idea').inputValue(),/IDR/);
   const originalIdea = await page.locator('#plugin-idea').inputValue();
   await page.evaluate(()=>{Tomo.api=async url=>{calls.push({url});return new Promise(resolve=>window.resolveIdeas=resolve)};});
   await page.locator('#plugin-ideas-refresh').click();assert.equal(await page.locator('#plugin-ideas-refresh').isDisabled(),true);
   await page.evaluate(()=>resolveIdeas({source:'llm',prompts:[{label:'Trip planner <script>',prompt:'Build a travel plugin with itinerary pages and agent tools.'},{label:'Recipe book',prompt:'Build a recipe plugin.'},{label:'Study notes',prompt:'Build a study plugin.'}]}));
   await page.waitForFunction(()=>document.getElementById('plugin-ideas-status').textContent==='Inspired by your recent activity.');
   assert.equal(await page.locator('#plugin-idea').inputValue(),originalIdea,'Generation does not overwrite a typed idea');assert.equal(await page.locator('#plugin-ideas script').count(),0,'AI labels render as text');
   await page.getByRole('button',{name:/Trip planner/}).click();assert.match(await page.locator('#plugin-idea').inputValue(),/travel plugin/);
   assert.equal(await page.evaluate(()=>calls.at(-1).url),'/api/plugins/ideas?refresh=true');
   await page.screenshot({path:'/tmp/tomo-plugin-builder-'+width+'.png',fullPage:true});
   await page.locator('#plugin-workplace').selectOption('local');await page.getByRole('button',{name:/Build with agent/}).click();await page.waitForURL('**/sessions?**');let query=new URL(page.url()).searchParams;assert.equal(query.get('agent'),'main');assert.equal(query.get('wp'),'local');assert.match(query.get('q'),/plugin-development/);assert.match(query.get('q'),/Do not restart Tomo/);
   await load();await page.getByRole('button',{name:/Install from source/}).click();await page.locator('#plugin-path').fill('https://github.com/example/plugin');await page.locator('#install-source summary').click();await page.locator('#plugin-ref').fill('v1');await page.locator('#plugin-subdirectory').fill('plugin');await page.locator('#plugin-install button[type=submit]').click();const body=await page.evaluate(()=>JSON.parse(calls[0].options.body));assert.deepEqual(body,{path:'https://github.com/example/plugin',ref:'v1',subdirectory:'plugin'});await page.keyboard.press('Escape');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'No horizontal overflow');
   await page.screenshot({path:'/tmp/tomo-plugins-'+width+'.png',fullPage:true});
   html=render(false);await load();assert.equal(await page.locator('[data-catalog-install],[data-action],[data-hub-create]').count(),0,'Non-admin has no management controls');
   html=render(true,true);await load();assert.equal(await page.locator('[data-hub-empty]:visible').count(),1);await page.getByRole('tab',{name:/Installed/}).click();await page.waitForFunction(()=>!document.getElementById('hub-installed').hidden);assert.equal(await page.locator('[data-hub-empty]:visible').count(),1);
   assert.deepEqual(errors,[]);console.log(width+'px: discovery, search, filters, dialogs, API errors, source options, agent handoff, permissions and empty states passed');await page.close();
  }
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
