export function secondsFromIntervalLabel(label) {
  const text = String(label || "").trim();
  const match = /^(\d+)\s*(秒|分钟)$/.exec(text);
  if (!match) return 0;
  const value = Number.parseInt(match[1], 10);
  if (!Number.isFinite(value) || value <= 0) return 0;
  return match[2] === "分钟" ? value * 60 : value;
}
