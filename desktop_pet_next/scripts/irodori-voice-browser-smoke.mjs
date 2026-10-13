// Real voice editor/controller in a browser; isolated service responses.
import assert from "node:assert/strict";
import { createServer } from "vite";
const { chromium } = await import(process.env.AKANE_PLAYWRIGHT_MODULE || "playwright");
const server = await createServer({ server: { host: "127.0.0.1", port: 0 } });
let browser;
try {
  await server.listen();
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(server.resolvedUrls.local[0] + "control-center-lab.html");
  await page.evaluate(async () => {
    const { renderVoice } = await import("/src/control-center-v2/components/voice.js");
    const { createVoiceController } = await import("/src/control-center-v2/app/voice.js");
    const { capabilityFormPayload } = await import("/src/control-center-v2/app/capability-payload.js");
    const root = document.createElement("main");
    document.body.replaceChildren(root);
    const iro = "provider.tts.irodori.local", gpt = "provider.tts.gpt_sovits.local";
    const provider = { name: "Irodori-TTS", ready: true, statusLabel: "已就绪" };
    const profile = { voiceProfileId: "cecilia_irodori_calm", name: "塞西莉亚 · Irodori", enabled: true,
      configured: true, irodoriVoiceId: "cecilia", mediaType: "wav", textLang: "ja", statusTone: "success", statusLabel: "已保存" };
    const state = { actionStates: {}, voiceProfileEditorOpen: true, viewModel: {
      shell: { connected: true, instanceLabel: "browser-fixture" }, bots: { activeId: "main" },
      character: { packId: "cecilia", displayName: "塞西莉亚", voice: { provider: "irodori", profileId: profile.voiceProfileId } },
      voice: { available: true, controlsAvailable: true, inputState: "idle", queueLength: 0,
        tts: { enabled: true, provider, volume: 70 }, asr: { enabled: true, provider },
        diagnostics: [] },
      abilities: { providers: [
        { id: gpt, adapter: "gpt_sovits", title: "GPT-SoVITS", type: "tts_provider", voiceProfiles: [] },
        { id: iro, adapter: "irodori", title: "Irodori-TTS", type: "tts_provider", voiceProfiles: [profile] }
      ] },
      actions: Object.fromEntries(["voice.setTtsEnabled", "voice.previewPlay", "voice.stop", "voice.setAsrEnabled",
        "character.openWorkshop", "abilities.provider.voiceProfile.save", "abilities.provider.ttsTest",
        "abilities.provider.voiceProfile.assignToCurrentCharacter", "abilities.provider.voiceProfile.clearCurrentCharacter"]
        .map(id => [id, { available: true }]))
    } };
    let controller, finish;
    const calls = [], presented = [];
    const render = () => { root.innerHTML = renderVoice(state); };
    const store = { getState: () => state, patch: patch => { Object.assign(state, patch); render(); } };
    const actions = {
      runAction(id, payload, ticket) {
        calls.push({ id, payload });
        return new Promise(resolve => { finish = result => resolve(ticket?.isCurrent() === false ? { ok: false } : result); });
      },
      present(id, value) { presented.push(value); }
    };
    controller = createVoiceController({ root, store, actions, drafts: { discard() {}, open() {} } });
    root.addEventListener("change", event => controller.handleChange(event));
    root.addEventListener("input", event => controller.handleInput(event));
    root.addEventListener("click", event => controller.handleClick(event));
    render();
    window.__iroSmoke = { state, controller, render, calls, presented, payload: capabilityFormPayload,
      finish: result => finish(result), preview: () => controller.runVoiceProfileTest({ providerId: iro, voiceProfileId: profile.voiceProfileId }) };
  });
  assert.equal(await page.locator("[data-voice-provider-select]").inputValue(), "provider.tts.irodori.local");
  const form = page.locator("form[data-capability-form='voice-profile']");
  assert.equal(await form.locator("[name='irodoriVoiceId']").count(), 1);
  assert.equal(await form.locator("[name='refAudioPath']").count(), 0);
  await form.locator("[name='voiceProfileId']").fill("iro_test");
  await form.locator("[name='irodoriVoiceId']").fill("cecilia");
  const payload = await page.evaluate(() => window.__iroSmoke.payload(document.querySelector("form[data-capability-form='voice-profile']"), "abilities.provider.voiceProfile.save"));
  assert.equal(payload.irodoriVoiceId, "cecilia");
  assert.equal(payload.providerId, "provider.tts.irodori.local");
  assert.equal(Object.hasOwn(payload, "refAudioPath"), false);
  await form.locator("[name='irodoriVoiceId']").fill("../invalid");
  assert.equal(await form.evaluate(node => node.checkValidity()), false);
  await page.evaluate(() => { window.__iroSmoke.pending = window.__iroSmoke.preview(); });
  assert.match(await page.evaluate(() => window.__iroSmoke.calls.at(-1).payload.text), /おかえり/);
  await page.selectOption("[data-voice-provider-select]", "provider.tts.gpt_sovits.local");
  assert.equal(await page.locator("[name='refAudioPath']").count(), 1);
  await page.evaluate(async () => { window.__iroSmoke.finish({ ok: true, audioBase64: "stale" }); await window.__iroSmoke.pending; });
  assert.equal(await page.evaluate(() => window.__iroSmoke.presented.length), 0, "An old audition cannot play after switching provider");
  await page.selectOption("[data-voice-provider-select]", "provider.tts.irodori.local");
  await page.evaluate(() => {
    window.__iroSmoke.state.voiceProfileEditorOpen = true;
    window.__iroSmoke.render();
    window.__iroSmoke.pending = window.__iroSmoke.preview();
    window.__iroSmoke.finish({ ok: false, reason: "tts_irodori_not_ready" });
  });
  await page.evaluate(() => window.__iroSmoke.pending);
  assert.equal(await page.evaluate(() => window.__iroSmoke.presented.length), 0);
  await page.evaluate(() => { window.__iroSmoke.state.viewModel.abilities.providers[1].voiceProfiles = []; window.__iroSmoke.render(); });
  assert.equal(await page.locator("[name='irodoriVoiceId']").count(), 1, "An empty provider still has its actual configuration form");
  assert.deepEqual(errors, []);
  console.log("Irodori voice browser smoke passed: form, validation, provider switching, slow/stale audition, service error and empty state.");
} finally {
  await browser?.close();
  await server.close();
}
