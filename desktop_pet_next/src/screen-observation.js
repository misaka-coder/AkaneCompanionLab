// One direct-vision input path. Packing changes image presentation, never the
// model, prompt profile, tool catalog, or cache family.
export const SCREEN_OBSERVATION_DEFAULTS = Object.freeze({
  screenVisionSampleIntervalSec: 2,
  screenVisionWindowSec: 30,
  screenVisionFrameCount: 4,
  screenVisionSheetCount: 1,
  screenVisionMaxEdge: 1280,
  screenVisionPacking: "contact_sheet"
});

export const SCREEN_OBSERVATION_COMMANDS = Object.freeze({
  "perception.screenVision.setEnabled": { command: "setScreenVisionEnabled", field: "screenVisionEnabled" },
  "perception.screenVision.setSampleIntervalSec": { command: "setScreenVisionSampleIntervalSec", field: "screenVisionSampleIntervalSec" },
  "perception.screenVision.setWindowSec": { command: "setScreenVisionWindowSec", field: "screenVisionWindowSec" },
  "perception.screenVision.setFrameCount": { command: "setScreenVisionFrameCount", field: "screenVisionFrameCount" },
  "perception.screenVision.setSheetCount": { command: "setScreenVisionSheetCount", field: "screenVisionSheetCount" },
  "perception.screenVision.setMaxEdge": { command: "setScreenVisionMaxEdge", field: "screenVisionMaxEdge" },
  "perception.screenVision.setPacking": { command: "setScreenVisionPacking", field: "screenVisionPacking" },
  "perception.screenVision.clear": { command: "clearScreenVision" },
  "perception.proactiveWake.setEnabled": { command: "setProactiveWakeEnabled", field: "proactiveWakeEnabled" },
  "perception.proactiveWake.setIntervalSec": { command: "setProactiveWakeIntervalSec", field: "proactiveWakeIntervalSec" }
});

function bounded(value, fallback, min, max) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? Math.min(max, Math.max(min, number)) : fallback;
}

export function normalizeScreenObservationSettings(value = {}) {
  return {
    screenVisionSampleIntervalSec: bounded(value.screenVisionSampleIntervalSec, 2, 0.5, 30),
    screenVisionWindowSec: bounded(value.screenVisionWindowSec, 30, 5, 300),
    // Both formats use the existing five-image turn contract, not a new limit.
    screenVisionFrameCount: Math.round(bounded(value.screenVisionFrameCount, 4, 1, 5)),
    screenVisionSheetCount: Math.round(bounded(value.screenVisionSheetCount, 1, 1, 4)),
    screenVisionMaxEdge: Math.round(bounded(value.screenVisionMaxEdge, 1280, 640, 1920)),
    screenVisionPacking: value.screenVisionPacking === "frames" ? "frames" : "contact_sheet"
  };
}

export class ScreenObservationBuffer {
  constructor() {
    this.frames = [];
    this.scope = "";
    this.revision = 0;
  }

  clear() {
    this.frames = [];
    this.scope = "";
    this.revision += 1;
  }

  push(frame, scope, settings) {
    if (this.scope !== scope) {
      this.clear();
      this.scope = scope;
    }
    if (!frame?.data_url?.startsWith("data:image/") || !Number.isFinite(frame.captured_at)) return;
    const config = normalizeScreenObservationSettings(settings);
    const retention = observationDuration(config);
    // Retain the whole requested period within a bounded memory budget.
    const bucketSec = Math.max(config.screenVisionSampleIntervalSec, retention / 120);
    if (this.frames.length && Math.floor(this.frames.at(-1).captured_at / bucketSec) === Math.floor(frame.captured_at / bucketSec)) this.frames.pop();
    this.frames.push(frame);
    this.frames = this.frames.filter((item) => frame.captured_at - item.captured_at <= retention);
  }

