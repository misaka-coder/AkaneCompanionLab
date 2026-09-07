// One direct-vision input path. Packing changes image presentation, never the
// model, prompt profile, tool catalog, or cache family.
export const SCREEN_OBSERVATION_DEFAULTS = Object.freeze({
  screenVisionSampleIntervalSec: 2,
  screenVisionWindowSec: 30,
  screenVisionFrameCount: 4,
  screenVisionMaxEdge: 1280,
  screenVisionPacking: "contact_sheet"
});

export const SCREEN_OBSERVATION_COMMANDS = Object.freeze({
  "perception.screenVision.setEnabled": { command: "setScreenVisionEnabled", field: "screenVisionEnabled" },
  "perception.screenVision.setSampleIntervalSec": { command: "setScreenVisionSampleIntervalSec", field: "screenVisionSampleIntervalSec" },
  "perception.screenVision.setWindowSec": { command: "setScreenVisionWindowSec", field: "screenVisionWindowSec" },
  "perception.screenVision.setFrameCount": { command: "setScreenVisionFrameCount", field: "screenVisionFrameCount" },
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
    this.frames.push(frame);
    this.frames = this.frames.filter((item) => frame.captured_at - item.captured_at <= config.screenVisionWindowSec);
    this.frames = this.frames.slice(-Math.ceil(config.screenVisionWindowSec / config.screenVisionSampleIntervalSec) - 2);
  }

  snapshot(scope, settings, nowMs = Date.now()) {
    if (scope !== this.scope) return [];
    const config = normalizeScreenObservationSettings(settings);
    const maxFreshAge = Math.max(5, config.screenVisionSampleIntervalSec * 2);
    if (!this.frames.length || nowMs / 1000 - this.frames.at(-1).captured_at > maxFreshAge) return [];
    const frames = this.frames.filter((frame) => {
      const age = nowMs / 1000 - frame.captured_at;
      return age >= -1 && age <= config.screenVisionWindowSec;
    });
    if (frames.length <= config.screenVisionFrameCount) return frames;
    if (config.screenVisionFrameCount === 1) return frames.slice(-1);
    return Array.from({ length: config.screenVisionFrameCount }, (_, index) =>
      frames[Math.round(index * (frames.length - 1) / (config.screenVisionFrameCount - 1))]);
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
  const ordered = frames.slice(-config.screenVisionFrameCount).sort((a, b) => a.captured_at - b.captured_at);
  if (!ordered.length) return [];
  if (config.screenVisionPacking === "frames" || ordered.length === 1) return ordered.map(publicFrame);

  const images = await Promise.all(ordered.map((frame) => loadImage(frame.data_url)));
  const columns = ordered.length <= 4 ? 2 : 3;
  const rows = Math.ceil(ordered.length / columns);
  const cellWidth = Math.min(640, Math.round(config.screenVisionMaxEdge / 2));
  const cellHeight = Math.max(1, Math.round(cellWidth * ordered[0].height / ordered[0].width));
  const labelHeight = 28;
  const gap = 6;
  const canvas = createCanvas();
  canvas.width = columns * (cellWidth + gap) + gap;
  canvas.height = rows * (cellHeight + labelHeight + gap) + gap;
  const ctx = canvas.getContext("2d", { alpha: false });
  if (!ctx) throw new Error("screen_canvas_unavailable");
  ctx.fillStyle = "#111827";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const latest = ordered.at(-1);
  ordered.forEach((frame, index) => {
    const x = gap + (index % columns) * (cellWidth + gap);
    const y = gap + Math.floor(index / columns) * (cellHeight + labelHeight + gap);
    const scale = Math.min(cellWidth / frame.width, cellHeight / frame.height);
    const width = frame.width * scale;
    const height = frame.height * scale;
    ctx.drawImage(images[index], x + (cellWidth - width) / 2, y + (cellHeight - height) / 2, width, height);
    ctx.fillStyle = "#f9fafb";
    ctx.font = "16px monospace";
    ctx.fillText(`#${index + 1}  ${(frame.captured_at - latest.captured_at).toFixed(1)}s`, x + 6, y + cellHeight + 20);
  });
  return [{
    captured_at: latest.captured_at,
    width: canvas.width,
    height: canvas.height,
    data_url: canvas.toDataURL("image/jpeg", 0.8),
    frame_times: ordered.map((frame) => frame.captured_at),
    layout: { columns, rows }
  }, publicFrame(latest)];
}

export const MODERATE_PROACTIVE_PROMPT = [
  "本轮是一次适度主动的陪伴观察，不是用户提问，也不是必须完成的搭话任务。",
  "结合最近的实际画面、对话以及你已经说过的话，自己决定现在是否值得开口。",
  "有值得一起聊的新变化时自然接话；也可以安静陪着。无需逐帧播报、重复上一句或每次问候。",
  "窗口标题只能作为背景，不能替代真实画面；屏幕图片不包含声音。",
  "决定安静时仍返回正常完整的回复 JSON，但 speech 精确设为空字符串，不要输出省略号或解释自己保持安静。",
  "决定开口时直接以当前角色回复，通常一到两个自然短句；不要先写视觉摘要再让别人转述。"
].join("\n");
