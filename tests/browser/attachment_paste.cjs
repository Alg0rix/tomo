/* NODE_PATH=/tmp/tomo-pw/node_modules node tests/browser/attachment_paste.cjs
   Real composer + clipboard events; HTTP/runtime security is covered by pytest. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');
(async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'tomo-paste-ui-'));
  const html = execFileSync(path.join(root, '.venv/bin/python'), ['-c', `
from fastapi.testclient import TestClient
from app.main import app
from app.services import store
store.create_user({'username':'paste_admin','password':'password123','role':'admin'})
c=TestClient(app,follow_redirects=False)
assert c.post('/login',data={'username':'paste_admin','password':'password123'}).status_code==303
print(c.get('/sessions').text)
`], {cwd:root, env:{...process.env,TOMO_HOME:home,TOMO_WORK:path.join(home,'work')}, maxBuffer:3*1024*1024}).toString().replaceAll('http://testserver','');
  const uploads=[], sends=[], imports=[];
  const server=http.createServer(async (req,res) => {
    const url=new URL(req.url,'http://local');
    const json=data=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));};
    if(url.pathname.startsWith('/static/')) {
      const file=path.join(root,'app',url.pathname);
      res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'image/png');
      return res.end(fs.existsSync(file)?fs.readFileSync(file):'');
    }
    if(url.pathname==='/sessions'){res.setHeader('Content-Type','text/html');return res.end(html);}
    if(req.method==='POST') {
      const chunks=[]; for await(const c of req) chunks.push(c);
      const body=Buffer.concat(chunks);
      if(url.pathname.endsWith('/attachments')) {
        uploads.push(body); return json({id:'att_paste_'+uploads.length});
      }
      if(url.pathname.endsWith('/import')) {
        imports.push(url.pathname); return json({path:'/work/.tomo-uploads/att_import.png'});
      }
      if(url.pathname.endsWith('/chat/stream')) {
        sends.push(JSON.parse(body.toString()));
        res.setHeader('Content-Type','text/event-stream');
        return res.end('event: turn.end\ndata: {"ok":true}\n\n');
      }
    }
    if(url.pathname==='/api/sessions') return json({sessions:[{id:'paste-test',title:'Paste test',agent_id:'main',agent_ids:['main'],active_turn:false}],agents:[{id:'main',name:'Tomo',enabled:true}]});
    if(url.pathname.endsWith('/chat/queries')) return json({queries:[]});
    if(url.pathname.endsWith('/chat')) return json({entries:[],has_more:false});
    if(url.pathname.endsWith('/pending')) return json({active_turn:false,approvals:[],clarifications:[]});
    return json({});
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage(); const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    const address=`http://127.0.0.1:${server.address().port}/sessions?s=paste-test`;
    async function paste() {
      await page.locator('.chat-input').evaluate(el=>{
        const bytes=Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='),c=>c.charCodeAt(0));
        const clipboard=new DataTransfer();clipboard.items.add(new File([bytes],'pasted.png',{type:'image/png'}));
        el.dispatchEvent(new ClipboardEvent('paste',{clipboardData:clipboard,bubbles:true,cancelable:true}));
      });
      await page.locator('.attachment-preview .attachment-chip').waitFor();
    }
    await page.goto(address); await page.locator('.chat-input').waitFor();
    await paste();
    await page.locator('.chat-input').fill('Describe this screenshot');
    await page.locator('.chat-send').click();
    await page.waitForFunction(()=>document.querySelector('.attachment-preview').classList.contains('hidden'));
    await new Promise(r=>setTimeout(r,300));
    assert.equal(uploads.length,1); assert.equal(sends.length,1);
    assert.deepEqual(sends[0].attachment_ids,['att_paste_1']);
    assert.equal(sends[0].message,'Describe this screenshot');
    assert.match(uploads[0].toString('latin1'),/filename="pasted.png"/);
    await page.goto(address); await paste();
    await page.locator('[data-import-uploads]').click();
    await page.waitForFunction(()=>document.querySelector('.chat-input').value.includes('/work/.tomo-uploads/att_import.png'));
    assert.equal(uploads.length,2); assert.deepEqual(imports,['/api/attachments/att_paste_2/import']);
    assert.equal(await page.locator('.attachment-preview .attachment-chip').count(),0);
    assert.deepEqual(errors,[]);
    console.log('PASS: PNG clipboard paste → upload/send; paste → working-folder import and composer path');
  } finally {
    await browser.close(); await new Promise(r=>server.close(r)); fs.rmSync(home,{recursive:true,force:true});
  }
})().catch(e=>{console.error(e);process.exit(1);});