  snapshot(scope, settings, nowMs = Date.now()) {
    if (scope !== this.scope) return [];
    const config = normalizeScreenObservationSettings(settings);
    const maxFreshAge = Math.max(5, config.screenVisionSampleIntervalSec * 2);
    if (!this.frames.length || nowMs / 1000 - this.frames.at(-1).captured_at > maxFreshAge) return [];
    const frames = this.frames.filter((frame) => {
      const age = nowMs / 1000 - frame.captured_at;
      return age >= -1 && age <= observationDuration(config);
    });
    const count = config.screenVisionFrameCount * (config.screenVisionPacking === "frames" ? 1 : config.screenVisionSheetCount);
    if (frames.length <= count) return frames;
    if (count === 1) return frames.slice(-1);
    return Array.from({ length: count }, (_, index) => frames[Math.round(index * (frames.length - 1) / (count - 1))]);
  }

  ready(scope, settings, nowMs = Date.now()) {
    const config = normalizeScreenObservationSettings(settings);
    const frames = this.snapshot(scope, config, nowMs);
    if (!frames.length) return false;
    if (config.screenVisionPacking === "frames" || config.screenVisionSheetCount === 1) return true;
    const tolerance = Math.min(observationDuration(config) / 10, Math.max(config.screenVisionSampleIntervalSec, observationDuration(config) / 120) * 2);
    return frames.length >= config.screenVisionSheetCount && frames.at(-1).captured_at - frames[0].captured_at >= observationDuration(config) - tolerance;
  }
}

export function observationDuration(settings) {
  return settings.screenVisionWindowSec * (settings.screenVisionPacking === "frames" ? 1 : settings.screenVisionSheetCount);
}

export function observationConfigurationIssue(settings) {
  return settings.screenVisionPacking === "contact_sheet" && settings.screenVisionSheetCount > 1
    && settings.screenVisionSampleIntervalSec > settings.screenVisionWindowSec
    ? "多张拼图的采样间隔不能大于每张拼图观察窗口，请先调小采样间隔或增大窗口。" : "";
}

export function proactiveWakeDelay({ now, nextAllowedAt, intervalMs, immediate = false, delayMs = null }) {
  const explicit = delayMs !== null && Number.isFinite(Number(delayMs));
  return Math.max(1000, explicit ? Number(delayMs) : immediate ? 1000 : nextAllowedAt > 0 ? Math.max(0, nextAllowedAt - now) : intervalMs);
}

export class ScreenObservationPreparation {
  constructor() { this.clear(); }
  clear() { this.scope = ""; this.status = "idle"; this.error = ""; this.pending = null; }
  begin(scope, prepare) {
    if (this.scope === scope && this.status !== "idle") return this.pending || Promise.resolve();
    this.scope = scope;
    this.status = "preparing";
    this.error = "";
    const pending = Promise.resolve().then(prepare).then(() => {
      if (this.pending === pending) this.status = "ready";
    }).catch((error) => {
      if (this.pending === pending) {
        this.status = "failed";
        this.error = String(error?.message || error).slice(0, 160);
      }
    });
    this.pending = pending;
    return pending;
  }
}

function publicFrame(frame) {
  return {
    captured_at: frame.captured_at,
    width: frame.width,
    height: frame.height,
    data_url: frame.data_url,
    frame_times: [frame.captured_at],
    layout: { columns: 1, rows: 1 }
  };
}

function loadBrowserImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("screen_frame_decode_failed"));
    image.src = url;
  });
}

export async function packScreenObservation(frames, settings, {
  createCanvas = () => document.createElement("canvas"),
  loadImage = loadBrowserImage
} = {}) {
  const config = normalizeScreenObservationSettings(settings);
  const count = config.screenVisionFrameCount * (config.screenVisionPacking === "frames" ? 1 : config.screenVisionSheetCount);
  const ordered = frames.slice(-count).sort((a, b) => a.captured_at - b.captured_at);
  if (!ordered.length) return [];
  if (config.screenVisionPacking === "frames" || ordered.length === 1) return ordered.map(publicFrame);

  const latest = ordered.at(-1);
  const sheets = [];
  for (let offset = 0; offset < ordered.length; offset += config.screenVisionFrameCount) {
    const group = ordered.slice(offset, offset + config.screenVisionFrameCount);
    sheets.push(await packContactSheet(group, config, latest.captured_at, offset, ordered.length, { createCanvas, loadImage }));
  }
  // The latest sample is already the last cell. Sending it again at a different
  // scale made the transport layout look like a four-panel scene becoming one.
  return sheets;
}

