// Classification is deliberately conservative: a browser's SMTC "Music"
// label does not establish that its tab is a song.
export function mediaKind(media = {}) {
  const type = String(media.mediaType || media.media_type || "").toLowerCase();
  const app = String(media.sourceApp || media.source_app || "").toLowerCase();
  if (type === "video") return "video";
  if (/chrome|msedge|firefox|brave|vivaldi|browser|opera|openai\.codex/.test(app)) return "browser_media";
  if (type === "music" || /qqmusic|cloudmusic|spotify|applemusic|music\.ui/.test(app)) return "music";
  return "unknown";
}

export function mediaPosition(media, now = Date.now()) {
  const raw = media?.positionSeconds;
  if (raw === null || raw === undefined || !Number.isFinite(Number(raw))) return null;
  const captured = Number(media.capturedAt || 0);
  const elapsed = media.isPlaying && captured > 0 ? Math.max(0, Math.min(5, (now - captured) / 1000)) : 0;
  const rate = Number.isFinite(media.playbackRate) ? media.playbackRate : 1;
  const position = Math.max(0, Number(raw) + elapsed * rate);
  return media.durationSeconds > 0 ? Math.min(position, media.durationSeconds) : position;
}
