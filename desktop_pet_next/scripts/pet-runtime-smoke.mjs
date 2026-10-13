import assert from "node:assert/strict";
import { createLifecycle } from "../src/pet/lifecycle.js";
import { createRuntimeScope } from "../src/pet/scope.js";
import { loadPetRuntime, createEnvironment, mockBackend, deferred, tick } from "./pet-runtime-harness.mjs";

const runtime = await loadPetRuntime();
const apps = [];
try {
  const make = options => {
    const env = createEnvironment(options);
    const app = runtime.createPetApplication({ ...env, bundledCharacterAssets: { "/synthetic/normal.png": "synthetic.png" } });
    apps.push(app);
    return { app, ...env };
  };

  // Construct the real controller graph and read a snapshot without rendering DOM.
  {
    const { app, elements } = make();
    const before = elements.chatForm.children.length;
    const snapshot = app.controllers.settings.buildSettingsSnapshot();
    assert.equal(snapshot.state.characterPackId, app.state.characterPackId);
    assert.deepEqual(snapshot.music.recommendations, []);
    assert.equal(elements.chatForm.children.length, before);
    assert.throws(() => { app.controllers.chat.sending = true; }, TypeError);
    await app.controllers.settings.handleSettingsCommand({ command: "setVoiceVolume", value: 0.4, operationId: "volume" });
    assert.equal(app.state.voiceVolume, 0.4);
    assert.equal(app.controllers.settings.lastSettingsCommandResult.ok, true);
    await app.controllers.settings.handleSettingsCommand({ command: "unknown", operationId: "unknown" });
    assert.equal(app.controllers.settings.lastSettingsCommandResult.status, "not-supported");
    app.dispose();
    await assert.rejects(app.start(), /disposed/);
  }

  // Offline startup stays usable; start is idempotent and listeners leave on disposal.
  {
    const { app, elements } = make();
    const startup = app.start();
    assert.equal(app.start(), startup);
    assert.equal(await startup, false);
    assert.equal(app.resourceState.health, "offline");
    elements.opacity.value = "0.7";
    elements.opacity.dispatchEvent(new Event("input"));
    assert.equal(app.state.opacity, 0.7);
    app.dispose();
    elements.opacity.value = "0.9";
    elements.opacity.dispatchEvent(new Event("input"));
    assert.equal(app.state.opacity, 0.7);
    const next = make();
    assert.equal(await next.app.start(), false);
    next.app.dispose();
  }

  // The real startup adopts a registered default Bot, then loads that Bot's resources.
  {
    const backend = mockBackend();
    const { app, nativeCalls, nativeHandlers } = make({ ...backend, tauri: true });
    assert.equal(await app.start(), true);
    assert.equal(app.state.boundBotId, "test-bot");
    assert.equal(app.resourceState.source, "manifest");
    assert.ok(backend.requests.some(({ url }) => url.pathname.startsWith("/api/bots/test-bot/") && url.pathname.endsWith("/resource-manifest")));
    assert.ok(nativeCalls.some(call => call.command === "report_ui_ready" && call.payload.status === "ready"));
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.ok(nativeHandlers.size > 0);
    assert.ok(nativeHandlers.has("gomoku:request"), "application startup registers the game bridge");
    app.dispose();
    await tick();
    assert.equal(nativeHandlers.size, 0);
  }

  // Disposal during native state loading cannot apply late persisted state.
  {
    const loaded = deferred();
    const { app } = make({ tauri: true, invoke: command => command === "load_pet_state" ? loaded.promise : undefined });
    const before = app.state.sessionId;
    const startup = app.start();
    await tick();
    app.dispose();
    loaded.resolve({ instanceId: "local-default", hostId: "local-default", sessionId: "obsolete-session" });
    assert.equal(await startup, false);
    assert.equal(app.state.sessionId, before);
  }

  // A late old scene registration/disposal cannot clear the newer scene's playback claim.
  {
    const scene = await runtime.load("/src/scene-desktop-bridge.js");
    const pending = deferred();
    const first = createEnvironment({ invoke: command => command === "scene_playback_active" ? pending.promise : undefined });
    const options = { stop: async () => {}, scope: () => ({}), equip: async () => {} };
    const old = scene.registerSceneDesktopBridge({ ...options, platform: first.platform });
    await tick();
    const second = createEnvironment({ invoke: command => command === "scene_playback_active" ? true : undefined });
    const disposeCurrent = await scene.registerSceneDesktopBridge({ ...options, platform: second.platform });
    assert.equal(scene.sceneOwnsPlayback(), true);
    pending.resolve(true);
    const disposeOld = await old;
    disposeOld();
    assert.equal(scene.sceneOwnsPlayback(), true);
    assert.equal(first.nativeHandlers.size, 0);
    disposeCurrent();
    assert.equal(scene.sceneOwnsPlayback(), false);
    assert.equal(second.nativeHandlers.size, 0);
  }

  // Recent history is updated through the panel's interface and remains bounded/deduplicated.
  {
    const { app, platform } = make();
    const events = [];
    platform.emitTo = async (...args) => events.push(args);
    for (let i = 0; i < 7; i++) app.controllers.panel.recordRecentTrack(`track-${i}`, "artist");
    app.controllers.panel.recordRecentTrack("track-5", "artist");
    await tick();
    assert.equal(events.at(-1)[1], "panel:recent-update");
    assert.deepEqual(events.at(-1)[2], ["track-5 · artist", "track-6 · artist", "track-4 · artist", "track-3 · artist", "track-2 · artist"]);
    app.dispose();
  }

  // Failed commands report the actual outcome; a slow older command cannot replace a new result.
  {
    const { app } = make();
    const originalUrl = app.state.backendUrl;
    const failed = await app.controllers.settings.handleSettingsCommand({ command: "setBackendUrl", value: "http://invalid.test", operationId: "binding" });
    assert.equal(failed.ok, false);
    assert.match(failed.reason, /验证|offline/);
    assert.equal(app.state.backendUrl, originalUrl);
    const media = await app.controllers.settings.handleSettingsCommand({ command: "smtcAction", action: "next", operationId: "system" });
    assert.equal(media.ok, false);
    assert.equal(media.reason, "desktop_runtime_required");
    const pending = deferred();
    // This isolates transport timing while executing the real command dispatcher.
    app.controllers.character.refreshCharacterPacksFromSettings = async value => value === "old" ? pending.promise : true;
    const old = app.controllers.settings.handleSettingsCommand({ command: "refreshCharacterPacks", value: "old", operationId: "old" });
    await app.controllers.settings.handleSettingsCommand({ command: "refreshCharacterPacks", value: "new", operationId: "new" });
    pending.resolve(true);
    await old;
    assert.equal(app.controllers.settings.lastSettingsCommandResult.operationId, "new");
    app.dispose();
  }

  // Execute the composed streaming path with real parsing, segment replay and bubble delivery.
  {
    const backend = mockBackend({ stream: [
      { type: "turn_start" }, { type: "speech_segment", index: 0, text: "模块拆分完成。" },
      { type: "speech_segment", index: 0, text: "模块拆分完成。" },
      { type: "final", payload: { speech: "模块拆分完成。" } }, { type: "stream_end" }
    ] });
    const { app, elements } = make(backend);
    await app.start();
    app.state.voiceEnabled = false;
    await app.controllers.chat.sendMessage("测试消息");
    assert.equal(app.controllers.chat.sending, false);
    assert.equal(elements.bubbleText.textContent, "模块拆分完成。");
    assert.deepEqual(app.controllers.bubble.streamedReplyTexts, ["模块拆分完成。"]);
    assert.equal(app.controllers.tts.ttsQueue.length, 0);
    assert.ok(backend.requests.some(({ init }) => init.body && JSON.parse(init.body).message === "测试消息"));
    app.dispose();
  }

  // Final replies and restored history only present media activity; explicit UI
  // commands execute once, even when repeated before the native reply arrives.
  {
    const payload = {
      speech: "已切换歌曲。",
      activity: { type: "music", action: "next", source_kind: "system_media", system_media: true }
    };
    const backend = mockBackend({ stream: [
      { type: "turn_start" }, { type: "final", payload }, { type: "stream_end" }
    ] });
    const result = deferred();
    const submitted = deferred();
    const { app, elements, nativeCalls } = make({ ...backend, tauri: true, invoke: command => {
      if (command === "control_system_media") {
        submitted.resolve();
        return result.promise;
      }
      if (command === "get_current_system_media") return { available: false, reason: "no_active_session" };
      return undefined;
    } });
    await app.start();
    app.state.voiceEnabled = false;
    app.controllers.music.musicQueue.push({ sourceId: "test-audio", cachedPath: "synthetic.mp3", displayName: "测试音轨" });
    const mediaCalls = () => nativeCalls.filter(call => call.command === "control_system_media");
    await app.controllers.chat.sendMessage("媒体最终回复");
    assert.equal(elements.bubbleText.textContent, payload.speech);
    assert.equal(mediaCalls().length, 0, "ordinary final activity cannot execute native media");
    assert.equal(app.controllers.music.musicPlaying, false);
    assert.equal(elements.musicPlayer.paused, true);

    const command = { command: "smtcAction", action: "next", operationId: "media-once" };
    const first = app.controllers.settings.handleSettingsCommand(command);
    await submitted.promise;
    const duplicate = await app.controllers.settings.handleSettingsCommand(command);
    assert.equal(duplicate.ok, false);
    assert.equal(duplicate.status, "duplicate");
    assert.equal(duplicate.reason, "operation_already_submitted");
    assert.equal(mediaCalls().length, 1);
    assert.deepEqual(mediaCalls()[0].payload, { action: "next", operationId: "media-once" });
    result.resolve({ ok: true, status: "executed" });
    assert.equal((await first).ok, true);

    assert.equal(app.controllers.bubble.restoreLatestReply({ latest_final_json: payload }), true);
    assert.equal(app.controllers.bubble.renderPayload(payload, { force: true, speaking: false }), true);
    assert.equal(elements.bubbleText.textContent, payload.speech);
    assert.equal(mediaCalls().length, 1, "restored and replayed replies cannot reissue a completed command");
    assert.equal(app.controllers.music.musicPlaying, false);
    assert.equal(elements.musicPlayer.paused, true);
    app.dispose();
  }

  // A delayed media play promise cannot revive a stopped track.
  {
    const play = deferred();
    const { app, elements } = make();
    app.controllers.music.musicQueue.push({ sourceId: "test-audio", cachedPath: "synthetic.mp3", displayName: "测试音轨" });
    elements.musicPlayer.play = () => play.promise;
    const pending = app.controllers.music.playMusicQueueIndex(0);
    app.controllers.music.stopMusic({ silent: true, clearQueue: true });
    play.resolve();
    assert.equal(await pending, false);
    assert.equal(app.controllers.music.musicPlaying, false);
    assert.equal(app.controllers.music.musicTrack, null);
    app.dispose();
  }

  // A slow ensure response cannot restore another character's reply or mark it offline.
  {
    const response = deferred();
    let requestedSignal;
    const backend = mockBackend({ override: async (url, init) => {
      if (!url.pathname.endsWith("/think")) return undefined;
      requestedSignal = init.signal;
      const body = new ReadableStream({ start(controller) {
        controller.enqueue(new TextEncoder().encode('{"type":"turn_start"}\n'));
        init.signal.addEventListener("abort", () => controller.error(new DOMException("Stopped", "AbortError")), { once: true });
      } });
      response.resolve();
      return new Response(body);
    } });
    const { app } = make(backend);
    await app.start();
    app.state.voiceEnabled = false;
    const pending = app.controllers.chat.sendMessage("持续流");
    await response.promise;
    await tick();
    app.controllers.chat.interruptReply({ reason: "test_stop" });
    assert.equal(requestedSignal.aborted, true, "stream remains abortable after response headers");
    await pending;
    assert.equal(app.controllers.chat.sending, false);
    app.dispose();
  }

  // Latest health wins even when two checks finish out of order in the same scope.
  {
    const responses = [];
    const backend = mockBackend({ override: url => {
      if (!url.pathname.endsWith("/desktop-pet/health")) return undefined;
      const pending = deferred(); responses.push(pending); return pending.promise;
    } });
    const { app } = make(backend);
    app.state.boundBotId = "test-bot";
    const first = app.controllers.backend.checkBackendHealth();
    for (let i = 0; i < 10 && responses.length < 1; i++) await tick();
    assert.equal(responses.length, 1);
    const second = app.controllers.backend.checkBackendHealth();
    for (let i = 0; i < 10 && responses.length < 2; i++) await tick();
    assert.equal(responses.length, 2);
    responses[1].resolve(backend.json({ status: "ok" }));
    assert.equal((await second).healthy, true);
    responses[0].reject(new Error("obsolete health error"));
    assert.equal((await first).healthy, false);
    assert.equal(app.resourceState.health, "online");
    app.dispose();
  }

  // Late microphone permission after cancellation releases the device in the real voice controller.
  {
    const permission = deferred();
    let acquisitions = 0, stopped = 0;
    const { app } = make({ MediaRecorder: class {}, mediaDevices: { getUserMedia: () => { acquisitions++; return permission.promise; } } });
    app.state.voiceInputEnabled = true;
    const opening = app.controllers.voice.startVoiceRecording();
    await tick();
    assert.equal(app.controllers.voice.voiceInputState, "opening");
    await app.controllers.voice.startVoiceRecording();
    assert.equal(acquisitions, 1);
    await app.controllers.voice.cancelVoiceRecording();
    await opening;
    permission.resolve({ getTracks: () => [{ stop: () => stopped++ }] });
    await tick();
    assert.equal(stopped, 1);
    assert.equal(app.controllers.voice.voiceInputState, "idle");
    app.dispose();
  }

  // A slow ensure response cannot restore another character's reply or mark it offline.
  {
    const response = deferred();
    const { app } = make({ fetch: () => response.promise });
    app.resourceState.health = "online";
    const pending = app.controllers.backend.ensureBackendSession({ restoreLatest: true });
    app.state.characterPackId = "another-character";
    response.resolve(new Response(JSON.stringify({ last_turn: { speech: "old reply" } }), { headers: { "Content-Type": "application/json" } }));
    assert.equal(await pending, null);
    assert.equal(app.controllers.settings.buildSettingsSnapshot().active.sending, false);
    assert.equal(app.resourceState.health, "online");
    app.dispose();
  }

  // A slow error after switching away and back must not become the current error.
  {
    const response = deferred();
    const { app } = make({ fetch: () => response.promise });
    app.resourceState.health = "online";
    const previous = app.state.boundBotId;
    const pending = app.controllers.backend.ensureBackendSession();
    app.state.boundBotId = "other";
    app.state.boundBotId = previous;
    response.reject(new Error("obsolete connection failure"));
    assert.equal(await pending, null);
    assert.equal(app.resourceState.health, "online");
    app.dispose();
  }

  // A disposed lifetime releases late native registrations and rejects new timers.
  {
    const env = createEnvironment();
    const lifecycle = createLifecycle(env.platform.window);
    let calls = 0;
    lifecycle.on(env.platform.window, "test", () => calls++);
    lifecycle.setTimeout(() => calls++, 5);
    lifecycle.dispose();
    lifecycle.track(() => calls++);
    env.platform.window.dispatchEvent(new Event("test"));
    assert.equal(lifecycle.setTimeout(() => calls++, 0), 0);
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(calls, 1);
  }

  // Busy state belongs to the coordinator: settling voice cannot clear a text turn.
  {
    const { app } = make();
    const chat = app.controllers.chat;
    const token = chat.beginTurn("test");
    chat.setVoiceSending(true);
    chat.setVoiceSending(false);
    assert.equal(chat.sending, true);
    chat.endTurn(token);
    assert.equal(chat.sending, false);
    app.dispose();
  }
  await tick();
  console.log("pet runtime smoke: startup/re-entry, streaming/abort, pure snapshots, command failures/races, latest health, scope changes, media final/history and duplicate execution, media stop, microphone cancellation, busy ownership and lifetime cleanup passed");
} finally {
  for (const app of apps) app.dispose();
  await runtime.close();
}
