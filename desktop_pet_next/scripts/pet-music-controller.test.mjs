import assert from "node:assert/strict";
import test, { after } from "node:test";
import { createLifecycle } from "../src/pet/lifecycle.js";
import { createRuntimeScope } from "../src/pet/scope.js";
import { normalizeMusicTrack } from "../src/desktop-music-player.js";
import { deferred, loadPetRuntime, tick } from "./pet-runtime-harness.mjs";

const runtime = await loadPetRuntime();
const { createMusicController } = await runtime.load("/src/pet/music.js");
const sceneBridge = await runtime.load("/src/scene-desktop-bridge.js");
after(() => runtime.close());

function fixture(t, { stage, prepare, fetch, assetFetch, play } = {}) {
  const calls = [], requests = [], effects = [], timers = new Map();
  let timerId = 0;
  const window = {
    setTimeout(callback, delay) { const id = ++timerId; timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch: assetFetch || (async () => ({ ok: true, blob: async () => new Blob(["audio"], { type: "audio/mpeg" }) }))
  };
  const lifecycle = createLifecycle(window);
  const state = {
    sessionId: "session-a", profileUserId: "master", characterPackId: "pack-a",
    boundBotId: "bot-a", backendUrl: "http://127.0.0.1:9", instanceId: "fixture",
    voiceVolume: 0.5, musicPlayMode: "顺序播放", currentEmotion: "normal"
  };
  const scope = createRuntimeScope(state, () => lifecycle.disposed);
  const player = {
    src: "", currentTime: 0, duration: 120, volume: 1, paused: true,
    pause() { this.paused = true; }, load() {},
    removeAttribute(name) { if (name === "src") this.src = ""; },
    play() { return play ? play(this) : (this.paused = false, Promise.resolve()); }
  };
  const json = payload => new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json" } });
  const asset = path => ({ cachedPath: path, fileName: path.split("/").pop(), extension: "mp3", lyricText: "[00:01]本地歌词" });
  const platform = {
    window, isTauriRuntime: true, convertFileSrc: path => path,
    async tauriCall(command, payload) {
      calls.push({ command, payload });
      return stage ? stage(payload) : { local_path: `asset://${payload.handle}.mp3` };
    },
    async invoke(command, payload) {
      calls.push({ command, payload });
      return prepare ? prepare(payload) : asset(payload.path);
    },
    async emit(name) { effects.push(["emit", name]); },
    async backendFetch(url, init = {}) {
      requests.push({ url, init });
      if (fetch) return fetch(url, init);
      if (url.includes("workspace/summary")) return json({ sections: { files: [], outputs: [] } });
      if (url.includes("attachments/audio")) return json({ ok: true, attachment: { handle: "uploaded-audio" } });
      return json({ ok: true, timeline: { status: "ready", segments: [{ time_seconds: 0, text: "后端歌词" }] } });
    }
  };
  const character = {
    getCurrentCharacterPackId: () => state.characterPackId,
    buildBackendCharacterContext: () => ({ user_id: state.sessionId, real_user_id: state.profileUserId, character_pack_id: state.characterPackId }),
    resolveEmotionEntry: id => ({ id }), setPetEmotion: id => effects.push(["emotion", id])
  };
  const systemMedia = {
    systemMedia: {}, systemMediaLyrics: {}, isMusicEmotionSourceActive: () => music.musicPlaying,
    isControllableSystemMedia: () => false, isFreshSystemMedia: () => false,
    summarizeSystemMedia: () => ({}), summarizeSystemMediaLyrics: () => ({}),
    controlSystemMediaPlayback: async () => ({ ok: false, reason: "unavailable" })
  };
  const settings = {
    scheduleSettingsSnapshot: () => effects.push(["snapshot"]), scheduleMusicSnapshot: () => effects.push(["music-snapshot"])
  };
  const resourceState = { health: "online" };
  const music = createMusicController({
    state, resourceState, els: { musicPlayer: player, chatForm: { hidden: true }, menu: { hidden: true } },
    platform, lifecycle, scope, character, systemMedia, settings,
    persistence: { getProfileUserId: () => state.profileUserId, scheduleSave() {}, normalizeMusicPlayMode: value => value, normalizeBooleanSetting: Boolean, DEFAULT_STATE: {} },
    status: { setRuntimeStatus: (...args) => effects.push(["status", ...args]), updateActivityControls: () => effects.push(["controls"]) },
    bubble: { showBubbleText: text => effects.push(["bubble", text]) },
    chat: { sending: false }, tts: { ttsActive: false, ttsQueue: [] }, voice: { voiceInputState: "idle" },
    window: { playState: {} }, interaction: {},
    backend: { buildBackendEndpointUrl: (_, path, params) => state.backendUrl + path + (params ? "?" + new URLSearchParams(params) : "") }
  });
  const seed = (...names) => music.musicQueue.push(...names.map(name => normalizeMusicTrack(asset(`asset://${name}.mp3`), { sourceId: name, displayName: name })));
  const runTimers = () => {
    for (const [id, timer] of [...timers]) { if (timers.has(id)) { timers.delete(id); timer.callback(); } }
  };
  t.after(() => { music.stopMusic({ silent: true, clearQueue: true }); lifecycle.dispose(); });
  return { music, state, scope, lifecycle, player, platform, resourceState, systemMedia, character, settings, calls, requests, effects, timers, json, asset, seed, runTimers };
}

test("workspace audio uses the real stage/prepare/play chain and deduplicates its queue", async t => {
  const f = fixture(t);
  const item = { handle: "one", title: "一首歌", itemType: "generated", format: "mp3" };
  assert.equal(await f.music.playWorkspaceAudioItem(item), true);
  assert.deepEqual(f.calls.map(call => call.command), ["open_workspace_item", "prepare_audio_asset"]);
  assert.equal(f.calls[0].payload.sessionId, "session-a");
  assert.equal(f.music.musicTrack.sourceId, "workspace:generated:one");
  assert.equal(f.music.musicLoading, false);
  assert.equal(await f.music.playWorkspaceAudioItem(item), true);
  assert.equal(f.music.musicQueue.length, 1);
  assert.equal(f.calls.length, 2);
});

test("empty audio handles and failed staging return failure without replacing a playing track", async t => {
  const f = fixture(t, { stage: async () => { throw new Error("stage failed"); } });
  f.seed("original");
  await f.music.playMusicQueueIndex(0);
  assert.equal(await f.music.playWorkspaceAudioItem({}), false);
  assert.equal(await f.music.playWorkspaceAudioItem({ handle: "failed" }), false);
  assert.equal(f.music.musicTrack.sourceId, "original");
  assert.equal(f.music.musicPlaying, true);
  assert.ok(f.effects.some(effect => effect[0] === "status" && effect[1].includes("stage failed")));
});

test("an older staging result cannot take playback back from a later selection", async t => {
  const old = deferred();
  const f = fixture(t, { stage: payload => payload.handle === "old" ? old.promise : { local_path: "asset://new.mp3" } });
  const pending = f.music.playWorkspaceAudioItem({ handle: "old" });
  assert.equal(await f.music.playWorkspaceAudioItem({ handle: "new" }), true);
  old.resolve({ local_path: "asset://old.mp3" });
  assert.equal(await pending, false);
  assert.equal(f.music.musicTrack.sourceId, "workspace:attachment:new");
  assert.equal(f.calls.filter(call => call.command === "prepare_audio_asset").length, 1);
});

test("repeated selection while preparation is pending shares one stage and one play", async t => {
  const staged = deferred();
  const f = fixture(t, { stage: () => staged.promise });
  const one = f.music.playWorkspaceAudioItem({ handle: "same" });
  const two = f.music.playWorkspaceAudioItem({ handle: "same" });
  assert.equal(f.calls.length, 1);
  staged.resolve({ local_path: "asset://same.mp3" });
  assert.deepEqual(await Promise.all([one, two]), [true, true]);
  assert.equal(f.calls.filter(call => call.command === "prepare_audio_asset").length, 1);
  assert.equal(f.music.musicQueue.length, 1);
});

test("stop during staging cancels the pending selection and clears loading immediately", async t => {
  const staged = deferred();
  const f = fixture(t, { stage: () => staged.promise });
  const pending = f.music.playWorkspaceAudioItem({ handle: "old" });
  assert.equal(f.music.musicLoading, true);
  f.music.stopMusic({ silent: true, clearQueue: true });
  assert.equal(f.music.musicLoading, false);
  const count = f.effects.length;
  staged.resolve({ local_path: "asset://old.mp3" });
  assert.equal(await pending, false);
  assert.equal(f.effects.length, count);
  assert.equal(f.music.musicQueue.length, 0);
});

test("late native preparation cannot clear the new selection's loading state", async t => {
  const old = deferred(), current = deferred();
  const f = fixture(t, { prepare: payload => payload.path.includes("old") ? old.promise : current.promise });
  const first = f.music.playWorkspaceAudioItem({ handle: "old" });
  await tick();
  f.music.stopMusic({ silent: true, clearQueue: true });
  const second = f.music.playWorkspaceAudioItem({ handle: "current" });
  await tick();
  old.resolve(f.asset("asset://old.mp3"));
  assert.equal(await first, false);
  assert.equal(f.music.musicLoading, true);
  current.resolve(f.asset("asset://current.mp3"));
  assert.equal(await second, true);
  assert.equal(f.music.musicLoading, false);
});

test("a new session refresh proceeds while the old catalog request is pending", async t => {
  const old = deferred();
  let f;
  f = fixture(t, { fetch: url => url.includes("session-a") ? old.promise : f.json({ sections: { files: [{ kind: "audio", handle: "new", title: "new.mp3" }] } }) });
  f.music.scheduleWorkspaceMusicRecommendationsRefresh(0);
  f.runTimers();
  await tick();
  f.state.sessionId = "session-b";
  f.music.scheduleWorkspaceMusicRecommendationsRefresh(0);
  f.runTimers();
  await tick();
  assert.equal(f.requests.length, 2);
  assert.equal(f.music.workspaceAudioCatalog[0]?.handle, "new");
  old.resolve(f.json({ sections: { files: [{ kind: "audio", handle: "old", title: "old.mp3" }] } }));
  await tick();
  assert.equal(f.music.workspaceAudioCatalog[0]?.handle, "new");
});

test("a refresh triggered by an import during a pending request is coalesced and retried", async t => {
  const first = deferred();
  let f;
  f = fixture(t, { fetch: () => f.requests.length === 1 ? first.promise : f.json({ sections: { files: [{ kind: "audio", handle: "imported", title: "imported.mp3" }] } }) });
  f.music.scheduleWorkspaceMusicRecommendationsRefresh(0);
  f.runTimers();
  await tick();
  f.music.scheduleWorkspaceMusicRecommendationsRefresh(0);
  f.runTimers();
  f.music.scheduleWorkspaceMusicRecommendationsRefresh(0);
  f.runTimers();
  assert.equal(f.requests.length, 1);
  first.resolve(f.json({ sections: { files: [] } }));
  await tick();
  f.runTimers();
  await tick();
  assert.equal(f.requests.length, 2);
  assert.equal(f.music.workspaceAudioCatalog[0]?.handle, "imported");
});

test("workspace songs remain available to the player without becoming chat activity", async t => {
  let f;
  f = fixture(t, { fetch: () => f.json({ sections: { files: [{ kind: "audio", handle: "available", title: "未播放的歌.mp3" }] } }) });
  f.music.scheduleWorkspaceMusicRecommendationsRefresh(0);
  f.runTimers();
  await tick();
  assert.equal(f.music.workspaceAudioCatalog[0]?.handle, "available");
  assert.equal(f.music.buildMusicSnapshot().recommendations[0]?.title, "未播放的歌.mp3");
  assert.equal(f.music.buildDesktopMusicActivity(), null);
  assert.equal(f.music.buildDesktopMusicActivity({ excludePaused: true }), null);
});

test("local chat activity contains the current track and lyrics without song lists", async t => {
  const f = fixture(t);
  f.seed("current", "next", "other");
  await f.music.playMusicQueueIndex(0);
  f.player.currentTime = 2;
  const activity = f.music.buildDesktopMusicActivity();
  assert.equal(activity.type, "audio_playback");
  assert.equal(activity.source_id, "current");
  assert.equal(activity.status, "running");
  assert.equal(activity.progress_seconds, 2);
  assert.equal(activity.queue_count, 3);
  assert.equal(activity.next_title, "next");
  assert.equal(activity.lyric_current, "本地歌词");
  for (const key of ["recommendations", "catalog", "queue_titles"]) assert.equal(key in activity, false);
  f.music.pauseMusic();
  assert.equal(f.music.buildDesktopMusicActivity().status, "paused");
  assert.equal(f.music.buildDesktopMusicActivity({ excludePaused: true }), null);
  f.music.stopMusic({ silent: true, clearQueue: true });
  assert.equal(f.music.buildDesktopMusicActivity(), null);
});

test("system music chat activity does not include the local queue", async t => {
  const f = fixture(t);
  f.seed("unused-local");
  Object.assign(f.systemMedia, {
    systemMedia: { sourceApp: "QQMusic.exe", trackKey: "system", title: "正在听的歌", isPlaying: true, capturedAt: Date.now(), positionSeconds: 2 },
    isFreshSystemMedia: () => true,
    isControllableSystemMedia: () => true,
    safePositiveSeconds: value => Number(value || 0),
    buildSystemMediaLyricSnapshot: () => ({ text: "附近歌词", previousText: "上一句", nextText: "下一句" })
  });
  const activity = f.music.buildDesktopMusicActivity();
  assert.equal(activity.type, "audio_playback");
  assert.equal(activity.source_id, "system_media:system");
  assert.equal(activity.source_kind, "system_media");
  assert.equal(activity.lyric_current, "附近歌词");
  for (const key of ["recommendations", "catalog", "queue_titles"]) assert.equal(key in activity, false);
});

test("audio preparation completed after a session switch is discarded", async t => {
  const prepared = deferred();
  const f = fixture(t, { prepare: () => prepared.promise });
  const pending = f.music.playWorkspaceAudioItem({ handle: "old" });
  await tick();
  f.state.sessionId = "session-b";
  const count = f.effects.length;
  prepared.resolve(f.asset("asset://old.mp3"));
  assert.equal(await pending, false);
  assert.equal(f.music.musicQueue.length, 0);
  assert.equal(f.effects.length, count);
});

test("a rejected new track clears the preceding track's playing state", async t => {
  const f = fixture(t, { play: player => player.src.includes("bad") ? Promise.reject(new Error("decode failed")) : (player.paused = false, Promise.resolve()) });
  f.seed("good", "bad");
  await f.music.playMusicQueueIndex(0);
  await assert.rejects(f.music.playMusicQueueIndex(1), /decode failed/);
  assert.equal(f.music.musicPlaying, false);
  assert.equal(f.music.musicPaused, false);
  assert.equal(f.player.paused, true);
  assert.equal(f.music.buildActiveMediaControlSnapshot().playbackStatus, "stopped");
});

test("a pending switch exposes stopped playback until the new play promise succeeds", async t => {
  const pendingPlay = deferred();
  const f = fixture(t, { play: player => player.src.includes("pending") ? pendingPlay.promise : (player.paused = false, Promise.resolve()) });
  f.seed("good", "pending");
  await f.music.playMusicQueueIndex(0);
  const pending = f.music.playMusicQueueIndex(1);
  assert.equal(f.music.musicPlaying, false);
  assert.equal(f.music.musicPaused, false);
  assert.equal(f.music.musicLoading, true);
  pendingPlay.resolve();
  assert.equal(await pending, true);
  assert.equal(f.music.musicPlaying, true);
  assert.equal(f.music.musicLoading, false);
});

test("late automatic-next completion cannot stop the user's newer track", async t => {
  const automatic = deferred();
  const f = fixture(t, { play: player => player.src.includes("automatic") ? automatic.promise : (player.paused = false, Promise.resolve()) });
  f.seed("ended", "automatic", "chosen");
  await f.music.playMusicQueueIndex(0);
  const ending = f.music.handleMusicEnded();
  assert.equal(await f.music.playMusicQueueIndex(2), true);
  automatic.resolve();
  await ending;
  assert.equal(f.music.musicTrack.sourceId, "chosen");
  assert.equal(f.music.musicPlaying, true);
  assert.equal(f.player.paused, false);
});

test("scene playback ownership also blocks resume of a stopped local track", async t => {
  const f = fixture(t);
  f.seed("local");
  await f.music.playMusicQueueIndex(0);
  const listeners = new Map();
  const dispose = await sceneBridge.registerSceneDesktopBridge({
    stop: () => f.music.stopMusic({ silent: true }), scope: () => ({}), equip() {},
    platform: { listen: async (event, callback) => { listeners.set(event, callback); return () => {}; }, invoke: async () => false }
  });
  t.after(dispose);
  await listeners.get("scene-playback-claim")({ payload: 1 });
  assert.equal(await f.music.toggleMusicPlayback(), false);
  assert.equal(f.music.musicPlaying, false);
  assert.equal(f.player.paused, true);
});

test("queue navigation, removal, seek and loop modes retain their behavior", async t => {
  const f = fixture(t);
  f.seed("one", "two", "three");
  await f.music.playMusicQueueIndex(1);
  assert.equal(f.music.hasPreviousMusicTrack(), true);
  assert.equal(f.music.hasNextMusicTrack(), true);
  await f.music.removeMusicTrackBySourceId("one");
  assert.equal(f.music.musicQueueIndex, 0);
  assert.equal(f.music.musicTrack.sourceId, "two");
  f.music.seekMusicPlayback(999);
  assert.equal(f.player.currentTime, 120);
  f.state.musicPlayMode = "单曲循环";
  await f.music.handleMusicEnded();
  assert.equal(f.music.musicTrack.sourceId, "two");
  f.state.musicPlayMode = "列表循环";
  await f.music.playNextMusicTrack();
  await f.music.handleMusicEnded();
  assert.equal(f.music.musicTrack.sourceId, "two");
  f.state.musicPlayMode = "顺序播放";
  await f.music.playNextMusicTrack();
  await f.music.handleMusicEnded();
  assert.equal(f.music.musicPlaying, false);
  assert.equal(f.music.musicQueue.length, 2);
});

function backendLyrics(f, index = 0) {
  const track = f.music.musicQueue[index];
  track.lyrics = [];
  track.lyricLineCount = 0;
  track.timelineStatus = "idle";
  return track;
}

test("a late local file read cannot upload its audio into a new session", async t => {
  const local = deferred();
  const f = fixture(t, { assetFetch: () => local.promise });
  f.seed("one");
  const track = backendLyrics(f);
  await f.music.playMusicQueueIndex(0);
  f.runTimers();
  await tick();
  assert.equal(track.timelineLoading, true);
  f.state.sessionId = "session-b";
  const count = f.effects.length;
  local.resolve({ ok: true, blob: async () => new Blob(["audio"]) });
  await tick();
  assert.equal(f.requests.length, 0);
  assert.equal(track.timelineLoading, false);
  assert.equal(f.effects.length, count);
});

test("late blob decoding after a character switch never starts an upload", async t => {
  const blob = deferred();
  const f = fixture(t, { assetFetch: async () => ({ ok: true, blob: () => blob.promise }) });
  f.seed("one");
  backendLyrics(f);
  await f.music.playMusicQueueIndex(0);
  f.runTimers();
  await tick();
  f.state.characterPackId = "pack-b";
  blob.resolve(new Blob(["audio"]));
  await tick();
  assert.equal(f.requests.length, 0);
});

test("switching tracks aborts upload and discards a late attachment result", async t => {
  const upload = deferred();
  const f = fixture(t, { fetch: () => upload.promise });
  f.seed("old", "new");
  const old = backendLyrics(f);
  await f.music.playMusicQueueIndex(0);
  f.runTimers();
  await tick();
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].init.body.get("session_id"), "session-a");
  await f.music.playMusicQueueIndex(1);
  assert.equal(f.requests[0].init.signal?.aborted, true);
  upload.resolve(f.json({ ok: true, attachment: { handle: "old-upload" } }));
  await tick();
  assert.equal(f.requests.length, 1);
  assert.equal(old.backendAttachment, null);
  assert.equal(f.music.musicTrack.sourceId, "new");
});

