// Production DOM/controller graph with native staging controlled; Edge decodes real WAV audio.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createServer } from 'vite';

const { chromium } = await import(process.env.AKANE_PLAYWRIGHT_MODULE || 'playwright');
const html = (await readFile(new URL('../index.html', import.meta.url), 'utf8'))
  .replace('<script type="module" src="/src/main.js"></script>', '')
  .replace('</head>', '<link rel="stylesheet" href="/src/styles.css?direct"></head>');
const samples = 8000 * 30;
const wav = Buffer.alloc(44 + samples * 2);
wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
wav.writeUInt32LE(8000, 24); wav.writeUInt32LE(16000, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
wav.write('data', 36); wav.writeUInt32LE(samples * 2, 40);
const server = await createServer({ server: { host: '127.0.0.1', port: 0, hmr: false }, appType: 'custom' });
server.middlewares.use((request, response, next) => {
  if (request.url === '/music-smoke.html') {
    response.setHeader('Content-Type', 'text/html; charset=utf-8'); response.end(html); return;
  }
  if (request.url?.startsWith('/music/')) {
    response.setHeader('Content-Type', 'audio/wav');
    response.setHeader('Accept-Ranges', 'bytes');
    const range = request.headers.range?.match(/^bytes=(\d+)-(\d*)$/);
    const start = range ? Number(range[1]) : 0;
    const end = range?.[2] ? Math.min(Number(range[2]), wav.length - 1) : wav.length - 1;
    if (range) { response.statusCode = 206; response.setHeader('Content-Range', `bytes ${start}-${end}/${wav.length}`); }
    response.setHeader('Content-Length', end - start + 1);
    response.end(wav.subarray(start, end + 1)); return;
  }
  next();
});
let browser;
try {
  await server.listen();
  browser = await chromium.launch({ channel: process.env.AKANE_BROWSER_CHANNEL || 'msedge', headless: true,
    args: ['--autoplay-policy=no-user-gesture-required', '--mute-audio'] });
  const page = await browser.newPage({ viewport: { width: 420, height: 680 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const open = async () => {
    await page.goto(server.resolvedUrls.local[0] + 'music-smoke.html');
    await page.evaluate(async () => {
      const { createPetApplication } = await import('/src/pet/application.js');
      const nativeCalls = [], pending = [];
      const invoke = async (command, payload) => {
        nativeCalls.push({ command, payload });
        if (command === 'open_workspace_item') {
          if (payload.handle === 'failure') throw new Error('staging unavailable');
          return { local_path: location.origin + '/music/' + payload.handle + '.wav' };
        }
        if (command === 'prepare_audio_asset') {
          const asset = { cachedPath: payload.path, fileName: payload.path.split('/').pop(), extension: 'wav', lyricText: '[00:00]浏览器歌词' };
          if (payload.path.includes('/slow.')) return new Promise(resolve => pending.push(() => resolve(asset)));
          return asset;
        }
        return null;
      };
      const platform = {
        window, document, navigator, MediaRecorder: window.MediaRecorder, isTauriRuntime: true, appWindow: null,
        invoke, tauriCall: invoke, convertFileSrc: path => path,
        backendFetch: async () => new Response(JSON.stringify({ ok: true, session: {}, sections: { files: [], outputs: [] } })),
        emit: async () => {}, emitTo: async () => {}, listen: async () => () => {}
      };
      const app = createPetApplication({ platform, bundledCharacterAssets: {} });
      app.state.backendUrl = location.origin;
      app.state.boundBotId = 'browser-fixture';
      app.state.sessionId = 'browser-session';
      app.state.voiceEnabled = false;
      app.resourceState.health = 'online';
      app.controllers.ui.bindUi();
      app.controllers.status.updateActivityControls();
      window.musicFixture = { app, music: app.controllers.music, nativeCalls, pending };
    });
  };
  const click = id => page.locator(id).evaluate(button => button.click());
  const playing = source => page.waitForFunction(source => window.musicFixture.music.musicPlaying &&
    window.musicFixture.music.musicTrack?.workspaceHandle === source && !document.querySelector('#music-player').paused, source);
  await open();
  assert.equal(await page.locator('#toggle-music').isDisabled(), true);
  assert.equal(await page.evaluate(() => window.musicFixture.music.playWorkspaceAudioItem({ handle: 'one', title: '第一首', format: 'wav' })), true,
    await page.locator('#runtime-status').textContent());
  await playing('one');
  assert.equal(await page.locator('#toggle-music').textContent(), '暂停音乐');
  await page.waitForFunction(() => Number.isFinite(document.querySelector('#music-player').duration));
  await page.evaluate(() => window.musicFixture.music.seekMusicPlayback(5));
  await page.waitForFunction(() => document.querySelector('#music-player').currentTime >= 4.9);
  assert.equal(await page.evaluate(() => window.musicFixture.music.playWorkspaceAudioItem({ handle: 'two', title: '第二首', format: 'wav' })), true);
  await playing('two');
  assert.equal(await page.locator('#previous-music').isEnabled(), true);
  await click('#previous-music');
  await playing('one');
  await click('#toggle-music');
  await page.waitForFunction(() => window.musicFixture.music.musicPaused && document.querySelector('#music-player').paused);
  assert.equal(await page.locator('#toggle-music').textContent(), '继续音乐');
  await click('#toggle-music');
  await playing('one');
  await click('#next-music');
  await playing('two');
  await click('#stop-music');
  await page.waitForFunction(() => !window.musicFixture.music.musicPlaying && document.querySelector('#music-player').paused);
  await click('#clear-music-queue');
  assert.equal(await page.evaluate(() => window.musicFixture.music.musicQueue.length), 0);
  assert.equal(await page.locator('#toggle-music').isDisabled(), true);

  await page.evaluate(() => {
    const { music } = window.musicFixture;
    window.musicFixture.result = music.playWorkspaceAudioItem({ handle: 'slow', title: '慢音频' });
  });
  await page.waitForFunction(() => window.musicFixture.pending.length === 1 && window.musicFixture.music.musicLoading);
  assert.equal(await page.locator('#toggle-music').isDisabled(), true);
  // A real Bot/character rebind still changes request ownership; chatting does not rotate sessions.
  await page.evaluate(() => { window.musicFixture.app.state.boundBotId = 'another-browser-fixture'; });
  await page.evaluate(() => window.musicFixture.pending.shift()());
  assert.equal(await page.evaluate(() => window.musicFixture.result), false);
  assert.equal(await page.evaluate(() => window.musicFixture.music.musicQueue.length), 0);
  assert.equal(await page.evaluate(() => window.musicFixture.music.musicLoading), false);
  assert.equal(await page.evaluate(() => window.musicFixture.music.playWorkspaceAudioItem({ handle: 'failure' })), false);
  assert.ok((await page.locator('#runtime-status').textContent()).includes('staging unavailable'));
  assert.equal(await page.evaluate(() => window.musicFixture.music.playWorkspaceAudioItem({ handle: 'one' })), true);
  await playing('one');
  await page.evaluate(() => window.musicFixture.app.dispose());
  assert.equal(await page.locator('#music-player').evaluate(audio => audio.paused && !audio.getAttribute('src')), true);
  await open();
  assert.equal(await page.locator('#toggle-music').isDisabled(), true);
  assert.equal(await page.evaluate(() => window.musicFixture.music.playWorkspaceAudioItem({ handle: 'two' })), true);
  await playing('two');
  await page.evaluate(() => window.musicFixture.app.dispose());
  assert.deepEqual(errors, []);
  console.log('pet music browser smoke: real DOM, controller graph, WAV decode/play/pause/resume, queue controls, seek, slow preparation/Bot switch, failure, dispose and re-entry; 0 page errors');
} finally {
  await browser?.close();
  await server.close();
}
