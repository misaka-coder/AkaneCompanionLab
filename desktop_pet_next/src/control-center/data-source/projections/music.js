import { stringValue } from "../shared.js";
import { coerceVolumePercent } from "../shared.js";

export function buildMusicRuntimePatch({ musicSnapshot, petState }) {
  if (!musicSnapshot || typeof musicSnapshot !== "object") return null;

  const track = musicSnapshot.track;
  const localDisplayName = stringValue(track?.displayName || musicSnapshot.displayName);
  const systemMedia = normalizeSystemMediaRuntime(musicSnapshot.systemMedia, musicSnapshot.systemLyrics);
  const control = musicSnapshot.control && typeof musicSnapshot.control === "object" ? musicSnapshot.control : {};
  const controlTarget = stringValue(control.target).toLowerCase();
  const hasExplicitControlTarget = controlTarget === "local" || controlTarget === "system" || controlTarget === "none";
  const legacySystemTrack = systemMedia.ready
    && !Boolean(musicSnapshot.playing)
    && !Boolean(musicSnapshot.paused)
    && !Boolean(localDisplayName)
    && !Number(musicSnapshot.queueCount);
  const hasSystemTrack = hasExplicitControlTarget ? controlTarget === "system" && systemMedia.ready : legacySystemTrack;
  const hasTrack = hasExplicitControlTarget ? controlTarget === "local" && Boolean(localDisplayName) : Boolean(localDisplayName) && !hasSystemTrack;
  const displayName = hasTrack ? localDisplayName : hasSystemTrack ? systemMedia.title : "";
  const queue = Array.isArray(musicSnapshot.queue) ? musicSnapshot.queue : [];
  const rawQueueIndex = Number(musicSnapshot.queueIndex);
  const queueIndex = Number.isInteger(rawQueueIndex) ? rawQueueIndex : -1;
  const activeQueueIndex = queueIndex >= 0 && queueIndex < queue.length ? queueIndex : -1;
  const progressSec = hasSystemTrack
    ? systemMedia.positionSeconds
    : Number.isFinite(musicSnapshot.progressSeconds) ? Math.max(0, musicSnapshot.progressSeconds) : 0;
  const durationSec = hasSystemTrack
    ? systemMedia.durationSeconds
    : Number.isFinite(musicSnapshot.durationSeconds) ? Math.max(0, musicSnapshot.durationSeconds) : 0;
  const progress = durationSec > 0 ? Math.min(100, Math.round((progressSec / durationSec) * 100)) : 0;
  const volume = coerceVolumePercent(petState?.voiceVolume, 68);
  const currentLyric = musicSnapshot.currentLyric && typeof musicSnapshot.currentLyric === "object"
    ? musicSnapshot.currentLyric
    : null;
  const hasLyrics = Boolean(currentLyric && currentLyric.lineCount > 0);
  const hasSystemLyrics = hasSystemTrack && Boolean(systemMedia.lyrics.current || systemMedia.lyrics.previous || systemMedia.lyrics.next);

  const nowPlaying = {
    title: hasTrack ? displayName : "暂无播放",
    artist: hasTrack ? "" : "",
    quality: hasTrack ? (track?.timelineQuality || track?.extension || "本地文件") : "",
    elapsed: formatSeconds(progressSec),
    duration: formatSeconds(durationSec),
    progressSeconds: progressSec,
    durationSeconds: durationSec,
    progress,
    volume,
    playing: Boolean(musicSnapshot.playing),
    paused: Boolean(musicSnapshot.paused),
    cover: "music"
  };
  if (hasSystemTrack) {
    nowPlaying.title = systemMedia.title;
    nowPlaying.artist = systemMedia.artist;
    nowPlaying.quality = "系统媒体";
    nowPlaying.playing = systemMedia.isPlaying;
    nowPlaying.paused = systemMedia.playbackStatus === "paused";
  }

  const playlist = hasTrack
    ? queue.map((item, index) => ({
        id: item.sourceId || item.id || item.fileName || item.displayName || `queue_${index + 1}`,
        sourceId: item.sourceId || item.id || "",
        title: item.displayName || item.fileName || "未命名曲目",
        artist: "",
        duration: "",
        active: index === activeQueueIndex
      }))
    : [];

  let lyrics = [];
  let activeLyric = -1;
  if (hasLyrics && currentLyric.text) {
    const lines = [];
    if (currentLyric.previousText) lines.push(currentLyric.previousText);
    lines.push(currentLyric.text);
    if (currentLyric.nextText) lines.push(currentLyric.nextText);
    lyrics = lines;
    activeLyric = currentLyric.previousText ? 1 : 0;
  } else if (hasSystemLyrics) {
    const lines = [];
    if (systemMedia.lyrics.previous) lines.push(systemMedia.lyrics.previous);
    const currentIndex = systemMedia.lyrics.current ? lines.length : -1;
    if (systemMedia.lyrics.current) lines.push(systemMedia.lyrics.current);
    if (systemMedia.lyrics.next) lines.push(systemMedia.lyrics.next);
    lyrics = lines;
    activeLyric = currentIndex;
  } else {
    lyrics = [hasSystemTrack ? "当前系统音乐暂无可用歌词" : "当前音乐暂无歌词"];
    activeLyric = -1;
  }

  const info = hasSystemTrack
    ? [
        { label: "系统媒体", value: systemMedia.statusLabel },
        { label: "歌词", value: systemMedia.lyrics.statusDetail || systemMedia.lyrics.statusLabel },
        { label: "来源", value: systemMedia.sourceApp || "-" }
      ]
    : hasTrack
    ? [
        { label: "时长", value: formatSeconds(durationSec) },
        { label: "来源", value: "本地音乐" },
        { label: "音质", value: track?.timelineQuality || track?.extension || "未知" }
      ]
    : [
        { label: "时长", value: "-" },
        { label: "来源", value: "-" },
        { label: "音质", value: "-" }
      ];

  const bottomStatus = hasSystemTrack
    ? `System music: ${systemMedia.statusLabel} · Lyrics: ${systemMedia.lyrics.statusDetail || systemMedia.lyrics.statusLabel}`
    : hasTrack
    ? `${musicSnapshot.playing ? "正在播放" : musicSnapshot.paused ? "已暂停" : "已停止"} · ${queue.length > 0 && activeQueueIndex >= 0 ? `${activeQueueIndex + 1}/${queue.length}` : "单曲"}`
    : "暂无播放 · 等待音乐加入队列";

  const petPlayMode = String(petState?.musicPlayMode ?? "").trim();
  const petVolumeNormalization = typeof petState?.musicVolumeNormalization === "boolean" ? petState.musicVolumeNormalization : undefined;

  const recommendations = Array.isArray(musicSnapshot?.recommendations)
    ? musicSnapshot.recommendations.map((item) => {
        const itemType = item.itemType === "generated" ? "generated" : "attachment";
        return {
          id: item.id || item.sourceId || "",
          sourceId: String(item.sourceId || item.source_id || "").trim(),
          itemType,
          handle: String(item.handle || "").trim(),
          title: String(item.title || "").trim(),
          artist: String(item.artist || "").trim(),
          duration: item.durationLabel || item.duration || "",
          durationLabel: item.durationLabel || "",
          durationSeconds: Number(item.durationSeconds || 0),
          reason: String(item.reason || "").trim(),
          playable: item.playable !== false
        };
      })
    : [];

  return {
    nowPlaying, playlist, lyrics, activeLyric, info, bottomStatus,
    systemMedia,
    control: {
      target: controlTarget || (hasSystemTrack ? "system" : hasTrack ? "local" : "none"),
      targetId: stringValue(control.targetId || control.target_id),
      playbackStatus: stringValue(control.playbackStatus || control.playback_status || (nowPlaying.playing ? "playing" : nowPlaying.paused ? "paused" : "stopped")),
      isPlaying: Boolean(control.isPlaying ?? control.is_playing ?? nowPlaying.playing),
      available: Boolean(control.available ?? (hasTrack || hasSystemTrack))
    },
    ...(petPlayMode ? { currentPlayMode: petPlayMode } : {}),
    ...(typeof petVolumeNormalization === "boolean" ? { volumeNormalization: petVolumeNormalization } : {}),
    recommendations,
  };
}

