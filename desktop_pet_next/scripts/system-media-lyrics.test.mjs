import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test, { after } from "node:test";
import { loadPetRuntime } from "./pet-runtime-harness.mjs";

const runtime = await loadPetRuntime();
const { createChatController } = await runtime.load("/src/pet/chat.js");
const { createMusicSnapshotController } = await runtime.load("/src/pet/music/snapshot.js");
const { createSystemMediaController } = await runtime.load("/src/pet/system-media.js");
after(() => runtime.close());

// Exercise the Python service's real public response, rather than a second
// hand-written fixture that could repeat the timestamp contract mismatch.
const repo = fileURLToPath(new URL("../../", import.meta.url));
const python = fileURLToPath(new URL("../../.venv/Scripts/python.exe", import.meta.url));
const generated = spawnSync(python, ["-c", `
import json
from companion_v01.music_lyrics import OnlineLyricsService
service = OnlineLyricsService(base_dir=None, search_func=lambda *_: "[00:02.50]First test line\\n[00:05.00]Second test line")
print(json.dumps(service.resolve_lyrics(profile_user_id="fixture", payload={"trackKey":"track-a", "title":"Test song", "artist":"Test artist"})))
`], { cwd: repo, encoding: "utf8" });
assert.equal(generated.status, 0, generated.stderr);
const serviceResult = JSON.parse(generated.stdout);

function fixture(result = serviceResult) {
  const state = { sessionId: "fixture", profileUserId: "fixture" };
  const scope = { capture: () => state.sessionId, isCurrent: value => value === state.sessionId };
  const lifecycle = {
    setTimeout: (...args) => globalThis.setTimeout(...args),
    clearTimeout: (...args) => globalThis.clearTimeout(...args)
  };
  const media = { ok: true, status: "ready", trackKey: "track-a", title: "Test song",
    artist: "Test artist", sourceApp: "QQMusic.exe", playbackStatus: "playing",
    isPlaying: true, positionSeconds: 3, durationSeconds: 20, capturedAt: Date.now() };
  const platform = { isTauriRuntime: true, window: globalThis,
    tauriCall: async () => media,
    backendFetch: async () => new Response(JSON.stringify(result)) };
  const system = createSystemMediaController({ state, resourceState: { health: "online" }, els: {},
    platform, lifecycle, scope, persistence: { getProfileUserId: () => state.profileUserId },
    backend: { buildBackendEndpointUrl: () => "/lyrics" }, settings: { scheduleMusicSnapshot() {} },
    panel: { schedulePanelStateSync() {}, recordRecentTrack() {} },
    music: { musicPlaying: false, scheduleMusicEmotionRestore() {}, setMusicEmotion() {} } });
  const music = createMusicSnapshotController({ els: { musicPlayer: {} }, systemMedia: system,
    playback: { musicQueue: [], musicQueueIndex: -1 },
    workspace: { workspaceMusicRecommendations: [], workspaceAudioCatalog: [] },
    controls: { buildActiveMediaControlSnapshot: () => ({ target: "system" }) }, timeline: {} });
  return { state, scope, lifecycle, platform, system, music };
}

test("online lyrics survive normalization and reach the real chat request", async () => {
  const f = fixture();
  await f.system.refreshSystemMediaSnapshot();
  await f.system.hydrateSystemMediaLyricsForTurn("歌词", { waitForLyricsHydration: true });
  assert.equal(f.system.systemMediaLyrics.segments.length, 2);
  assert.equal(f.system.buildSystemMediaLyricSnapshot(3).text, "First test line");
  assert.equal(f.system.buildSystemMediaLyricSnapshot(6).text, "Second test line");
  assert.equal(f.system.buildSystemMediaLyricSnapshot(0).nextText, "First test line");

  const requests = [];
  const chat = createChatController({ ...f, resourceState: {}, els: {}, systemMedia: f.system,
    music: f.music, platform: { ...f.platform, backendFetch: async (_url, init) => {
      requests.push(JSON.parse(init.body));
      return new Response("", { status: 200 });
    } },
    persistence: { getProfileUserId: () => f.state.profileUserId },
    perception: { latestDesktopScreenFramesForThink: async () => [] },
    character: { getCurrentCharacterPackId: () => "fixture", buildCurrentVisual: () => ({}) },
    bubble: { buildClientCapabilities: () => ["audio_playback"] },
    backend: { buildBackendEndpointUrl: () => "/think", async *readNdjsonEvents() {} },
    care: { buildDesktopCareContext: () => null, getCareFeatureStatus: () => ({ enabled: false }) } });
  const stream = chat.sendThinkStream("当前歌词是什么", chat.beginTurn("message"));
  await stream.next();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].desktop_activity.lyric_current, "First test line");
  assert.equal(requests[0].desktop_activity.lyric_next, "Second test line");
  assert.equal(requests[0].desktop_activity.lyric_line_count, 2);
});

test("an unavailable provider retains its failure without invented lyrics", async () => {
  const f = fixture({ ok: false, status: "unavailable", reason: "lyrics_provider_failed", trackKey: "track-a", segments: [] });
  await f.system.refreshSystemMediaSnapshot();
  await f.system.hydrateSystemMediaLyricsForTurn("歌词", { waitForLyricsHydration: true });
  const activity = f.music.buildDesktopMusicActivity();
  assert.equal(activity.lyric_current, "");
  assert.equal(activity.lyric_status, "unavailable");
  assert.equal(activity.lyric_reason, "lyrics_provider_failed");
});