test("a timeline result arriving after stop cannot update lyrics or schedule a retry", async t => {
  const timeline = deferred();
  let f;
  f = fixture(t, { fetch: url => url.includes("attachments/audio") ? f.json({ ok: true, attachment: { handle: "audio" } }) : timeline.promise });
  f.seed("one");
  const track = backendLyrics(f);
  await f.music.playMusicQueueIndex(0);
  f.runTimers();
  await tick();
  assert.equal(f.requests.length, 2);
  f.music.stopMusic({ silent: true });
  const count = f.effects.length;
  timeline.resolve(f.json({ ok: true, timeline: { status: "ready", segments: [{ time_seconds: 1, text: "旧歌词" }] } }));
  await tick();
  assert.deepEqual(track.timelineLyrics, []);
  assert.equal(f.effects.length, count);
  assert.equal(f.timers.size, 0);
});

test("a cached timeline attachment is never reused in another session", async t => {
  let uploads = 0, f;
  f = fixture(t, { fetch: (url, init) => url.includes("attachments/audio")
    ? f.json({ ok: true, attachment: { handle: `audio-${++uploads}-${init.body.get("session_id")}` } })
    : f.json({ ok: true, timeline: { status: "ready", segments: [{ time_seconds: 1, text: "歌词" }] } }) });
  f.seed("one");
  const track = backendLyrics(f);
  await f.music.playMusicQueueIndex(0);
  f.runTimers();
  await tick();
  assert.equal(track.timelineStatus, "ready");
  f.state.sessionId = "session-b";
  const observed = f.music.buildDesktopMusicActivity();
  assert.equal(observed.attachment_handle, "");
  assert.equal(observed.lyric_current, "");
  await f.music.playMusicQueueIndex(0);
  f.runTimers();
  await tick();
  assert.equal(uploads, 2);
  assert.equal(track.backendAttachment.handle, "audio-2-session-b");
  const preparations = f.requests.filter(request => request.url.includes("music-timeline"));
  assert.equal(JSON.parse(preparations[1].init.body).session_id, "session-b");
  assert.equal(JSON.parse(preparations[1].init.body).activity.attachment_handle, "audio-2-session-b");
});

