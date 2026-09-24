// One draft shared by the pet and control center. Only public material refs,
// never source paths, bytes, cache locations, or cross-session restored state.
export function createPendingAttachments({ readScope, changed = () => {} }) {
  let scopeKey = "";
  let items = [];
  let scopeVersion = 0;
  let importing = 0;
  const scope = () => JSON.stringify(readScope());
  function sync() {
    const current = scope();
    if (current !== scopeKey) { scopeKey = current; items = []; importing = 0; scopeVersion += 1; }
    return current;
  }
  function normalize(value) {
    const id = String(value?.attachment_id || value?.attachmentId || "").trim();
    if (!id || ["failed", "cleared"].includes(value?.status)) return null;
    return { attachmentId: id, handle: String(value.handle || value.attachment_handle || id),
      title: String(value.title || value.origin_name || "附件").slice(0, 120),
      format: String(value.format || value.file_ext || ""),
      kind: String(value.kind || "file"), status: String(value.status || "ready"),
      itemType: "attachment", canOpen: true,
      sizeBytes: Math.max(0, Number(value.size_bytes || value.file_size) || 0) };
  }
  function add(values, expectedScope = sync()) {
    if (sync() !== expectedScope) return false;
    const unique = new Map(items.map(item => [item.attachmentId, item]));
    for (const value of values || []) {
      const item = normalize(value);
      if (!item) throw new Error("附件尚未就绪或后端未返回可绑定的附件 ID，请更新后端后重试。");
      if (item) unique.set(item.attachmentId, item);
    }
    if (unique.size > 40) throw new Error("一条消息最多附带 40 个附件；已接收文件仍保留在工作台，请先移除部分待发送附件。");
    items = [...unique.values()];
    changed();
    return true;
  }
  return {
    scope: sync,
    token() { sync(); return String(scopeVersion); },
    importing() { sync(); return importing; },
    beginImport() {
      const key = sync(); importing += 1; changed();
      let ended = false;
      return () => { if (!ended && sync() === key) { importing = Math.max(0, importing - 1); changed(); } ended = true; };
    },
    list() { sync(); return items.map(item => ({ ...item })); },
    add,
    remove(id) { sync(); items = items.filter(item => item.attachmentId !== id); changed(); },
    take() { const key = sync(); const batch = { scope: key, items }; items = []; changed(); return batch; },
    restore(batch) {
      if (sync() !== batch.scope) return false;
      const existingIds = new Set(items.map(item => item.attachmentId));
      const missing = batch.items.filter(item => !existingIds.has(item.attachmentId));
      if (missing.length + items.length > 40) return false;
      return add(batch.items, batch.scope);
    },
  };
}

export async function serializeBrowserAttachments(files) {
  const entries = Array.from(files || []);
  if (entries.length > 40 || entries.some(file => file.size > 8 * 1024 * 1024)
      || entries.reduce((sum, file) => sum + file.size, 0) > 20 * 1024 * 1024) {
    throw new Error("粘贴附件限每个 8MB、合计 20MB；更大的文件请用添加附件或拖入。");
  }
  return Promise.all(entries.map(file => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ name: file.name || "clipboard.png", type: file.type, dataUrl: reader.result });
    reader.onerror = () => reject(new Error("无法读取粘贴附件"));
    reader.readAsDataURL(file);
  })));
}
