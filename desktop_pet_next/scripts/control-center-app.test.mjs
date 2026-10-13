import assert from "node:assert/strict";
import { test } from "node:test";
import { createControlCenterStore, createInitialControlCenterState } from "../src/control-center-v2/store.js";
import { createActionController } from "../src/control-center-v2/app/actions.js";
import { createModelController } from "../src/control-center-v2/app/model.js";
import { MODEL_SERVICE_ACTIONS } from "../src/control-center-v2/model-service.js";
import { createCapabilityDrafts } from "../src/control-center-v2/app/capability-drafts.js";
import { createCapabilityController } from "../src/control-center-v2/app/capabilities.js";
import { createVoiceController } from "../src/control-center-v2/app/voice.js";
import { createChatController } from "../src/control-center-v2/app/chat.js";
import { createSnapshotController } from "../src/control-center-v2/app/snapshot.js";
import { createControlCenterRenderer } from "../src/control-center-v2/app/render.js";
import { perceptionRenderScope, modelRenderScope, renderDecision } from "../src/control-center-v2/app/render-policy.js";

const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const tick = async () => { await Promise.resolve(); await Promise.resolve(); };
const viewModel = (sessionId = "one", botId = "main", packId = "akane") => ({
  shell: { instanceLabel: "fixture", connected: true }, bots: { activeId: botId }, character: { packId },
  chat: { sessionId }, model: { available: true, providers: [{ id: "first" }, { id: "second", baseUrl: "https://second.invalid" }] }
});
function harness(run = async () => ({ ok: true, status: "completed" })) {
  const store = createControlCenterStore({ ...createInitialControlCenterState(), viewModel: viewModel() });
  const callbacks = new Map();
  let sequence = 0;
  const timers = { setTimeout: callback => { callbacks.set(++sequence, callback); return sequence; },
    clearTimeout: id => callbacks.delete(id) };
  const actions = createActionController({ store, bridge: { runAction: run }, requestFrame: callback => callback(), timers });
  function switchTo(next) {
    actions.synchronizeScope(next);
    store.patch({ viewModel: next, actionStates: {}, chatJobControls: {} });
  }
  return { store, actions, callbacks, switchTo };
}

test("old feedback expiry cannot clear a newer result with the same phase", async () => {
  const h = harness();
  await h.actions.runAction("voice.test");
  const oldExpiry = [...h.callbacks.values()][0];
  await h.actions.runAction("voice.test");
  const latest = h.store.getState().actionStates["voice.test"];
  oldExpiry();
  assert.equal(h.store.getState().actionStates["voice.test"], latest);
  h.actions.dispose(); assert.equal(h.callbacks.size, 0);
});

test("an operation stays locked during dispatch and releases for retry after a real failure", async () => {
  const reply = deferred(); let calls = 0;
  const h = harness(() => { calls++; return calls === 1 ? reply.promise : { ok: true, status: "completed" }; });
  const first = h.actions.runAction("voice.test");
  assert.equal(await h.actions.runAction("voice.test"), null);
  await tick();
  assert.equal(calls, 1);
  reply.resolve({ ok: false, status: "failed", reason: "service_offline" });
  await first;
  assert.equal(h.store.getState().actionStates["voice.test"].detail, "service_offline");
  await h.actions.runAction("voice.test");
  assert.equal(calls, 2);
});

test("a late job control result belongs to its original conversation", async () => {
  const reply = deferred();
  const h = harness(() => reply.promise);
  const first = h.actions.runAction("chat.jobControl", { jobId: "old-job", action: "cancel" });
  await tick();
  h.switchTo(viewModel("two"));
  reply.resolve({ ok: true, status: "completed", jobStatus: "cancelled" });
  assert.equal((await first).status, "stale");
  assert.deepEqual(h.store.getState().chatJobControls, {});
  assert.deepEqual(h.store.getState().actionStates, {});
});

test("switching away and back cannot revive an earlier chat request", async () => {
  const reply = deferred();
  const h = harness(() => reply.promise);
  const first = h.actions.runAction("chat.send", { text: "original" });
  await tick();
  h.switchTo(viewModel("two")); h.switchTo(viewModel());
  reply.resolve({ ok: true });
  assert.equal((await first).status, "stale");
});

