import {
  asArray,
  asObject,
  normalizeStringList,
  numberOrFallback,
  positiveNumber,
  stringValue
} from "../shared.js";
import { buildHealthTiles, inferLatencyLabel, parsePrometheusMetrics } from "./metrics.js";
import { findManifestEntry, normalizeEmotionCards } from "./characters.js";
import { buildRecentOutputsPatch } from "./abilities.js";
import { buildAbilityLabels } from "./capability-common.js";

export function buildOverviewRuntimePatch({ health, diagnostics, workspace, metricsText, petState, baseUrl, resourceManifest, connected }) {
  const resources = asObject(diagnostics?.resources);
  const capabilities = asObject(diagnostics?.capabilities);
  const workspaceCounts = asObject(diagnostics?.workspace);
  const runtime = asObject(diagnostics?.runtime);
  const runtimeMetrics = asObject(runtime.metrics);
  const metrics = parsePrometheusMetrics(metricsText);
  const declared = normalizeStringList(capabilities.declared);
  const modules = normalizeStringList(capabilities.effective_modules || capabilities.effectiveModules);
  const tools = normalizeStringList(capabilities.tool_names || capabilities.toolNames);
  const packId = stringValue(resources.character_pack_id || resources.characterPackId);
  const outfit = stringValue(resources.outfit);
  const defaultEmotion = stringValue(resources.default_emotion || resources.defaultEmotion);
  const activeEmotionId = String(petState?.currentEmotion || defaultEmotion || "").trim();
  const emotionCount = positiveNumber(resources.emotion_count ?? resources.emotionCount);

  const manifest = asObject(resourceManifest);
  const rawOutfits = asArray(asObject(manifest.characters).outfits);
  const activeOutfitId = String(petState?.outfit || outfit || resources.outfit || "").trim();
  const activeOutfit = findManifestEntry(rawOutfits, activeOutfitId) || rawOutfits.find(Boolean) || null;
  const emotionCards = normalizeEmotionCards(asArray(activeOutfit?.emotions), { activeEmotionId, baseUrl });
  const activeEmotion = emotionCards.find((card) =>
    card.id === activeEmotionId || card.name === activeEmotionId
  );
  const counts = asObject(workspace?.counts);
  const effectiveWorkspaceCounts = {
    files: numberOrFallback(workspaceCounts.files, counts.files, 0),
    outputs: numberOrFallback(workspaceCounts.outputs, counts.outputs, 0)
  };
  const serviceOk = connected || stringValue(health?.status) === "ok" || stringValue(diagnostics?.status) === "ok";
  const senseRuntime = buildOverviewSenseRuntimePatch(petState);
  const toolCount = tools.length;
  const moduleCount = modules.length || declared.length;

  return {
    shell: {
      status: serviceOk ? "Akane 在线" : "Akane 待连接",
      statusDetail: serviceOk ? "陪伴中 · 后端连接正常" : "等待后端连接"
    },
    statusBadge: serviceOk ? "在线 / Connected" : "离线 / Offline",
    connectionBadge: serviceOk ? "连接正常" : "等待连接",
    statusItems: {
      "连接状态": serviceOk ? "已连接" : "未连接",
      "当前角色包": packId || "Akane Default",
      "当前表情": defaultEmotion || "微笑",
      "今日能力摘要": toolCount ? `${moduleCount} 模块 · ${toolCount} 工具` : `${declared.length || 0} 项能力`
    },
    connectionRows: {
      "服务状态": serviceOk ? "正常运行" : "未连接",
      "响应延迟": inferLatencyLabel(runtimeMetrics, ["control_center.snapshot", "desktop_pet_workspace_summary"]),
      "同步状态": workspace?.ok === false ? "待同步" : "稳定",
      "会话时长": diagnostics?.server_time ? "已同步" : "待同步"
    },
    pack: {
      name: packId || "Akane Default",
      version: stringValue(diagnostics?.contract_version || diagnostics?.contractVersion) || "desktop_pet",
      publishedAt: outfit || "当前服装"
    },
    emotion: {
      name: activeEmotion?.name || activeEmotionId || defaultEmotion || "微笑",
      image: activeEmotion?.image || activeEmotion?.url || activeEmotion?.key || activeEmotionId || ""
    },
    voice: {
      ttsEnabled: Boolean(health?.contracts?.desktop_pet?.tts || health?.contracts?.desktop_pet?.health || serviceOk),
      asrEnabled: serviceOk,
      status: serviceOk ? "语音状态：后端已连接" : "语音状态：等待连接"
    },
    ...(senseRuntime ? { sense: senseRuntime } : {}),
    recentOutputs: buildRecentOutputsPatch(workspace),
    abilities: buildAbilityLabels({ tools, workspaceCounts: effectiveWorkspaceCounts }),
    health: buildHealthTiles({
      metrics,
      runtimeMetrics,
      workspaceCounts: effectiveWorkspaceCounts,
      health,
      toolCount,
      serviceOk
    })
  };
}

function buildOverviewSenseRuntimePatch(petState) {
  const state = asObject(petState);
  const entries = [
    ["screenVisionEnabled", state.screenVisionEnabled],
    ["proactiveWakeEnabled", state.proactiveWakeEnabled]
  ].filter(([, value]) => typeof value === "boolean");
  return entries.length ? Object.fromEntries(entries) : null;
}
