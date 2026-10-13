import { hasPreviousMusicTrack as hasPreviousMusicTrackHelper, hasNextMusicTrack as hasNextMusicTrackHelper, getPreviousMusicTrack as getPreviousMusicTrackHelper, getNextMusicTrack as getNextMusicTrackHelper, getMusicQueueLabel as getMusicQueueLabelHelper, findMusicTrackIndexBySourceId as findMusicTrackIndexBySourceIdHelper, getSafeMusicQueueIndex as getSafeMusicQueueIndexHelper } from '../../desktop-music-player.js';
import { sceneOwnsPlayback } from '../../scene-desktop-bridge.js';
import { friendlyErrorMessage, formatError, clamp } from '../utils.js';

/** Owns the queue, local player and the latest music selection. */
export function createMusicPlaybackController({
  state, els, platform: platformPort, lifecycle, scope: runtimeScope,
  status: statusPort, settings: settingsPort, bubble: bubblePort,
  emotion: musicEmotionPort, timeline: musicTimelinePort
}) {
  let musicTrack = null;
  let musicQueue = [];
  let musicQueueIndex = -1;
  let musicPlaying = false;
  let musicPaused = false;
  let musicPlaybackRevision = 0;
  let musicPlayRequest = null;

  async function queueAndPlayMusicTrack(track, { request } = {}) {
    if (!track?.cachedPath || request && !isCurrentMusicSelection(request)) return false;
    const key = track.queueDedupeKey || track.sourceId || "";
    let index = key ? musicQueue.findIndex(existing => existing.queueDedupeKey === key || existing.sourceId === key) : -1;
    if (index < 0) {
      index = musicQueue.length;
      musicQueue.push(track);
    }
    return playMusicQueueIndex(index, { request });
  }

  function beginMusicSelection() {
    return { revision: ++musicPlaybackRevision, scope: runtimeScope.capture() };
  }

  function isCurrentMusicSelection(request) {
    return !lifecycle.disposed && request.revision === musicPlaybackRevision && runtimeScope.isCurrent(request.scope);
  }

  async function playMusicQueueIndex(index, { message = "", request = null } = {}) {
    if (sceneOwnsPlayback()) return false;
    if (index < 0 || index >= musicQueue.length || !musicQueue[index]) return false;
    const selection = request || beginMusicSelection();
    const isCurrent = () => isCurrentMusicSelection(selection);
    if (!isCurrent()) return false;
    if (!musicQueue[index].cachedPath) throw new Error("缺少可播放音频路径");
    musicTimelinePort.clearBackendMusicTimelineTimer();
    resetMusicElement();
    musicQueueIndex = index;
    musicTrack = musicQueue[index];
    musicTimelinePort.prepareMusicTimelineTrack(musicTrack);
    musicPlaying = false;
    musicPaused = false;
    musicPlayRequest = selection;
    musicEmotionPort.setMusicEmotion(false);
    statusPort.setRuntimeStatus(`正在开始播放：${getMusicDisplayName()}`, { mode: "music" });
    statusPort.updateActivityControls();
    settingsPort.scheduleSettingsSnapshot();
    try {
      els.musicPlayer.src = platformPort.convertFileSrc(musicTrack.cachedPath);
      els.musicPlayer.currentTime = 0;
      els.musicPlayer.volume = state.voiceVolume;
      await els.musicPlayer.play();
      if (!isCurrent()) return false;
      musicPlaying = true;
      musicEmotionPort.setMusicEmotion(true);
      const queueLabel = getMusicQueueLabel();
      statusPort.setRuntimeStatus(`播放中：${getMusicDisplayName()}${queueLabel ? ` · ${queueLabel}` : ""}`, { mode: "music" });
      if (message) bubblePort.showBubbleText(message, { transient: true, durationMs: 2400, kind: "music" });
      musicTimelinePort.scheduleBackendMusicTimeline(musicTrack, { immediate: true });
      return true;
    } catch (error) {
      if (!isCurrent()) return false;
      statusPort.setRuntimeStatus(`音乐播放失败：${friendlyErrorMessage(formatError(error))}`, { mode: "error" });
      throw error;
    } finally {
      if (musicPlayRequest === selection) musicPlayRequest = null;
      if (isCurrent()) {
        statusPort.updateActivityControls();
        settingsPort.scheduleSettingsSnapshot();
      }
    }
  }

  async function playNextMusicTrack({ auto = false } = {}) {
    if (!hasNextMusicTrack()) {
      if (!auto) bubblePort.showBubbleText("后面没有下一首啦。", { transient: true, durationMs: 1800, kind: "music" });
      return false;
    }
    const next = musicQueue[musicQueueIndex + 1];
    return playMusicQueueIndex(musicQueueIndex + 1, {
      message: auto ? `下一首，《${next.displayName}》。` : `切到下一首：《${next.displayName}》。`
    });
  }

  async function playPreviousMusicTrack() {
    if (!hasPreviousMusicTrack()) {
      bubblePort.showBubbleText("前面没有上一首啦。", { transient: true, durationMs: 1800, kind: "music" });
      return false;
    }
    const previous = musicQueue[musicQueueIndex - 1];
    return playMusicQueueIndex(musicQueueIndex - 1, {
      message: `切回上一首：《${previous.displayName}》。`
    });
  }

  async function playMusicTrackBySourceId(sourceId) {
    const index = findMusicTrackIndexBySourceId(sourceId);
    if (index < 0) {
      bubblePort.showBubbleText("这首不在当前队列里。", { transient: true, durationMs: 1800, kind: "music" });
      return false;
    }
    if (index === musicQueueIndex) {
      return musicPlaying || await toggleMusicPlayback();
    }
    return playMusicQueueIndex(index, {
      message: `切到这首：《${musicQueue[index].displayName}》。`
    });
  }

  async function removeMusicTrackBySourceId(sourceId) {
    const index = findMusicTrackIndexBySourceId(sourceId);
    if (index < 0) {
      bubblePort.showBubbleText("队列里找不到这首啦。", { transient: true, durationMs: 1800, kind: "music" });
      return false;
    }

    const removed = musicQueue[index];
    const wasCurrent = index === musicQueueIndex;
    musicQueue.splice(index, 1);

    if (!musicQueue.length) {
      stopMusic({ announce: true, silent: false, clearQueue: true });
      return true;
    }

    if (wasCurrent) {
      const nextIndex = Math.min(index, musicQueue.length - 1);
      await playMusicQueueIndex(nextIndex, {
        message: `已移除《${removed.displayName}》，接着放《${musicQueue[nextIndex].displayName}》。`
      });
      return true;
    }

    if (index < musicQueueIndex) musicQueueIndex -= 1;
    const text = `已从队列移除：《${removed.displayName}》。`;
    statusPort.setRuntimeStatus(text, { mode: musicPlaying ? "music" : "music-paused" });
    bubblePort.showBubbleText(text, { transient: true, durationMs: 2000, kind: "music" });
    statusPort.updateActivityControls();
    settingsPort.scheduleSettingsSnapshot();
    return true;
  }

  async function handleMusicEnded() {
    if (!musicTrack || sceneOwnsPlayback()) return;
    let nextIndex = -1;
    let message = "";
    if (state.musicPlayMode === "单曲循环" && musicQueueIndex >= 0) {
      nextIndex = musicQueueIndex;
      message = `单曲循环：《${musicTrack.displayName}》。`;
    } else if (state.musicPlayMode === "随机播放" && musicQueue.length > 1) {
      nextIndex = (musicQueueIndex + 1 + Math.floor(Math.random() * (musicQueue.length - 1))) % musicQueue.length;
      message = `随机播放：《${musicQueue[nextIndex].displayName}》。`;
    } else if (hasNextMusicTrack()) {
      nextIndex = musicQueueIndex + 1;
      message = `下一首，《${musicQueue[nextIndex].displayName}》。`;
    } else if (state.musicPlayMode === "列表循环" && musicQueue.length > 1) {
      nextIndex = 0;
      message = `列表循环：《${musicQueue[0].displayName}》。`;
    }
    if (nextIndex < 0) { stopMusic({ ended: true }); return; }
    const request = beginMusicSelection();
    try { await playMusicQueueIndex(nextIndex, { message, request }); }
    catch {
      if (isCurrentMusicSelection(request)) await handleMusicPlaybackError();
    }
  }

  async function handleMusicPlaybackError() {
    if (!musicTrack) return;
    const request = beginMusicSelection();
    const failed = musicTrack;
    const failedIndex = musicQueueIndex;
    const name = getMusicDisplayName();
    if (musicQueue.length > 1 && failedIndex >= 0) {
      musicQueue.splice(failedIndex, 1);
      const nextIndex = Math.min(failedIndex, musicQueue.length - 1);
      try {
        await playMusicQueueIndex(nextIndex, {
          request,
          message: `《${name}》暂时放不了，先跳到《${musicQueue[nextIndex].displayName}》。`
        });
        return;
      } catch (error) {
        if (!isCurrentMusicSelection(request)) return;
        statusPort.setRuntimeStatus(`音乐播放失败：${friendlyErrorMessage(formatError(error))}`, { mode: "error" });
      }
    }
    stopMusic({ silent: true, clearQueue: true });
    statusPort.setRuntimeStatus(`音乐播放失败${name ? `：${name}` : ""}`, { mode: "error" });
    bubblePort.showBubbleText("这首歌好像没放出来……", { transient: true, durationMs: 2200, kind: "music" });
    if (failed?.displayName) settingsPort.scheduleSettingsSnapshot();
  }

  async function toggleMusicPlayback() {
    if (sceneOwnsPlayback()) return false;
    if (!musicTrack) {
      bubblePort.showBubbleText("把音频文件拖给我就能放啦。", { transient: true, durationMs: 2200, kind: "music" });
      statusPort.setRuntimeStatus("等待拖入音频文件", { mode: "idle" });
      return false;
    }
    const request = beginMusicSelection();
    const isCurrent = () => isCurrentMusicSelection(request);
    if (musicPlaying) {
      els.musicPlayer.pause();
      musicPlaying = false;
      musicPaused = true;
      musicEmotionPort.setMusicEmotion(false);
      statusPort.setRuntimeStatus(`音乐已暂停：${getMusicDisplayName()}`, { mode: "music-paused" });
    } else {
      musicPlayRequest = request;
      statusPort.setRuntimeStatus(`正在恢复播放：${getMusicDisplayName()}`, { mode: "music" });
      statusPort.updateActivityControls();
      settingsPort.scheduleSettingsSnapshot();
      try {
        await els.musicPlayer.play();
        if (!isCurrent()) return false;
        musicPlaying = true;
        musicPaused = false;
        musicEmotionPort.setMusicEmotion(true);
        const queueLabel = getMusicQueueLabel();
        statusPort.setRuntimeStatus(`播放中：${getMusicDisplayName()}${queueLabel ? ` · ${queueLabel}` : ""}`, { mode: "music" });
        musicTimelinePort.scheduleBackendMusicTimeline(musicTrack, { immediate: true });
      } catch (error) {
        if (!isCurrent()) return false;
        statusPort.setRuntimeStatus(`音乐继续失败：${friendlyErrorMessage(formatError(error))}`, { mode: "error" });
        statusPort.updateActivityControls();
        settingsPort.scheduleSettingsSnapshot();
        return false;
      } finally {
        if (musicPlayRequest === request) musicPlayRequest = null;
        if (isCurrent()) {
          statusPort.updateActivityControls();
          settingsPort.scheduleSettingsSnapshot();
        }
      }
    }
    statusPort.updateActivityControls();
    settingsPort.scheduleSettingsSnapshot();
    return true;
  }

  function seekMusicPlayback(seconds) {
    if (!musicTrack || !els.musicPlayer) {
      statusPort.setRuntimeStatus("暂无可跳转的音乐", { mode: "idle" });
      return;
    }
    const duration = Number(els.musicPlayer.duration || 0);
    if (!Number.isFinite(duration) || duration <= 0) {
      statusPort.setRuntimeStatus("音乐时长仍在读取中", { mode: "idle" });
      return;
    }
    const nextTime = clamp(Number(seconds || 0), 0, duration);
    try {
      els.musicPlayer.currentTime = nextTime;
      statusPort.setRuntimeStatus(`已跳转音乐进度：${getMusicDisplayName()}`, {
        mode: musicPlaying ? "music" : musicPaused ? "music-paused" : "idle"
      });
      settingsPort.scheduleMusicSnapshot(80);
    } catch {
      statusPort.setRuntimeStatus("音乐进度跳转失败", { mode: "error" });
    }
  }

  function stopMusic({ announce = false, ended = false, silent = false, clearQueue = false } = {}) {
    musicPlaybackRevision += 1;
    const hadTrack = Boolean(musicTrack);
    const name = getMusicDisplayName();
    musicPlaying = false;
    musicPaused = false;
    musicTimelinePort.clearBackendMusicTimelineTimer();
    if (els.musicPlayer) {
      els.musicPlayer.pause();
      try {
        els.musicPlayer.currentTime = 0;
      } catch {
        // Some media backends reject currentTime until metadata is ready.
      }
      if (clearQueue) {
        resetMusicElement();
      }
    }
    if (clearQueue) {
      musicTrack = null;
      musicQueue = [];
      musicQueueIndex = -1;
    }
    musicEmotionPort.setMusicEmotion(false);
    if (!silent && hadTrack) {
      const message = ended ? `《${name}》放完啦。` : `已停止音乐${name ? `：${name}` : ""}`;
      statusPort.setRuntimeStatus(message, { mode: ended ? "idle" : "stopped" });
      if (announce || ended) {
        bubblePort.showBubbleText(ended ? "这首放完啦。" : "音乐停好啦。", { transient: true, durationMs: 1900, kind: "music" });
      }
    }
    statusPort.updateActivityControls();
    settingsPort.scheduleSettingsSnapshot();
  }

  function clearMusicQueue({ announce = false } = {}) {
    if (!musicTrack && !musicQueue.length) {
      bubblePort.showBubbleText("队列现在是空的。", { transient: true, durationMs: 1600, kind: "music" });
      return;
    }
    stopMusic({ announce, clearQueue: true });
  }

  function resetMusicElement() {
    if (!els.musicPlayer) return;
    els.musicPlayer.pause();
    els.musicPlayer.removeAttribute("src");
    els.musicPlayer.load();
  }

  function getMusicDisplayName() {
    return String(musicTrack?.displayName || musicTrack?.fileName || "").trim();
  }

  function hasPreviousMusicTrack() {
    return hasPreviousMusicTrackHelper(musicQueue, musicQueueIndex);
  }

  function hasNextMusicTrack() {
    return hasNextMusicTrackHelper(musicQueue, musicQueueIndex);
  }

  function getPreviousMusicTrack() {
    return getPreviousMusicTrackHelper(musicQueue, musicQueueIndex);
  }

  function getNextMusicTrack() {
    return getNextMusicTrackHelper(musicQueue, musicQueueIndex);
  }

  function getMusicQueueLabel() {
    return getMusicQueueLabelHelper(musicQueue, musicQueueIndex);
  }

  function findMusicTrackIndexBySourceId(sourceId) {
    return findMusicTrackIndexBySourceIdHelper(musicQueue, sourceId);
  }

  function getSafeMusicQueueIndex() {
    return getSafeMusicQueueIndexHelper(musicQueue, musicQueueIndex);
  }

  function pauseMusic() {
    musicPlaybackRevision += 1;
    els.musicPlayer.pause();
    musicPlaying = false;
    musicPaused = true;
    musicEmotionPort.setMusicEmotion(false);
  }

  return {
    get musicTrack() { return musicTrack; },
    get musicQueue() { return musicQueue; },
    get musicQueueIndex() { return musicQueueIndex; },
    get musicPlaying() { return musicPlaying; },
    get musicPaused() { return musicPaused; },
    get musicPlaybackLoading() { return Boolean(musicPlayRequest && isCurrentMusicSelection(musicPlayRequest)); },
    queueAndPlayMusicTrack,
    beginMusicSelection,
    isCurrentMusicSelection,
    playMusicQueueIndex,
    playNextMusicTrack,
    playPreviousMusicTrack,
    playMusicTrackBySourceId,
    removeMusicTrackBySourceId,
    handleMusicEnded,
    handleMusicPlaybackError,
    toggleMusicPlayback,
    seekMusicPlayback,
    stopMusic,
    clearMusicQueue,
    getMusicDisplayName,
    hasPreviousMusicTrack,
    hasNextMusicTrack,
    getPreviousMusicTrack,
    getNextMusicTrack,
    getMusicQueueLabel,
    findMusicTrackIndexBySourceId,
    getSafeMusicQueueIndex,
    pauseMusic,
  };
}
