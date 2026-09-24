export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function safeStyleUrl(value) {
  const raw = String(value || "").trim();
  if (!(/^\/(?!\/)/.test(raw) || /^(https?:|blob:|asset:)/i.test(raw) || /^data:image\//i.test(raw))) return "";
  const cssString = raw.replaceAll("\\", "\\\\").replaceAll('"', '\\"').replace(/[\n\r\f]/g, "");
  return escapeHtml(`url("${cssString}")`);
}

export function initial(value) {
  const text = String(value || "桌宠").trim();
  return escapeHtml(Array.from(text)[0] || "伴");
}
