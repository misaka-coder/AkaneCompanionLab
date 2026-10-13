// Production bubble and chat presentation with isolated fixtures, no live chat.
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'vite';

const { chromium } = await import(process.env.AKANE_PLAYWRIGHT_MODULE || 'playwright');
const output = new URL('../../output/reply-aside-20261006/', import.meta.url);
await mkdir(output, { recursive: true });
let portrait, portraitType = 'image/png';
try {
  portrait = await readFile(new URL('../../desktop_pet_creator_kit/characters/cecilia/assets/characters/default/normal.png', import.meta.url));
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
  portraitType = 'image/svg+xml';
  portrait = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1536"><ellipse cx="512" cy="420" rx="280" ry="350" fill="#b8cdb2"/><path d="M200 850 Q512 900 824 850 L950 1500 H74Z" fill="#b8cdb2"/></svg>');
}
const server = await createServer({ server: { host:'127.0.0.1', port:0, hmr:false }, plugins:[{
  name:'isolated-reply-asides', configureServer(instance) {
    instance.middlewares.use((req,res,next) => {
      if (req.url === '/__cecilia.png') {res.setHeader('Content-Type',portraitType);res.end(portrait);return;}
      if (req.url === '/__aside.html') {
        res.setHeader('Content-Type','text/html');
        res.end(`<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/src/pet-surface.css">
<style>body{margin:0;background:#e6eff1}.stage{position:relative;width:420px;height:650px}</style></head><body>
<main class="stage"><div class="pet-hitbox"><img src="/__cecilia.png"></div><div id="bubble" class="bubble"><span id="text" class="bubble-text"></span></div></main>
<script type="module">import {createBubbleController} from '/src/pet/bubble.js';
window.bubble=createBubbleController({state:{voiceEnabled:false},resourceState:{tts:{enabled:false}},
els:{bubble:document.querySelector('#bubble'),bubbleText:document.querySelector('#text')},
lifecycle:{setTimeout:(...args)=>setTimeout(...args),clearTimeout:id=>clearTimeout(id)},
visualRenderer:{setMotion(){}},chat:{activeTurnToken:1,isTurnActive:token=>token===1,sending:false},
interaction:{clearLocalInteraction(){}},status:{setRuntimeStatus(){},updateActivityControls(){}},
window:{scheduleNativeHitTestSync(){}},music:{scheduleMusicEmotionRestore(){}},
tts:{hasLocalTtsPlayback:()=>false},voice:{hasRealtimeVoiceCallPlayback:()=>false},
attachments:{applyPayloadFileDeliveries(){throw Error('History cannot deliver files')},applyPayloadBrowserEvents(){throw Error('History cannot reopen tabs')}},
character:{setPetEmotion(){}},care:{applyAuthoritativeCareSnapshot(){}}});</script></body></html>`);return;
      }
      if (req.url === '/__aside-chat.html') {
        res.setHeader('Content-Type','text/html');
        res.end('<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/src/control-center-v2/styles.css"><style>#chat{padding:20px;height:100%}</style></head><body><div id="chat"></div></body></html>');return;
      }
      next();
    });
  }
}] });
let browser;
const checks=[];
try {
  await server.listen();
  browser=await chromium.launch({channel:process.env.AKANE_BROWSER_CHANNEL||'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:420,height:650}});
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(server.resolvedUrls.local[0]+'__aside.html');
  await page.waitForFunction(()=>window.bubble);
  const cases=[
    ['（轻轻拉住你的袖口）今天也一起慢慢来吧。',['（轻轻拉住你的袖口）']],
    ['我在呀。(轻轻点头)别担心。',['(轻轻点头)']],
    ['（低下头（悄悄脸红））我记住了。',['（低下头（悄悄脸红））']],
    ['嗯（微笑）！(眨眨眼)那就说好了。',['（微笑）','(眨眨眼)']],
    ['🙂我在这里（轻轻',['（轻轻']],
    ['🙂我在这里（轻轻点头）。',['（轻轻点头）']],
    ['这个括号)是正文。',[]],
    ['（<img src=x onerror="window.unsafe=true">）<script>window.unsafe=true</script>&',['（<img src=x onerror="window.unsafe=true">）']],
    ['没有动作描写的普通台词。',[]],['',[]]
  ];
  for (const [text,asides] of cases) {
    const rendered=await page.evaluate(text=>{
      bubble.showBubbleText(text,{kind:'reply'});
      return {text:document.querySelector('#text').textContent,
        asides:[...document.querySelectorAll('#text .reply-aside')].map(span=>span.textContent),
        markup:document.querySelectorAll('#text img,#text script').length,unsafe:Boolean(window.unsafe)};
    },text);
    assert.equal(rendered.text,text);
    assert.deepEqual(rendered.asides,asides);
    assert.equal(rendered.markup,0);assert.equal(rendered.unsafe,false);
  }
  checks.push('Exact source text, both parenthesis types, nesting, partial updates, empty text and safe literal markup');
  await page.evaluate(()=>bubble.showBubbleText('状态码（详情）',{kind:'status'}));
  assert.equal(await page.locator('#text .reply-aside').count(),0);
  await page.evaluate(()=>bubble.restoreLatestReply({latest_final_json:{emotion:'happy',utterances:[{text:'（微笑）我记得呀。',say:'覚えています。'}],speech:'（微笑）我记得呀。'}}));
  assert.equal(await page.locator('#text .reply-aside').textContent(),'（微笑）');
  checks.push('Restored reply styling and unstyled system status');
  const colors={};
  for (const style of ['soft','paper','clear','dark']) {
    colors[style]=await page.evaluate(style=>{
      document.querySelector('.stage').dataset.bubbleStyle=style;
      bubble.showBubbleText('（轻轻拉住你的袖口）那就这么说好了，今天也一起慢慢来吧。',{kind:'reply'});
      const text=document.querySelector('#text'),aside=text.querySelector('.reply-aside');
      return {speech:getComputedStyle(text).color,aside:getComputedStyle(aside).color,
        sameSize:getComputedStyle(text).fontSize===getComputedStyle(aside).fontSize,
        fits:text.getBoundingClientRect().right<=innerWidth};
    },style);
    assert.notEqual(colors[style].speech,colors[style].aside);
    assert.equal(colors[style].sameSize,true);assert.equal(colors[style].fits,true);
    await page.screenshot({path:new URL('bubble-'+style+'.png',output).pathname.replace(/^\/([A-Za-z]:)/,'$1')});
  }
  await page.evaluate(()=>bubble.hideBubble());
  assert.equal(await page.locator('#text .reply-aside').count(),0);
  checks.push('Four bubble themes, unchanged font metrics and cleanup');

  await page.setViewportSize({width:1100,height:720});
  await page.goto(server.resolvedUrls.local[0]+'__aside-chat.html');
  const text='（轻轻拉住你的袖口）那就这么说好了。\n(微笑)今天也一起慢慢来吧。';
  await page.evaluate(async text=>{
    const {renderChat}=await import('/src/control-center-v2/components/chat.js');
    window.chatState={actionStates:{},viewModel:{shell:{connected:true},character:{displayName:'塞西莉亚'},
      activity:{phase:'idle'},actions:{'chat.send':{available:true},'chat.attach':{available:true}},
      chat:{characterName:'塞西莉亚',totalCount:2,messages:[{id:'user',role:'user',content:'今天也想和你一起休息一下（不是在发指令）。'},
        {id:'assistant',role:'assistant',content:text}],outputs:[]}}};
    window.render=()=>document.querySelector('#chat').innerHTML=renderChat(chatState);
    render();
  },text);
  for (const theme of ['dark','light']) {
    const chatColor=await page.evaluate(theme=>{
      document.documentElement.dataset.theme=theme;render();
      const body=document.querySelector('.chat-message.is-assistant .message-content > p');
      return {body:getComputedStyle(body).color,aside:getComputedStyle(body.querySelector('.reply-aside')).color,
        asides:[...body.querySelectorAll('.reply-aside')].map(span=>span.textContent),
        userAsides:document.querySelectorAll('.chat-message.is-user .reply-aside').length};
    },theme);
    assert.notEqual(chatColor.body,chatColor.aside);assert.deepEqual(chatColor.asides,['（轻轻拉住你的袖口）','(微笑)']);
    assert.equal(chatColor.userAsides,0);
    assert.equal(await page.evaluate(()=>chatState.viewModel.chat.messages[1].content),text);
    await page.screenshot({path:new URL('chat-'+theme+'.png',output).pathname.replace(/^\/([A-Za-z]:)/,'$1')});
  }
  await page.evaluate(()=>{chatState.viewModel.chat.messages[1].content='（<img src=x onerror="window.unsafe=true">）<script>window.unsafe=true</script>&';render();});
  assert.equal(await page.locator('.chat-message.is-assistant .message-content > p img,.chat-message.is-assistant .message-content > p script').count(),0);
  assert.equal(await page.evaluate(()=>Boolean(window.unsafe)),false);
  await page.evaluate(()=>{chatState.viewModel.chat.messages=[];render();});
  assert.equal(await page.locator('.reply-aside').count(),0);
  checks.push('Light/dark chat, assistant-only highlighting, safe HTML, unchanged history and re-entry');
  assert.deepEqual(errors,[]);
  await writeFile(new URL('verification.json',output),JSON.stringify({checks,colors,errors},null,2));
  console.log('Reply aside browser checks passed:',checks.join('; '));
} finally {await browser?.close();await server.close();}
