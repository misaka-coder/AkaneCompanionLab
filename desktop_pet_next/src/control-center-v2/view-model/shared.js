

export function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

export function text(value) {
  return typeof value === "string" ? value.trim() : value == null ? "" : String(value).trim();
}

export function finiteNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

export function finitePercent(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, Math.min(100, number)) : null;
}

export function optionalFiniteNumber(value) {
  if (!["number", "string"].includes(typeof value) || (typeof value === "string" && !value.trim())) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function normalizeLabelValueRows(value, limit) {
  return (Array.isArray(value) ? value : []).slice(0, limit).map((item) => {
    const entry = asObject(item);
    return {
      label: text(entry.label),
      value: text(entry.value) || text(entry.status)
    };
  }).filter((item) => item.label && item.value);
}

export function safeAssetUrl(value) {
  const raw = text(value);
  if (!raw) return "";
  if (/^\/(?!\/)/.test(raw) || /^(https?:|blob:|asset:)/i.test(raw) || /^data:image\//i.test(raw)) return raw;
  return "";
}
