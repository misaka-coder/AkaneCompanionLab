import { buildCurrentLyricSnapshot as buildCurrentLyricSnapshotHelper, dedupeMusicRecommendations, buildQueueMusicRecommendationsSnapshot as buildQueueMusicRecommendationsSnapshotHelper, summarizeMusicTrack, simpleHash } from '../../desktop-music-player.js';
import { MEDIA_CONTROL_TARGETS } from '../../media-control.js';
import { mediaKind, mediaPosition } from '../../media-awareness.js';

/** Builds music snapshots from owner interfaces without starting any work. */
export function createMusicSnapshotController({
  els, playback: musicPlaybackPort, workspace: musicWorkspacePort,
  controls: musicControlsPort, systemMedia: systemMediaPort, timeline: musicTimelinePort
}) {
  function buildCurrentLyricSnapshot(timeSeconds = Number(els.musicPlayer?.currentTime || 0)) {
    return buildCurrentLyricSnapshotHelper(musicTimelinePort.readMusicTimelineTrack(musicPlaybackPort.musicTrack), timeSeconds);
  }

  function buildQueueMusicRecommendationsSnapshot(limit = 3) {
    return buildQueueMusicRecommendationsSnapshotHelper(musicPlaybackPort.musicQueue, musicPlaybackPort.musicQueueIndex, Number(els.musicPlayer?.duration || 0), limit);
  }

  function buildMusicRecommendationsSnapshot(limit = 3) {
    return dedupeMusicRecommendations([
      ...buildQueueMusicRecommendationsSnapshot(limit),
      ...musicWorkspacePort.workspaceMusicRecommendations
    ]).slice(0, Math.max(0, limit));
  }

  function buildMusicSnapshot() {
    const previous = musicPlaybackPort.getPreviousMusicTrack();
    const next = musicPlaybackPort.getNextMusicTrack();
    const progress = Number(els.musicPlayer?.currentTime || 0);
    const duration = Number(els.musicPlayer?.duration || 0);
    const currentLyric = buildCurrentLyricSnapshot(progress);
    return {
      track: summarizeMusicTrack(musicTimelinePort.readMusicTimelineTrack(musicPlaybackPort.musicTrack)),
      queue: musicPlaybackPort.musicQueue.map(track => summarizeMusicTrack(musicTimelinePort.readMusicTimelineTrack(track))).filter(Boolean),
      queueIndex: musicPlaybackPort.musicQueueIndex,
      queueCount: musicPlaybackPort.musicQueue.length,
      queueLabel: musicPlaybackPort.getMusicQueueLabel(),
      hasPrevious: musicPlaybackPort.hasPreviousMusicTrack(),
      hasNext: musicPlaybackPort.hasNextMusicTrack(),
      previousDisplayName: previous?.displayName || "",
      nextDisplayName: next?.displayName || "",
      progressSeconds: Number.isFinite(progress) ? Math.max(0, progress) : 0,
      durationSeconds: Number.isFinite(duration) ? Math.max(0, duration) : 0,
      currentLyric,
      playing: musicPlaybackPort.musicPlaying,
      paused: musicPlaybackPort.musicPaused,
      loading: musicWorkspacePort.musicLoading || musicPlaybackPort.musicPlaybackLoading,
      displayName: musicPlaybackPort.getMusicDisplayName(),
      systemMedia: systemMediaPort.summarizeSystemMedia(),
      systemLyrics: systemMediaPort.summarizeSystemMediaLyrics(),
      recommendations: buildMusicRecommendationsSnapshot(),
      control: musicControlsPort.buildActiveMediaControlSnapshot()
    };
  }

  function buildDesktopMusicActivity({ excludePaused = false } = {}) {
    const systemActivity = buildSystemMediaActivity();

    if (shouldUseSystemMediaActivity(systemActivity)) return excludePaused && systemActivity.status !== "running" ? null : systemActivity;
    return buildLocalMusicActivity({ excludePaused });
  }

  function buildLocalMusicActivity({ excludePaused = false } = {}) {
    const track = musicTimelinePort.readMusicTimelineTrack(musicPlaybackPort.musicTrack);
    if (excludePaused && !musicPlaybackPort.musicPlaying) return null;
    if (!track) return null;

    const currentTime = Number(els.musicPlayer?.currentTime || 0);
    const duration = Number(els.musicPlayer?.duration || 0);
    const currentLyric = buildCurrentLyricSnapshot(currentTime);
    const status = musicPlaybackPort.musicPlaying ? "running" : musicPlaybackPort.musicPaused ? "paused" : "stopped";
    const progressSeconds = Number.isFinite(currentTime) ? Math.max(0, currentTime) : 0;
    if (status === "stopped" && progressSeconds <= 0) return null;
    return {
      type: "audio_playback",
      title: musicPlaybackPort.getMusicDisplayName() || "未命名音乐",
      source_id: track.sourceId || "local_music_current",
      handle: "current",
      status,
      progress_seconds: progressSeconds,
      duration_seconds: Number.isFinite(duration) ? Math.max(0, duration) : 0,
      source_kind: "local_file",
      file_name: track.fileName,
      extension: track.extension,
      attachment_handle: track.backendAttachment?.handle || "",
      attachment_id: track.backendAttachment?.attachmentId || "",
      timeline_id: track.timeline?.timeline_id || "",
      timeline_status: track.timelineStatus || "",
      timeline_quality: track.timelineQuality || "",
      queue_count: musicPlaybackPort.musicQueue.length,
      queue_index: musicPlaybackPort.musicQueueIndex >= 0 ? musicPlaybackPort.musicQueueIndex + 1 : 0,
      previous_title: musicPlaybackPort.getPreviousMusicTrack()?.displayName || "",
      next_title: musicPlaybackPort.getNextMusicTrack()?.displayName || "",
      lyric_file_name: currentLyric?.fileName || "",
      lyric_line_count: currentLyric?.lineCount || 0,
      lyric_index: currentLyric?.index ?? -1,
      lyric_current: currentLyric?.text || "",
      lyric_previous: currentLyric?.previousText || "",
      lyric_next: currentLyric?.nextText || ""
    };
  }

  function shouldUseSystemMediaActivity(systemActivity) {
    return Boolean(systemActivity && musicControlsPort.buildActiveMediaControlSnapshot().target === MEDIA_CONTROL_TARGETS.system);
  }

  function buildSystemMediaActivity() {
    if (!systemMediaPort.isFreshSystemMedia(systemMediaPort.systemMedia)) return null;
    const titleParts = [systemMediaPort.systemMedia.title, systemMediaPort.systemMedia.artist].filter(Boolean);
    const title = titleParts.length ? titleParts.join(" - ") : "系统媒体";
    const kind = mediaKind(systemMediaPort.systemMedia);
    const position = mediaPosition(systemMediaPort.systemMedia);
    const currentLyric = kind === "music" ? systemMediaPort.buildSystemMediaLyricSnapshot(position) : null;
    const status = systemMediaPort.systemMedia.isPlaying
      ? "running"
      : systemMediaPort.systemMedia.playbackStatus === "paused"
        ? "paused"
        : "stopped";
    if (status === "stopped") return null;
    return {
      type: kind === "music" ? "audio_playback" : kind === "video" ? "video_playback" : "media_playback",
      media_kind: kind,
      captured_at: Date.now() / 1000,
      source_captured_at: systemMediaPort.systemMedia.capturedAt / 1000,
      timeline_updated_at: systemMediaPort.systemMedia.timelineUpdatedAt / 1000,
      title,
      source_id: `system_media:${systemMediaPort.systemMedia.trackKey || simpleHash(title)}`,
      handle: "system_media_current",
      status,
      progress_seconds: position,
      duration_seconds: systemMediaPort.safePositiveSeconds(systemMediaPort.systemMedia.durationSeconds),
      source_kind: "system_media",
      source_app: systemMediaPort.systemMedia.sourceApp || "",
      artist: systemMediaPort.systemMedia.artist || "",
      album: systemMediaPort.systemMedia.album || "",
      system_media: true,
      playback_status: systemMediaPort.systemMedia.playbackStatus || "unknown",
      ...(kind === "music" ? {
      lyric_file_name: currentLyric?.text ? currentLyric.source || "online" : "",
      lyric_line_count: currentLyric?.text ? currentLyric.lineCount || 0 : 0,
      lyric_index: currentLyric?.text ? currentLyric.index ?? -1 : -1,
      lyric_current: currentLyric?.text || "",
      lyric_previous: currentLyric?.previousText || "",
      lyric_next: currentLyric?.nextText || "",
      lyric_status: currentLyric?.status || systemMediaPort.systemMediaLyrics.status || "unavailable",
      lyric_reason: currentLyric?.reason || systemMediaPort.systemMediaLyrics.reason || "",
      lyric_confidence: currentLyric?.confidence || systemMediaPort.systemMediaLyrics.confidence || "",
      lyric_source: currentLyric?.source || systemMediaPort.systemMediaLyrics.source || ""
      } : {})
    };
  }

  return { buildMusicSnapshot, buildDesktopMusicActivity, buildLocalMusicActivity };
}
