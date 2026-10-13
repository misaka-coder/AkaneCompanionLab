import { inferMusicMimeType, normalizeBackendAttachment, normalizeTimelineLyricSegments } from '../../desktop-music-player.js';
import { readJsonResponse, extractBackendErrorMessage } from '../../desktop-voice-session.js';
import { MUSIC_TIMELINE_INITIAL_DELAY_MS, MUSIC_TIMELINE_POLL_MS, MUSIC_TIMELINE_RETRY_MS, CLIENT_MODE } from '../constants.js';
import { friendlyErrorMessage, formatError } from '../utils.js';

/** Owns lyric requests, retries and the session ownership of uploaded attachments. */
export function createMusicTimelineController({
  state, platform: platformPort, lifecycle, scope: runtimeScope, persistence: statePort,
  status: statusPort, settings: settingsPort, character: characterPort, backend: backendPort,
  playback: musicPlaybackPort, snapshot: musicSnapshotPort
}) {
  const { window } = platformPort;
  const timelineScopes = new WeakMap();
  let musicTimelineTimer = 0;
  let musicTimelineRevision = 0;
  let musicTimelineTask = null;

  function shouldUseBackendMusicTimeline(track) {
    return Boolean(platformPort.isTauriRuntime && track?.cachedPath && !hasLocalMusicLyrics(track));
  }

  function hasLocalMusicLyrics(track) {
    return Number(track?.lyricLineCount || 0) > 0 || (Array.isArray(track?.lyrics) && track.lyrics.length > 0);
  }

  function emptyMusicTimeline() {
    return {
      backendAttachment: null, timeline: null, timelineStatus: 'idle', timelineQuality: '',
      timelineError: '', timelineLyrics: [], timelineLyricLineCount: 0, timelineUpdatedAt: 0, timelineLoading: false
    };
  }

  function readMusicTimelineTrack(track) {
    const scope = track && timelineScopes.get(track);
    return scope && !runtimeScope.isCurrent(scope) ? { ...track, ...emptyMusicTimeline() } : track;
  }

  function prepareMusicTimelineTrack(track) {
    if (!track || hasLocalMusicLyrics(track)) return;
    const scope = timelineScopes.get(track);
    if (scope && !runtimeScope.isCurrent(scope)) Object.assign(track, emptyMusicTimeline());
    timelineScopes.set(track, runtimeScope.capture());
  }

  function cancelMusicTimelineRequest() {
    const task = musicTimelineTask;
    musicTimelineTask = null;
    if (!task) return;
    task.controller.abort();
    task.track.timelineLoading = false;
    if (['uploading', 'pending', 'processing'].includes(task.track.timelineStatus)) {
      task.track.timelineStatus = task.track.backendAttachment?.handle ? 'pending' : 'idle';
    }
  }

  function clearBackendMusicTimelineTimer() {
    lifecycle.clearTimeout(musicTimelineTimer);
    musicTimelineTimer = 0;
    musicTimelineRevision += 1;
    cancelMusicTimelineRequest();
  }

  function scheduleBackendMusicTimeline(track, { immediate = false, delayMs = null } = {}) {
    lifecycle.clearTimeout(musicTimelineTimer);
    musicTimelineTimer = 0;
    if (!shouldUseBackendMusicTimeline(track) || lifecycle.disposed) return;
    prepareMusicTimelineTrack(track);
    if (track.timelineStatus === 'ready' && track.timelineLyrics?.length) return;
    const scope = runtimeScope.capture();
    const revision = musicTimelineRevision;
    const delay = immediate ? MUSIC_TIMELINE_INITIAL_DELAY_MS
      : Number.isFinite(delayMs) ? Math.max(1200, delayMs) : MUSIC_TIMELINE_POLL_MS;
    musicTimelineTimer = lifecycle.setTimeout(() => {
      musicTimelineTimer = 0;
      if (revision !== musicTimelineRevision || musicPlaybackPort.musicTrack !== track || !runtimeScope.isCurrent(scope)) return;
      void ensureBackendMusicTimeline(track);
    }, delay);
  }

  function isCurrentMusicTimeline(task) {
    return musicTimelineTask === task && task.revision === musicTimelineRevision && !task.controller.signal.aborted &&
      musicPlaybackPort.musicTrack === task.track && runtimeScope.isCurrent(task.scope);
  }

  async function ensureBackendMusicTimeline(track, { force = false } = {}) {
    if (lifecycle.disposed || musicPlaybackPort.musicTrack !== track || !shouldUseBackendMusicTimeline(track)) return;
    if (musicTimelineTask && isCurrentMusicTimeline(musicTimelineTask)) return;
    cancelMusicTimelineRequest();
    prepareMusicTimelineTrack(track);
    if (!force && track.timelineStatus === 'ready' && track.timelineLyrics?.length) return;
    const identity = {
      user_id: state.sessionId || 'desktop_pet_next', session_id: state.sessionId || 'desktop_pet_next',
      real_user_id: statePort.getProfileUserId(), ...characterPort.buildBackendCharacterContext()
    };
    const task = {
      track, identity, scope: runtimeScope.capture(), revision: musicTimelineRevision, controller: new AbortController(),
      uploadUrl: backendPort.buildBackendEndpointUrl('desktop_audio_upload', '/desktop-pet/attachments/audio', { t: Date.now() }),
      prepareUrl: backendPort.buildBackendEndpointUrl('desktop_music_timeline_prepare', '/desktop-pet/music-timeline/prepare', { t: Date.now() })
    };
    musicTimelineTask = task;
    track.timelineLoading = true;
    if (!track.timelineStatus || track.timelineStatus === 'idle') track.timelineStatus = 'uploading';
    track.timelineError = '';
    settingsPort.scheduleSettingsSnapshot();
    try {
      if (!track.backendAttachment?.handle) {
        const attachment = await uploadMusicTrackForTimeline(track, task);
        if (!isCurrentMusicTimeline(task)) return;
        track.backendAttachment = attachment;
        track.timelineStatus = 'pending';
        settingsPort.scheduleSettingsSnapshot();
      }
      const result = await prepareBackendMusicTimeline(track, task);
      if (!isCurrentMusicTimeline(task)) return;
      applyBackendMusicTimeline(track, result);
      if (track.timelineStatus === 'ready') {
        statusPort.setRuntimeStatus(`后端歌词线索已准备好：${track.timelineLyricLineCount || 0} 行`, {
          mode: musicPlaybackPort.musicPlaying ? 'music' : musicPlaybackPort.musicPaused ? 'music-paused' : null
        });
      } else {
        scheduleBackendMusicTimeline(track, {
          delayMs: ['pending', 'processing'].includes(track.timelineStatus) ? MUSIC_TIMELINE_POLL_MS : MUSIC_TIMELINE_RETRY_MS
        });
      }
    } catch (error) {
      if (!isCurrentMusicTimeline(task)) return;
      track.timelineStatus = 'failed';
      track.timelineError = friendlyErrorMessage(formatError(error));
      scheduleBackendMusicTimeline(track, { delayMs: MUSIC_TIMELINE_RETRY_MS });
    } finally {
      const current = isCurrentMusicTimeline(task);
      if (musicTimelineTask === task) {
        musicTimelineTask = null;
        track.timelineLoading = false;
      }
      if (current) settingsPort.scheduleSettingsSnapshot();
    }
  }

  async function uploadMusicTrackForTimeline(track, task) {
    const assetResponse = await window.fetch(platformPort.convertFileSrc(track.cachedPath), { signal: task.controller.signal });
    if (!isCurrentMusicTimeline(task)) return null;
    if (!assetResponse.ok) throw new Error(`读取本地音频失败：HTTP ${assetResponse.status}`);
    let blob = await assetResponse.blob();
    if (!isCurrentMusicTimeline(task)) return null;
    const mimeType = blob.type || inferMusicMimeType(track);
    if (mimeType && blob.type !== mimeType) blob = new Blob([blob], { type: mimeType });
    const form = new FormData();
    form.append('file', blob, track.fileName || 'akane_music.audio');
    form.append('user_id', task.identity.user_id);
    form.append('session_id', task.identity.session_id);
    form.append('real_user_id', task.identity.real_user_id);
    form.append('client_mode', CLIENT_MODE);
    form.append('character_pack_id', task.identity.character_pack_id || '');
    const response = await platformPort.backendFetch(task.uploadUrl, {
      method: 'POST', cache: 'no-store', body: form, connectTimeout: 60_000, signal: task.controller.signal
    });
    if (!isCurrentMusicTimeline(task)) return null;
    const payload = await readJsonResponse(response);
    if (!isCurrentMusicTimeline(task)) return null;
    if (!response.ok) throw new Error(extractBackendErrorMessage(payload) || `音频上传失败：HTTP ${response.status}`);
    const attachment = normalizeBackendAttachment(payload?.attachment);
    if (!payload?.ok || !attachment.handle) throw new Error(extractBackendErrorMessage(payload) || '后端没有返回可用音频附件');
    return attachment;
  }

  async function prepareBackendMusicTimeline(track, task) {
    const activity = musicSnapshotPort.buildLocalMusicActivity();
    if (!activity || activity.source_id !== track.sourceId) throw new Error('当前没有可分析的音乐');
    const attachment = track.backendAttachment || {};
    const response = await platformPort.backendFetch(task.prepareUrl, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, cache: 'no-store',
      body: JSON.stringify({ ...task.identity, activity: {
        ...activity, attachment_handle: attachment.handle || activity.attachment_handle || '',
        attachment_id: attachment.attachmentId || activity.attachment_id || '', handle: attachment.handle || activity.handle || 'current'
      } }), connectTimeout: 60_000, signal: task.controller.signal
    });
    if (!isCurrentMusicTimeline(task)) return null;
    const result = await readJsonResponse(response);
    if (!isCurrentMusicTimeline(task)) return null;
    if (!response.ok) throw new Error(extractBackendErrorMessage(result) || `音乐歌词线索准备失败：HTTP ${response.status}`);
    if (!result?.ok) throw new Error(extractBackendErrorMessage(result) || '后端暂时找不到这首音乐');
    return result;
  }

  function applyBackendMusicTimeline(track, result) {
    const timeline = result?.timeline && typeof result.timeline === 'object' ? result.timeline : null;
    if (!timeline) {
      track.timelineStatus = result?.ok ? 'pending' : 'failed';
      track.timelineError = extractBackendErrorMessage(result);
      return;
    }
    const status = String(timeline.status || 'pending').trim().toLowerCase() || 'pending';
    track.timeline = timeline;
    track.timelineStatus = status;
    track.timelineQuality = String(timeline.quality || '').trim();
    track.timelineUpdatedAt = Number(timeline.updated_at || Date.now() / 1000) || 0;
    track.timelineError = extractBackendErrorMessage(timeline) || extractBackendErrorMessage(result);
    const lines = status === 'ready' ? normalizeTimelineLyricSegments(timeline.segments) : [];
    track.timelineLyrics = lines;
    track.timelineLyricLineCount = lines.length || Number(timeline.segment_count || 0) || 0;
  }

  lifecycle.track(clearBackendMusicTimelineTimer);
  return { prepareMusicTimelineTrack, readMusicTimelineTrack, clearBackendMusicTimelineTimer, scheduleBackendMusicTimeline };
}
