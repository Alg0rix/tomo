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
from app.web.context import room_tint
env=Environment(loader=FileSystemLoader('app/templates'), autoescape=True)
env.globals.update(plugin_icons=ICONS, url_for=lambda name, **kwargs: '/static/'+kwargs.get('path',''), avatar_color=lambda *args:'#777', eval_ui_enabled=False)
plugins=[dict(id='token_monitor', icon='chart-column', kanji='計', name='Token Monitor', description='Usage heatmap and recent agent activity.', version='0.3', commit='a'*40, origin=dict(url='https://github.com/example/repo',ref='main'), running=True, enabled=True, error=None, source='git', home_cards=1, tools=[dict(id='usage',name='token_usage',description='Read token usage.')], skills=[], dependencies=dict(status='ready',requirements=[]), pages=[dict(label='Usage',path='/plugins/token_monitor/')]), dict(id='kanban', dependencies=dict(status='missing',requirements=['opencv-python-headless'],missing=['opencv-python-headless']), icon='columns-3', kanji='', name='Kanban', description='A board for your project.', version='0.1', running=False, enabled=False, error=None, source='git', home_cards=0, tools=[], skills=[], pages=[])]
catalog=[dict(id='money', icon='wallet', kanji='金', category='Money', name='Money', description='Track income and expenses with your agents.', version='0.1.0', author='Tomo', publisher='Tomo Official', marketplace='tomo-official', source=dict(url='https://github.com/Alg0rix/tomo-plugins', ref='main', subdirectory='plugins/money')),dict(id='token_monitor', icon='chart-column', kanji='計', category='Developer', name='Token Monitor', description='Usage heatmap and recent agent activity.', version='0.3', author='Tomo', publisher='Tomo Official', marketplace='tomo-official', source=dict(url='https://github.com/Alg0rix/tomo-plugins', ref='main', subdirectory='plugins/token_monitor')),dict(id='library', icon='library', kanji='', category='', name='Reading Library <script>x</script>', description='Private notes and reading progress.', version='0.1', author='Test author', publisher='Community', marketplace='tomo-community', source=dict(url='https://github.com/example/library',ref='main',subdirectory=''))]
for item in plugins+catalog: item['tint']=room_tint(item['id'])
empty=${empty ? 'True' : 'False'}
print(env.get_template('plugins.html').render(brand='Tomo', page='plugins', static_ver='test', app_version='test', request=SimpleNamespace(session={'username':'test'}), plugins=[] if empty else plugins, catalog=[] if empty else catalog, marketplaces=[dict(id='tomo-official',name='Tomo Official',source='https://example.com/m.json',reserved=True,refreshed_at=None,plugin_count=2),dict(id='tomo-community',name='Community',source='git:https://github.com/x/y@main',reserved=False,refreshed_at=None,plugin_count=1)], builder_agents=[dict(id='main',name='Main')], builder_workplaces=[dict(id='local',name='Plugin projects')], plugin_icon_paths=ICONS, plugin_nav=[dict(id='token_monitor',label='Token Monitor',path='/plugins/token_monitor/',page='plugin-token_monitor',icon='chart-column',kanji='計',tint='ok')], can_manage_plugins=${admin ? 'True' : 'False'}))
`], {cwd:root, encoding:'utf8'}).replace(/<script\b(?![^>]*type="application\/json")[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
}
const UPDATES=[{id:'token_monitor',status:'available',installed_commit:'a'.repeat(40),latest_commit:'b'.repeat(40),installed_version:'0.3',latest_version:'1.1.0'},{id:'kanban',status:'local',message:'Edit the local source, then reload.'}];
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
  for(const width of [390,1280]) {
   const page=await browser.newPage({viewport:{width,height:1000}});const errors=[];
   page.on('pageerror',e=>errors.push(e.message));
   let html=render();let loads=0;
   await page.route('http://plugins.test/**',route=>route.fulfill({contentType:'text/html',body:html}));
   async function load(hash='') {
    await page.goto('http://plugins.test/extensions?load='+(++loads)+hash);
    await page.addStyleTag({path:path.join(root,'app/static/css/tomo.css')});
    await page.addStyleTag({path:path.join(root,'app/static/css/plugins.css')});
    await page.evaluate(updates=>{window.calls=[];window.toasts=[];window.Tomo={escapeHtml:s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),api:async(url,options)=>{calls.push({url,options});if(url==='/api/plugins/check-updates')return updates;throw new Error('Test request failed');},toast:(msg,kind)=>toasts.push(msg)};},UPDATES);
    await page.addScriptTag({path:path.join(root,'app/static/js/plugins.js')});
   }
   await load();
   // Installed: background update check, needs-you lane, kind groups
   await page.waitForFunction(()=>document.querySelector('.pl-lane .pl-lr.up'));
   assert.deepEqual(await page.evaluate(()=>JSON.parse(calls.find(c=>c.url==='/api/plugins/check-updates').options.body)),{force:false});
   assert.equal(await page.locator('.pl-lane .pl-lr').count(),2,'update + missing packages');
   assert.match(await page.locator('.pl-lane .pl-lr.bad').textContent(),/Missing Python packages: opencv-python-headless/);
   assert.match(await page.locator('#pl-tab-updates').textContent(),/Updates\s*1/);
   assert.deepEqual(await page.locator('.pl-grp-h h2').allTextContents(),['間Rooms','休Switched off']);
   assert.equal(await page.locator('.pl-tg[data-tg=kanban]').isDisabled(),true,'Broken plugin cannot switch on');
   assert.equal(await page.locator('.pl-strip .pl-stamp').count(),2);
   await page.evaluate(()=>{calls=[];toasts=[]});
   await page.locator('.pl-lane [data-act=sync-dependencies]').click();
   assert.equal(await page.evaluate(()=>calls[0].url),'/api/plugins/kanban/sync-dependencies');
   await page.waitForFunction(()=>toasts[0]==='Test request failed');
   // Search + list layout
   await page.locator('#pl-q').fill('kanban');assert.equal(await page.locator('.pl-plug').count(),1);
   await page.locator('[data-layout=list]').click();assert.equal(await page.locator('.pl-row').count(),1);
   await page.locator('#pl-q').fill('zzz');assert.equal(await page.locator('.pl-empty').count(),1);
   await page.locator('[data-clear]').click();await page.locator('[data-layout=cards]').click();
   // Detail panel
   await page.locator('.pl-plug .pl-name',{hasText:'Token Monitor'}).click();
   assert.equal(await page.locator('#pl-panel').isVisible(),true);
   assert.match(await page.locator('#pl-panel').textContent(),/v1\.1\.0 is ready/);
   assert.match(await page.locator('#pl-panel .pl-tools').textContent(),/token_usage/);
   await page.locator('#pl-panel [data-pop=more]').click();await page.locator('#pl-panel [data-confirm]').click();
   assert.match(await page.locator('#pl-panel .pl-note.bad').textContent(),/Uninstall Token Monitor\?/);
   await page.locator('#pl-panel [data-keep]').click();
   await page.keyboard.press('Escape');assert.equal(await page.locator('#pl-panel').isVisible(),false);
   // Browse: groups, installed entry has no install button, escaped names
   await page.locator('#pl-tab-browse').click();
   assert.equal(await page.locator('.pl-plug').count(),3);
   assert.equal(await page.locator('[data-install="token_monitor@tomo-official"]').count(),0,'Already installed entry has no install action');
   assert.equal(await page.locator('.pl-plug script').count(),0,'Names render as text');
   await page.locator('.pl-plug[data-hover="money@tomo-official"]').hover();
   await page.waitForFunction(()=>document.querySelector('.pl-stamp.is-ghost'));
   await page.locator('[data-need="c:money"]').click();assert.equal(await page.locator('.pl-plug').count(),1);
   await page.locator('[data-need=""]').click();
   await page.evaluate(()=>{calls=[]});await page.locator('[data-install="money@tomo-official"]').click();
   assert.deepEqual(await page.evaluate(()=>JSON.parse(calls[0].options.body)),{path:'money@tomo-official'});
   await page.locator('#pl-q').fill('https://github.com/example/plugin');await page.getByRole('button',{name:'Review this source'}).click();
   assert.equal(await page.locator('#plugin-path').inputValue(),'https://github.com/example/plugin');
   await page.locator('#plugin-ref').fill('v1');await page.locator('#plugin-subdirectory').fill('plugin');
   await page.evaluate(()=>{calls=[]});await page.locator('#plugin-install button[type=submit]').click();
   assert.deepEqual(await page.evaluate(()=>JSON.parse(calls[0].options.body)),{path:'https://github.com/example/plugin',ref:'v1',subdirectory:'plugin'});
   await page.screenshot({path:'/tmp/tomo-plugins-browse-'+width+'.png',fullPage:true});
   // Updates tab
   await load('#updates');await page.waitForSelector('[data-act=update][data-id=token_monitor]');
   assert.match(await page.locator('.pl-body').textContent(),/aaaaaaa → bbbbbbb/);
   await page.locator('[data-check]').first().click();await page.waitForFunction(()=>calls.some(c=>c.url==='/api/plugins/check-updates'&&JSON.parse(c.options.body).force));
   // Catalogs tab
   await load('#catalogs');assert.equal(await page.locator('.pl-cat').count(),2);
   assert.equal(await page.locator('.pl-cat [data-mkt=remove]').count(),1,'Reserved catalog cannot be removed');
   // Build tab: ideas, caps, agent handoff
   await load('#build');
   await page.evaluate(()=>{Tomo.api=async url=>{calls.push({url});return new Promise(resolve=>window.resolveIdeas=resolve)};});
   await page.locator('#plugin-ideas-refresh').click();assert.equal(await page.locator('#plugin-ideas-refresh').isDisabled(),true);
   await page.evaluate(()=>resolveIdeas({source:'llm',prompts:[{label:'Trip planner <script>',prompt:'Build a travel plugin with itinerary pages and agent tools.'},{label:'Recipe book',prompt:'Build a recipe plugin.'},{label:'Study notes',prompt:'Build a study plugin.'}]}));
   await page.waitForFunction(()=>/recent activity/.test(document.querySelector('.pl-build-form [role=status]')?.textContent||''));
   assert.equal(await page.locator('#plugin-ideas script').count(),0,'AI labels render as text');
   await page.getByRole('button',{name:/Trip planner/}).click();assert.match(await page.locator('#plugin-idea').inputValue(),/travel plugin/);
   await page.locator('[data-cap=skill]').click();assert.equal(await page.locator('[data-cap=skill]').getAttribute('aria-pressed'),'true');
   await page.screenshot({path:'/tmp/tomo-plugin-builder-'+width+'.png',fullPage:true});
   await page.locator('#plugin-workplace').selectOption('local');await page.getByRole('button',{name:/Build with agent/}).click();await page.waitForURL('**/sessions?**');
   let query=new URL(page.url()).searchParams;assert.equal(query.get('agent'),'main');assert.equal(query.get('wp'),'local');assert.match(query.get('q'),/plugin-development/);assert.match(query.get('q'),/Do not restart Tomo/);assert.match(query.get('q'),/SKILL\.md/);
   await load();
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'No horizontal overflow');
   await page.screenshot({path:'/tmp/tomo-plugins-'+width+'.png',fullPage:true});
   html=render(false);await load('#build');
   assert.equal(await page.locator('[data-install],[data-act],[data-tg],#plugin-build,[data-pop=add]').count(),0,'Non-admin has no management controls');
   html=render(true,true);await load();assert.equal(await page.locator('.pl-empty').count(),1,'Empty install base starts in Browse with an empty state');
   assert.deepEqual(errors,[]);console.log(width+'px: installed lane, groups, panel, browse, install, updates, catalogs, build handoff, permissions and empty states passed');await page.close();
  }
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
