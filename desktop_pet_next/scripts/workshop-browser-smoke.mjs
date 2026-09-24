import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { resolve, dirname, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
const { chromium } = await import(process.env.AKANE_PLAYWRIGHT_MODULE || "playwright");
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
// Exercise production DOM and event handlers with deferred I/O at the Tauri boundary.
// This fixture never writes a real character pack or activates a desktop character.
let source = await readFile(resolve(root, "src/workshop.js"), "utf8");
source = source.replace(/^import[\s\S]*?from "@tauri[^\n]*\n/gm, "")
  .replace('import "./workshop.css";', '')
  .replace('const isTauriRuntime = Boolean(window.__TAURI_INTERNALS__);', 'const isTauriRuntime = true;')
  .replace('const appWindow = isTauriRuntime ? getCurrentWindow() : null;', 'const appWindow = null;')
  .replace('boot();', 'bindUi(); bindInstanceStorage("workshop-smoke");');
source = `const invoke = (...args) => window.fixtureInvoke(...args);\nconst tauriFetch = (...args) => window.fixtureFetch(...args);\n` + source + `
window.workshop = { view, switchTab, saveCalibration, saveDraft, loadDraft, autoSaveDraft,
  loadCalibrationTab, loadCalibrationOutfitPreview, normalizePacks, findPack,
  sendTestChatMessage, resetTestChatSession, clearDraft,
  refreshPacks, applySnapshot, applyPack, openCreateDialog, createCharacterPack,
  openContextLibraryDialog, createContextLibrary, importPack, exportPack,
  render, calibrationPreview, loadPortraitsTab, createOutfit, setDefaultPortrait, previewEmotion, runPortraitCutoutForPreview,
  ready: () => calibrationReadyTarget, clearImages: () => portraitImageUrlCache.clear() };
`;
const html = (await readFile(resolve(root, "workshop.html"), "utf8"))
  .replace('</head>', '<link rel="stylesheet" href="/src/workshop.css"></head>');
const png = [...await readFile(resolve(root, "src/assets/characters/猫娘/正常.png"))];
const server = createServer(async (req, res) => {
  try {
    if (req.url === "/") { res.setHeader("Content-Type", "text/html; charset=utf-8"); res.end(html); return; }
    if (req.url === "/src/workshop.js") { res.setHeader("Content-Type", "text/javascript"); res.end(source); return; }
    const path = resolve(root, `.${decodeURIComponent(new URL(req.url, 'http://local').pathname)}`);
    if (!path.startsWith(root + sep)) { res.writeHead(403).end(); return; }
    res.setHeader("Content-Type", ({ '.js': 'text/javascript', '.css': 'text/css' })[extname(path)] || 'application/octet-stream');
    res.end(await readFile(path));
  } catch { res.writeHead(404).end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
let browser;
try {
  browser = await chromium.launch({ channel: process.env.AKANE_BROWSER_CHANNEL || 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1180, height: 820 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(({ png }) => {
    window.calls = []; window.pending = [];
    window.outfits = [{ id: 'default', emotions: [{ id: 'normal', path: 'normal.png' }] }, { id: 'empty', emotions: [] }];
    window.fixtureInvoke = (command, args) => {
      calls.push({ command, args });
      if (command === "verify_backend_instance") return Promise.resolve({ ok: true, instanceId: args?.instanceId || "" });
      if (command === 'read_portrait_image' && !window.deferImage) return Promise.resolve(png);
      if (command === 'list_pack_assets' && !window.deferAssets) return Promise.resolve(outfits);
      return new Promise((resolve, reject) => pending.push({ command, args, resolve, reject }));
    };
    window.fetches = [];
    window.fixtureFetch = (url, init) => {
      if (new URL(url).pathname === '/capabilities/workflows') {
        if (window.deferWorkflow) return new Promise(resolve => { window.workflowResolve = resolve; });
        return Promise.resolve(Response.json({ workflows: [] }));
      }
      return new Promise((resolve, reject) => fetches.push({ url, init, resolve, reject }));
    };
    window.makeStream = () => {
      let controller;
      const stream = new ReadableStream({ start(c) { controller = c; } });
      return { response: new Response(stream),
        push(event) { controller.enqueue(new TextEncoder().encode(JSON.stringify(event) + '\n')); },
        close() { controller.close(); } };
    };
    window.resolveNext = (command, value, reject = false) => {
      const index = pending.findIndex(p => p.command === command);
      if (index < 0) throw Error('No pending ' + command);
      const item = pending.splice(index, 1)[0];
      item[reject ? 'reject' : 'resolve'](value);
    };
  }, { png });
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.waitForFunction(() => window.workshop);
  await page.evaluate(() => {
    workshop.view.packs = workshop.normalizePacks(['a', 'b'].map(id => ({ id, profile: {
      identity: { name: id }, layout: { outfits: { default: { window: { width: 380, height: 620 }, portrait: { scale: 0.8, offset_x: 12, offset_y: -20 }, bubble: { anchor_x: 0, anchor_y: 0.7, style: 'paper' } } } }
    } })));
    window.deferAssets = true;
    workshop.switchTab('calibration', 'a'); workshop.switchTab('calibration', 'b');
    const b = pending.findIndex(p => p.args.packId === 'b'); pending.splice(b, 1)[0].resolve(outfits);
  });
  await page.waitForFunction(() => workshop.ready()?.packId === 'b');
  assert.equal(await page.locator('#cal-scale').inputValue(), '80', 'saved calibration fills actual input');
  assert.equal(await page.locator('#cal-bubble-x').inputValue(), '0', 'zero anchor preserved');
  await page.evaluate(() => resolveNext('list_pack_assets', [], true));
  assert.equal(await page.locator('#calibration-content').isVisible(), true, 'stale failure cannot hide B');
  await page.locator('#cal-scale').fill('124');
  assert.match(await page.locator('#calibration-image').getAttribute('style'), /scale\(1.24\)/, 'real slider input updates preview');
  await page.locator('#calibration-save-btn').click();
  await page.evaluate(() => { void workshop.saveCalibration('b'); });
  assert.equal(await page.evaluate(() => calls.filter(c => c.command === 'save_calibration').length), 1, 'duplicate save suppressed');
  assert.equal(await page.locator('#cal-scale').isDisabled(), true);
  await page.evaluate(() => { workshop.switchTab('persona', 'a'); resolveNext('save_calibration', null); });
  await page.waitForFunction(() => workshop.findPack('b')._rawProfile.layout.outfits.default.portrait.scale === 1.24);
  assert.match(await page.locator('#workshop-status').textContent(), /正在编辑/, 'old save leaves current tab status alone');
  // Empty outfit must invalidate a slow image read and leave controls disabled.
  await page.evaluate(() => { window.deferAssets = false; window.deferImage = true; workshop.clearImages(); workshop.switchTab('calibration', 'a'); });
  await page.waitForFunction(() => pending.some(p => p.command === 'read_portrait_image'));
  await page.locator('#calibration-outfit-select').selectOption('empty');
  await page.evaluate(png => resolveNext('read_portrait_image', png), png);
  assert.equal(await page.locator('#calibration-save-btn').isDisabled(), true);
  assert.equal(await page.locator('#calibration-image').getAttribute('src'), null);
  await page.evaluate(() => { window.deferImage = false; workshop.switchTab('calibration', 'a'); });
  await page.waitForFunction(() => workshop.ready()?.packId === 'a');
  await page.locator('#calibration-save-btn').click();
  await page.evaluate(() => resolveNext('save_calibration', 'disk unavailable', true));
  await page.waitForFunction(() => !document.querySelector('#calibration-save-btn').disabled);
  assert.match(await page.locator('#workshop-status').textContent(), /保存校准失败/);
  // Real persona input, repeated tab selection, deferred save and continued typing.
  await page.evaluate(() => workshop.switchTab('persona', 'a'));
  await page.locator('#field-name').fill('A first');
  assert.match(await page.locator('#draft-status').textContent(), /未保存/);
  await page.evaluate(() => { workshop.switchTab('persona', 'a'); window.firstSave = workshop.saveDraft(); void workshop.saveDraft(); });
  assert.equal(await page.locator('#field-name').inputValue(), 'A first');
  assert.equal(await page.evaluate(() => calls.filter(c => c.command === 'save_character_pack').length), 1);
  await page.locator('#field-name').fill('A newer');
  await page.evaluate(() => { workshop.switchTab('persona', 'b'); resolveNext('save_character_pack', { id: 'a', profile: { identity: { name: 'A first' } } }); });
  assert.equal(await page.evaluate(async () => (await firstSave).reason), 'newer-edits-pending');
  assert.equal(await page.locator('#field-name').inputValue(), 'b');
  assert.equal(await page.evaluate(() => workshop.loadDraft('a').identity.name), 'A newer');
  await page.locator('#field-name').fill('B local');
  await page.evaluate(() => { window.secondSave = workshop.saveDraft(); workshop.switchTab('persona', 'a'); resolveNext('save_character_pack', 'disk unavailable', true); });
  assert.equal(await page.evaluate(async () => (await secondSave).packId), 'b');
  assert.equal(await page.locator('#field-name').inputValue(), 'A newer');
  assert.equal(await page.evaluate(() => workshop.loadDraft('b').identity.name), 'B local');
  assert.equal(await page.evaluate(() => workshop.loadDraft('a').identity.name), 'A newer');
  // A successful save clears only the exact saved draft; the latest form survives.
  await page.evaluate(() => { window.thirdSave = workshop.saveDraft(); resolveNext('save_character_pack', { id: 'a', profile: { identity: { name: 'A newer' } } }); });
  assert.equal(await page.evaluate(async () => (await thirdSave).ok), true);
  assert.equal(await page.evaluate(() => workshop.loadDraft('a')), null);
  assert.ok(await page.evaluate(() => workshop.loadDraft('b')));
  // Testing a different character must save that character's stored draft, not the open form.
  await page.evaluate(() => { workshop.switchTab('test', 'b'); document.querySelector('#test-chat-input').value = 'test B';
    window.testB = workshop.sendTestChatMessage(); void workshop.sendTestChatMessage(); });
  const bSave = await page.evaluate(() => pending.find(p => p.command === 'save_character_pack').args.request);
  assert.equal(bSave.packId, 'b'); assert.equal(bSave.identity.name, 'B local');
  assert.equal(await page.evaluate(() => pending.filter(p => p.command === 'save_character_pack').length), 1);
  await page.evaluate(() => { workshop.switchTab('test', 'a'); resolveNext('save_character_pack', { id: 'b', profile: { identity: { name: 'B local' } } }); });
  await page.evaluate(() => testB);
  assert.equal(await page.evaluate(() => fetches.length), 0, 'switch during preflight cancels the model request');
  // Start A, switch to B, then deliver A late. It cannot clear B's busy state or diagnostics.
  await page.locator('#test-chat-input').fill('test A');
  await page.evaluate(() => { window.testA = workshop.sendTestChatMessage(); });
  await page.waitForFunction(() => fetches.length === 1);
  const requestA = await page.evaluate(() => JSON.parse(fetches[0].init.body));
  assert.equal(requestA.character_pack_id, 'a'); assert.equal(requestA.workshop_test, true);
  assert.match(requestA.real_user_id, /^workshop_test_profile_a_/);
  await page.evaluate(() => { workshop.switchTab('test', 'b'); document.querySelector('#test-chat-input').value = 'new B'; window.newB = workshop.sendTestChatMessage(); });
  await page.waitForFunction(() => fetches.length === 2);
  assert.equal(await page.evaluate(() => fetches[0].init.signal.aborted), true);
  await page.evaluate(() => fetches[0].resolve(new Response('{"type":"final","speech":"old A","emotion":"angry"}\n')));
  await page.evaluate(() => testA);
  assert.equal(await page.locator('#test-chat-send').isDisabled(), true, 'A finally cannot unlock B');
  assert.equal(await page.locator('#test-response-emotion').textContent(), '-');
  assert.doesNotMatch(await page.locator('#test-chat-log').textContent(), /old A/);
  await page.evaluate(() => { window.streamB = makeStream(); fetches[1].resolve(streamB.response);
    streamB.push({ type: 'speech_segment', text: 'old segment' }); });
  await page.waitForFunction(() => document.querySelector('#test-chat-log').textContent.includes('old segment'));
  await page.evaluate(() => { streamB.push({ type: 'speech_reset', speech: 'corrected' });
    streamB.push({ type: 'final', speech: 'corrected final', emotion: 'happy' }); streamB.close(); });
  await page.evaluate(() => newB);
  assert.match(await page.locator('#test-chat-log').textContent(), /corrected final/);
  assert.doesNotMatch(await page.locator('#test-chat-log').textContent(), /old segment/);
  assert.equal(await page.locator('#test-response-emotion').textContent(), 'happy');
  // Reset is available while streaming and replaces the isolated session immediately.
  await page.locator('#test-chat-input').fill('reset in flight');
  await page.evaluate(() => { window.resetRequest = workshop.sendTestChatMessage(); });
  await page.waitForFunction(() => fetches.length === 3);
  const oldSession = await page.evaluate(() => workshop.view.testScopes.b.sessionId);
  await page.locator('#test-chat-clear').click();
  assert.notEqual(await page.evaluate(() => workshop.view.testScopes.b.sessionId), oldSession);
  assert.equal(await page.locator('#test-chat-send').isDisabled(), false);
  await page.evaluate(() => fetches[2].reject(new Error('late failure')));
  await page.evaluate(() => resetRequest);
  assert.doesNotMatch(await page.locator('#test-chat-log').textContent(), /late failure/);
  assert.match(await page.locator('#test-chat-status').textContent(), /新的隔离测试会话/);
  // Portrait lists and mutation completion cannot restore a previous character's controls.
  await page.evaluate(() => {
    window.deferAssets = true; workshop.switchTab('portraits', 'a'); workshop.switchTab('portraits', 'b');
    pending.splice(pending.findIndex(p => p.command === 'list_pack_assets' && p.args.packId === 'b'), 1)[0]
      .resolve([{ id: 'blue', name: 'B coat', emotions: [{ id: 'normal', path: 'normal.png' }] }]);
  });
  await page.waitForFunction(() => document.querySelector('#outfits-container').textContent.includes('B coat'));
  await page.evaluate(() => resolveNext('list_pack_assets', 'old A error', true));
  assert.equal(await page.locator('#portraits-content').isVisible(), true);
  await page.locator('#outfits-container .emotion-tile').first().click();
  await page.getByRole('button', { name: '设为默认立绘', exact: true }).click();
  await page.evaluate(() => { void workshop.setDefaultPortrait('b', 'blue', 'normal'); });
  assert.equal(await page.evaluate(() => calls.filter(c => c.command === 'set_default_portrait').length), 1);
  await page.evaluate(() => { window.deferAssets = false; workshop.switchTab('portraits', 'a'); });
  await page.waitForFunction(() => !document.querySelector('#add-outfit-btn').disabled);
  assert.equal(await page.locator('#emotion-preview button').count(), 0, 'new character has no old preview actions');
  await page.locator('#new-outfit-id').fill('A new');
  await page.evaluate(() => resolveNext('set_default_portrait', 'ok'));
  assert.equal(await page.evaluate(() => workshop.findPack('b').defaultOutfit), 'blue');
  assert.equal(await page.locator('#new-outfit-id').inputValue(), 'A new');
  assert.doesNotMatch(await page.locator('#portrait-import-status').textContent(), /blue/);
  await page.locator('#add-outfit-btn').click();
  await page.evaluate(() => { void workshop.createOutfit('a'); workshop.switchTab('portraits', 'b'); });
  await page.waitForFunction(() => !document.querySelector('#add-outfit-btn').disabled);
  await page.locator('#new-outfit-id').fill('B keep');
  await page.evaluate(() => resolveNext('create_portrait_outfit', []));
  assert.equal(await page.locator('#new-outfit-id').inputValue(), 'B keep');
  assert.equal(await page.evaluate(() => calls.filter(c => c.command === 'create_portrait_outfit').length), 1);
  // Repeated loads within the same character still honor newest-request ownership.
  await page.evaluate(() => { window.deferAssets = true; void workshop.loadPortraitsTab('b'); void workshop.loadPortraitsTab('b');
    const indexes = pending.map((p, i) => p.command === 'list_pack_assets' ? i : -1).filter(i => i >= 0);
    pending.splice(indexes.at(-1), 1)[0].resolve([{ id: 'fresh', name: 'Fresh coat', emotions: [] }]); });
  await page.waitForFunction(() => document.querySelector('#outfits-container').textContent.includes('Fresh coat'));
  await page.evaluate(() => resolveNext('list_pack_assets', [{ id: 'stale', name: 'Stale coat', emotions: [] }]));
  assert.doesNotMatch(await page.locator('#outfits-container').textContent(), /Stale coat/);
  await page.evaluate(() => { window.deferAssets = false; });
  // A real file chooser exercises the input path; the write ends at the fixture bridge.
  const fileChooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: '导入图片', exact: true }).click();
  await (await fileChooser).setFiles([{ name: 'portrait.png', mimeType: 'image/png', buffer: Buffer.from(png) }]);
  await page.waitForFunction(() => pending.some(p => p.command === 'upload_portrait_image'));
  await page.evaluate(() => resolveNext('upload_portrait_image', 'write failed', true));
  await page.waitForFunction(() => !document.querySelector('#add-outfit-btn').disabled);
  assert.match(await page.locator('#portrait-import-status').textContent(), /成功 0 张.*失败 1 张/);
  assert.equal(await page.locator('input[type=file]').count(), 0, 'temporary chooser is removed');
  await page.locator('#outfits-container .emotion-tile').first().click();
  await page.evaluate(() => { window.deferWorkflow = true; window.cutoutRequest = workshop.runPortraitCutoutForPreview(); void workshop.runPortraitCutoutForPreview(); });
  await page.waitForFunction(() => window.workflowResolve);
  assert.equal(await page.evaluate(() => workshop.view.portraitCutoutRunning), true, 'cutout locks before capability refresh');
  assert.equal(await page.locator('#add-outfit-btn').isDisabled(), true);
  assert.equal(await page.evaluate(async () => (await workshop.saveDraft({ packId: 'b' })).reason), 'save-in-progress', 'other save paths share the pack write guard');
  await page.evaluate(() => { workshop.switchTab('persona', 'a'); workflowResolve(Response.json({ workflows: [] })); });
  await page.evaluate(() => cutoutRequest);
  assert.equal(await page.evaluate(() => workshop.view.portraitCutoutRunning), false);
  assert.equal(await page.evaluate(() => calls.filter(c => c.command === 'backend_admin_request').length), 0, 'leaving before capability check starts no job');
  await page.evaluate(() => { window.deferWorkflow = false; workshop.switchTab('portraits', 'b'); });
  await page.waitForFunction(() => !document.querySelector('#add-outfit-btn').disabled);
  await page.evaluate(() => {
    workshop.findPack('a')._rawProfile.layout = { outfits: { default: {
      window: { width: 730, height: 870 }, portrait: { scale: 0.835, offset_x: 12.5, offset_y: -20.25 },
      bubble: { anchor_x: 0.573, anchor_y: 0.827, max_width: 320, style: 'paper' }
    } } };
    workshop.switchTab('calibration', 'a');
  });
  await page.waitForFunction(() => workshop.ready()?.packId === 'a');
  assert.equal(Number(await page.locator('#cal-scale').inputValue()), 83.5, 'load keeps fractional saved scale');
  assert.equal(Number(await page.locator('#cal-offset-x').inputValue()), 12.5);
  assert.ok(Math.abs(Number(await page.locator('#cal-bubble-x').inputValue()) - 57.3) < 1e-9);
  await page.locator('#calibration-save-btn').click();
  const preserved = await page.evaluate(() => pending.find(p => p.command === 'save_calibration').args.layout);
  assert.equal(preserved.portrait.scale, 0.835); assert.equal(preserved.bubble.max_width, 320);
  assert.equal(preserved.window.width, 730); assert.equal(preserved.window.height, 870);
  await page.evaluate(() => resolveNext('save_calibration', 'ok'));
  await page.waitForFunction(() => !document.querySelector('#calibration-save-btn').disabled);
  for (const viewport of [{ width: 1180, height: 820 }, { width: 730, height: 760 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await page.waitForFunction(() => {
      const host = document.querySelector('#calibration-preview-area').getBoundingClientRect();
      const stage = document.querySelector('#calibration-frame').getBoundingClientRect();
      return stage.width <= host.width && stage.height <= host.height && Math.abs(stage.width / stage.height - 730 / 870) < 0.001;
    });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no page-level horizontal overflow');
    const frame = await page.locator('#calibration-frame').boundingBox();
    assert.ok(frame.x >= 0 && frame.x + frame.width <= viewport.width);
  }
  // Dragging uses the visible scaled rectangle; saved anchors still use logical percentages.
  await page.locator("#calibration-bubble-dot").scrollIntoViewIfNeeded();
  const frame = await page.locator('#calibration-frame').boundingBox();
  const bubbleBox = await page.locator('#calibration-bubble-dot').boundingBox();
  await page.mouse.move(bubbleBox.x + bubbleBox.width / 2, bubbleBox.y + bubbleBox.height / 2);
  await page.mouse.down(); await page.mouse.move(frame.x + frame.width * 0.75, frame.y + frame.height * 0.3); await page.mouse.up();
  assert.equal(Number(await page.locator('#cal-bubble-x').inputValue()), 75);
  assert.equal(Number(await page.locator('#cal-bubble-y').inputValue()), 30);
  await page.locator('#calibration-bubble-dot').focus();
  await page.keyboard.press('ArrowLeft');
  assert.equal(Number(await page.locator('#cal-bubble-x').inputValue()), 74);
  if (process.env.AKANE_VISUAL_SMOKE_OUTPUT) {
    await mkdir(process.env.AKANE_VISUAL_SMOKE_OUTPUT, { recursive: true });
    await page.screenshot({ path: resolve(process.env.AKANE_VISUAL_SMOKE_OUTPUT, 'workshop-calibration-mobile.png'), fullPage: true });
  }
  await page.setViewportSize({ width: 1180, height: 820 });
  if (process.env.AKANE_VISUAL_SMOKE_OUTPUT) {
    await mkdir(process.env.AKANE_VISUAL_SMOKE_OUTPUT, { recursive: true });
    await page.evaluate(() => { workshop.render(); workshop.switchTab('portraits', 'b'); });
    await page.waitForFunction(() => !document.querySelector('#add-outfit-btn').disabled);
    await page.screenshot({ path: resolve(process.env.AKANE_VISUAL_SMOKE_OUTPUT, 'workshop-portraits.png'), fullPage: true });
    await page.evaluate(() => workshop.switchTab('test', 'b'));
    await page.waitForFunction(() => { const img = document.querySelector('#test-visual-preview img'); return img?.naturalWidth > 0 && getComputedStyle(img).opacity === '1'; });
    await page.evaluate(() => Promise.all(document.getAnimations().filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))));
    await page.screenshot({ path: resolve(process.env.AKANE_VISUAL_SMOKE_OUTPUT, 'workshop-test.png'), fullPage: true });
    await page.evaluate(() => workshop.switchTab('calibration', 'a'));
    await page.waitForFunction(() => workshop.ready()?.packId === 'a');
    await page.waitForFunction(() => getComputedStyle(document.querySelector('#calibration-image')).opacity === '1');
    await page.evaluate(() => Promise.all(document.getAnimations().filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))));
    await page.screenshot({ path: resolve(process.env.AKANE_VISUAL_SMOKE_OUTPUT, 'workshop-calibration.png'), fullPage: true });
  }
  // Public operations must keep catalog writes and dialog ownership separate.
  await page.evaluate(() => {
    window.fixturePacks = () => workshop.view.packs.map(p => ({ id: p.id, profile: structuredClone(p._rawProfile) }));
    window.finishRefresh = (packs = fixturePacks(), state = {}) => {
      resolveNext('list_character_packs', packs);
      resolveNext('load_pet_state', { instanceId: 'workshop-smoke', characterPackId: 'a', sessionId: 'old', ...state });
      resolveNext('get_client_launch_binding', { instanceId: 'workshop-smoke' });
    };
    workshop.switchTab('list');
    void workshop.refreshPacks(); void workshop.refreshPacks();
    const lists = pending.filter(p => p.command === 'list_character_packs');
    lists[1].resolve(fixturePacks().map(p => ({ ...p, profile: { ...p.profile, identity: { name: 'latest-' + p.id } } })));
    pending.splice(pending.indexOf(lists[1]), 1);
    resolveNext('load_pet_state', { instanceId: 'workshop-smoke' }); resolveNext('get_client_launch_binding', { instanceId: 'workshop-smoke' });
    resolveNext('load_pet_state', { instanceId: 'workshop-smoke' }); resolveNext('get_client_launch_binding', { instanceId: 'workshop-smoke' });
  });
  await page.waitForFunction(() => workshop.findPack('a').characterName === 'latest-a');
  await page.evaluate(() => resolveNext('list_character_packs', fixturePacks().map(p => ({ ...p, profile: { identity: { name: 'stale' } } }))));
  assert.equal(await page.evaluate(() => workshop.findPack('a').characterName), 'latest-a', 'older refresh cannot replace newer catalog');
  await page.evaluate(() => {
    void workshop.refreshPacks();
    workshop.applySnapshot({ state: { characterPackId: 'b', sessionId: 'new-session', scale: 1.2 } });
    finishRefresh();
  });
  await page.waitForFunction(() => workshop.view.activeSessionId === 'new-session');
  assert.equal(await page.evaluate(() => workshop.view.activePackId), 'b', 'refresh preserves newer desktop snapshot');
  await page.evaluate(() => workshop.applySnapshot({ state: { characterPackId: 'b', sessionId: 'new-session', scale: 1.4 } }));
  assert.equal(await page.evaluate(() => workshop.view.desktopScale), 1.4, 'scale-only snapshot is not discarded');
  await page.evaluate(() => { workshop.view.activePackId = ''; workshop.switchTab('persona', 'a'); void workshop.refreshPacks(); });
  await page.locator('#field-name').fill('saved-after-refresh');
  await page.evaluate(() => { void workshop.saveDraft(); });
  await page.evaluate(() => {
    const request = pending.find(p => p.command === 'save_character_pack').args.request;
    resolveNext('save_character_pack', { id: 'a', profile: { ...workshop.findPack('a')._rawProfile, identity: request.identity } });
    finishRefresh(fixturePacks().map(p => ({ ...p, profile: { identity: { name: 'older-disk-read' } } })));
  });
  await page.waitForFunction(() => workshop.findPack('a').characterName === 'saved-after-refresh');
  // Failed asset views can be retried from the public refresh button.
  await page.evaluate(() => { window.deferAssets = true; workshop.switchTab('portraits', 'a'); });
  await page.evaluate(() => resolveNext('list_pack_assets', 'fixture asset failure', true));
  await page.waitForFunction(() => document.querySelector('#portraits-content').hidden);
  await page.locator('#refresh-packs').click();
  await page.evaluate(() => { window.deferAssets = false; finishRefresh(); });
  await page.waitForFunction(() => !document.querySelector('#add-outfit-btn').disabled);
  // Closing a pending create and reopening a new dialog must not erase that dialog.
  await page.evaluate(() => workshop.openCreateDialog());
  await page.locator('#create-pack-id').fill('c'); await page.locator('#create-name').fill('C');
  await page.locator('#create-form').evaluate(form => form.requestSubmit());
  await page.evaluate(() => { void workshop.createCharacterPack(); });
  assert.equal(await page.evaluate(() => calls.filter(c => c.command === 'create_character_pack').length), 1);
  await page.locator('#create-cancel').click();
  await page.evaluate(() => { workshop.switchTab('persona', 'b'); workshop.openCreateDialog(); resolveNext('create_character_pack', { id: 'c-canonical', profile: { identity: { name: 'C' } } }); });
  await page.waitForFunction(() => !document.querySelector('#create-name').disabled);
  assert.equal(await page.locator('#create-dialog').isVisible(), true, 'old success leaves reopened dialog open');
  assert.equal(await page.evaluate(() => workshop.view.workspacePackId), 'b', 'old create does not jump to new character');
  assert.ok(await page.evaluate(() => workshop.findPack('c-canonical')), 'result uses canonical backend id');
  assert.equal(await page.locator('#pack-count').textContent(), '3', 'created pack updates the visible catalog count');
  await page.locator('#create-pack-id').fill('d'); await page.locator('#create-name').fill('D');
  await page.locator('#create-form').evaluate(form => form.requestSubmit());
  await page.evaluate(() => resolveNext('create_character_pack', 'fixture create failure', true));
  await page.waitForFunction(() => !document.querySelector('#create-name').disabled);
  assert.equal(await page.locator('#create-name').inputValue(), 'D', 'failed create preserves form');
  assert.match(await page.locator('#create-error').textContent(), /fixture create failure/);
  if (process.env.AKANE_VISUAL_SMOKE_OUTPUT) {
    await page.evaluate(() => Promise.all(document.getAnimations().filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))));
    await page.screenshot({ path: resolve(process.env.AKANE_VISUAL_SMOKE_OUTPUT, 'workshop-create-error.png'), fullPage: true });
  }
  await page.locator('#create-cancel').click();
  await page.evaluate(() => { workshop.switchTab('context', 'a'); workshop.openContextLibraryDialog(); });
  await page.locator('#context-library-name').fill('A library');
  await page.locator('#context-library-description').fill('description');
  await page.locator('#context-library-load-when').fill('when relevant');
  await page.locator('#context-library-form').evaluate(form => form.requestSubmit());
  await page.evaluate(() => { void workshop.createContextLibrary(); });
  assert.equal(await page.evaluate(() => calls.filter(c => c.command === 'create_character_context_library').length), 1);
  assert.equal(await page.evaluate(async () => (await workshop.saveDraft({ packId: 'a' })).reason), 'save-in-progress', 'context save shares character write lock');
  await page.locator('#context-library-cancel').click();
  await page.evaluate(() => { workshop.switchTab('context', 'b'); workshop.openContextLibraryDialog(); });
  await page.locator('#context-library-name').fill('new B form');
  await page.evaluate(() => resolveNext('create_character_context_library', { id: 'a', profile: { ...workshop.findPack('a')._rawProfile, context_libraries: [{ name: 'A library', folder: 'a-library' }] } }));
  await page.waitForFunction(() => workshop.findPack('a')._rawProfile.context_libraries?.length === 1);
  assert.equal(await page.locator('#context-library-dialog').isVisible(), true);
  assert.equal(await page.locator('#context-library-name').inputValue(), 'new B form');
  assert.doesNotMatch(await page.locator('#context-library-list').textContent(), /A library/, 'old context result never renders into B');
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.locator('#context-library-dialog').evaluate(el => el.scrollWidth <= el.clientWidth), true);
  if (process.env.AKANE_VISUAL_SMOKE_OUTPUT) {
    await page.evaluate(() => Promise.all(document.getAnimations().filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))));
    await page.screenshot({ path: resolve(process.env.AKANE_VISUAL_SMOKE_OUTPUT, 'workshop-context-mobile.png'), fullPage: true });
  }
  await page.locator('#context-library-cancel').click();
  await page.setViewportSize({ width: 1180, height: 820 });
  await page.evaluate(() => { void workshop.exportPack(); void workshop.exportPack(); });
  assert.equal(await page.evaluate(() => calls.filter(c => c.command === 'export_character_pack').length), 1);
  await page.evaluate(() => resolveNext('export_character_pack', { ok: true, fileName: 'b.zip' }));
  await page.waitForFunction(() => document.querySelector('#workshop-status').textContent.includes('b.zip'));
  assert.doesNotMatch(await page.locator('#workshop-status').textContent(), /zip\.zip/);
  const zipChooser = page.waitForEvent('filechooser');
  await page.locator('#import-pack-btn').click();
  await (await zipChooser).setFiles({ name: 'fixture.zip', mimeType: 'application/zip', buffer: Buffer.from('boundary fixture') });
  await page.waitForFunction(() => pending.some(p => p.command === 'install_character_pack_zip_bytes'));
  await page.evaluate(() => { void workshop.importPack(); resolveNext('install_character_pack_zip_bytes', 'fixture zip failure', true); });
  await page.waitForFunction(() => !document.querySelector('#import-pack-btn').disabled);
  assert.equal(await page.evaluate(() => calls.filter(c => c.command === 'install_character_pack_zip_bytes').length), 1);
  assert.equal(await page.locator('input[type=file]').count(), 0, 'zip chooser cleaned up after selection');
  assert.match(await page.locator('#workshop-status').textContent(), /fixture zip failure/);
  await page.evaluate(() => { workshop.switchTab('calibration', 'a'); });
  await page.waitForFunction(() => workshop.ready()?.packId === 'a');
  await page.evaluate(() => workshop.applySnapshot({ state: { characterPackId: 'b', sessionId: 'new-session', scale: 1.65 } }));
  assert.equal(await page.locator('#calibration-frame').evaluate(el => el.style.getPropertyValue('--pet-scale')), '1.65', 'scale reaches visible calibration');
  await page.evaluate(() => { void workshop.refreshPacks(); window.applying = workshop.applyPack('a'); });
  assert.equal(await page.evaluate(async () => (await workshop.refreshPacks()).reason), 'operation-in-progress');
  assert.equal(await page.evaluate(async () => (await workshop.saveDraft({ packId: 'a' })).reason), 'save-in-progress');
  await page.evaluate(() => { resolveNext('activate_character_pack', 'fixture activation failure', true); finishRefresh(); });
  assert.equal(await page.evaluate(async () => (await applying).ok), false);
  assert.equal(await page.evaluate(() => workshop.view.activePackId), 'b', 'old refresh cannot replace active state during apply');
  await page.evaluate(() => { workshop.switchTab('persona', 'a'); });
  await page.locator('#field-name').fill('draft survives removed pack');
  await page.evaluate(() => { void workshop.refreshPacks(); finishRefresh([]); });
  await page.waitForFunction(() => document.querySelector('#persona-form').hidden);
  assert.equal(await page.evaluate(() => workshop.loadDraft('a').identity.name), 'draft survives removed pack');
  assert.equal(await page.locator('#persona-empty-state').isVisible(), true);
  assert.equal(await page.evaluate(() => workshop.view.workspacePackId), '');
  assert.deepEqual(errors, [], 'no uncaught browser errors');
  console.log('workshop browser smoke passed: calibration geometry/ownership, persona drafts, test request isolation, portrait mutations, refresh/write races, dialog ownership, import/export, removed-pack empty state; 0 page errors');
} finally { await browser?.close(); await new Promise(resolve => server.close(resolve)); }