async function packContactSheet(ordered, config, latestTime, offset, total, { createCanvas, loadImage }) {
  const images = await Promise.all(ordered.map((frame) => loadImage(frame.data_url)));
  // A vertical filmstrip makes time direction explicit instead of suggesting
  // simultaneous desktop tiles. The original screen is never cropped.
  const columns = 1;
  const rows = ordered.length;
  const cellWidth = Math.min(640, Math.round(config.screenVisionMaxEdge / 2));
  const cellHeight = Math.max(1, Math.round(cellWidth * ordered[0].height / ordered[0].width));
  const labelHeight = 42;
  const headerHeight = 62;
  const gap = 12;
  const canvas = createCanvas();
  canvas.width = columns * (cellWidth + gap) + gap;
  canvas.height = headerHeight + rows * (cellHeight + labelHeight + gap) + gap;
  const ctx = canvas.getContext("2d", { alpha: false });
  if (!ctx) throw new Error("screen_canvas_unavailable");
  ctx.fillStyle = "#111827";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#f9fafb";
  ctx.font = "bold 20px sans-serif";
  ctx.fillText("TIME SEQUENCE | TOP TO BOTTOM", gap, 26, cellWidth);
  ctx.font = "16px sans-serif";
  ctx.fillText("One shared screen, captured at different times", gap, 50, cellWidth);
  const latest = ordered.at(-1);
  ordered.forEach((frame, index) => {
    const x = gap + (index % columns) * (cellWidth + gap);
    const y = headerHeight + gap + index * (cellHeight + labelHeight + gap);
    const scale = Math.min(cellWidth / frame.width, cellHeight / frame.height);
    const width = frame.width * scale;
    const height = frame.height * scale;
    ctx.drawImage(images[index], x + (cellWidth - width) / 2, y + labelHeight + (cellHeight - height) / 2, width, height);
    ctx.fillStyle = "#f9fafb";
    ctx.font = "bold 20px monospace";
    const position = offset + index + 1;
    const age = Math.max(0, latestTime - frame.captured_at);
    ctx.fillText(`FRAME ${position}/${total} | ${position === total ? "LATEST SAMPLE" : `${age.toFixed(1)}s EARLIER`}`, x + 6, y + 28, cellWidth - 12);
  });
  return {
    captured_at: latest.captured_at,
    width: canvas.width,
    height: canvas.height,
    data_url: canvas.toDataURL("image/jpeg", 0.8),
    frame_times: ordered.map((frame) => frame.captured_at),
    layout: { columns, rows }
  };
}

export const MODERATE_PROACTIVE_PROMPT = [
  "本轮是一次适度主动的陪伴观察，不是用户提问，也不是必须完成的搭话任务。",
  "结合最近的实际画面、对话以及你已经说过的话，自己决定现在是否值得开口。",
  "有值得一起聊的新变化时自然接话；也可以安静陪着。无需逐帧播报、重复上一句或每次问候。",
  "根据画面中能确认的主体、动作或可读字幕自然回应，不评论截图排版、帧数或采样标签。看不清或无法确认内容与当前话题的关系时安静，不靠标题、旧对话或猜测补出视频剧情。",
  "窗口标题只能作为背景，不能替代真实画面；屏幕图片不包含声音。",
  "决定安静时仍返回正常完整的回复 JSON，但 speech 精确设为空字符串，不要输出省略号或解释自己保持安静。",
  "决定开口时直接以当前角色回复，通常一到两个自然短句；不要先写视觉摘要再让别人转述。"
].join("\n");
