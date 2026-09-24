import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { mediaKind, mediaPosition } from "../src/media-awareness.js";
import { resolveActiveMediaControl, MEDIA_CONTROL_TARGETS } from "../src/media-control.js";

assert.equal(mediaKind({ sourceApp: "QQMusic.exe" }), "music");
assert.equal(mediaKind({ sourceApp: "chrome.exe", mediaType: "music" }), "browser_media");
assert.equal(mediaKind({ sourceApp: "msedge.exe", mediaType: "video" }), "video");
assert.equal(mediaKind({ sourceApp: "UnknownPlayer" }), "unknown");
assert.equal(mediaPosition({ positionSeconds: null }), null);
assert.equal(mediaPosition({ positionSeconds: 10, capturedAt: 1000, isPlaying: true, playbackRate: 2 }, 2500), 13);
assert.equal(mediaPosition({ positionSeconds: 80, capturedAt: 1000, isPlaying: false }, 9000), 80);
assert.equal(mediaPosition({ positionSeconds: 5, capturedAt: 9000, isPlaying: true }, 9000), 5);

const source = fs.readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
const ctx = {
  systemMedia: {}, musicTrack: null, musicPlaying: false, musicPaused: false,
  mediaKind, mediaPosition, MEDIA_CONTROL_TARGETS,
  buildMusicRecommendationsSnapshot: () => [], buildPlayableMusicCatalog: () => [],
  isFreshSystemMedia: (media) => Boolean(media.ok), simpleHash: () => "hash",
  safePositiveSeconds: (value) => Number(value || 0),
  buildSystemMediaLyricSnapshot: () => { throw Error("video must not request lyrics"); },
};
ctx.buildActiveMediaControlSnapshot = () => resolveActiveMediaControl({
  localTrack: ctx.musicTrack, localPlaying: ctx.musicPlaying, localPaused: ctx.musicPaused,
  systemMedia: ctx.systemMedia, systemControllable: Boolean(ctx.systemMedia.ok)
});
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf("function buildDesktopMusicActivity("), source.indexOf("function buildPlayableMusicCatalog(")), ctx);
vm.runInContext(source.slice(source.indexOf("function isMusicEmotionSourceActive("), source.indexOf("function summarizeSystemMedia(")), ctx);
ctx.musicTrack = { sourceId: "local-paused" }; ctx.musicPaused = true;
ctx.systemMedia = { ok: true, sourceApp: "chrome.exe", mediaType: "music", title: "Bilibili video", isPlaying: true, positionSeconds: 80, capturedAt: Date.now() };
let result = ctx.buildDesktopMusicActivity({ excludePaused: true });
assert.equal(result.type, "media_playback");
assert.equal(result.title, "Bilibili video");
assert.equal("lyric_current" in result, false);
assert.equal(ctx.isMusicEmotionSourceActive(), false);
ctx.musicTrack = null; ctx.musicPaused = false;
ctx.systemMedia = { ok: true, sourceApp: "QQMusic.exe", title: "Paused song", isPlaying: false, playbackStatus: "paused", capturedAt: Date.now() };
ctx.buildSystemMediaLyricSnapshot = () => null; ctx.systemMediaLyrics = {};
assert.equal(ctx.buildDesktopMusicActivity({ excludePaused: true }), null);
assert.equal(ctx.buildDesktopMusicActivity().status, "paused");
ctx.systemMedia.isPlaying = true;
assert.equal(ctx.isMusicEmotionSourceActive(), true);
assert.equal(ctx.buildDesktopMusicActivity().type, "audio_playback");
// Run the production polling path: a seek must cancel an obsolete proactive
// remark and discard its frames, while normal rate-adjusted playback must not.
let interruptions = 0, clears = 0, captures = 0;
Object.assign(ctx, {
  state: { screenVisionEnabled: true }, proactiveWakeRunning: true,
  screenObservationLastSentAt: 123,
  screenObservation: { clear: () => { clears += 1; } },
  interruptReply: () => { interruptions += 1; },
  scheduleScreenVisionCapture: () => { captures += 1; },
  normalizeSystemMediaSnapshot: (value) => value,
  applySystemMediaLyricsForTrack: () => {}, recentTracksHistory: [],
  emitPanelEvent: () => {}, emptySystemMediaLyricsSnapshot: () => ({}),
  setMusicEmotion: () => {}, scheduleMusicSnapshot: () => {}, schedulePanelStateSync: () => {},
});
vm.runInContext(source.slice(source.indexOf("async function readAndApplySystemMediaSnapshot("), source.indexOf("function emptySystemMediaSnapshot(")), ctx);
ctx.systemMedia = { ok: true, sourceApp: "chrome.exe", trackKey: "video", positionSeconds: 10, capturedAt: Date.now(), isPlaying: true, playbackRate: 2 };
ctx.tauriCall = async () => ({ ...ctx.systemMedia, positionSeconds: 80, capturedAt: Date.now() });
await ctx.readAndApplySystemMediaSnapshot();
assert.equal(interruptions, 1);
assert.equal(clears, 1);
assert.equal(captures, 1);
assert.equal(ctx.screenObservationLastSentAt, 0);
ctx.tauriCall = async () => ({ ...ctx.systemMedia, positionSeconds: mediaPosition(ctx.systemMedia), capturedAt: Date.now() });
await ctx.readAndApplySystemMediaSnapshot();
assert.equal(interruptions, 1);
assert.equal(clears, 1);
console.log("media awareness: classification, active target, paused exclusion, seek cancellation and playback rate passed");
