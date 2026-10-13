/**
 * Desktop Music Player & Playlist Utilities.
 * Handles supported formats, LRC lyric parsing, track normalization,
 * playlist queue queries, and workspace recommendations.
 */

export const MUSIC_FILE_EXTENSIONS = new Set(["mp3", "wav", "flac", "ogg", "oga", "m4a", "aac", "opus", "webm"]);

export function simpleHash(value) {
  const text = String(value || "");
  let hash = 0;
  for (let index = 0; index < text.length; index += 1) {
    hash = ((hash << 5) - hash + text.charCodeAt(index)) | 0;
  }
  return Math.abs(hash).toString(36);
}

export function parseLrcText(value) {
  const text = String(value || "").replace(/\r\n?/gu, "\n");
  if (!text.trim()) return [];
  const lines = [];
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();
    if (!line) continue;
    const matches = [...line.matchAll(/\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]/gu)];
    if (!matches.length) continue;
    const lyricText = line.replace(/\[[^\]]+\]/gu, "").trim();
    if (!lyricText) continue;
    for (const match of matches) {
      const minutes = Number(match[1] || 0);
      const seconds = Number(match[2] || 0);
      const fraction = String(match[3] || "");
      const millis = fraction ? Number(fraction.padEnd(3, "0").slice(0, 3)) : 0;
      const timeSeconds = minutes * 60 + seconds + millis / 1000;
      if (Number.isFinite(timeSeconds)) {
        lines.push({ timeSeconds, text: lyricText });
      }
    }
  }
  return lines
    .sort((left, right) => left.timeSeconds - right.timeSeconds)
    .filter((line, index, source) => {
      const previous = source[index - 1];
      return !previous || previous.timeSeconds !== line.timeSeconds || previous.text !== line.text;
    });
}

export function inferMusicMimeType(track) {
  const extension = String(track?.extension || "").toLowerCase();
  return (
    {
      mp3: "audio/mpeg",
      wav: "audio/wav",
      flac: "audio/flac",
      ogg: "audio/ogg",
      oga: "audio/ogg",
      opus: "audio/ogg",
      m4a: "audio/mp4",
      aac: "audio/aac",
      webm: "audio/webm"
    }[extension] || "application/octet-stream"
  );
}

