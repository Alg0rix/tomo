/* Run with Playwright installed: node tests/browser/artifact_preview.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');

const artifact = `<!doctype html><meta charset="utf-8">
<iframe id="nested"></iframe><p id="result"></p>
<script>
const nested = document.getElementById('nested');
nested.onload = () => {
  try { document.getElementById('result').textContent = nested.contentDocument.body.textContent; }
  catch (_) { document.getElementById('result').textContent = 'Preview tidak bisa dibaca. Pastikan browser mendukung iframe srcdoc.'; }
};
nested.srcdoc = '<p>Preview berhasil 🦝</p>';
</script>`;

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.route('http://preview.test/**', route => route.fulfill({
      contentType: route.request().url().endsWith('/page.html') ? 'text/plain' : 'text/html',
      body: route.request().url().endsWith('/page.html') ? artifact :
        '<div id="preview"></div><main id="artifactViewStage" data-url="/page.html"><iframe id="avHtmlFrame"></iframe></main>',
    }));
    const template = fs.readFileSync(path.join(root, 'app/templates/artifact_view.html'), 'utf8');
    const viewerScript = template.match(/<script>\s*(\(function \(\) \{\s*var stage = document.getElementById\('artifactViewStage'\);[\s\S]*?)<\/script>/)[1];
    for (const mode of ['panel', 'viewer']) {
      await page.goto('http://preview.test/');
      await page.evaluate(() => { localStorage.setItem('secret', 'Tomo session secret'); document.cookie = 'session=secret'; });
      await page.addScriptTag({ path: path.join(root, 'app/static/js/artifact_preview.js') });
      if (mode === 'panel') {
        await page.addScriptTag({ path: path.join(root, 'app/static/js/artifacts.js') });
        await page.evaluate(() => TomoArtifacts.renderFullPage(document.getElementById('preview'), {
          url: '/page.html', filename: 'page.html', category: 'html',
        }));
      } else {
        await page.addScriptTag({ content: viewerScript });
      }
      const selector = mode === 'panel' ? '#preview iframe' : '#avHtmlFrame';
      const frame = page.frameLocator(selector);
      await frame.locator('#result').filter({ hasText: 'Preview berhasil 🦝' }).waitFor();
      assert.equal(await frame.locator('#result').innerText(), 'Preview berhasil 🦝');
      assert.equal(await frame.locator('body').evaluate(() => {
        try { return window.parent.document.body.innerHTML; } catch (_) { return 'blocked'; }
      }), 'blocked');
      for (const expression of ['window.parent.localStorage.getItem("secret")', 'window.localStorage.getItem("secret")', 'window.parent.document.cookie', 'window.document.cookie']) {
        assert.equal(await frame.locator('body').evaluate((_, expression) => {
          try { return eval(expression); } catch (_) { return 'blocked'; }
        }, expression), 'blocked');
      }
      assert.match(await page.locator(selector).getAttribute('src'), /^data:text\/html;charset=utf-8,/);
      assert.equal(await page.locator(selector).getAttribute('srcdoc'), null);
      // Reload the same frame with new HTML; no stale nested document survives.
      await page.evaluate((selector) => TomoArtifactPreview.setContent(document.querySelector(selector), '<h1>Updated</h1>'), selector);
      await frame.locator('h1').filter({ hasText: 'Updated' }).waitFor();
      console.log(`${mode}: nested preview, Unicode, origin isolation and reload passed`);
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
