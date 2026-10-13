import { asObject, finiteNumber, optionalFiniteNumber, text } from "./shared.js";

export function normalizeVoiceRuntime(value, live, connected) {
  const source = asObject(value);
  const tts = asObject(source.tts);
  const asr = asObject(source.asr);
  const liveState = asObject(live.state);
  const active = asObject(live.active);
  const providerTts = normalizeVoiceProvider(tts.providerStatus, "TTS 语音输出");
  const providerAsr = normalizeVoiceProvider(asr.providerStatus, "ASR 语音输入");
  const liveVolume = optionalFiniteNumber(liveState.voiceVolume);
  const volume = Number.isFinite(liveVolume)
    ? liveVolume * 100
    : finiteNumber(tts.volume, 80);
  const liveSnapshotAvailable = Boolean(connected && Object.keys(liveState).length);
  return {
    available: connected && Object.keys(source).length > 0,
    controlsAvailable: liveSnapshotAvailable,
    controlsUnavailableReason: connected ? "请在桌面端控制中心中调整" : "桌宠尚未连接",
    speaking: Boolean(active.speaking),
    inputState: text(active.voiceInput) || (asr.enabled ? "idle" : "disabled"),
    queueLength: Math.max(0, Math.round(finiteNumber(asObject(live.tts).queueLength, 0))),
    tts: {
      enabled: typeof liveState.voiceEnabled === "boolean" ? liveState.voiceEnabled : Boolean(tts.enabled),
      volume: Math.max(0, Math.min(100, Math.round(volume))),
      provider: providerTts
    },
    asr: {
      enabled: typeof liveState.voiceInputEnabled === "boolean" ? liveState.voiceInputEnabled : Boolean(asr.enabled),
      provider: providerAsr
    },
    diagnostics: normalizeVoiceDiagnostics(source.diagnostics)
  };
}

function normalizeVoiceProvider(value, fallbackLabel) {
  const source = asObject(value);
  const status = text(source.status);
  const activeName = text(source.activeProviderName);
  return {
    name: activeName || text(source.requestedProviderName) || fallbackLabel,
    status: status || (activeName ? "ready" : "unknown"),
    statusLabel: text(source.statusLabel) || (activeName ? "已就绪" : "待确认"),
    reason: text(source.reasonLabel) || text(source.reason),
    ready: status === "ready" || Boolean(!status && activeName)
  };
}

function normalizeVoiceDiagnostics(value) {
  return (Array.isArray(value) ? value : []).slice(0, 8).map((item) => {
    const source = asObject(item);
    return {
      label: text(source.label),
      value: text(source.value),
      tone: ["good", "warning", "danger", "muted"].includes(text(source.tone)) ? text(source.tone) : "muted"
    };
  }).filter((item) => item.label && item.value);
}
