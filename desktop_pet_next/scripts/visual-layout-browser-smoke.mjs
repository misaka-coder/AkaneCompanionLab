import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { dirname, resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";

const { chromium } = await import(process.env.AKANE_PLAYWRIGHT_MODULE || "playwright");
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const output = process.env.AKANE_VISUAL_SMOKE_OUTPUT;
// Render the production DOM, styles and adapters, without starting a live chat,
// microphone, model request, or a substitute Tauri bridge.
const originalHtml = await readFile(resolve(root, "index.html"), "utf8");
const fixture = originalHtml.replace(/<script type="module"[^>]*><\/script>/u, `
  <link rel="stylesheet" href="/src/styles.css">
  <script type="module">
    import { createVisualRenderer } from '/src/visual-renderer.js';
    import { observeBubbleLayout } from '/src/bubble-layout.js';
    const stage = document.querySelector('.stage');
    const bubble = document.querySelector('#bubble');
    window.renderer = createVisualRenderer({ stage, image: document.querySelector('#pet-image') });
    observeBubbleLayout({ stage, bubble, obstruction: document.querySelector('#chat-form') });
    renderer.setExpression({ id: 'normal', url: '/src/assets/characters/猫娘/正常.png' });
    window.fixtureReady = true;
  </script>`);
const previewHtml = `<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="/src/pet-surface.css"><link rel="stylesheet" href="/src/portrait-motion.css">
<link rel="stylesheet" href="/src/scaled-pet-preview.css">
<style>body{margin:0}#preview{width:100vw;height:100vh;padding:24px;box-sizing:border-box}</style></head>
<body><div id="preview"></div><script type="module">
import { createScaledPetPreview } from '/src/scaled-pet-preview.js';
window.preview = createScaledPetPreview({ host: document.querySelector('#preview') });
preview.setExpression({ id:'normal', url:'/src/assets/characters/猫娘/正常.png' });
</script></body></html>`;
const server = createServer(async (req, res) => {
  try {
    if (req.url === "/") { res.setHeader("Content-Type", "text/html; charset=utf-8"); res.end(fixture); return; }
    if (req.url === "/preview") { res.setHeader("Content-Type", "text/html; charset=utf-8"); res.end(previewHtml); return; }
    if (req.url === "/runtime") { res.setHeader("Content-Type", "text/html; charset=utf-8"); res.end(await readFile(resolve(root, "dist/index.html"))); return; }
    const requestPath = decodeURIComponent(new URL(req.url, "http://local").pathname);
    const path = resolve(requestPath.startsWith('/assets/') ? resolve(root, 'dist') : root, `.${requestPath}`);
    if (!path.startsWith(root + sep)) { res.writeHead(403).end(); return; }
    res.setHeader("Content-Type", ({ ".css": "text/css", ".js": "text/javascript", ".png": "image/png" })[extname(path)] || "application/octet-stream");
    res.end(await readFile(path));
  } catch { res.writeHead(404).end(); }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({ channel: process.env.AKANE_BROWSER_CHANNEL || "msedge", headless: true });
  const page = await browser.newPage({ viewport: { width: 340, height: 560 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.waitForFunction(() => window.fixtureReady && document.querySelector('#pet-image').dataset.imageState === 'ready');
  assert.equal(await page.locator('#chat-form').isVisible(), false, 'hidden composer stays visually hidden');
  await page.locator('#chat-form').evaluate(el => { el.hidden = false; });
  let cases = 0;
  for (const viewport of [{ width: 340, height: 560 }, { width: 260, height: 400 }, { width: 600, height: 720 }]) {
    await page.setViewportSize(viewport);
    for (const anchor of [0, 0.5, 1]) {
      for (const text of ["晚上好呀。", "这是长回复，气泡贴近窗口边缘时仍然应该完整可读。".repeat(15)]) {
        await page.evaluate(({ anchor, text }) => {
          renderer.setLayout({ portrait: { scale: 0.8, offset_x: 12, offset_y: -20 }, bubble: { anchor_x: anchor, anchor_y: anchor } });
          const bubble = document.querySelector('#bubble');
          bubble.dataset.size = text.length > 50 ? 'long' : 'short';
          document.querySelector('#bubble-text').textContent = text;
          bubble.classList.add('visible');
        }, { anchor, text });
        await page.waitForFunction(() => {
          const bubble = document.querySelector('#bubble');
          return Number.parseFloat(document.querySelector('.stage').style.getPropertyValue('--bubble-half-width')) === bubble.offsetWidth / 2;
        });
        // Finish the production entrance transition before geometry assertions.
        await page.locator('#bubble').evaluate(async (el) => Promise.all(el.getAnimations().map((a) => a.finished.catch(() => {}))));
        const box = await page.locator('#bubble').boundingBox();
        assert.ok(box.x >= 11 && box.x + box.width <= viewport.width - 11, JSON.stringify({ viewport, anchor, box }));
        assert.ok(box.y >= 7 && box.y + box.height <= viewport.height - 17, JSON.stringify({ viewport, anchor, box }));
        const composer = await page.locator('#chat-form').boundingBox();
        if (composer) assert.ok(box.y + box.height + 8 <= composer.y, 'bubble clears the visible composer');
        if (text.length > 50) {
          const scrolled = await page.locator('#bubble-text').evaluate((el) => { el.scrollTop = 100; return el.scrollTop; });
          assert.ok(scrolled > 0, "long responses remain scrollable");
        }
        cases++;
      }
    }
  }
  for (const height of [96, 42]) {
    await page.locator('#chat-input').evaluate((el, height) => { el.style.height = `${height}px`; }, height);
    await page.waitForFunction(() => {
      const stage = document.querySelector('.stage');
      return Number.parseFloat(stage.style.getPropertyValue('--bubble-bottom-limit')) === document.querySelector('#chat-form').getBoundingClientRect().top;
    });
    const box = await page.locator('#bubble').boundingBox();
    const composer = await page.locator('#chat-form').boundingBox();
    assert.ok(box.y + box.height + 8 <= composer.y, 'composer expansion keeps the reply readable');
  }
  await page.locator('#chat-form').evaluate((el) => { el.hidden = true; });
  await page.waitForFunction(() => !document.querySelector('.stage').style.getPropertyValue('--bubble-bottom-limit'));
  await page.locator('#chat-form').evaluate((el) => { el.hidden = false; });
  await page.waitForFunction(() => document.querySelector('.stage').style.getPropertyValue('--bubble-bottom-limit'));
  for (const motion of ['idle', 'thinking', 'speaking', 'dragging', 'drag-release', 'thrown', 'land', 'hit-wall', 'jump', 'click']) {
    const style = await page.evaluate((motion) => {
      renderer.setMotion(motion);
      return getComputedStyle(document.querySelector('#pet-image')).transform;
    }, motion);
    assert.equal(style, 'matrix(0.8, 0, 0, 0.8, 12, -20)', `calibration survives ${motion}`);
  }
  await page.emulateMedia({ reducedMotion: 'reduce' });
  for (const motion of ['idle', 'speaking', 'click', 'jump']) {
    assert.deepEqual(await page.evaluate((motion) => {
      renderer.setMotion(motion);
      document.querySelector('.stage').classList.add('is-reaching');
      return ['.pet-hitbox', '.portrait-motion'].map((s) => getComputedStyle(document.querySelector(s)).animationName);
    }, motion), ['none', 'none']);
  }
  if (output) {
    await mkdir(output, { recursive: true });
    await page.setViewportSize({ width: 340, height: 560 });
    await page.evaluate(() => {
      document.querySelector('.stage').classList.remove('is-reaching');
      renderer.setMotion('idle');
      renderer.setLayout({ portrait: { scale: 1, offset_x: 0, offset_y: 0 }, bubble: { anchor_x: 0.5, anchor_y: 0.12 } });
      document.querySelector('#bubble').dataset.size = 'medium';
      document.querySelector('#bubble-text').textContent = '晚上好。今天想聊点什么？我在这里陪着你。';
    });
    for (const style of ['soft', 'paper', 'clear', 'dark']) {
      await page.evaluate((style) => { document.querySelector('.stage').dataset.bubbleStyle = style; }, style);
      await page.screenshot({ path: resolve(output, `${style}.png`), omitBackground: true });
    }
  }
  // Compare the shared workshop preview to the actual desktop surface in logical pixels.
  const previewPage = await browser.newPage({ viewport: { width: 620, height: 650 } });
  previewPage.on('pageerror', error => errors.push(error.message));
  await previewPage.goto(`http://127.0.0.1:${server.address().port}/preview`);
  await previewPage.waitForFunction(() => window.preview?.image.dataset.imageState === 'ready' && preview.image.naturalWidth > 0);
  await page.evaluate(() => {
    document.querySelector('#chat-form').hidden = true;
    document.querySelector('.stage').classList.remove('is-reaching');
    renderer.setMotion('idle');
  });
  const geometry = () => {
    const stage = document.querySelector('.stage');
    const rect = stage.getBoundingClientRect();
    const factor = rect.width / stage.offsetWidth;
    return Object.fromEntries(['.pet-hitbox', '.pet-hitbox img', '.bubble', '.bubble-text'].map(selector => {
      const element = stage.querySelector(selector);
      const box = element.getBoundingClientRect();
      const css = getComputedStyle(element);
      return [selector, { x: (box.x - rect.x) / factor, y: (box.y - rect.y) / factor,
        width: box.width / factor, height: box.height / factor,
        color: css.color, background: css.backgroundColor }];
    }));
  };
  let previewCases = 0;
  for (const size of [{ width: 340, height: 560 }, { width: 600, height: 400 }, { width: 260, height: 800 }]) {
    await page.setViewportSize(size);
    for (const style of ['soft', 'paper', 'clear', 'dark']) {
      for (const text of ['晚上好呀。今天想聊些什么？', '这是一段较长的预览文字，边缘应当保持完整。'.repeat(12)]) {
        const layout = { window: size, portrait: { scale: 0.83, offset_x: 12, offset_y: -21 },
          bubble: { style, anchor_x: style === 'paper' ? 0 : 1, anchor_y: 0.98, max_width: 280 } };
        await page.evaluate(({ layout, text }) => { renderer.setLayout(layout);
          document.querySelector('#bubble').dataset.size = text.length > 50 ? 'long' : 'short';
          document.querySelector('#bubble-text').textContent = text;
        }, { layout, text });
        await previewPage.evaluate(({ layout, text }) => { preview.setLayout(layout); preview.setText(text); }, { layout, text });
        for (const target of [page, previewPage]) {
          await target.waitForFunction(() => {
            const stage = document.querySelector('.stage'); const bubble = stage.querySelector('.bubble');
            return Number.parseFloat(stage.style.getPropertyValue('--bubble-half-width')) === bubble.offsetWidth / 2
              && Number.parseFloat(stage.style.getPropertyValue('--bubble-height')) === bubble.offsetHeight;
          });
        }
        const actual = await page.evaluate(geometry);
        const scaled = await previewPage.evaluate(geometry);
        for (const selector of Object.keys(actual)) {
          for (const key of ['x', 'y', 'width', 'height']) {
            assert.ok(Math.abs(actual[selector][key] - scaled[selector][key]) < 0.08,
              JSON.stringify({ size, style, selector, key, actual: actual[selector], scaled: scaled[selector] }));
          }
          if (selector.startsWith('.bubble')) {
            assert.equal(scaled[selector].color, actual[selector].color);
            assert.equal(scaled[selector].background, actual[selector].background);
          }
        }
        previewCases += 1;
      }
    }
  }
  await previewPage.close();
  assert.deepEqual(errors, []);
  const runtime = await browser.newPage({ viewport: { width: 340, height: 560 } });
  const origin = `http://127.0.0.1:${server.address().port}`;
  // Exercise the built main entry's real browser fallback. No service response
  // is fabricated, and no request is allowed out to the user's live backend.
  await runtime.route('**/*', (route) => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
  const runtimeErrors = [];
  runtime.on('pageerror', (error) => runtimeErrors.push(error.message));
  await runtime.goto(origin + '/runtime');
  await runtime.waitForFunction(() => document.querySelector('#pet-image').dataset.imageState === 'ready');
  assert.equal(await runtime.locator('.portrait-motion > #pet-image').count(), 1);
  assert.equal(await runtime.locator('#chat-form').isVisible(), false, 'built main starts with composer hidden');
  assert.deepEqual(runtimeErrors, []);
  console.log(`visual layout browser smoke passed: ${cases} edge/length/window cases, ${previewCases} desktop/preview geometry comparisons, composer resize/hide, 10 motions, reduced motion, built main fallback, 0 page errors`);
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