test("same session ID under a different bot permits a new request and keeps its own result", async () => {
  const replies = [deferred(), deferred()]; let calls = 0;
  const h = harness(() => replies[calls++].promise);
  const old = h.actions.runAction("chat.send"); await tick();
  h.switchTo(viewModel("one", "other"));
  const next = h.actions.runAction("chat.send"); await tick();
  replies[0].resolve({ ok: true }); await old;
  assert.equal(h.store.getState().actionStates["chat.send"].phase, "pending");
  replies[1].resolve({ ok: true, status: "completed" }); await next;
  assert.equal(calls, 2);
  assert.equal(h.store.getState().actionStates["chat.send"].phase, "confirmed");
});

test("a scope change before the animation frame prevents dispatch against the new session", async () => {
  const h = harness(); let frame, calls = 0;
  const actions = createActionController({ store: h.store,
    bridge: { runAction: () => { calls++; } }, requestFrame: callback => { frame = callback; }, timers: {} });
  const pending = actions.runAction("chat.send");
  h.store.patch({ viewModel: viewModel("two") }); frame(); await pending;
  assert.equal(calls, 0);
});

test("dispose clears feedback timers and fences a late completion", async () => {
  const reply = deferred();
  const h = harness(id => id === "chat.send" ? reply.promise : { ok: true });
  await h.actions.runAction("voice.test");
  const pending = h.actions.runAction("chat.send"); await tick();
  h.actions.dispose();
  const state = h.store.getState();
  reply.resolve({ ok: true }); await pending;
  assert.equal(h.callbacks.size, 0);
  assert.equal(h.store.getState(), state);
});