export function formatPlaylistDuration(seconds) {
  const sec = Math.max(0, Math.round(Number(seconds) || 0));
  if (!sec) return "";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function normalizeMusicTrack(asset, metadata) {
  const value = asset && typeof asset === "object" ? asset : {};
  const fileName = String(value.fileName || "audio");
  const cachedPath = String(value.cachedPath || "");
  const lyricFileName = String(value.lyricFileName || "").trim();
  const lyrics = parseLrcText(value.lyricText || "");
  const sizeBytes = Number(value.sizeBytes || value.size_bytes || 0);
  let metaSourceId = "", metaDisplayName = "", metaQueueDedupeKey = "", metaWorkspaceItemType = "", metaWorkspaceHandle = "";
  if (metadata && typeof metadata === "object") {
    metaSourceId = String(metadata.sourceId || "").trim();
    metaDisplayName = String(metadata.displayName || "").trim();
    metaQueueDedupeKey = String(metadata.queueDedupeKey || "").trim();
    metaWorkspaceItemType = metadata.workspaceItemType === "generated" ? "generated" : metadata.workspaceItemType === "attachment" ? "attachment" : "";
    metaWorkspaceHandle = String(metadata.workspaceHandle || "").trim();
  }
  return {
    originalPath: String(value.originalPath || ""),
    cachedPath,
    sourceId: metaSourceId || `local:${fileName}:${simpleHash(cachedPath || fileName)}`,
    queueDedupeKey: metaQueueDedupeKey || metaSourceId || "",
    workspaceItemType: metaWorkspaceItemType || "",
    workspaceHandle: metaWorkspaceHandle || "",
    fileName,
    displayName: metaDisplayName || String(value.displayName || value.fileName || "未命名音乐"),
    extension: String(value.extension || "").toLowerCase(),
    sizeBytes,
    lyricFileName,
    lyricLineCount: lyrics.length,
    lyrics,
    backendAttachment: null,
    timeline: null,
    timelineStatus: lyrics.length ? "skipped_lrc" : "idle",
    timelineQuality: "",
    timelineError: "",
    timelineLyrics: [],
    timelineLyricLineCount: 0,
    timelineUpdatedAt: 0,
    timelineLoading: false
  };
}

export function normalizeBackendAttachment(payload) {
  const value = payload && typeof payload === "object" ? payload : {};
  const handle = String(value.handle || value.attachment_handle || value.source_id || value.attachment_id || "").trim();
  return {
    attachmentId: String(value.attachment_id || value.attachmentId || "").trim(),
    handle,
    sourceId: String(value.source_id || handle).trim(),
    title: String(value.title || value.origin_name || "").trim(),
    url: String(value.url || "").trim(),
    mimeType: String(value.mime_type || value.mimeType || "").trim(),
    sizeBytes: Number(value.size_bytes || value.sizeBytes || 0)
  };
}

export function normalizeTimelineLyricSegments(segments) {
  if (!Array.isArray(segments)) return [];
  return segments
    .map((item) => {
      const timeSeconds = Number(item?.time_seconds ?? item?.timeSeconds);
      const text = String(item?.text || "").trim();
      if (!Number.isFinite(timeSeconds) || !text) return null;
      return {
        timeSeconds: Math.max(0, timeSeconds),
        text
      };
    })
    .filter(Boolean)
    .sort((left, right) => left.timeSeconds - right.timeSeconds);
}

export function summarizeMusicTrack(track) {
  if (!track || typeof track !== "object") return null;
  return {
    sourceId: track.sourceId,
    fileName: track.fileName,
    displayName: track.displayName,
    extension: track.extension,
    hasLyrics: Boolean(track.lyricFileName || track.lyrics?.length || track.timelineLyrics?.length),
    lyricFileName: track.lyricFileName || track.timeline?.transcript_generated_handle || "",
    lyricLineCount: track.lyricLineCount || track.timelineLyricLineCount || 0,
    timelineStatus: track.timelineStatus || "",
    timelineQuality: track.timelineQuality || ""
  };
}

export function buildCurrentLyricSnapshot(track, timeSeconds = 0) {
  const lrcLines = Array.isArray(track?.lyrics) ? track.lyrics : [];
  const timelineLines = Array.isArray(track?.timelineLyrics) ? track.timelineLyrics : [];
  const source = lrcLines.length ? "lrc" : timelineLines.length ? "timeline" : "";
  const lines = source === "lrc" ? lrcLines : timelineLines;
  if (!lines.length) return null;
  let currentIndex = -1;
  const currentTime = Number.isFinite(timeSeconds) ? Math.max(0, timeSeconds) : 0;
  for (let index = 0; index < lines.length; index += 1) {
    if (lines[index].timeSeconds <= currentTime + 0.12) currentIndex = index;
    else break;
  }
  const current = currentIndex >= 0 ? lines[currentIndex] : null;
  const previous = currentIndex > 0 ? lines[currentIndex - 1] : null;
  const next = lines[Math.max(0, currentIndex + 1)] || null;
  return {
    source,
    fileName: source === "lrc" ? track.lyricFileName || "" : track.timeline?.transcript_generated_handle || "",
    lineCount: lines.length,
    index: currentIndex,
    timeSeconds: current?.timeSeconds ?? 0,
    text: current?.text || "",
    previousText: previous?.text || "",
    nextText: next?.text || ""
  };
}

export function hasPreviousMusicTrack(queue, queueIndex) {
  return queueIndex > 0 && queueIndex < queue.length;
}

export function hasNextMusicTrack(queue, queueIndex) {
  return queueIndex >= 0 && queueIndex < queue.length - 1;
}

export function getPreviousMusicTrack(queue, queueIndex) {
  return hasPreviousMusicTrack(queue, queueIndex) ? queue[queueIndex - 1] : null;
}

export function getNextMusicTrack(queue, queueIndex) {
  return hasNextMusicTrack(queue, queueIndex) ? queue[queueIndex + 1] : null;
}

export function getMusicQueueLabel(queue, queueIndex) {
  if (!queue.length || queueIndex < 0) return "";
  return queue.length > 1 ? `${queueIndex + 1}/${queue.length}` : "";
}

export function findMusicTrackIndexBySourceId(queue, sourceId) {
  const normalized = String(sourceId || "").trim();
  if (!normalized) return -1;
  return queue.findIndex((track) =>
    track?.sourceId === normalized || track?.queueDedupeKey === normalized
  );
}

export function getSafeMusicQueueIndex(queue, queueIndex) {
  if (!queue.length) return -1;
  if (queueIndex >= 0 && queueIndex < queue.length) return queueIndex;
  return 0;
}

export function dedupeMusicRecommendations(items) {
  const seenHandles = new Set();
  const seenSoftKeys = new Set();
  return items.filter((item) => {
    const itemType = String(item.itemType || "").trim().toLowerCase();
    const handle = String(item.handle || "").trim();
    const handleKey = handle ? `${itemType}:${handle}` : "";
    if (handleKey && seenHandles.has(handleKey)) return false;
    const title = String(item.title || "").trim().toLowerCase();
    const format = String(item.format || "").trim().toLowerCase().replace(/^\.+/, "");
    const sizeBytes = Number(item.sizeBytes || item.size_bytes || 0);
    const hasSoftKey = Boolean(title) && Number.isFinite(sizeBytes) && sizeBytes > 0;
    const softKey = hasSoftKey ? `${title}|${format}|${sizeBytes}` : "";
    if (softKey && seenSoftKeys.has(softKey)) return false;
    if (handleKey) seenHandles.add(handleKey);
    if (softKey) seenSoftKeys.add(softKey);
    return true;
  });
}

export function buildWorkspaceMusicRecommendationFromItem(item, section) {
  const handle = String(item.handle || item.id || "").trim();
  const title = String(item.title || item.name || item.fileName || "").trim();
  const format = String(item.format || "").trim();
  const prefix = section === "outputs" ? "workspace:generated" : "workspace:attachment";
  const durationSeconds = Number(item.durationSeconds || item.duration_seconds || 0);
  return {
    id: handle ? `${prefix}:${handle}` : `${section}_${title}_${format}`,
    sourceId: handle ? `${prefix}:${handle}` : "",
    itemType: section === "outputs" ? "generated" : "attachment",
    handle,
    title,
    format,
    sizeBytes: Number(item.sizeBytes || item.size_bytes || 0),
    durationSeconds: Number.isFinite(durationSeconds) ? Math.max(0, durationSeconds) : 0,
    durationLabel: formatPlaylistDuration(durationSeconds),
    reason: section === "outputs" ? "生成音频" : "手边音频",
    playable: true
  };
}

export function buildQueueMusicRecommendationsSnapshot(queue, queueIndex, currentDuration = 0, limit = 3) {
  if (!queue.length) return [];
  if (queue.length === 1) {
    const current = queue[0];
    const durationSeconds = Number(currentDuration || 0);
    return [{
      id: current.sourceId || "current",
      sourceId: String(current.sourceId || ""),
      title: String(current.displayName || current.fileName || ""),
      artist: "",
      durationSeconds: Number.isFinite(durationSeconds) ? Math.max(0, durationSeconds) : 0,
      durationLabel: formatPlaylistDuration(durationSeconds),
      reason: "当前播放",
      playable: true
    }];
  }
  const recommendations = [];
  const maxIterations = Math.min(queue.length - 1, limit);
  for (let offset = 1; offset <= maxIterations && recommendations.length < limit; offset += 1) {
    const index = (queueIndex + offset) % queue.length;
    const track = queue[index];
    if (!track) continue;
    recommendations.push({
      id: track.sourceId || `rec_${index}`,
      sourceId: String(track.sourceId || ""),
      title: String(track.displayName || track.fileName || ""),
      artist: "",
      durationSeconds: 0,
      durationLabel: "",
      reason: offset === 1 ? "下一首" : "队列中",
      playable: true
    });
  }
  return recommendations;
}