test("local timeline preparation keeps its own track when system media becomes active", async t => {
  const upload = deferred();
  let f;
  f = fixture(t, { fetch: url => url.includes("attachments/audio") ? upload.promise : f.json({ ok: true, timeline: { status: "ready", segments: [{ time_seconds: 0, text: "local lyric" }] } }) });
  f.seed("local-song");
  backendLyrics(f);
  await f.music.playMusicQueueIndex(0);
  f.runTimers();
  await tick();
  f.music.pauseMusic();
  f.systemMedia.systemMedia = { sourceApp: "QQMusic.exe", title: "system-song", isPlaying: true, capturedAt: Date.now() };
  f.systemMedia.isFreshSystemMedia = () => true;
  f.systemMedia.isControllableSystemMedia = () => true;
  f.systemMedia.safePositiveSeconds = () => 0;
  f.systemMedia.buildSystemMediaLyricSnapshot = () => null;
  upload.resolve(f.json({ ok: true, attachment: { handle: "local-upload" } }));
  await tick();
  const request = f.requests.find(request => request.url.includes("music-timeline"));
  const activity = JSON.parse(request.init.body).activity;
  assert.equal(activity.source_id, "local-song");
  assert.equal(activity.title, "local-song");
});

test("pending and failed timelines retain the reason, retry once, and stop polling when ready", async t => {
  let preparations = 0, f;
  f = fixture(t, { fetch: url => {
    if (url.includes("attachments/audio")) return f.json({ ok: true, attachment: { handle: "audio" } });
    preparations++;
    if (preparations === 1) return f.json({ ok: true, timeline: { status: "processing" } });
    if (preparations === 2) return new Response(JSON.stringify({ detail: "timeline unavailable" }), { status: 503 });
    return f.json({ ok: true, timeline: { status: "ready", segments: [{ time_seconds: 1, text: "ready lyric" }] } });
  } });
  f.seed("one");
  const track = backendLyrics(f);
  await f.music.playMusicQueueIndex(0);
  f.runTimers();
  await tick();
  assert.equal(track.timelineStatus, "processing");
  f.runTimers();
  await tick();
  assert.equal(track.timelineStatus, "failed");
  assert.match(track.timelineError, /timeline unavailable/);
  f.runTimers();
  await tick();
  assert.equal(track.timelineStatus, "ready");
  assert.equal(track.timelineError, "");
  assert.equal(track.timelineLyrics[0].text, "ready lyric");
  assert.equal(f.requests.filter(request => request.url.includes("attachments/audio")).length, 1);
  assert.equal(preparations, 3);
  assert.equal(f.timers.size, 0);
});

