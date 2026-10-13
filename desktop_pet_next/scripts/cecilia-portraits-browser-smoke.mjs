import assert from 'node:assert/strict';
import { createServer as createHttpServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const { chromium } = await import(process.env.AKANE_PLAYWRIGHT_MODULE || 'playwright');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const pack = resolve(root, 'desktop_pet_creator_kit/characters/cecilia');
const output = resolve(root, 'output/imagegen/cecilia-music-v1/browser');
await mkdir(output, { recursive: true });
const motionOutput = resolve(root, 'output/imagegen/cecilia-idle-motion-v1/browser');
await mkdir(motionOutput, { recursive: true });
const wav = Buffer.alloc(44 + 8000 * 60 * 2);
wav.write('RIFF',0); wav.writeUInt32LE(wav.length-8,4); wav.write('WAVEfmt ',8);
wav.writeUInt32LE(16,16); wav.writeUInt16LE(1,20); wav.writeUInt16LE(1,22);
wav.writeUInt32LE(8000,24); wav.writeUInt32LE(16000,28); wav.writeUInt16LE(2,32); wav.writeUInt16LE(16,34);
wav.write('data',36); wav.writeUInt32LE(wav.length-44,40);
let normalOverride = null;
// This second origin models the backend image route, including its CORS header.
const images = createHttpServer(async (req, res) => {
  const id = /\/([a-z_-]+)\.png$/.exec(new URL(req.url, 'http://localhost').pathname)?.[1];
  if (!id) { res.writeHead(404).end(); return; }
  try {
    if (id === 'delayed') await new Promise(resolve => setTimeout(resolve, 600));
    res.setHeader('Access-Control-Allow-Origin', '*'); res.setHeader('Content-Type', 'image/png');
    res.setHeader('Cache-Control', 'public, max-age=86400');
    const file = id === 'normal-blink' ? 'assets/animations/default/normal-blink.png' : `assets/characters/default/${id === 'delayed' ? 'normal' : id}.png`;
    res.end(id === 'normal' && normalOverride ? normalOverride : await readFile(resolve(pack, file)));
  } catch { res.writeHead(404).end(); }
});
await new Promise(resolve => images.listen(0, '127.0.0.1', resolve));
const imageOrigin = `http://127.0.0.1:${images.address().port}`;
const source = `
import '/src/styles.css';
import { createPetApplication } from '/src/pet/application.js';
import { selectCharacterPack } from '/src/character-profile.js';
selectCharacterPack('cecilia', { persist: false });
window.nativeCalls = [];
window.careFixture = { authority:'care_runtime', hunger:60, energy:60, affection:50, coins:100, updated_at:Date.now() };
const json = value => new Response(JSON.stringify(value), {headers:{'Content-Type':'application/json'}});
const app = createPetApplication({bundledCharacterAssets:{}, platform:window.fixturePlatform={
 window, document, navigator, isTauriRuntime:false,
 tauriCall: async (command,payload) => {
  nativeCalls.push({command,payload});
  if(command==='open_workspace_item')return {local_path:location.origin+'/motion.wav'};
  return null;
 },
 invoke: async(command,payload)=>{
  nativeCalls.push({command,payload});
  if(command==='open_workspace_item')return {local_path:location.origin+'/motion.wav'};
  if(command==='prepare_audio_asset')return {cachedPath:payload.path,fileName:'motion.wav',extension:'wav',lyricText:'[00:00]测试音乐'};
  return null;
 },
 emitTo: async()=>{}, emit:async()=>{}, listen:async()=>()=>{}, convertFileSrc:value=>value,
 backendFetch:async(input,init)=>{
  const url=new URL(input);
  if(url.pathname==='/health')return json({status:'ok',root_binding:'valid',instance_id:'local-default'});
  if(url.pathname==='/api/bots')return json({defaultBotId:'fixture',bots:[{botId:'fixture',available:true}]});
  if(url.pathname.endsWith('/desktop-pet/health'))return json({status:'ok',features:{care:{enabled:true}},tts:{enabled:false}});
  if(url.pathname.endsWith('/resource-manifest'))return json({characters:{outfits:[{id:'default',emotions:['normal','hungry','tired','music','happy','serious'].map(id=>({id,aliases:id==='normal'?['music']:[],path:'${imageOrigin}/characters/default/'+id+'.png'}))}]}});
  if(url.pathname.endsWith('/desktop-pet/care/snapshot'))return json({ok:true,snapshot:careFixture});
  if(url.pathname.endsWith('/workspace/summary'))return json({sections:{files:[],outputs:[]}});
  throw new Error('unmocked route: '+url.pathname);
 }
}});
window.petFixture=app;
window.ready=app.start();
`;
const server = await createServer({ server: { host: '127.0.0.1', port: 0, hmr: false }, plugins: [{
  name: 'isolated-portrait-fixture', configureServer(server) {
    server.middlewares.use((req, res, next) => {
      if (req.url?.split('?')[0] === '/motion.wav') {
        res.setHeader('Content-Type','audio/wav'); res.setHeader('Content-Length',wav.length); res.end(wav); return;
      }
      if (req.url?.split('?')[0] !== '/src/main.js') { next(); return; }
      res.setHeader('Content-Type', 'text/javascript'); res.end(source);
    });
  }
}] });
let browser;
try {
  await server.listen();
  browser = await chromium.launch({ channel: process.env.AKANE_BROWSER_CHANNEL || 'msedge', headless: true, args:['--autoplay-policy=no-user-gesture-required','--mute-audio'] });
  const page = await browser.newPage({ viewport: { width: 420, height: 680 }, deviceScaleFactor: 1.5 });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('http://127.0.0.1:9999/**', route => {
    const path = new URL(route.request().url()).pathname;
    const body = path === '/health' ? {status:'ok',root_binding:'valid',instance_id:'local-default'}
      : {defaultBotId:'fixture',bots:[{botId:'fixture',available:true}]};
    return route.fulfill({contentType:'application/json',headers:{'Access-Control-Allow-Origin':'*'},body:JSON.stringify(body)});
  });
  await page.goto(server.resolvedUrls.local[0]);
  await page.waitForFunction(() => window.ready, undefined, {timeout:15000});
  assert.equal(await page.evaluate(() => ready), true, await page.evaluate(() => JSON.stringify({ health: petFixture.resourceState.healthMessage, status: document.querySelector('#status')?.textContent, instance:petFixture.state.instanceId })));
  await page.waitForFunction(() => document.querySelector('#pet-image').naturalWidth === 1024 && document.querySelector('.stage').dataset.petHitShape === 'alpha');
  await page.waitForFunction(() => petFixture.state.currentEmotion === 'normal');
  // The image extends beyond its parent after visual scaling. Native alpha hits
  // on that visible outline must still reach the parent's drag handlers.
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.evaluate(() => {
    petFixture.state.hitboxOverlay=true;
    petFixture.controllers.window.scheduleNativeHitTestSync({force:true});
  });
  await page.waitForFunction(() => document.querySelectorAll('.hitbox-overlay-shape[data-kind="pet"]').length > 5);
  const edge = await page.evaluate(() => {
    const parent=document.querySelector('#pet-hitbox').getBoundingClientRect();
    const topHit=[...document.querySelectorAll('.hitbox-overlay-shape[data-kind="pet"]')]
      .map(element=>element.getBoundingClientRect())
      .find(rect=>rect.width>0 && rect.height>0 && rect.bottom<parent.top-2 && rect.top>=0);
    if (!topHit) return null;
    const x=topHit.left+topHit.width/2, y=topHit.top+topHit.height/2;
    return {x,y,reachesPet:!!document.elementFromPoint(x,y)?.closest('#pet-hitbox')};
  });
  assert.ok(edge, 'real portrait has an opaque head edge outside the scaled parent');
  assert.equal(edge.reachesPet, true, 'visible head edge must route pointer events to the pet');
  await page.evaluate(() => {fixturePlatform.isTauriRuntime=true; nativeCalls.length=0;});
  try {
    await page.mouse.move(edge.x,edge.y);
    await page.mouse.down();
    assert.equal(await page.evaluate(()=>!!petFixture.controllers.window.dragState), true);
    await page.mouse.move(edge.x+12,edge.y+8,{steps:2});
    await page.waitForFunction(()=>nativeCalls.some(call=>call.command==='move_window_by'));
  } finally {
    await page.evaluate(()=>{
      fixturePlatform.isTauriRuntime=false;
      petFixture.controllers.window.endManualDrag();
    });
    await page.mouse.up();
    await page.evaluate(()=>{
      petFixture.controllers.bubble.setPetMotion('idle');
      petFixture.state.hitboxOverlay=false;
      petFixture.controllers.window.scheduleNativeHitTestSync({force:true});
    });
    await page.emulateMedia({reducedMotion:'no-preference'});
  }
  await page.waitForFunction(() => petFixture.controllers.settings.buildSettingsSnapshot().visual.idleAnimation.blink === 'ready');
  assert.equal(await page.locator('.portrait-blink-frame').evaluate(img=>CSS.supports('mask-image',img.style.maskImage)), true);
  assert.equal(await page.locator('.portrait-blink-frame').evaluate(img=>getComputedStyle(img).maskImage !== 'none'), true);
  await page.waitForFunction(() => document.querySelector('.stage').dataset.blink === 'closed', undefined, {polling:'raf',timeout:12000});
  // Hold a captured frame for visual inspection, then let the actual timer reopen it.
  await page.screenshot({path:resolve(motionOutput,'blink-closed.png')});
  await page.waitForFunction(() => document.querySelector('.stage').dataset.blink === 'open');
  await page.screenshot({path:resolve(motionOutput,'blink-open.png')});
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.waitForFunction(()=>document.querySelector('.stage').dataset.blink==='open');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.evaluate(async()=>{
    fixturePlatform.isTauriRuntime=true;
    try { if(!await petFixture.controllers.music.playWorkspaceAudioItem({handle:'motion',title:'动效测试'}))throw new Error('fixture_music_failed'); }
    finally {fixturePlatform.isTauriRuntime=false;}
    petFixture.controllers.bubble.hideBubble();
  });
  await page.waitForFunction(()=>document.querySelector('.stage').dataset.passiveMotion==='music');
  assert.equal(await page.locator('.portrait-motion').evaluate(el=>getComputedStyle(el).animationName),'pet-music-sway');
  const firstMatrix=await page.locator('.portrait-motion').evaluate(el=>getComputedStyle(el).transform);
  await page.waitForTimeout(800);
  assert.notEqual(await page.locator('.portrait-motion').evaluate(el=>getComputedStyle(el).transform),firstMatrix);
  await page.evaluate(()=>petFixture.controllers.input.showChatInput());
  await page.waitForFunction(()=>document.querySelector('.stage').dataset.passiveMotion!=='music');
  await page.evaluate(()=>petFixture.controllers.input.hideChatInput());
  await page.waitForFunction(()=>document.querySelector('.stage').dataset.passiveMotion==='music');
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.waitForFunction(()=>document.querySelector('.stage').dataset.passiveMotion!=='music');
  assert.equal(await page.locator('.portrait-motion').evaluate(el=>getComputedStyle(el).animationName),'none');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.waitForFunction(()=>document.querySelector('.stage').dataset.passiveMotion==='music');
  await page.evaluate(()=>petFixture.controllers.care.applyAuthoritativeCareSnapshot({...careFixture,energy:20,updated_at:Date.now()}));
  await page.waitForFunction(()=>petFixture.state.currentEmotion==='tired' && document.querySelector('.stage').dataset.passiveMotion!=='music');
  await page.evaluate(()=>petFixture.controllers.care.applyAuthoritativeCareSnapshot({...careFixture,updated_at:Date.now()}));
  await page.waitForFunction(()=>document.querySelector('.stage').dataset.passiveMotion==='music');
  await page.evaluate(()=>petFixture.controllers.music.stopMusic({silent:true,announce:false}));
  await page.waitForFunction(()=>petFixture.state.currentEmotion==='normal' && document.querySelector('.stage').dataset.passiveMotion!=='music');
  for (const [id, background] of [['normal','#f4f7fc'],['music','#232832'],['hungry','#f4f7fc'],['tired','#232832']]) {
    await page.evaluate(({id,background}) => {document.body.style.background=background;petFixture.controllers.character.setPetEmotion(id,{persist:false});}, {id,background});
    await page.waitForFunction(id => document.querySelector('.stage').dataset.expression === id && document.querySelector('#pet-image').complete, id);
    await page.screenshot({ path: resolve(output, `${id}.png`) });
    await page.evaluate(() => {petFixture.state.hitboxOverlay=true;petFixture.controllers.window.scheduleNativeHitTestSync({force:true});});
    await page.waitForFunction(() => document.querySelectorAll('.hitbox-overlay-shape[data-kind="pet"]').length > 5);
    assert.ok(await page.locator('.hitbox-overlay-shape[data-kind="pet"]').count() < 1000, 'small mask bounds native work');
    await page.evaluate(() => {petFixture.state.hitboxOverlay=false;petFixture.controllers.window.scheduleNativeHitTestSync({force:true});});
  }
  // Overwriting a filename must invalidate both HTTP image and alpha-mask caches.
  await page.evaluate(() => petFixture.controllers.character.setPetEmotion('normal',{persist:false}));
  await page.waitForFunction(() => document.querySelector('.stage').dataset.expression === 'normal' && !document.querySelector('.stage').dataset.expressionPending);
  const firstPortraitUrl = await page.locator('#pet-image').getAttribute('src');
  const opaquePng = await page.evaluate(() => {
    const canvas=document.createElement('canvas'); canvas.width=canvas.height=16;
    const ctx=canvas.getContext('2d'); ctx.fillStyle='white'; ctx.fillRect(0,0,16,16);
    return canvas.toDataURL('image/png').split(',')[1];
  });
  normalOverride = Buffer.from(opaquePng, 'base64');
  assert.equal(await page.evaluate(() => petFixture.controllers.character.reloadCharacterResources({silent:true})), true);
  await page.waitForFunction(() => document.querySelector('#pet-image').naturalWidth === 16 && document.querySelector('.stage').dataset.petHitShape === 'fallback');
  const replacedPortraitUrl = await page.locator('#pet-image').getAttribute('src');
  assert.notEqual(replacedPortraitUrl, firstPortraitUrl);
  normalOverride = null;
  assert.equal(await page.evaluate(() => petFixture.controllers.character.reloadCharacterResources({silent:true})), true);
  await page.waitForFunction(() => document.querySelector('#pet-image').naturalWidth === 1024 && document.querySelector('.stage').dataset.petHitShape === 'alpha');
  const refreshedPortraitUrl = await page.locator('#pet-image').getAttribute('src');
  assert.notEqual(refreshedPortraitUrl, replacedPortraitUrl);
  await page.evaluate(() => petFixture.controllers.character.setPetEmotion('normal',{persist:false,force:true}));
  await page.waitForFunction(() => !document.querySelector('.stage').dataset.expressionPending);
  assert.equal(await page.locator('#pet-image').getAttribute('src'), refreshedPortraitUrl, 'regular expression rendering keeps the same cache version');
  // The same authoritative snapshot handler receives desktop actions and QQ updates.
  await page.evaluate(() => {
    petFixture.controllers.character.setPetEmotion('happy',{persist:false});
    petFixture.controllers.bubble.showBubbleText('我先把这句话说完呀。',{kind:'reply'});
    petFixture.controllers.care.applyAuthoritativeCareSnapshot({...careFixture,hunger:20,updated_at:Date.now()});
  });
  await page.waitForTimeout(100);
  assert.equal(await page.evaluate(() => petFixture.state.currentEmotion), 'happy');
  await page.evaluate(() => petFixture.controllers.bubble.hideBubble());
  await page.waitForFunction(() => petFixture.state.currentEmotion === 'hungry');
  await page.evaluate(() => petFixture.controllers.care.applyAuthoritativeCareSnapshot({...careFixture,hunger:34,updated_at:Date.now()}));
  await page.waitForTimeout(50); assert.equal(await page.evaluate(() => petFixture.state.currentEmotion), 'hungry');
  await page.evaluate(() => petFixture.controllers.care.applyAuthoritativeCareSnapshot({...careFixture,hunger:35,updated_at:Date.now()}));
  await page.waitForFunction(() => petFixture.state.currentEmotion === 'normal');
  await page.locator('#pet-hitbox').dblclick();
  assert.equal(await page.locator('#chat-form').isVisible(), true);
  await page.evaluate(() => petFixture.controllers.input.hideChatInput());
  for (const scale of [0.75, 1.25]) {
    await page.evaluate(scale => {petFixture.state.scale=scale;petFixture.controllers.window.applyVisualState();}, scale);
    await page.screenshot({ path: resolve(output, `scale-${scale}.png`) });
    assert.equal(await page.evaluate(() => document.querySelector('.stage').dataset.petHitShape), 'alpha');
  }
  // A failed/slow new asset cannot replace the visible expression or its hit mask.
  await page.evaluate(imageOrigin => {petFixture.resourceState.outfit.emotions.push({id:'delayed',url:imageOrigin+'/delayed.png'});petFixture.controllers.character.setPetEmotion('delayed',{persist:false});petFixture.controllers.character.setPetEmotion('music',{persist:false});}, imageOrigin);
  await page.waitForTimeout(750);
  assert.equal(await page.evaluate(() => document.querySelector('.stage').dataset.expression), 'music');
  await page.evaluate(imageOrigin => {petFixture.resourceState.outfit.emotions.find(e=>e.id==='hungry').url=imageOrigin+'/missing.png';petFixture.state.care.hunger=20;petFixture.controllers.music.setRestingPetEmotion();}, imageOrigin);
  await page.waitForFunction(() => petFixture.state.currentEmotion === 'normal');
  assert.deepEqual(errors, []);
  await page.evaluate(() => petFixture.dispose());
  if (process.env.AKANE_MOTION_PREVIEW === '1') {
    const context = await browser.newContext({viewport:{width:420,height:680},recordVideo:{dir:motionOutput,size:{width:420,height:680}}});
    const preview = await context.newPage();
    await preview.route('http://127.0.0.1:9999/**',route=>route.fulfill({contentType:'application/json',headers:{'Access-Control-Allow-Origin':'*'},body:JSON.stringify({status:'ok',root_binding:'valid',instance_id:'local-default',defaultBotId:'fixture',bots:[{botId:'fixture',available:true}]})}));
    preview.on('pageerror',error=>errors.push(error.message));
    await preview.goto(server.resolvedUrls.local[0]);
    await preview.waitForFunction(()=>window.ready);
    assert.equal(await preview.evaluate(()=>ready),true);
    await preview.waitForFunction(()=>petFixture.controllers.settings.buildSettingsSnapshot().visual.idleAnimation.blink==='ready');
    await preview.evaluate(()=>{document.body.style.background='#f4f7fc';});
    await preview.waitForTimeout(10000);
    await preview.evaluate(async()=>{
      fixturePlatform.isTauriRuntime=true;
      try{if(!await petFixture.controllers.music.playWorkspaceAudioItem({handle:'preview',title:'听歌'}))throw new Error('preview_music_failed');}
      finally{fixturePlatform.isTauriRuntime=false;}
      petFixture.controllers.bubble.hideBubble();
    });
    await preview.waitForFunction(()=>document.querySelector('.stage').dataset.passiveMotion==='music');
    await preview.waitForTimeout(6500);
    await preview.screenshot({path:resolve(motionOutput,'music-sway.png')});
    await preview.evaluate(()=>petFixture.dispose());
    await context.close();
    await preview.video().saveAs(resolve(motionOutput,'idle-and-music.webm'));
    assert.deepEqual(errors,[]);
  }
  console.log('Cecilia browser smoke passed: real controller graph, scaled head edge pointer/drag routing, blink texture/CSS mask/timer, real WAV playback/sway, input/reduced-motion/care pause and restore, 4 transparent expressions, 2 scales, DPI 1.5, CORS alpha masks, overwritten image/cache reload, ID before aliases, deferred reply/care recovery, double-click input, slow/failed images; 0 page errors');
} finally {
  await browser?.close(); await server.close(); await new Promise(resolve => images.close(resolve));
}