function normalizeSystemMediaRuntime(systemMediaSnapshot, systemLyricsSnapshot) {
  const media = systemMediaSnapshot && typeof systemMediaSnapshot === "object" ? systemMediaSnapshot : {};
  const lyric = systemLyricsSnapshot && typeof systemLyricsSnapshot === "object" ? systemLyricsSnapshot : {};
  const title = stringValue(media.title);
  const artist = stringValue(media.artist);
  const sourceApp = stringValue(media.sourceApp || media.source_app);
  const status = stringValue(media.status || "unavailable").toLowerCase();
  const playbackStatus = stringValue(media.playbackStatus || media.playback_status || "unknown").toLowerCase();
  const positionSeconds = Number.isFinite(media.positionSeconds) ? Math.max(0, media.positionSeconds) : 0;
  const durationSeconds = Number.isFinite(media.durationSeconds) ? Math.max(0, media.durationSeconds) : 0;
  const lyricStatus = stringValue(lyric.status || "unavailable").toLowerCase();
  const lyricReason = stringValue(lyric.reason);
  const lyricFound = lyricStatus === "ready" && Boolean(lyric.current || lyric.previous || lyric.next);
  const lyricStatusLabel = lyricFound ? "Found" : lyricsStatusLabel(lyricStatus, lyricReason);
  const lyricReasonLabel = lyricsReasonLabel(lyricReason);
  return {
    ready: Boolean(media.ok && (media.controllable || media.fresh) && (title || artist) && status === "ready"),
    status,
    statusLabel: systemMediaStatusLabel(status),
    title: title && artist ? `${title} - ${artist}` : title || artist || "系统正在播放的音乐",
    artist,
    sourceApp: shortSystemMediaSource(sourceApp),
    playbackStatus,
    isPlaying: Boolean(media.isPlaying || playbackStatus === "playing"),
    positionSeconds,
    durationSeconds,
    lyrics: {
      status: lyricStatus,
      statusLabel: lyricStatusLabel,
      statusDetail: lyricReasonLabel ? `${lyricStatusLabel} · ${lyricReasonLabel}` : lyricStatusLabel,
      reason: lyricReason,
      reasonLabel: lyricReasonLabel,
      source: stringValue(lyric.source),
      confidence: stringValue(lyric.confidence),
      lineCount: Number(lyric.lineCount || 0),
      current: stringValue(lyric.current),
      previous: stringValue(lyric.previous),
      next: stringValue(lyric.next)
    }
  };
}

