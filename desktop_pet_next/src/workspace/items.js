import { kindMark, formatSize, formatUpdatedAt } from "./view.js";

export function createWorkspaceItemsView({ document, els, buildText }, { isLoading = () => false } = {}) {
  const itemMap = new Map();
  let busy = false;

  function renderPayload(payload) {
    itemMap.clear();
    const sections = payload.sections && typeof payload.sections === "object" ? payload.sections : {};
    const files = normalizeItems(sections.files);
    const outputs = normalizeItems(sections.outputs);

    els.fileCount.textContent = String(files.length);
    els.outputCount.textContent = String(outputs.length);
    els.summary.textContent = `${files.length} 文件 · ${outputs.length} 成果`;
    updateWorkspaceActions({ files, outputs });

    if (!files.length && !outputs.length) {
      renderEmpty("现在手边还很清爽，没有文件或成果。");
      return;
    }

    els.content.replaceChildren(
      ...[
        renderSection("手边文件", "刚递给当前角色的原始材料", files, "file"),
        renderSection("做好的东西", "文档、音频和其他生成物", outputs, "output")
      ].filter(Boolean)
    );
    setBusy(busy);
  }

  function renderSection(title, subtitle, items, kind) {
    if (!items.length) return null;
    const section = document.createElement("section");
    section.className = "workspace-section";

    const header = document.createElement("header");
    header.className = "section-header";
    header.append(buildText("h2", title), buildText("p", subtitle));

    const grid = document.createElement("div");
    grid.className = "item-grid";
    for (const item of items) {
      grid.append(renderItem(item, kind));
    }

    section.append(header, grid);
    return section;
  }

  function renderItem(item, kind) {
    const identifier = itemKey(item);
    const key = identifier ? `${kind}:${identifier}` : "";
    if (key) itemMap.set(key, item);

    const article = document.createElement("article");
    article.className = "workspace-item";
    const statusGroup = resolveItemStatusGroup(item);
    article.dataset.statusGroup = statusGroup;
    if (key) article.dataset.key = key;

    const mark = document.createElement("div");
    mark.className = "item-mark";
    mark.textContent = kind === "output" ? "✦" : kindMark(item.kind || item.format);

    const body = document.createElement("div");
    body.className = "item-body";

    const top = document.createElement("div");
    top.className = "item-topline";
    const statusBadge = buildText("span", item.status_label || item.status || "已放好");
    statusBadge.className = "item-status";
    statusBadge.dataset.statusGroup = statusGroup;
    top.append(buildText("h3", item.title || item.handle || "未命名"), statusBadge);

    const meta = buildItemMetaParts(item).filter(Boolean).join(" · ");
    const detail = buildText("p", meta || "放在当前角色手边");

    const actions = document.createElement("div");
    actions.className = "item-actions";
    if (item.can_open) {
      actions.append(
        buildItemActionButton("open-file", "打开"),
        buildItemActionButton("reveal-file", "位置"),
        buildItemActionButton("export-desktop", "存桌面"),
        buildItemActionButton("copy-path", "复制路径")
      );
    }
    if (isPlayableAudioItem(item)) {
      const play = document.createElement("button");
      play.type = "button";
      play.dataset.action = "play-audio";
      play.textContent = "播放";
      actions.append(play);
    }
    const copy = document.createElement("button");
    copy.type = "button";
    copy.dataset.action = "copy-id";
    copy.textContent = "复制编号";
    actions.append(copy);
    if (item.can_clear) {
      const clear = document.createElement("button");
      clear.type = "button";
      clear.dataset.action = "clear-item";
      clear.className = "danger-button";
      clear.textContent = "收起";
      actions.append(clear);
    }

    body.append(top, detail);
    body.append(actions);
    article.append(mark, body);
    return article;
  }

  function buildItemActionButton(action, label) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.action = action;
    button.textContent = label;
    return button;
  }

  function resolveItemStatusGroup(item) {
    return String(item?.status || "").trim().toLowerCase() === "failed" ? "failed" : "idle";
  }

  function buildItemMetaParts(item) {
    return [
      item.subtitle || "",
      item.size_bytes ? formatSize(item.size_bytes) : "",
      item.updated_at ? formatUpdatedAt(item.updated_at) : ""
    ];
  }

  function isPlayableAudioItem(item) {
    const kind = String(item?.kind || "").toLowerCase();
    const format = String(item?.format || "").toLowerCase();
    const subtitle = String(item?.subtitle || "").toLowerCase();
    return (
      kind === "audio" ||
      subtitle.includes("音频") ||
      ["mp3", "wav", "flac", "ogg", "oga", "m4a", "aac", "opus", "webm"].includes(format)
    );
  }

  function normalizeItems(value) {
    return Array.isArray(value) ? value.filter((item) => item && typeof item === "object") : [];
  }

  function renderLoading() {
    renderEmpty("我在翻翻手边的小托盘……");
  }

  function renderEmpty(message) {
    itemMap.clear();
    els.content.replaceChildren();
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = message;
    els.content.append(empty);
    els.summary.textContent = "手边物品";
    els.fileCount.textContent = "0";
    els.outputCount.textContent = "0";
    updateWorkspaceActions({ files: [], outputs: [] });
  }

  function updateWorkspaceActions({ files = null, outputs = null } = {}) {
    if (!els.clearFiles) return;
    const fileCount = Array.isArray(files) ? files.length : Number(els.fileCount?.textContent || 0);
    const outputCount = Array.isArray(outputs) ? outputs.length : Number(els.outputCount?.textContent || 0);
    els.clearFiles.disabled = isLoading() || busy || fileCount + outputCount <= 0;
  }

  function setBusy(value) {
    busy = Boolean(value);
    for (const button of els.content.querySelectorAll("button")) button.disabled = busy;
    updateWorkspaceActions();
  }

  return { renderPayload, renderEmpty, renderLoading, updateWorkspaceActions, setBusy, findItem: key => itemMap.get(key) };
}

export function itemKey(item) {
  return String(item.id || item.handle || item.target || item.path || item.title || "").trim();
}
