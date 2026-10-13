// Actual renderer and CSS with isolated image transport; no Bot or QQ requests.
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const { chromium } = await import(process.env.AKANE_PLAYWRIGHT_MODULE || 'playwright');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const assets = resolve(root, 'desktop_pet_creator_kit/characters/cecilia/assets/characters/default');
const output = resolve(root, 'output/portrait-transition-180');
await mkdir(output, { recursive: true });
const portraits = new Map(await Promise.all(['normal', 'happy', 'shy', 'chin_rest'].map(async (id, index) => {
  try {
    return [id, {bytes:await readFile(resolve(assets, `${id}.png`)),type:'image/png'}];
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    // Character packs are user data. A clean checkout still tests real alpha blending.
    const color = ['#b8cdb2','#dfbfc9','#c5c0de','#b3ced8'][index];
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1536"><ellipse cx="512" cy="420" rx="280" ry="350" fill="${color}"/><path d="M200 850 Q512 ${900-index*70} 824 850 L950 1500 H74Z" fill="${color}"/></svg>`;
    return [id, {bytes:Buffer.from(svg),type:'image/svg+xml'}];
  }
})));
const html = `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;background:#e6eff1}.stage{position:relative;width:420px;height:680px}
</style></head><body><main class="stage"><div class="pet-hitbox"><div class="portrait-motion">
<img id="pet-image" draggable="false"></div></div></main><script type="module">
import '/src/portrait-motion.css'; import '/src/pet-surface.css';
import { createVisualRenderer } from '/src/visual-renderer.js';
window.failures=[]; window.portrait=document.querySelector('#pet-image');
window.renderer=createVisualRenderer({stage:document.querySelector('.stage'),image:portrait,
  onImageLoadError:failure=>failures.push(failure.error.message)});
window.select=id=>renderer.setExpression({id,url:'/__portrait/'+id+'.png'});
</script></body></html>`;
const server = await createServer({ server: { host: '127.0.0.1', port: 0, hmr: false }, plugins: [{
  name: 'isolated-portrait-transition',
  configureServer(instance) {
    instance.middlewares.use(async (req, res, next) => {
      const url = new URL(req.url, 'http://localhost');
      if (url.pathname === '/__transition.html') {
        res.setHeader('Content-Type', 'text/html'); res.end(html); return;
      }
      const id = /^\/__portrait\/([a-z_]+)\.png$/.exec(url.pathname)?.[1];
      if (!id) return next();
      if (id === 'delayed') await new Promise(resolve => setTimeout(resolve, 450));
      const portrait = portraits.get(id === 'delayed' ? 'happy' : id);
      if (!portrait) { res.writeHead(404).end(); return; }
      res.setHeader('Content-Type', portrait.type);
      res.setHeader('Cache-Control', 'public, max-age=3600'); res.end(portrait.bytes);
    });
  }
}] });
let browser;
const checks = [];
try {
  await server.listen();
  browser = await chromium.launch({ channel: process.env.AKANE_BROWSER_CHANNEL || 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 420, height: 680 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(server.resolvedUrls.local[0] + '__transition.html');
  await page.waitForFunction(() => window.renderer);
  const select = async id => {
    await page.evaluate(id => select(id), id);
    await page.waitForFunction(id => portrait.dataset.emotion === id && portrait.complete &&
      !renderer.getStatus().portraitTransition.active, id, { polling: 'raf' });
  };
  await select('normal');
  assert.equal(await page.locator('.portrait-transition-frame').count(), 0);
  checks.push('Initial portrait loads directly');
  await page.evaluate(() => {
    renderer.setLayout({portrait:{scale:1.03,offset_x:7,offset_y:-8}});
    renderer.setMotion('speaking');
    select('happy');
  });
  await page.waitForFunction(() => renderer.getStatus().portraitTransition.active, undefined, {polling:'raf'});
  const halfway = await page.evaluate(() => {
    const old = document.querySelector('.portrait-transition-frame');
    const animations = [...old.getAnimations(), ...portrait.getAnimations()];
    animations.forEach(animation => { animation.pause(); animation.currentTime = 90; });
    select('happy');
    return {
      duration: renderer.getStatus().portraitTransition.durationMs,
      oldSource: old.getAttribute('src'), currentSource: portrait.getAttribute('src'),
      opacitySum: Number(getComputedStyle(old).opacity) + Number(getComputedStyle(portrait).opacity),
      blend: getComputedStyle(portrait).mixBlendMode,
      samePlacement: old.style.transform === portrait.style.transform &&
        old.style.transformOrigin === portrait.style.transformOrigin,
      sameAnimation: animations[1] === portrait.getAnimations()[0],
      parentMotion: document.querySelector('.stage').dataset.motion
    };
  });
  assert.equal(halfway.duration, 180);
  assert.equal(halfway.oldSource, '/__portrait/normal.png');
  assert.equal(halfway.currentSource, '/__portrait/happy.png');
  assert.ok(Math.abs(halfway.opacitySum - 1) < .002, JSON.stringify(halfway));
  assert.equal(halfway.blend, 'plus-lighter');
  assert.equal(halfway.samePlacement, true);
  assert.equal(halfway.sameAnimation, true, 'Duplicate selection must not restart the fade');
  assert.equal(halfway.parentMotion, 'speaking');
  await page.screenshot({path:resolve(output,'halfway.png')});
  await page.evaluate(() => document.querySelectorAll('.portrait-transition-layer').forEach(img =>
    img.getAnimations().forEach(animation => animation.play())
  ));
  await page.waitForFunction(() => !renderer.getStatus().portraitTransition.active);
  assert.equal(await page.locator('.portrait-transition-frame').count(), 0);
  await page.screenshot({path:resolve(output,'settled.png')});
  checks.push('180 ms complementary fade, layout, motion, duplicate selection and cleanup');

  // A delayed or failed image keeps the last good portrait fully visible.
  await page.evaluate(() => select('delayed'));
  assert.equal(await page.evaluate(() => portrait.dataset.emotion), 'happy');
  assert.equal(await page.locator('.portrait-transition-frame').count(), 0);
  await page.evaluate(() => select('shy'));
  await page.waitForFunction(() => portrait.dataset.emotion === 'shy' && !renderer.getStatus().portraitTransition.active);
  await page.waitForTimeout(500);
  assert.equal(await page.evaluate(() => portrait.dataset.emotion), 'shy');
  await page.evaluate(() => select('missing'));
  await page.waitForFunction(() => failures.length === 1);
  assert.equal(await page.evaluate(() => portrait.dataset.emotion), 'shy');
  assert.equal(await page.evaluate(() => getComputedStyle(portrait).opacity), '1');
  checks.push('Slow, stale and failed loads preserve the current portrait');

  await page.evaluate(() => { select('normal'); select('happy'); select('chin_rest'); });
  await page.waitForFunction(() => portrait.dataset.emotion === 'chin_rest' && !renderer.getStatus().portraitTransition.active);
  assert.equal(await page.locator('.portrait-transition-frame').count(), 0);
  await page.screenshot({path:resolve(output,'pose.png')});
  checks.push('Rapid expression and pose switching keeps the newest choice');

  // The shared head silhouette must not turn translucent over a dark desktop.
  await page.evaluate(async () => {
    document.body.style.background='#000';
    renderer.setLayout(null);
    renderer.setMotion('idle');
    document.querySelector('.portrait-motion').style.animation='none';
    const canvas=document.createElement('canvas'); canvas.width=1024; canvas.height=1536;
    const context=canvas.getContext('2d'); context.fillStyle='#fff'; context.fillRect(100,100,824,1336);
    window.white=canvas.toDataURL();
    renderer.setExpression({id:'white1',url:white});
  });
  await page.waitForFunction(() => portrait.dataset.emotion === 'white1' && !renderer.getStatus().portraitTransition.active);
  await page.evaluate(() => renderer.setExpression({id:'white2',url:white+'#same-pixels'}));
  await page.waitForFunction(() => renderer.getStatus().portraitTransition.active, undefined, {polling:'raf'});
  await page.evaluate(() => document.querySelectorAll('.portrait-transition-layer').forEach(img =>
    img.getAnimations().forEach(animation => { animation.pause(); animation.currentTime=90; })
  ));
  const png = await page.screenshot();
  const pixel = await page.evaluate(async encoded => {
    const img=new Image(); img.src='data:image/png;base64,'+encoded; await img.decode();
    const canvas=document.createElement('canvas'); canvas.width=img.width; canvas.height=img.height;
    const context=canvas.getContext('2d'); context.drawImage(img,0,0);
    return [...context.getImageData(210,430,1,1).data];
  }, png.toString('base64'));
  assert.ok(pixel.slice(0,3).every(value => value >= 253), `Midpoint dimmed: ${pixel}`);
  checks.push('Transparent portrait midpoint preserves silhouette opacity');

  await page.emulateMedia({reducedMotion:'reduce'});
  await page.waitForFunction(() => !renderer.getStatus().portraitTransition.active);
  await select('normal');
  assert.equal(await page.locator('.portrait-transition-frame').count(), 0);
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.evaluate(() => select('happy'));
  await page.waitForFunction(() => renderer.getStatus().portraitTransition.active, undefined, {polling:'raf'});
  await page.evaluate(() => renderer.setLayout(null));
  assert.equal(await page.locator('.portrait-transition-frame').count(), 0);
  await page.evaluate(() => select('shy'));
  await page.waitForFunction(() => renderer.getStatus().portraitTransition.active, undefined, {polling:'raf'});
  await page.evaluate(() => renderer.clearExpression());
  assert.equal(await page.locator('.portrait-transition-frame').count(), 0);
  assert.equal(await page.evaluate(() => portrait.getAttribute('src')), null);
  await select('normal');
  await page.evaluate(() => select('happy'));
  await page.waitForFunction(() => renderer.getStatus().portraitTransition.active, undefined, {polling:'raf'});
  await page.evaluate(() => renderer.dispose());
  assert.equal(await page.locator('.portrait-transition-frame').count(), 0);
  assert.equal(await page.locator('.portrait-blink-frame').count(), 0);
  await page.waitForTimeout(220);
  assert.equal(await page.evaluate(() => getComputedStyle(portrait).opacity), '1');
  checks.push('Reduced motion, layout changes, clear and disposal release animation layers');
  assert.deepEqual(errors, []);
  await writeFile(resolve(output,'verification.json'), JSON.stringify({checks,halfway,pixel,errors},null,2));
  console.log('Portrait transition browser checks passed:', checks.join('; '));
} finally {
  await browser?.close();
  await server.close();
}
