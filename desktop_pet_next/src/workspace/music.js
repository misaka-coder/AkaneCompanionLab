export function createWorkspaceMusicView({ document, els, buildText }) {
  function renderMusicPanel(music) {
    const payload = music && typeof music === "object" ? music : {};
    const queue = Array.isArray(payload.queue) ? payload.queue : [];
    const current = payload.track && typeof payload.track === "object" ? payload.track : null;
    const hasTrack = Boolean(current || payload.displayName);
    const name = String(payload.displayName || current?.displayName || current?.fileName || "").trim();
    const queueCount = Number(payload.queueCount || queue.length || 0);
    const queueIndex = Number(payload.queueIndex ?? -1);
    const queueLabel = queueCount > 1 && queueIndex >= 0 ? `${queueIndex + 1}/${queueCount}` : "";
    const progressLabel = formatMusicProgress(payload.progressSeconds, payload.durationSeconds);

    els.music.replaceChildren();
    const header = document.createElement("header");
    header.className = "music-panel-header";
    const title = document.createElement("div");
    title.append(buildText("h2", "手边音乐"));
    const status = document.createElement("p");
    if (payload.loading) {
      status.textContent = "正在准备音乐……";
    } else if (payload.playing) {
      status.textContent = `正在播放：${name || "未命名音乐"}${queueLabel ? ` · ${queueLabel}` : ""}${progressLabel ? ` · ${progressLabel}` : ""}`;
    } else if (payload.paused) {
      status.textContent = `已暂停：${name || "未命名音乐"}${queueLabel ? ` · ${queueLabel}` : ""}${progressLabel ? ` · ${progressLabel}` : ""}`;
    } else {
      status.textContent = hasTrack ? `已停止：${name || "未命名音乐"}` : "把本地音乐拖到桌宠身上，就会放到这里。";
    }
    title.append(status);
    const lyricLine = buildMusicLyricText(payload, hasTrack);
    if (lyricLine) {
      const lyric = document.createElement("p");
      lyric.className = "music-panel-lyric";
      lyric.textContent = lyricLine;
      title.append(lyric);
    }

    const controls = document.createElement("div");
    controls.className = "music-panel-actions";
    controls.append(
      buildMusicButton("上一首", "previous", Boolean(payload.loading) || !payload.hasPrevious),
      buildMusicButton(payload.playing ? "暂停" : hasTrack ? "继续" : "播放", "toggle", Boolean(payload.loading) || !hasTrack),
      buildMusicButton("下一首", "next", Boolean(payload.loading) || !payload.hasNext),
      buildMusicButton("停止", "stop", Boolean(payload.loading) || !hasTrack)
    );
    header.append(title, controls);
    els.music.append(header);

    const list = document.createElement("div");
    list.className = "music-panel-list";
    if (!queue.length) {
      const empty = document.createElement("div");
      empty.className = "music-panel-empty";
      empty.textContent = "当前没有音乐队列。";
      list.append(empty);
    } else {
      const currentId = String(current?.sourceId || "").trim();
      for (const [index, track] of queue.slice(0, 8).entries()) {
        list.append(renderMusicQueueItem(track, index, currentId));
      }
    }
    els.music.append(list);
  }

  function renderMusicQueueItem(track, index, currentId) {
    const sourceId = String(track?.sourceId || "").trim();
    const row = document.createElement("article");
    row.className = "music-panel-item";
    if (sourceId) row.dataset.sourceId = sourceId;
    const isCurrent = sourceId && sourceId === currentId;
    if (isCurrent) row.classList.add("is-current");

    const mark = document.createElement("span");
    mark.className = "music-panel-index";
    mark.textContent = String(index + 1);

    const body = document.createElement("div");
    body.className = "music-panel-body";
    const title = document.createElement("strong");
    title.textContent = String(track?.displayName || track?.fileName || "未命名音乐");
    const meta = document.createElement("span");
    meta.textContent = [
      isCurrent ? "当前" : "",
      track?.extension ? String(track.extension).toUpperCase() : "",
      track?.lyricLineCount ? `LRC ${track.lyricLineCount} 行` : "",
      track?.timelineLyricLineCount ? `后端 ${track.timelineLyricLineCount} 行` : "",
      track?.timelineLoading || ["uploading", "pending", "processing"].includes(String(track?.timelineStatus || "")) ? "准备歌词中" : ""
    ]
      .filter(Boolean)
      .join(" · ");
    body.append(title, meta);

    const actions = document.createElement("div");
    actions.className = "music-panel-item-actions";
    actions.append(
      buildMusicButton(isCurrent ? "当前" : "播放", "play", isCurrent || !sourceId),
      buildMusicButton("移除", "remove", !sourceId)
    );

    row.append(mark, body, actions);
    return row;
  }

  function buildMusicButton(label, action, disabled = false) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.musicAction = action;
    button.textContent = label;
    button.disabled = Boolean(disabled);
    return button;
  }

  function buildMusicLyricText(payload, hasTrack) {
    const lyric = payload.currentLyric && typeof payload.currentLyric === "object" ? payload.currentLyric : {};
    const current = String(lyric.text || "").trim();
    const next = String(lyric.nextText || "").trim();
    const track = payload.track && typeof payload.track === "object" ? payload.track : {};
    const lineCount = Number(track.lyricLineCount || lyric.lineCount || track.timelineLyricLineCount || 0);
    const timelineStatus = String(track.timelineStatus || "").trim();
    if (current) return `歌词：${current}`;
    if (next && hasTrack) return `下一句：${next}`;
    if (hasTrack && lineCount > 0) return `已载入歌词 ${lineCount} 行。`;
    if (hasTrack && (track.timelineLoading || ["uploading", "pending", "processing"].includes(timelineStatus))) return "正在准备后端歌词线索……";
    return "";
  }

  function formatMusicProgress(progressValue, durationValue) {
    const progress = Number(progressValue || 0);
    const duration = Number(durationValue || 0);
    if (!Number.isFinite(progress) || progress <= 0) return "";
    const current = formatDuration(progress);
    if (!Number.isFinite(duration) || duration <= 0) return current;
    return `${current}/${formatDuration(duration)}`;
  }

  function formatDuration(value) {
    const total = Math.max(0, Math.floor(Number(value || 0)));
    const minutes = Math.floor(total / 60);
    const seconds = total % 60;
    return `${minutes}:${String(seconds).padStart(2, "0")}`;
  }

  return renderMusicPanel;
}
