import { createMusicWorkspaceController } from './music/workspace.js';
import { createMusicPlaybackController } from './music/playback.js';
import { createMusicControlsController } from './music/controls.js';
import { createMusicEmotionController } from './music/emotion.js';
import { createMusicSnapshotController } from './music/snapshot.js';
import { createMusicTimelineController } from './music/timeline.js';

/** Composes music owners and preserves the main-window music interface. */
export function createMusicController({
  state,
  resourceState,
  visualRenderer,
  els,
  platform: platformPort,
  lifecycle,
  scope: runtimeScope,
  persistence: statePort,
  status: statusPort,
  settings: settingsPort,
  bubble: bubblePort,
  systemMedia: systemMediaPort,
  character: characterPort,
  chat: chatPort,
  tts: ttsPort,
  voice: voicePort,
  window: windowPort,
  interaction: interactionPort,
  backend: backendPort,
  care: carePort
}) {
  const musicPlaybackPort = {};
  const musicTimelinePort = {};
  const musicEmotionPort = createMusicEmotionController({
    state, els, lifecycle, systemMedia: systemMediaPort, character: characterPort,
    chat: chatPort, tts: ttsPort, voice: voicePort, window: windowPort, interaction: interactionPort,
    bubble: bubblePort, care: carePort, visualRenderer
  });
  Object.defineProperties(musicPlaybackPort, Object.getOwnPropertyDescriptors(createMusicPlaybackController({
    state, els, platform: platformPort, lifecycle, scope: runtimeScope,
    status: statusPort, settings: settingsPort, bubble: bubblePort,
    emotion: musicEmotionPort, timeline: musicTimelinePort
  })));
  const musicControlsPort = createMusicControlsController({
    playback: musicPlaybackPort, systemMedia: systemMediaPort, status: statusPort,
    settings: settingsPort, bubble: bubblePort
  });
  const musicWorkspacePort = createMusicWorkspaceController({
    state, resourceState, platform: platformPort, lifecycle, scope: runtimeScope,
    persistence: statePort, status: statusPort, settings: settingsPort, bubble: bubblePort,
    character: characterPort, backend: backendPort, playback: musicPlaybackPort
  });
  const musicSnapshotPort = createMusicSnapshotController({
    els, playback: musicPlaybackPort, workspace: musicWorkspacePort,
    controls: musicControlsPort, systemMedia: systemMediaPort, timeline: musicTimelinePort
  });
  Object.defineProperties(musicTimelinePort, Object.getOwnPropertyDescriptors(createMusicTimelineController({
    state, platform: platformPort, lifecycle, scope: runtimeScope, persistence: statePort,
    status: statusPort, settings: settingsPort, character: characterPort, backend: backendPort,
    playback: musicPlaybackPort, snapshot: musicSnapshotPort
  })));

  function setMusicPlayMode(value) {
    state.musicPlayMode = statePort.normalizeMusicPlayMode(value);
    statePort.scheduleSave(0);
    statusPort.setRuntimeStatus(`播放模式：${state.musicPlayMode}`, { mode: musicPlaybackPort.musicPlaying ? "music" : "idle" });
    settingsPort.scheduleSettingsSnapshot();
  }

  function setMusicVolumeNormalization(value) {
    state.musicVolumeNormalization = statePort.normalizeBooleanSetting(value, statePort.DEFAULT_STATE.musicVolumeNormalization);
    statePort.scheduleSave(0);
    statusPort.setRuntimeStatus(state.musicVolumeNormalization ? "音量均衡已开启" : "音量均衡已关闭", {
      mode: musicPlaybackPort.musicPlaying ? "music" : "idle"
    });
    settingsPort.scheduleSettingsSnapshot();
  }

  return {
    pauseMusic: musicPlaybackPort.pauseMusic,
    get musicTrack() { return musicPlaybackPort.musicTrack; },
    get musicQueue() { return musicPlaybackPort.musicQueue; },
    get musicQueueIndex() { return musicPlaybackPort.musicQueueIndex; },
    get musicPlaying() { return musicPlaybackPort.musicPlaying; },
    get musicPaused() { return musicPlaybackPort.musicPaused; },
    get musicLoading() { return musicWorkspacePort.musicLoading || musicPlaybackPort.musicPlaybackLoading; },
    get musicEmotionRestoreTimer() { return musicEmotionPort.musicEmotionRestoreTimer; },
    get workspaceAudioCatalog() { return musicWorkspacePort.workspaceAudioCatalog; },
    setMusicPlayMode,
    setMusicVolumeNormalization,
    playWorkspaceAudioItem: musicWorkspacePort.playWorkspaceAudioItem,
    summarizeWorkspaceImportSkipped: musicWorkspacePort.summarizeWorkspaceImportSkipped,
    notifyWorkspaceRefresh: musicWorkspacePort.notifyWorkspaceRefresh,
    playMusicQueueIndex: musicPlaybackPort.playMusicQueueIndex,
    playNextMusicTrack: musicPlaybackPort.playNextMusicTrack,
    playPreviousMusicTrack: musicPlaybackPort.playPreviousMusicTrack,
    playMusicTrackBySourceId: musicPlaybackPort.playMusicTrackBySourceId,
    removeMusicTrackBySourceId: musicPlaybackPort.removeMusicTrackBySourceId,
    handleMusicEnded: musicPlaybackPort.handleMusicEnded,
    handleMusicPlaybackError: musicPlaybackPort.handleMusicPlaybackError,
    toggleMusicPlayback: musicPlaybackPort.toggleMusicPlayback,
    buildActiveMediaControlSnapshot: musicControlsPort.buildActiveMediaControlSnapshot,
    executeActiveMusicCommand: musicControlsPort.executeActiveMusicCommand,
    seekMusicPlayback: musicPlaybackPort.seekMusicPlayback,
    stopMusic: musicPlaybackPort.stopMusic,
    clearMusicQueue: musicPlaybackPort.clearMusicQueue,
    setMusicEmotion: musicEmotionPort.setMusicEmotion,
    scheduleMusicEmotionRestore: musicEmotionPort.scheduleMusicEmotionRestore,
    setRestingPetEmotion: musicEmotionPort.setRestingPetEmotion,
    handleRestingEmotionLoadError: musicEmotionPort.handleRestingEmotionLoadError,
    canApplyRestingEmotionNow: musicEmotionPort.canApplyRestingEmotionNow,
    resetRestingEmotionFailures: musicEmotionPort.resetRestingEmotionFailures,
    getMusicDisplayName: musicPlaybackPort.getMusicDisplayName,
    hasPreviousMusicTrack: musicPlaybackPort.hasPreviousMusicTrack,
    hasNextMusicTrack: musicPlaybackPort.hasNextMusicTrack,
    findMusicTrackIndexBySourceId: musicPlaybackPort.findMusicTrackIndexBySourceId,
    getSafeMusicQueueIndex: musicPlaybackPort.getSafeMusicQueueIndex,
    notifyMusicActivityUnavailable: musicControlsPort.notifyMusicActivityUnavailable,
    scheduleWorkspaceMusicRecommendationsRefresh: musicWorkspacePort.scheduleWorkspaceMusicRecommendationsRefresh,
    buildMusicSnapshot: musicSnapshotPort.buildMusicSnapshot,
    buildDesktopMusicActivity: musicSnapshotPort.buildDesktopMusicActivity
  };
}
