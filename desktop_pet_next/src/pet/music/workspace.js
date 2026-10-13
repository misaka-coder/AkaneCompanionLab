import { normalizeMusicTrack, MUSIC_FILE_EXTENSIONS, buildWorkspaceMusicRecommendationFromItem, dedupeMusicRecommendations } from '../../desktop-music-player.js';
import { WORKSPACE_REFRESH_EVENT, PROFILE_USER_ID } from '../constants.js';
import { friendlyErrorMessage, formatError } from '../utils.js';

/** Owns workspace audio preparation and the catalog belonging to its request scope. */
export function createMusicWorkspaceController({
  state, resourceState, platform: platformPort, lifecycle, scope: runtimeScope,
  persistence: statePort, status: statusPort, settings: settingsPort, bubble: bubblePort,
  character: characterPort, backend: backendPort, playback: musicPlaybackPort
}) {
  let preparation = null;
  let refresh = null;
  let catalogScope = null;
  let workspaceMusicRecommendations = [];
  let workspaceAudioCatalog = [];
  let workspaceMusicRecommendationsError = '';
  let workspaceMusicRecommendationsRefreshTimer = 0;

  function buildWorkspaceAudioSourceId(itemType, handle) {
    const normalizedType = itemType === 'generated' ? 'generated' : 'attachment';
    const normalizedHandle = String(handle || '').trim();
    return normalizedHandle ? `workspace:${normalizedType}:${normalizedHandle}` : '';
  }

  function findMusicQueueIndexByWorkspaceAudio(itemType, handle) {
    const sourceId = buildWorkspaceAudioSourceId(itemType, handle);
    if (!sourceId) return -1;
    return musicPlaybackPort.musicQueue.findIndex(track =>
      track?.sourceId === sourceId || track?.queueDedupeKey === sourceId || (
        String(track?.workspaceItemType || '') === (itemType === 'generated' ? 'generated' : 'attachment') &&
        String(track?.workspaceHandle || '') === String(handle || '').trim()
      )
    );
  }

  async function playWorkspaceAudioItem(item) {
    const value = item && typeof item === 'object' ? item : {};
    const itemType = value.itemType === 'generated' ? 'generated' : 'attachment';
    const handle = String(value.handle || '').trim();
    const title = String(value.title || '').trim();
    const sourceId = buildWorkspaceAudioSourceId(itemType, handle);
    if (!handle) {
      bubblePort.showBubbleText('这首没有可播放的编号。', { transient: true, durationMs: 1800, kind: 'music' });
      return false;
    }
    if (preparation?.sourceId === sourceId && musicPlaybackPort.isCurrentMusicSelection(preparation.request)) return preparation.promise;

    const existingIndex = findMusicQueueIndexByWorkspaceAudio(itemType, handle);
    if (existingIndex >= 0) {
      return musicPlaybackPort.playMusicQueueIndex(existingIndex, { message: `播放《${musicPlaybackPort.musicQueue[existingIndex]?.displayName || title}》。` });
    }
    if (!platformPort.isTauriRuntime || lifecycle.disposed) return false;

    const task = { sourceId, request: musicPlaybackPort.beginMusicSelection(), promise: null };
    preparation = task;
    statusPort.updateActivityControls();
    settingsPort.scheduleSettingsSnapshot();
    task.promise = prepareWorkspaceAudioItem({ ...value, itemType, handle, title }, task);
    return task.promise;
  }

  async function prepareWorkspaceAudioItem(item, task) {
    const isCurrent = () => musicPlaybackPort.isCurrentMusicSelection(task.request);
    try {
      statusPort.setRuntimeStatus('正在从手边取音乐', { mode: 'music' });
      const path = await stageWorkspaceItem(item);
      if (!isCurrent()) return false;
      statusPort.setRuntimeStatus('正在准备音乐', { mode: 'music' });
      const asset = await platformPort.invoke('prepare_audio_asset', { path, lyricPath: null });
      if (!isCurrent()) return false;
      const track = normalizeMusicTrack(asset, {
        sourceId: task.sourceId, queueDedupeKey: task.sourceId,
        workspaceItemType: item.itemType, workspaceHandle: item.handle, displayName: item.title
      });
      if (!track.cachedPath) throw new Error('没有可播放的音频文件');
      await musicPlaybackPort.queueAndPlayMusicTrack(track, { request: task.request });
      return isCurrent() && musicPlaybackPort.musicPlaying && musicPlaybackPort.musicTrack?.sourceId === task.sourceId;
    } catch (error) {
      if (!isCurrent()) return false;
      statusPort.setRuntimeStatus(`手边音乐播放失败：${friendlyErrorMessage(formatError(error))}`, { mode: 'error' });
      bubblePort.showBubbleText('这首手边音乐暂时放不了。', { transient: true, durationMs: 2200, kind: 'music' });
      return false;
    } finally {
      if (preparation === task) preparation = null;
      if (isCurrent()) {
        statusPort.updateActivityControls();
        settingsPort.scheduleSettingsSnapshot();
      }
    }
  }

  async function stageWorkspaceItem({ itemType, handle, title = '', format = '' }) {
    const sessionId = String(state.sessionId || '').trim();
    if (!sessionId) throw new Error('会话还没准备好');
    const cleanFormat = String(format || '').trim().replace(/^\.+/, '');
    const cleanTitle = String(title || handle || 'workspace-audio').trim();
    const fileName = cleanFormat && !cleanTitle.toLowerCase().endsWith(`.${cleanFormat.toLowerCase()}`) ? `${cleanTitle}.${cleanFormat}` : cleanTitle;
    const result = await platformPort.tauriCall("open_workspace_item", {
      handle: String(handle || '').trim(), itemType: itemType === 'generated' ? 'generated' : 'attachments',
      action: "stage", backendUrl: state.backendUrl, botId: state.boundBotId,
      userId: sessionId, sessionId, realUserId: statePort.getProfileUserId(), fileName
    }, { quiet: true });
    const localPath = String(result?.local_path || '').trim();
    if (!localPath) throw new Error('手边音频暂存失败');
    return localPath;
  }

  function summarizeWorkspaceImportSkipped(payload) {
    const first = Array.isArray(payload?.skipped) ? payload.skipped[0] : null;
    const reason = String(first?.reason || '').trim();
    if (!reason) return '';
    const labels = {
      unsupported_type: '这个文件类型暂时还不支持', empty_file: '文件是空的', file_too_large: '文件太大了',
      not_found: '没有找到这个路径', file_read_failed: '文件无法读取，可能已被移动或正在被占用',
      upload_failed: '上传失败，请检查后端连接后重试', upload_rejected: '后端没有接受这个文件',
      duplicate_source: '这个音频已经在手边了，没有重复导入', total_too_large: '这批文件总大小超出上限',
      directory_scan_limit: '这个文件夹太大了，先挑具体文件给我', max_files_reached: '一次给的文件有点多，已经达到上限'
    };
    return labels[reason] || '文件暂时不能导入手边';
  }

  async function notifyWorkspaceRefresh() {
    if (!platformPort.isTauriRuntime) return;
    try { await platformPort.emit(WORKSPACE_REFRESH_EVENT, { t: Date.now() }); }
    catch { /* The workspace window may not be open yet. */ }
    scheduleWorkspaceMusicRecommendationsRefresh();
  }

  function isWorkspaceAudioItem(item) {
    if (!item || typeof item !== 'object') return false;
    if (String(item.kind || '').trim().toLowerCase() === 'audio') return true;
    if (String(item.subtitle || '').toLowerCase().includes('音频')) return true;
    const format = String(item.format || '').toLowerCase().replace(/^\.+/, '');
    if (MUSIC_FILE_EXTENSIONS.has(format)) return true;
    const extension = String(item.title || item.name || '').toLowerCase().split('.').pop();
    return Boolean(extension && MUSIC_FILE_EXTENSIONS.has(extension));
  }

  function setWorkspaceMusicRecommendations(recommendations, catalog, requestScope, error = '') {
    if (!runtimeScope.isCurrent(requestScope)) return;
    const changed = JSON.stringify(workspaceMusicRecommendations) !== JSON.stringify(recommendations) ||
      JSON.stringify(workspaceAudioCatalog) !== JSON.stringify(catalog) || !catalogScope || !runtimeScope.isCurrent(catalogScope);
    workspaceMusicRecommendations = recommendations;
    workspaceAudioCatalog = catalog;
    catalogScope = requestScope;
    workspaceMusicRecommendationsError = error;
    if (changed) settingsPort.scheduleSettingsSnapshot();
  }

  async function refreshWorkspaceMusicRecommendations() {
    if (lifecycle.disposed) return;
    if (refresh && runtimeScope.isCurrent(refresh.scope)) { refresh.again = true; return; }
    const task = { scope: runtimeScope.capture() };
    refresh = task;
    try {
      const sessionId = String(state.sessionId || '');
      if (!sessionId || resourceState.health !== 'online') {
        setWorkspaceMusicRecommendations([], [], task.scope);
        return;
      }
      const url = backendPort.buildBackendEndpointUrl('workspaceSummary', '/desktop-pet/workspace/summary', {
        user_id: sessionId, real_user_id: String(state.profileUserId || PROFILE_USER_ID),
        ...characterPort.buildBackendCharacterContext(), limit: 24, t: String(Date.now())
      });
      const response = await platformPort.backendFetch(url, { headers: { Accept: 'application/json' }, cache: 'no-store' });
      if (!runtimeScope.isCurrent(task.scope)) return;
      if (!response.ok) throw new Error(`手边音频目录读取失败：HTTP ${response.status}`);
      const payload = await response.json();
      if (!runtimeScope.isCurrent(task.scope)) return;
      const sections = payload?.sections || payload;
      if (!sections || typeof sections !== 'object') throw new Error('手边音频目录暂时不可用');
      const candidates = [];
      for (const sectionName of ['files', 'outputs']) {
        const items = Array.isArray(sections[sectionName]) ? sections[sectionName] : [];
        for (const item of items) {
          if (isWorkspaceAudioItem(item)) candidates.push(buildWorkspaceMusicRecommendationFromItem(item, sectionName));
        }
      }
      const catalog = dedupeMusicRecommendations(candidates);
      if (!task.again) setWorkspaceMusicRecommendations(catalog.slice(0, 3), catalog.slice(0, 12), task.scope);
    } catch (error) {
      if (!task.again) setWorkspaceMusicRecommendations([], [], task.scope, friendlyErrorMessage(formatError(error)));
    } finally {
      if (refresh === task) {
        refresh = null;
        if (task.again && runtimeScope.isCurrent(task.scope)) scheduleWorkspaceMusicRecommendationsRefresh(0);
      }
    }
  }

  function scheduleWorkspaceMusicRecommendationsRefresh(delay = 300) {
    lifecycle.clearTimeout(workspaceMusicRecommendationsRefreshTimer);
    workspaceMusicRecommendationsRefreshTimer = lifecycle.setTimeout(() => {
      workspaceMusicRecommendationsRefreshTimer = 0;
      void refreshWorkspaceMusicRecommendations();
    }, delay);
  }

  return {
    get musicLoading() { return Boolean(preparation && musicPlaybackPort.isCurrentMusicSelection(preparation.request)); },
    get workspaceAudioCatalog() { return catalogScope && runtimeScope.isCurrent(catalogScope) ? workspaceAudioCatalog : []; },
    get workspaceMusicRecommendations() { return catalogScope && runtimeScope.isCurrent(catalogScope) ? workspaceMusicRecommendations : []; },
    get workspaceMusicRecommendationsError() { return catalogScope && runtimeScope.isCurrent(catalogScope) ? workspaceMusicRecommendationsError : ''; },
    playWorkspaceAudioItem, summarizeWorkspaceImportSkipped, notifyWorkspaceRefresh, scheduleWorkspaceMusicRecommendationsRefresh
  };
}