function modelHarness() {
  const pending = deferred();
  const h = harness(() => pending.promise);
  h.store.patch({ modelDraft: { providerId: "first", baseUrl: "https://first.invalid", chatModel: "" } });
  const fields = new Map();
  const root = { querySelector: selector => fields.get(selector.match(/data-model-field="([^"]+)"/)?.[1]) || null };
  const model = createModelController({ root, store: h.store, actions: h.actions });
  const edit = (field, value) => {
    const target = { value, dataset: { modelField: field }, matches: selector => selector === "[data-model-field]" };
    fields.set(field, target); model.handleInput({ target });
  };
  return { ...h, model, pending, edit, fields };
}

test("model list preserves edits typed while the provider request is in flight", async () => {
  const h = modelHarness();
  const request = h.model.runModelAction(MODEL_SERVICE_ACTIONS.models); await tick();
  h.edit("chatModel", "my-new-choice");
  h.pending.resolve({ ok: true, models: ["provider-choice"] }); await request;
  assert.equal(h.store.getState().modelDraft.chatModel, "my-new-choice");
  assert.deepEqual(h.store.getState().modelModels, []);
});

test("a provider switch fences the old model list", async () => {
  const h = modelHarness();
  const request = h.model.runModelAction(MODEL_SERVICE_ACTIONS.models); await tick();
  const target = { value: "second", matches: selector => selector.includes('providerId') };
  h.fields.set("providerId", target); h.model.handleChange({ target });
  h.pending.resolve({ ok: true, models: ["first-model"] }); await request;
  assert.equal(h.store.getState().modelDraft.providerId, "second");
  assert.deepEqual(h.store.getState().modelModels, []);
});

test("successful model save does not erase a later edit", async () => {
  const h = modelHarness();
  const request = h.model.runModelAction(MODEL_SERVICE_ACTIONS.save); await tick();
  h.edit("baseUrl", "https://later.invalid");
  h.pending.resolve({ ok: true }); await request;
  assert.equal(h.store.getState().modelDraft.baseUrl, "https://later.invalid");
});

test("normal model list fills an empty selection but retains existing selections", async () => {
  const h = modelHarness();
  const request = h.model.runModelAction(MODEL_SERVICE_ACTIONS.models); await tick();
  h.pending.resolve({ ok: true, models: ["available", "", null] }); await request;
  assert.deepEqual(h.store.getState().modelModels, ["available"]);
  assert.equal(h.store.getState().modelDraft.chatModel, "available");
});

function capabilityHarness(run) {
  const h = harness(run);
  h.store.patch({ activePage: "abilities" });
  let details, input, form;
  function mount(value = "", key = "plugin:installer", opened = true) {
    details = { dataset: { capabilityKey: key }, open: opened, querySelector: () => form };
    input = { name: "pluginPath", type: "text", value, checked: false, dispatchEvent() {}, focus() {} };
    const elements = [input]; elements.namedItem = name => name === input.name ? input : null;
    form = { dataset: { capabilityForm: "plugin-stage" }, elements, closest: () => details };
    input.closest = () => form;
  }
  mount();
  const root = { querySelectorAll: () => [details], querySelector: selector => selector.includes("pluginPath") ? input : null };
  const drafts = createCapabilityDrafts({ root, store: h.store });
  const capabilities = createCapabilityController({ root, store: h.store, actions: h.actions, drafts });
  return { ...h, root, drafts, capabilities, mount, get input() { return input; }, get form() { return form; }, get details() { return details; } };
}

test("capability drafts survive a same-scope render and preserve the initial panel state", () => {
  const h = capabilityHarness();
  h.drafts.restore(); assert.equal(h.details.open, true);
  h.input.value = "typed-path"; h.drafts.capture();
  h.mount(); h.drafts.restore();
  assert.equal(h.input.value, "typed-path");
});

test("discarding a saved draft cannot recapture it before the next render", () => {
  const h = capabilityHarness();
  h.input.value = "old-path"; h.drafts.capture();
  h.drafts.discard("plugin:installer"); h.drafts.open("plugin:installer");
  h.details.open = false; h.drafts.capture();
  h.mount("fresh-value", "plugin:installer", false); h.drafts.restore();
  assert.equal(h.input.value, "fresh-value"); assert.equal(h.details.open, true);
});

test("capability drafts from a previous bot cannot populate the new form", () => {
  const h = capabilityHarness();
  h.input.value = "old-path"; h.drafts.capture();
  const next = viewModel("one", "other");
  h.drafts.synchronizeScope(next); h.store.patch({ viewModel: next });
  h.drafts.capture(); h.mount("new-path"); h.drafts.restore();
  assert.equal(h.input.value, "new-path");
});

test("a slow path picker cannot replace a path entered in the meantime", async () => {
  const reply = deferred(); const h = capabilityHarness(() => reply.promise);
  const picker = { dataset: { pluginPathPicker: "abilities.plugin.pickSource" }, closest: () => h.form };
  const request = h.capabilities.pickPluginPath(picker); await tick();
  h.input.value = "manually-edited"; h.drafts.handleEdit({ target: h.input });
  reply.resolve({ ok: true, path: "picked-path" }); await request;
  assert.equal(h.input.value, "manually-edited");
});

test("returning to the capabilities page cannot revive a previous path picker", async () => {
  const reply = deferred(); const h = capabilityHarness(() => reply.promise);
  const request = h.capabilities.pickPluginPath({ dataset: { pluginPathPicker: "abilities.plugin.pickSource" }, closest: () => h.form });
  await tick(); h.drafts.synchronizePage("chat"); h.drafts.synchronizePage("abilities");
  reply.resolve({ ok: true, path: "picked-path" }); await request;
  assert.equal(h.input.value, "");
});

test("a path picker may populate its current form after an ordinary DOM render", async () => {
  const reply = deferred(); const h = capabilityHarness(() => reply.promise);
  const request = h.capabilities.pickPluginPath({ dataset: { pluginPathPicker: "abilities.plugin.pickSource" }, closest: () => h.form });
  await tick(); h.drafts.capture(); h.mount(); h.drafts.restore();
  reply.resolve({ ok: true, path: "picked-path" }); await request;
  assert.equal(h.input.value, "picked-path");
});

function voiceHarness(run, play = async () => {}) {
  const h = capabilityHarness(run), revoked = [], instances = [];
  class FakeAudio {
    constructor() { instances.push(this); this.paused = false; this.events = {}; }
    pause() { this.paused = true; }
    play() { return play(); }
    addEventListener(type, callback) { this.events[type] = callback; }
  }
  const voice = createVoiceController({ root: h.root, store: h.store, actions: h.actions, drafts: h.drafts,
    AudioClass: FakeAudio, urls: { createObjectURL: () => "fixture-url", revokeObjectURL: url => revoked.push(url) },
    requestFrame: callback => callback() });
  return Object.assign(h, { voice, revoked, instances });
}

test("folder inspection cannot replace a different voice profile selected later", async () => {
  const reply = deferred(); const h = voiceHarness(() => reply.promise);
  h.mount("", "voice-profile:editor");
  h.store.patch({ viewModel: { ...viewModel(), abilities: { providers: [{ id: "tts", voiceProfiles: [{ voiceProfileId: "new", name: "New" }] }] } } });
  const ticket = h.drafts.ticket(h.form);
  const request = h.voice.inspectFolder({ providerId: "tts", folderPath: "fixture" }, ticket); await tick();
  h.voice.editVoiceProfile("tts", "new");
  reply.resolve({ ok: true, suggestedProfile: { voiceProfileId: "old" } }); await request;
  assert.equal(h.store.getState().voiceProfileSuggestion.voiceProfileId, "new");
});

test("a late TTS profile test does not start audio after changing character", async () => {
  const reply = deferred(); const h = voiceHarness(() => reply.promise);
  const request = h.voice.runVoiceProfileTest({ voiceProfileId: "old" }); await tick();
  const next = viewModel("one", "main", "other"); h.voice.synchronizeScope(next); h.store.patch({ viewModel: next });
  reply.resolve({ ok: true, audioBase64: "YQ==" }); await request;
  assert.equal(h.instances.length, 0);
});

test("unloading a pending audio play releases the URL once and ignores its late success", async () => {
  const playing = deferred(); const h = voiceHarness(async () => ({ ok: true, audioBase64: "YQ==" }), () => playing.promise);
  const request = h.voice.runVoiceProfileTest({ voiceProfileId: "voice" }); await tick(); await tick();
  h.voice.dispose(); const feedback = h.store.getState().actionStates["abilities.provider.ttsTest"];
  playing.resolve(); await request;
  assert.equal(h.instances[0].paused, true); assert.deepEqual(h.revoked, ["fixture-url"]);
  h.instances[0].events.ended(); assert.deepEqual(h.revoked, ["fixture-url"]);
  assert.equal(h.store.getState().actionStates["abilities.provider.ttsTest"], feedback);
});

test("real audio play failure retains the cause and releases the URL", async () => {
  const h = voiceHarness(async () => ({ ok: true, audioBase64: "YQ==" }), async () => { throw Error("fixture_audio_denied"); });
  await h.voice.runVoiceProfileTest({ voiceProfileId: "voice" });
  assert.equal(h.store.getState().actionStates["abilities.provider.ttsTest"].detail, "fixture_audio_denied");
  assert.deepEqual(h.revoked, ["fixture-url"]);
});

function chatHarness(run, history = async () => ({ ok: true })) {
  const h = harness(run);
  const input = { value: "", focus() {}, disabled: false };
  const viewport = { scrollHeight: 1000, clientHeight: 300, scrollTop: 100 };
  const root = { querySelector: selector => selector === "[data-chat-input]" ? input : selector === "[data-chat-viewport]" ? viewport : null };
  const chat = createChatController({ root, store: h.store, actions: h.actions, bridge: { loadOlderChatMessages: history },
    autoPreviews: { refresh() {}, dispose() {} }, documentRef: { activeElement: null } });
  return { ...h, input, viewport, chat, form: { querySelector: () => input } };
}

test("a chat send receipt leaves the next draft typed while waiting intact", async () => {
  const reply = deferred(), h = chatHarness(() => reply.promise);
  h.input.value = "sent text";
  const request = h.chat.send(h.form); await tick();
  h.input.value = "next draft";
  h.chat.handleInput({ target: { matches: () => true, value: h.input.value } });
  reply.resolve({ ok: true, status: "accepted" }); await request;
  assert.equal(h.input.value, "next draft");
});

test("a chat send receipt cannot erase or focus a new conversation's draft", async () => {
  const reply = deferred(), h = chatHarness(() => reply.promise);
  h.input.value = "old text";
  const request = h.chat.send(h.form); await tick();
  const next = viewModel("two"); h.chat.synchronizeScope(next); h.switchTo(next);
  h.input.value = "new conversation draft";
  h.chat.handleInput({ target: { matches: () => true, value: h.input.value } });
  let focused = false; h.input.focus = () => { focused = true; };
  reply.resolve({ ok: false, reason: "late_failure" }); await request;
  assert.equal(h.input.value, "new conversation draft"); assert.equal(focused, false);
});

test("an explicitly empty chat input cannot fall back to a previous unsent draft", async () => {
  let calls = 0; const h = chatHarness(() => { calls++; });
  h.chat.handleInput({ target: { matches: () => true, value: "previous" } });
  h.input.value = "";
  assert.equal(await h.chat.send(h.form), null); assert.equal(calls, 0);
});

test("a normal accepted send clears only the submitted draft", async () => {
  const h = chatHarness(async () => ({ ok: true, status: "accepted" }));
  h.input.value = "sent text"; await h.chat.send(h.form);
  assert.equal(h.input.value, "");
});

test("chat history failure preserves the cause and allows a real retry", async () => {
  let calls = 0;
  const h = chatHarness(undefined, async () => ++calls === 1 ? { ok: false, reason: "fixture_history_offline" } : { ok: true });
  h.store.patch({ viewModel: { ...viewModel(), chat: { sessionId: "one", history: { hasMore: true, nextBeforeSeq: 40 } } } });
  await h.chat.loadOlderChatHistory();
  assert.equal(h.store.getState().chatHistory.error, "fixture_history_offline");
  await h.chat.loadOlderChatHistory(); assert.equal(h.store.getState().chatHistory.phase, "idle"); assert.equal(calls, 2);
});

test("history deduplication and scoped completion cannot overwrite a new bot with the same session ID", async () => {
  const replies = [deferred(), deferred()]; let calls = 0;
  const h = chatHarness(undefined, () => replies[calls++].promise);
  const withHistory = bot => ({ ...viewModel("one", bot), chat: { sessionId: "one", history: { hasMore: true, nextBeforeSeq: 40 } } });
  h.store.patch({ viewModel: withHistory("main") });
  const first = h.chat.loadOlderChatHistory();
  assert.equal(await h.chat.loadOlderChatHistory(), null);
  const next = withHistory("other"); h.chat.synchronizeScope(next); h.switchTo(next);
  const second = h.chat.loadOlderChatHistory();
  replies[0].resolve({ ok: true }); await first;
  assert.equal(h.store.getState().chatHistory.phase, "loading");
  replies[1].resolve({ ok: true }); await second;
  assert.equal(h.store.getState().chatHistory.phase, "idle"); assert.equal(calls, 2);
});

test("a stale bridge history result does not leave the current load stuck", async () => {
  const h = chatHarness(undefined, async () => ({ ok: false, status: "stale" }));
  h.store.patch({ viewModel: { ...viewModel(), chat: { sessionId: "one", history: { hasMore: true, nextBeforeSeq: 40 } } } });
  await h.chat.loadOlderChatHistory(); assert.equal(h.store.getState().chatHistory.phase, "idle");
});

test("prepending history keeps the reader's viewport anchored", async () => {
  const reply = deferred(), h = chatHarness(undefined, () => reply.promise);
  h.store.patch({ viewModel: { ...viewModel(), chat: { sessionId: "one", messages: [{ id: "recent" }], history: { hasMore: true, nextBeforeSeq: 40 } } } });
  const request = h.chat.loadOlderChatHistory();
  h.store.patch({ viewModel: { ...h.store.getState().viewModel, chat: { ...h.store.getState().viewModel.chat, messages: [{ id: "older" }, { id: "recent" }] } } });
  h.viewport.scrollHeight = 1400;
  h.chat.afterRender(h.store.getState(), null);
  assert.equal(h.viewport.scrollTop, 500);
  reply.resolve({ ok: true }); await request;
});

function snapshotHarness() {
  const pending = deferred(), h = harness(); let calls = 0;
  const model = createModelController({ root: { querySelector: () => null }, store: h.store, actions: h.actions });
  const inert = { synchronizeScope: () => false, synchronizePack() {} };
  const snapshot = createSnapshotController({ store: h.store, actions: h.actions, model, drafts: inert, voice: inert,
    chat: inert, presentation: inert, bridge: { refresh: () => { calls++; return pending.promise; }, stop() {} } });
  return { ...h, snapshot, pending, calls: () => calls };
}

test("live publications cannot unlock an in-flight refresh or replay its older result", async () => {
  const h = snapshotHarness(), first = h.snapshot.refresh();
  h.snapshot.receive(viewModel("two"));
  assert.equal(h.store.getState().phase, "refreshing");
  assert.equal(h.snapshot.refresh(), first); assert.equal(h.calls(), 1);
  h.pending.resolve(viewModel("one")); await first;
  assert.equal(h.store.getState().viewModel.chat.sessionId, "two");
  assert.equal(h.store.getState().phase, "ready");
});

test("snapshot scope includes bot identity and resets paging and model drafts without echoing keys", () => {
  const h = snapshotHarness();
  h.store.patch({ chatHistory: { phase: "loading" }, modelDraft: { apiKey: "fixture-key" }, modelModels: ["old"] });
  h.snapshot.receive({ ...viewModel("one", "other"), model: { available: true, providerId: "new", hasApiKey: true } });
  assert.equal(h.store.getState().chatHistory.phase, "idle");
  assert.equal(h.store.getState().modelDraft.apiKey, ""); assert.equal(h.store.getState().modelDraft.providerId, "new");
  assert.deepEqual(h.store.getState().modelModels, []);
});

test("refresh disposal fences a completion after the window is closed", async () => {
  const h = snapshotHarness(), request = h.snapshot.refresh();
  h.snapshot.dispose(); const state = h.store.getState();
  h.pending.resolve(viewModel("two")); await request;
  assert.equal(h.store.getState(), state);
});

test("focused controls defer ordinary snapshots but cannot hide a changed conversation", () => {
  const state = { activePage: "chat", viewModel: viewModel() };
  const previous = { page: "chat", scope: perceptionRenderScope(state) };
  const input = { matches: selector => selector.includes("input") };
  assert.equal(renderDecision(state, previous, input, false), "defer");
  assert.equal(renderDecision({ ...state, viewModel: viewModel("two") }, previous, input, false), "render");
});

test("independent vision and key-clearing toggles invalidate the model layout", () => {
  const state = { activePage: "model", viewModel: viewModel(), modelDraft: {}, actionStates: {} };
  for (const field of ["standaloneVision", "clearApiKey"]) {
    const changed = { ...state, modelDraft: { [field]: true } };
    assert.notEqual(modelRenderScope(changed), modelRenderScope(state));
  }
});

test("deferred render flushes the current store instead of replaying an older state", () => {
  const h = harness(), doc = { activeElement: null }, rendered = [];
  h.store.patch({ activePage: "voice" });
  const noop = () => {}, drafts = { synchronizePage: noop, capture: noop, restore: noop };
  const renderer = createControlCenterRenderer({ root: { querySelector: () => null }, store: h.store, documentRef: doc,
    model: { captureFocusedModelField: () => null, restoreFocusedModelField: noop }, drafts,
    voice: { beforeRender: noop, afterRender: noop }, chat: { hasNewMessage: () => false, beforeRender: () => null, afterRender: noop },
    presentation: { isDragging: () => false, applyCurrent: noop }, renderShell: (_, state) => rendered.push(state) });
  renderer.render(h.store.getState());
  doc.activeElement = { matches: () => true };
  renderer.render({ ...h.store.getState(), error: "old" });
  h.store.patch({ error: "latest" }); doc.activeElement = null; renderer.flush();
  assert.equal(rendered.at(-1).error, "latest"); assert.equal(rendered.length, 2);
});