function systemMediaStatusLabel(status) {
  return {
    ready: "Ready",
    empty: "Empty",
    unavailable: "Unavailable"
  }[status] || "Unavailable";
}

function lyricsStatusLabel(status, reason = "") {
  if (status === "pending" && reason === "lyrics_lookup_slow") return "Checking";
  if (status === "unavailable" && reason === "backend_offline") return "Unavailable";
  return {
    ready: "Found",
    "not-found": "Not found",
    disabled: "Disabled",
    unavailable: "Unavailable",
    pending: "Checking",
    "low-confidence": "Unavailable"
  }[status] || "Unavailable";
}

function lyricsReasonLabel(reason) {
  return {
    lyrics_lookup_pending: "pending",
    lyrics_lookup_slow: "provider slow",
    lyrics_request_failed: "request failed",
    lyrics_provider_failed: "provider failed",
    backend_offline: "backend offline",
    syncedlyrics_missing: "dependency missing",
    network_lyrics_disabled: "disabled",
    lyrics_not_found: "not found",
    ambiguous_track_metadata: "low confidence",
    insufficient_synced_lines: "low confidence"
  }[reason] || "";
}

function shortSystemMediaSource(value) {
  const text = stringValue(value).replace(/\.(exe|app)$/i, "");
  if (!text) return "";
  const parts = text.split(/[.!\\/:]+/).filter(Boolean);
  return parts.length ? parts[parts.length - 1].slice(0, 40) : text.slice(0, 40);
}

function formatSeconds(seconds) {
  const sec = Math.max(0, Math.round(Number(seconds) || 0));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