test("disposal aborts lyric analysis and late failure cannot notify the closed window", async t => {
  const timeline = deferred();
  let f;
  f = fixture(t, { fetch: url => url.includes("attachments/audio") ? f.json({ ok: true, attachment: { handle: "audio" } }) : timeline.promise });
  f.seed("one");
  const track = backendLyrics(f);
  await f.music.playMusicQueueIndex(0);
  f.runTimers();
  await tick();
  const request = f.requests.at(-1);
  f.lifecycle.dispose();
  assert.equal(request.init.signal.aborted, true);
  const count = f.effects.length;
  timeline.reject(new Error("late backend failure"));
  await tick();
  assert.equal(track.timelineLoading, false);
  assert.equal(f.effects.length, count);
  assert.equal(f.timers.size, 0);
});

test("local LRC lyrics do not start a backend analysis request", async t => {
  const f = fixture(t);
  f.seed("local-lrc");
  await f.music.playMusicQueueIndex(0);
  f.runTimers();
  await tick();
  assert.equal(f.requests.length, 0);
  assert.equal(f.music.buildMusicSnapshot().currentLyric.source, "lrc");
});

test("the facade preserves its interface, live read-only getters and pure snapshots", async t => {
  const f = fixture(t);
  const keys = [
    "pauseMusic", "musicTrack", "musicQueue", "musicQueueIndex", "musicPlaying", "musicPaused", "musicLoading",
    "musicEmotionRestoreTimer", "workspaceAudioCatalog", "setMusicPlayMode", "setMusicVolumeNormalization",
    "playWorkspaceAudioItem", "summarizeWorkspaceImportSkipped", "notifyWorkspaceRefresh", "playMusicQueueIndex",
    "playNextMusicTrack", "playPreviousMusicTrack", "playMusicTrackBySourceId", "removeMusicTrackBySourceId",
    "handleMusicEnded", "handleMusicPlaybackError", "toggleMusicPlayback", "buildActiveMediaControlSnapshot",
    "executeActiveMusicCommand", "seekMusicPlayback", "stopMusic", "clearMusicQueue", "setMusicEmotion",
    "scheduleMusicEmotionRestore", "setRestingPetEmotion", "handleRestingEmotionLoadError", "canApplyRestingEmotionNow", "resetRestingEmotionFailures",
    "getMusicDisplayName", "hasPreviousMusicTrack",
    "hasNextMusicTrack", "findMusicTrackIndexBySourceId", "getSafeMusicQueueIndex", "notifyMusicActivityUnavailable",
    "scheduleWorkspaceMusicRecommendationsRefresh", "buildMusicSnapshot", "buildDesktopMusicActivity"
  ];
  assert.deepEqual(Object.keys(f.music), keys);
  for (const name of keys.filter(name => /^music(?:Track|Queue|QueueIndex|Playing|Paused|Loading|EmotionRestoreTimer)$|^workspaceAudioCatalog$/.test(name))) {
    const descriptor = Object.getOwnPropertyDescriptor(f.music, name);
    assert.equal(typeof descriptor.get, "function");
    assert.equal(descriptor.set, undefined);
  }
  assert.deepEqual(f.effects, []);
  f.music.buildMusicSnapshot();
  f.music.buildDesktopMusicActivity();
  assert.deepEqual(f.effects, []);
  assert.equal(f.requests.length, 0);
  f.seed("one");
  await f.music.playMusicQueueIndex(0);
  assert.equal(f.music.musicPlaying, true);
  const count = f.effects.length;
  f.music.buildMusicSnapshot();
  f.music.buildDesktopMusicActivity();
  assert.equal(f.effects.length, count);
  f.music.pauseMusic();
  assert.equal(f.music.musicPaused, true);
});
