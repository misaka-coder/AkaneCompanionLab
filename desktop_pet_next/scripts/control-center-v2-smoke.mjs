import assert from "node:assert/strict";

import {
  createControlCenterViewModel,
  isObservedActionConfirmation,
  observedActionOutcome,
  normalizeActionPresentation
} from "../src/control-center-v2/view-model.js";
import { renderCharacterAppearance } from "../src/control-center-v2/components/appearance.js";
import { renderAbilities } from "../src/control-center-v2/components/abilities.js";
import { renderChat } from "../src/control-center-v2/components/chat.js";
import { renderVoice } from "../src/control-center-v2/components/voice.js";
import { renderSystem } from "../src/control-center-v2/components/system.js";
import { renderModelService } from "../src/control-center-v2/components/model.js";
import { renderOverview } from "../src/control-center-v2/components/overview.js";
import { createTrailingAsyncRefresh, mergeChatSessions, prependedHistoryScrollTop } from "../src/control-center-v2/chat-history.js";
import {
  createModelServiceDraft,
  modelServicePayload,
  MODEL_SERVICE_ACTIONS,
  runModelServiceBridgeAction
} from "../src/control-center-v2/model-service.js";
import { createControlCenterBridge, shouldUseInstanceAdminProxy } from "../src/control-center-v2/bridge.js";
import { bindInstanceStorage } from "../src/instance-storage.js";
import {
  resolveActiveMediaControl,
  resolveMediaControlAction
} from "../src/media-control.js";
import {
  loadPresentationPreferences,
  normalizePresentationPreferences,
  presentationCssVariables,
  resetPresentationFrame,
  resolveThemeMode,
  savePresentationPreferences,
  updatePresentationFrame
} from "../src/control-center-v2/presentation-preferences.js";
import {
  buildMusicRuntimePatch,
  buildCharacterRuntimePatchFromSettingsSnapshot,
  createBackendControlCenterSource,
  createControlCenterRuntimeSnapshot
} from "../src/control-center/data-sources.js";
import {
  SETTINGS_COMMAND_EVENT,
  createTargetedEventEmitter
} from "../src/control-center/event-bridge.js";

assert.equal(shouldUseInstanceAdminProxy("http://127.0.0.1:9999/plugins/catalog", "GET"), false);
assert.equal(shouldUseInstanceAdminProxy("http://127.0.0.1:9999/admin/plugins/status", "GET"), true);
assert.equal(shouldUseInstanceAdminProxy("http://127.0.0.1:9999/api/bots/personal/admin/plugins/status", "GET"), true);
assert.equal(shouldUseInstanceAdminProxy("http://127.0.0.1:9999/admin/plugins/stages/one", "DELETE"), true);
assert.equal(shouldUseInstanceAdminProxy("http://127.0.0.1:9999/admin/plugins/foo/connections/default", "PUT"), true);
assert.equal(shouldUseInstanceAdminProxy("http://127.0.0.1:9999/admin/plugins/stages", "PATCH"), false);

const targetedEvents = [];
const fallbackEvents = [];
const targetedEmitter = createTargetedEventEmitter({
  targetLabel: "main",
  eventName: SETTINGS_COMMAND_EVENT,
  emitTo: async (...args) => targetedEvents.push(args),
  emit: async (...args) => fallbackEvents.push(args)
});
const targetedPayload = { command: "controlActiveMusic", action: "toggle", value: { action: "toggle" }, source: "control-center-v2" };
await targetedEmitter(SETTINGS_COMMAND_EVENT, targetedPayload);
assert.deepEqual(targetedEvents, [["main", SETTINGS_COMMAND_EVENT, targetedPayload]]);
assert.deepEqual(fallbackEvents, []);

const fallbackEmitter = createTargetedEventEmitter({
  targetLabel: "main",
  eventName: SETTINGS_COMMAND_EVENT,
  emitTo: async () => { throw new Error("target unavailable"); },
  emit: async (...args) => fallbackEvents.push(args)
});
await fallbackEmitter(SETTINGS_COMMAND_EVENT, targetedPayload);
assert.deepEqual(fallbackEvents, [[SETTINGS_COMMAND_EVENT, targetedPayload]]);
await assert.rejects(() => targetedEmitter(SETTINGS_COMMAND_EVENT, SETTINGS_COMMAND_EVENT), /invalid_targeted_event_payload/);

const refreshResolvers = [];
let refreshRunCount = 0;
const trailingRefresh = createTrailingAsyncRefresh(() => {
  refreshRunCount += 1;
  return new Promise((resolve) => refreshResolvers.push(resolve));
});
const firstRefresh = trailingRefresh();
assert.equal(trailingRefresh(), firstRefresh, "overlapping refreshes should share the active request");
assert.equal(refreshRunCount, 1);
refreshResolvers.shift()("stale");
await new Promise((resolve) => setImmediate(resolve));
assert.equal(refreshRunCount, 2, "a refresh requested in flight must run once more after the active request");
refreshResolvers.shift()("fresh");
assert.equal(await firstRefresh, "fresh", "callers should observe the trailing refresh result");

const liveCharacterPatch = buildCharacterRuntimePatchFromSettingsSnapshot({
  state: { characterPackId: "reimu", currentEmotion: "不满" },
  character: { appName: "灵梦Pet" },
  currentExpression: { id: "不满", name: "不满", image: "asset://localhost/reimu/unhappy.png" },
  resource: {
    activeOutfit: "default",
    emotions: [
      { id: "普通", name: "普通", image: "asset://localhost/reimu/normal.png" },
      { id: "不满", name: "不满", image: "asset://localhost/reimu/unhappy.png" }
    ]
  }
});
assert.equal(liveCharacterPatch.emotions.length, 2);
assert.equal(liveCharacterPatch.emotions.find((item) => item.current)?.id, "不满");
assert.notEqual(liveCharacterPatch.emotions[0].image, liveCharacterPatch.emotions[1].image);

const rawSnapshot = {
  sourceKind: "backend",
  fallbackReason: null,
  backendUrl: "http://127.0.0.1:9999",
  controlCenterV2: { liveSnapshotStatus: "connected" },
  generatedAt: "2026-08-20T08:00:00.000Z",
  controlCenterRuntime: {
    health: { ok: true, status: 200, data: { instance_id: "desktop-local" } },
    diagnostics: { ok: true, status: 200, data: { status: "ok" } },
    workspace: { ok: true, status: 200, data: { ok: true } },
    metrics: { ok: true, status: 200, data: "akane_tracemalloc_current_bytes 120000" },
    capabilitiesCatalog: { ok: true, status: "available", data: { ok: true } }
  },
  modelRuntime: {
    status: "configured",
    source: "local_file",
    providerId: "deepseek",
    protocol: "openai",
    baseUrl: "https://api.deepseek.com/v1",
    chatModel: "deepseek-chat",
    hasApiKey: true,
    useForVision: true,
    visionModel: "vision-model",
    timeoutSeconds: 120,
    providers: [
      { id: "deepseek", label: "DeepSeek", protocol: "openai", baseUrl: "https://api.deepseek.com/v1" },
      { id: "ollama", label: "Ollama", protocol: "ollama", baseUrl: "http://127.0.0.1:11434/v1", apiKeyRequired: false }
    ]
  },
  botCatalog: {
    defaultBotId: "bot-main",
    bots: [
      { botId: "bot-main", displayName: "主桌宠", available: true, default: true },
      { botId: "bot-work", displayName: "工作桌宠", available: true }
    ]
  },
  pluginRuntime: {
    status: "available",
    data: {
      ok: true,
      status: "active",
      generation: 7,
      plugin_count: 2,
      dependency_supply: { kind: "wheelhouse", configured: true, status: "ready" },
      plugins: [{
        plugin_id: "akane.sample.gentle-checkin",
        version: "0.2.0",
        source: "managed",
        manageable: true,
        enabled: true,
        runtime_status: "active",
        reason: "",
        generation: 7,
        surfaces: ["desktop", "qq"],
        contributions: {
          capabilities: ["akane.sample.gentle-checkin.schedule"],
          commands: ["checkin"],
          event_handlers: [],
          hooks: [],
          background_services: ["scheduler"],
          prompt_blocks: [],
          skills: [],
          connections: [{
            name: "query_api", version: 1, description: "HTTP JSON query endpoint",
            schema: { type: "object", properties: {
              endpoint: { type: "string", format: "uri", title: "服务地址", description: "HTTP endpoint" },
              credential: { type: "string", title: "访问令牌" },
              timeout_seconds: { type: "number", minimum: 0.1, maximum: 60, default: 10 }
            }, required: ["endpoint", "credential"], additionalProperties: false },
            private_fields: ["credential"]
          }]
        },
        connection_configs: [{
          ok: true, status: "configured", revision: 2, enabled: true,
          connection: { name: "query_api" }, values: { endpoint: "https://example.test/query", timeout_seconds: 10 },
          configured_private_fields: ["credential"], private_fields: ["credential"]
        }],
        permissions: ["agent.event.submit"],
        declared_only: false,
        pending_activation: false,
        rollback_available: true
      }, {
        plugin_id: "akane.sample.dependency-wait",
        version: "0.1.0",
        source: "managed",
        manageable: true,
        enabled: true,
        runtime_status: "waiting_dependency",
        reason: "service_provider_unavailable",
        dependency_errors: [{
          service_id: "statistics",
          selected_provider: "akane.missing",
          reason: "service_provider_unavailable",
          diagnostic_id: "diag-statistics-missing"
        }],
        generation: 7,
        surfaces: ["desktop"],
        contributions: {
          capabilities: [], commands: [], event_handlers: [], hooks: [],
          background_services: [], prompt_blocks: [], skills: [], connections: []
        },
        permissions: ["service.consume"],
        declared_only: true,
        pending_activation: false,
        rollback_available: false
      }]
    }
  },
  pluginManagementRuntime: {
    status: "available",
    data: {
      management: {
        status: "ready",
        supports: ["stage_source", "stage_wheel", "stage_market", "install", "discard_stage", "uninstall"]
      },
      artifacts: {
        status: "ready",
        stages: [{
          ok: true,
          status: "staged",
          stage_id: "stage-review-1",
          plugin_id: "akane.sample.timer",
          distribution_name: "akane-sample-timer",
          version: "1.0.0",
          permissions: ["agent.event.submit", "storage.write"],
          contribution_snapshot: {
            capabilities: ["akane.sample.timer.schedule"],
            background_services: ["timer-scheduler"]
          }
        }]
      }
    }
  },
  pluginMarketRuntime: {
    status: "available",
    data: { ok: true, status: "ready", source_kind: "local_release", plugins: [{
      plugin_id: "akane.media-convert", display_name: "媒体格式转换", version: "0.1.0", summary: "提取与转换音频",
      sha256: "a".repeat(64), size_bytes: 12000, permissions: ["resource.read", "artifact.write"],
      requirements: ["FFmpeg 和 FFprobe"], installed_status: "not_installed", installed_version: ""
    }] }
  },
  overviewRuntime: {
    shell: { status: "在线" },
    emotion: { name: "开心", image: "https://127.0.0.1/assets/happy.png" },
    abilities: ["文件处理", "文档交付"],
    recentOutputs: [{ id: "gen_1", title: "交付结果.png", format: "PNG", status: "available" }]
  },
  characterRuntime: {
    selectedPack: "测试角色",
    selectedPackId: "test_character",
    voice: { provider: "gpt_sovits", profileId: "reimu_main", notes: "主声线" },
    hero: "https://127.0.0.1/assets/hero.png",
    availablePacks: [
      { id: "test_character", appName: "测试角色", defaultOutfit: "default", selected: true },
      { id: "second_character", appName: "另一位角色", defaultOutfit: "casual" }
    ],
    outfits: [
      { id: "default", name: "默认服装", image: "https://127.0.0.1/assets/default.png", current: true },
      { id: "casual", name: "日常服装", image: "https://127.0.0.1/assets/casual.png" }
    ],
    emotions: [
      { id: "happy", name: "开心", image: "https://127.0.0.1/assets/happy.png", current: true },
      { id: "thinking", name: "思考", image: "https://127.0.0.1/assets/thinking.png" }
    ],
    packInfo: [{ label: "版本", value: "0.2" }],
    completeness: 100,
    warning: { headline: "资源状态良好", body: "已加载统一资源清单" },
    emotions: [{ current: true, image: "https://127.0.0.1/assets/happy.png" }]
  },
  musicRuntime: {
    nowPlaying: { title: "星河", artist: "本地媒体", playing: true },
    bottomStatus: "正在播放"
  },
  abilitiesRuntime: {
    overview: {
      availability: 86,
      note: "本地能力目录已同步，2 项能力等待配置",
      stats: [
        { label: "能力模块", value: "6" },
        { label: "可用能力", value: "24" },
        { label: "待完善", value: "2" }
      ]
    },
    modules: [
      { title: "文件与工作区", description: "材料整理 / 文件交付", permission: "工作区文件访问", count: "8 项能力", tone: "orange", statusLabel: "可用", statusTone: "ready" },
      { title: "安全与契约", description: "权限确认 / 风险隔离", permission: "安全与确认", count: "3 项能力", tone: "pink", statusLabel: "已生效", statusTone: "ready" }
    ],
    providers: [{
      id: "provider.tts.gpt_sovits.local",
      title: "本地 GPT-SoVITS",
      adapter: "gpt_sovits",
      type: "tts_provider",
      status: "ready",
      statusLabel: "可用",
      statusTone: "ready",
      reason: "已连接",
      enabled: true,
      endpoint: "http://127.0.0.1:9880",
      actionsEnabled: true,
      voiceProfiles: [{
        voiceProfileId: "reimu_main",
        providerId: "provider.tts.gpt_sovits.local",
        name: "灵梦 · 主声线",
        enabled: true,
        configured: true,
        status: "ready",
        statusLabel: "可用",
        statusTone: "ready",
        textLang: "zh",
        promptLang: "zh",
        mediaType: "wav",
        parallelInfer: false,
        splitBucket: false,
        batchSize: 1,
        topK: 8,
        topP: 0.85,
        temperature: 0.6,
        speedFactor: 1,
        fragmentInterval: 0.3,
        textSplitMethod: "cut1",
        referenceAudioName: "reimu_ref.wav",
        promptTextLength: 12,
        emotionSampleCount: 1,
        emotionSamples: [{
          emotionId: "happy",
          aliases: ["开心", "高兴"],
          referenceAudioName: "reimu_happy.wav",
          promptTextLength: 9
        }]
      }]
    }],
    mcpServers: [{ serverId: "anysearch", title: "AnySearch", status: "missing_config", statusLabel: "待配置", reason: "需要配置 Akane Host 上的 MCP", transport: "stdio", executionLocation: "host", executionLocationLabel: "Akane Host", executionLocationDetail: "MCP 进程由 Akane Host 启动。", safeToolLabels: ["网页搜索"], actionsEnabled: true }],
    workflows: [{ workflowId: "workflow.workshop.portrait.cutout", title: "透明背景处理", status: "ready", statusLabel: "可用", detail: "工作流已绑定", enabled: true, configured: true, workflowPath: "workflows/portrait.json", actionsEnabled: true }],
    skills: {
      status: "ready",
      catalogRevision: "catalog123",
      total: 2,
      bundled: 1,
      managed: 1,
      entries: [
        { name: "coding-project", description: "Use for multi-file coding projects.", source: "bundled", revision: "rev1", requiredTools: ["exec_run"], resourceCount: 1 },
        { name: "personal-workflow", description: "Use for the owner's repeatable local workflow.", source: "managed", revision: "rev2", requiredTools: [], resourceCount: 0 }
      ],
      diagnostics: []
    },
    execution: {
      policy: {
        ok: true,
        status: "available",
        policy: {
          policies: [{
            policy_id: "safe.default",
            service_id: "policy.safe.default.v1",
            version: 1,
            order: 0,
            stages: ["before", "observe"],
            status: "available"
          }],
          diagnostics: []
        }
      },
      resource: {
        ok: true,
        status: "available",
        policy: {
          policy_id: "deployment.execution_resources.v1",
          limits: {
            max_input_bytes: { value: 4096, source: "deployment_config" },
            max_dependency_depth: { value: 4, source: "deployment_config" },
            max_dependency_calls: { value: 7, source: "deployment_config" }
          }
        }
      }
    },
    safety: {
      status: "已生效",
      approvalPolicy: {
        families: [
          { id: "ops", label: "本机与外部操作", summary: "Shell、浏览器交互和有外部影响的工具。", mode: "ask_each_time", availableModes: [
            { id: "trusted_auto_allow", label: "直接允许", summary: "无需逐次确认。" },
            { id: "ask_each_time", label: "每次询问", summary: "确认后再执行。" },
            { id: "disabled", label: "关闭", summary: "不允许执行。" }
          ] },
          { id: "extensions", label: "扩展管理", summary: "安装、启停与移除扩展。", mode: "ask_each_time", availableModes: [
            { id: "trusted_auto_allow", label: "直接允许", summary: "无需逐次确认。" },
            { id: "ask_each_time", label: "每次询问", summary: "确认后再执行。" },
            { id: "disabled", label: "关闭", summary: "不允许执行。" }
          ] }
        ]
      },
      items: [
        { label: "当前审批模式", status: "请求批准" },
        { label: "密钥与敏感信息", status: "未暴露" }
      ],
      approvalRequests: [{ requestId: "approval_001", title: "执行本地命令", summary: "需要运行项目测试", risk: "high", status: "pending" }]
    },
    calls: [{ time: "14:20", module: "能力注册表", description: "已同步 24 项能力", status: "成功", method: "能力目录" }]
  },
  voiceRuntime: {
    tts: {
      enabled: true,
      volume: 82,
      speed: "1.15x",
      providerStatus: { status: "ready", statusLabel: "已就绪", activeProviderName: "GPT-SoVITS" }
    },
    asr: {
      enabled: true,
      providerStatus: { status: "degraded", statusLabel: "已降级", activeProviderName: "本地 ASR", reasonLabel: "使用本地通道" }
    },
    wakeWord: "Akane",
    wakeSensitivity: "中等",
    diagnostics: [
      { label: "整体状态", value: "正常运行", tone: "good" },
      { label: "响应延迟", value: "420 ms", tone: "good" }
    ]
  },
  advancedRuntime: {
    systemStrip: {
      "运行中": { tone: "green" },
      "CPU": { value: "18%" },
      "内存": { value: "42%" },
      "网络": { value: "良好", tone: "green" }
    },
    coreSettings: [
      { id: "hitTest", enabled: true },
      { id: "hitbox", enabled: false }
    ],
    diagnostics: {
      metrics: {
        "应用状态": { value: "运行中", tone: "green" },
        "后端健康": { value: "良好", tone: "green" },
        "内存占用": { value: "117.2 KB" }
      }
    }
  },
  chatSession: {
    session: { session_id: "session-chat", display_title: "下午的对话" },
    message_page: { limit: 120, has_more: true, next_before_seq: 1 },
    latest_final_json: {
      status: "completed",
      tool_events: [{
        tool_type: "akane.sample.gentle-checkin.schedule",
        status: "completed",
        reason: "已登记下一次提醒"
      }, {
        type: "generated_file_ready",
        status: "completed",
        send_to_user: true
      }],
      value: { scheduled: true },
      _debug: { token: "must-not-reach-ui", local_path: "C:/private/runtime" }
    },
    messages: [
      { source_id: "msg-user", seq_no: 1, role: "user", content: "今天一起做什么？", timestamp: 1787198400 },
      { source_id: "msg-assistant", seq_no: 2, role: "assistant", content: "先把界面做舒服。", timestamp: 1787198460 },
      {
        source_id: "msg-progress",
        seq_no: 3,
        role: "assistant",
        content: "我正在检查现有页面。",
        timestamp: 1787198520,
        memory_metadata: { turn_role: "intermediate" }
      }
    ]
  }
};

const runtimeSnapshot = {
  state: {
    instanceId: "desktop-local",
    boundBotId: "bot-main",
    sessionId: "session-chat",
    characterPackId: "test_character",
    outfit: "default",
    voiceEnabled: true,
    voiceInputEnabled: true,
    voiceVolume: 0.82,
    voiceSpeed: "1.15x",
    wakeWord: "Akane",
    wakeSensitivity: "中等",
    hitTestEnabled: true,
    hitboxOverlay: false
  },
  currentExpression: { id: "happy", name: "开心", image: "https://127.0.0.1/assets/happy.png" },
  runtimeStatus: "正在整理文件",
  runtimeMode: "thinking",
  active: { sending: true, speaking: false, voiceInput: "idle", replyDisplayActive: false },
  tts: { active: false, queueLength: 0 },
  resource: { health: "online", source: "character-pack", healthMessage: "" }
};

const viewModel = createControlCenterViewModel(rawSnapshot, runtimeSnapshot);
assert.equal(viewModel.shell.connected, true);
assert.equal(viewModel.shell.instanceLabel, "desktop-local");
assert.equal(viewModel.bots.activeId, "bot-main");
assert.equal(viewModel.bots.items.length, 2);
assert.equal(viewModel.actions["settings.selectBot"].available, true);
assert.equal(viewModel.character.displayName, "测试角色");
assert.equal(viewModel.character.emotion, "开心");
assert.equal(viewModel.character.resourceWarnings.length, 0);
assert.equal(viewModel.character.availablePacks.length, 2);
assert.equal(viewModel.character.availablePacks[0].selected, true);
assert.equal(viewModel.character.outfits[0].current, true);
assert.equal(viewModel.character.emotions[0].current, true);
assert.equal(viewModel.character.completeness, 100);
assert.equal(viewModel.activity.phase, "thinking");
assert.equal(viewModel.activity.label, "正在整理文件");
assert.equal(viewModel.music.playback, "playing");
assert.equal(viewModel.chat.latestOutcome.statusLabel, "已完成");
assert.equal(viewModel.chat.latestOutcome.toolCount, 1);
assert.equal(viewModel.chat.latestOutcome.artifactCount, 1);
assert.equal(viewModel.chat.latestOutcome.events[0].toolType, "akane.sample.gentle-checkin.schedule");
assert.equal(viewModel.chat.latestOutcome.events[1].toolType, "文件产物");
const overviewHtml = renderOverview({ viewModel, actionStates: {} });
assert.match(overviewHtml, /data-bound-bot-select/);
assert.match(overviewHtml, /class="mood-line"/);
assert.match(overviewHtml, /基础设置已就绪/);
assert.match(overviewHtml, /class="setup-center glass-panel is-complete"/);
assert.match(overviewHtml, /data-page="appearance"/);
assert.match(overviewHtml, /data-page="model"/);
assert.match(overviewHtml, /data-page="abilities"/);
assert.match(overviewHtml, /data-page="voice"/);
assert.doesNotMatch(overviewHtml, /apiKey|baseUrl|local_path|cached_path/);
assert.equal(viewModel.recentOutputs[0].title, "交付结果.png");
assert.equal(viewModel.abilities.available, true);
assert.equal(viewModel.abilities.availability, 86);
assert.equal(viewModel.abilities.modules.length, 2);
assert.deepEqual(viewModel.abilities.policy.families.map((family) => family.mode), ["ask_each_time", "ask_each_time"]);
assert.equal(viewModel.abilities.integrations.length, 3);
assert.equal(viewModel.abilities.providers[0].id, "provider.tts.gpt_sovits.local");
assert.equal(viewModel.abilities.providers[0].voiceProfiles[0].voiceProfileId, "reimu_main");
assert.equal(viewModel.abilities.providers[0].voiceProfiles[0].topK, 8);
assert.equal(viewModel.abilities.providers[0].voiceProfiles[0].topP, 0.85);
assert.equal(viewModel.abilities.providers[0].voiceProfiles[0].temperature, 0.6);
assert.equal(viewModel.abilities.providers[0].voiceProfiles[0].emotionSampleCount, 1);
assert.deepEqual(viewModel.abilities.providers[0].voiceProfiles[0].emotionSamples[0].aliases, ["开心", "高兴"]);
assert.equal(viewModel.character.voice.profileId, "reimu_main");
assert.equal(viewModel.actions["abilities.provider.ttsTest"].available, true);
assert.equal(viewModel.actions["abilities.provider.voiceProfile.assignToCurrentCharacter"].available, true);
assert.equal(viewModel.actions["abilities.provider.voiceProfile.clearCurrentCharacter"].available, true);
assert.equal(viewModel.abilities.mcpServers[0].serverId, "anysearch");
assert.equal(viewModel.abilities.plugins.dependencySupply.status, "ready");
assert.equal(viewModel.abilities.plugins.entries.find((item) => item.pluginId === "akane.sample.dependency-wait").statusLabel, "等待依赖");
assert.equal(viewModel.abilities.plugins.entries.find((item) => item.pluginId === "akane.sample.dependency-wait").dependencyErrors[0].selectedProvider, "akane.missing");
assert.equal(viewModel.abilities.execution.available, true);
assert.equal(viewModel.abilities.execution.policy.entries[0].policyId, "safe.default");
assert.equal(viewModel.abilities.execution.resource.limits.find((item) => item.key === "max_input_bytes").value, 4096);
assert.equal(viewModel.abilities.workflows[0].workflowId, "workflow.workshop.portrait.cutout");
assert.equal(viewModel.abilities.approvalRequests[0].requestId, "approval_001");
assert.equal(viewModel.abilities.skills.total, 2);
assert.equal(viewModel.abilities.skills.entries[1].source, "managed");
assert.equal(viewModel.model.available, true);
assert.equal(viewModel.model.providerId, "deepseek");
assert.equal(viewModel.model.hasApiKey, true);
assert.equal(viewModel.model.providers.length, 2);
assert.equal(viewModel.setup.coreReadyCount, 3);
assert.equal(viewModel.setup.coreComplete, true);
assert.equal(viewModel.setup.optionalReadyCount, 2);
assert.deepEqual(viewModel.setup.items.map((item) => item.id), ["character", "model", "permissions", "skills", "voice"]);
assert.equal(viewModel.voice.available, true);
assert.equal(viewModel.voice.controlsAvailable, true);
assert.equal(viewModel.voice.tts.provider.name, "GPT-SoVITS");
assert.equal(viewModel.voice.tts.volume, 82);
assert.equal(viewModel.voice.asr.provider.status, "degraded");
assert.equal(viewModel.voice.wakeSensitivity, "中等");
assert.equal(viewModel.system.available, true);
assert.equal(viewModel.system.overallTone, "warning");
assert.equal(viewModel.system.issues.some((item) => item.title.includes("本地 ASR")), true);
assert.equal(viewModel.system.services.length, 5);
assert.equal(viewModel.system.services.every((item) => item.ready), true);
assert.equal(viewModel.system.metrics.some((item) => item.label === "CPU" && item.value === "18%"), true);
assert.equal(viewModel.system.settings[0].enabled, true);
assert.equal(viewModel.system.settings[1].enabled, false);
assert.equal(viewModel.actions["abilities.approvalPolicy.save"].available, true);
assert.equal(viewModel.actions["abilities.provider.config.save"].available, true);
assert.equal(viewModel.actions["abilities.mcp.discover"].available, true);
assert.equal(viewModel.actions["abilities.workflow.validate"].available, true);
assert.equal(viewModel.actions["abilities.approvalRequest.decide"].available, true);
assert.equal(viewModel.actions["abilities.skills.openFolder"].available, true);
assert.equal(viewModel.actions[MODEL_SERVICE_ACTIONS.models].available, true);
assert.equal(viewModel.actions[MODEL_SERVICE_ACTIONS.test].available, true);
assert.equal(viewModel.actions[MODEL_SERVICE_ACTIONS.save].available, true);
assert.equal(viewModel.chat.title, "下午的对话");
assert.equal(viewModel.chat.messages.length, 3);
assert.equal(viewModel.chat.messages[2].intermediate, true);
assert.equal(viewModel.chat.history.hasMore, true);
assert.equal(viewModel.chat.history.nextBeforeSeq, 1);
assert.equal(viewModel.actions["chat.send"].available, true, "busy tasks accept steering input");
assert.equal(viewModel.actions["chat.stop"].available, true);
assert.equal(viewModel.actions["character.openWorkshop"].available, true);
assert.equal(viewModel.actions["character.selectPack"].available, true);
assert.equal(viewModel.actions["character.setOutfit"].available, true);
assert.equal(viewModel.actions["character.previewEmotion"].available, true);
assert.equal(viewModel.actions["voice.previewPlay"].available, true);
assert.equal(viewModel.actions["voice.stop"].available, false);
assert.equal(viewModel.actions["voice.setTtsEnabled"].available, true);
assert.equal(viewModel.actions["perception.runDiagnostics"].available, true);
assert.equal(viewModel.actions["advanced.setHitTestEnabled"].available, true);
assert.equal(viewModel.actions["advanced.setHitboxOverlay"].available, true);
assert.equal(viewModel.actions["window.minimize"].available, true);
assert.equal(viewModel.actions["window.maximize"].available, true);
assert.equal(viewModel.actions["window.close"].available, true);

const incompleteSetup = createControlCenterViewModel({
  ...rawSnapshot,
  modelRuntime: { ...rawSnapshot.modelRuntime, status: "unconfigured", chatModel: "" },
  characterRuntime: {
    ...rawSnapshot.characterRuntime,
    warning: { headline: "角色资源需要处理", body: "缺少默认立绘" }
  },
  abilitiesRuntime: {}
}, runtimeSnapshot).setup;
assert.equal(incompleteSetup.coreReadyCount, 0);
assert.equal(incompleteSetup.coreComplete, false);
assert.equal(incompleteSetup.items.find((item) => item.id === "voice").optional, true);

const liveVoiceOverride = createControlCenterViewModel(rawSnapshot, {
  ...runtimeSnapshot,
  state: {
    ...runtimeSnapshot.state,
    voiceEnabled: false,
    voiceInputEnabled: false,
    voiceVolume: 0.47,
    voiceSpeed: "0.85x",
    wakeWord: "灵梦",
    wakeSensitivity: "低"
  }
});
assert.equal(liveVoiceOverride.voice.tts.enabled, false);
assert.equal(liveVoiceOverride.voice.asr.enabled, false);
assert.equal(liveVoiceOverride.voice.tts.volume, 47);
assert.equal(liveVoiceOverride.voice.tts.speed, "0.85x");
assert.equal(liveVoiceOverride.voice.wakeWord, "灵梦");
assert.equal(liveVoiceOverride.voice.wakeSensitivity, "低");

const runtimeOnly = createControlCenterRuntimeSnapshot({
  ...rawSnapshot,
  overviewPage: { title: "legacy mock title" },
  characterPage: { selectedPack: "legacy mock character" },
  navItems: [{ id: "overview", label: "legacy mock nav" }]
});
assert.equal(Object.hasOwn(runtimeOnly, "overviewPage"), false);
assert.equal(Object.hasOwn(runtimeOnly, "characterPage"), false);
assert.equal(Object.hasOwn(runtimeOnly, "navItems"), false);
assert.equal(runtimeOnly.characterRuntime.selectedPack, "测试角色");

const unsafeAsset = createControlCenterViewModel({
  ...rawSnapshot,
  overviewRuntime: { emotion: { name: "未知", image: "file:///C:/private/character.png" } },
  characterRuntime: { selectedPack: "无图角色", hero: "C:/private/hero.png" }
});
assert.equal(unsafeAsset.character.visuals.avatar, "");
assert.equal(unsafeAsset.character.visuals.hero, "");

const relativeAsset = createControlCenterViewModel({
  ...rawSnapshot,
  overviewRuntime: { emotion: { name: "本地资源", image: "/assets/character.png" } }
});
assert.equal(relativeAsset.character.visuals.avatar, "/assets/character.png");

const unsafeDataAsset = createControlCenterViewModel({
  ...rawSnapshot,
  overviewRuntime: { emotion: { name: "坏资源", image: "data:text/html,<script>alert(1)</script>" } }
});
assert.equal(unsafeDataAsset.character.visuals.avatar, "");

assert.deepEqual(normalizeActionPresentation({ ok: true, status: "executed" }), {
  phase: "confirmed",
  label: "已完成",
  detail: ""
});
assert.deepEqual(normalizeActionPresentation({ ok: false, status: "cancelled", reason: "picker_cancelled" }), {
  phase: "confirmed",
  label: "已取消",
  detail: ""
});
assert.deepEqual(normalizeActionPresentation({ ok: false, status: "execution_unknown", reason: "state_not_confirmed" }), {
  phase: "unknown",
  label: "已发送，未确认",
  detail: "state_not_confirmed"
});
assert.deepEqual(normalizeActionPresentation({ ok: false, status: "execution_unknown", reason: "runtime_state_not_confirmed" }), {
  phase: "unknown",
  label: "已发送，未确认",
  detail: "指令已发出，但没有在时限内观察到状态变化"
});
assert.deepEqual(normalizeActionPresentation({ ok: false, status: "failed", error: "permission_denied" }), {
  phase: "failed",
  label: "操作失败",
  detail: "permission_denied"
});
assert.deepEqual(normalizeActionPresentation({ ok: false, status: "failed", reason: "admin_auth_required" }), {
  phase: "failed",
  label: "操作失败",
  detail: "此操作只能从桌面控制中心执行"
});
assert.deepEqual(normalizeActionPresentation({ ok: true, status: "executed", actionId: "advanced.resetWindow" }), {
  phase: "unknown",
  label: "重置请求已发送",
  detail: "请观察桌宠窗口是否已经恢复"
});

assert.equal(isObservedActionConfirmation(
  "chat.send",
  { active: { sending: false } },
  {
    active: { sending: true },
    settingsCommandResult: { command: "sendChatMessage", operationId: "chat-op-1", ok: true, status: "accepted" }
  },
  { text: "你好", operationId: "chat-op-1" }
), true);
assert.equal(isObservedActionConfirmation(
  "music.togglePlayback",
  { settingsCommandResult: null },
  {
    settingsCommandResult: {
      command: "controlActiveMusic",
      operationId: "music-op-1",
      ok: true,
      status: "completed",
      target: "system",
      action: "pause"
    }
  },
  { operationId: "music-op-1" }
), true);
assert.equal(isObservedActionConfirmation(
  "music.togglePlayback",
  { settingsCommandResult: null },
  {
    settingsCommandResult: {
      command: "controlActiveMusic",
      operationId: "older-op",
      ok: true,
      status: "completed"
    }
  },
  { operationId: "music-op-2" }
), false);
assert.deepEqual(observedActionOutcome(
  "music.togglePlayback",
  { operationId: "music-op-3" },
  {
    settingsCommandResult: {
      command: "controlActiveMusic",
      operationId: "music-op-3",
      ok: false,
      status: "execution_unknown",
      reason: "media_state_not_confirmed",
      target: "system",
      action: "play"
    }
  }
), {
  ok: false,
  status: "execution_unknown",
  reason: "media_state_not_confirmed",
  target: "system",
  targetId: "",
  mediaAction: "play"
});

const stoppedLocalVsPlayingSystem = resolveActiveMediaControl({
  localTrack: { sourceId: "local-stale" },
  localPlaying: false,
  localPaused: false,
  systemMedia: { trackKey: "system-live", playbackStatus: "playing", isPlaying: true },
  systemControllable: true
});
assert.equal(stoppedLocalVsPlayingSystem.target, "system");
assert.equal(resolveMediaControlAction("toggle", stoppedLocalVsPlayingSystem), "pause");
const pausedLocalVsPausedSystem = resolveActiveMediaControl({
  localTrack: { sourceId: "local-paused" },
  localPaused: true,
  systemMedia: { trackKey: "system-paused", playbackStatus: "paused", isPlaying: false },
  systemControllable: true
});
assert.equal(pausedLocalVsPausedSystem.target, "local");
const canonicalSystemMusicPatch = buildMusicRuntimePatch({
  musicSnapshot: {
    track: { displayName: "旧的本地曲目" },
    queue: [{ displayName: "旧的本地曲目" }],
    queueCount: 1,
    playing: false,
    paused: false,
    control: { target: "system", targetId: "system-live", playbackStatus: "playing", isPlaying: true, available: true },
    systemMedia: {
      ok: true,
      status: "ready",
      fresh: true,
      controllable: true,
      title: "当前系统曲目",
      artist: "系统播放器",
      playbackStatus: "playing",
      isPlaying: true
    }
  },
  petState: {}
});
assert.equal(canonicalSystemMusicPatch.control.target, "system");
assert.equal(canonicalSystemMusicPatch.nowPlaying.title, "当前系统曲目 - 系统播放器");
assert.equal(isObservedActionConfirmation(
  "chat.send",
  { active: { sending: false } },
  {
    active: { sending: false },
    settingsCommandResult: { command: "sendChatMessage", operationId: "chat-op-busy", ok: false, status: "busy" }
  },
  { text: "你好", operationId: "chat-op-busy" }
), true);

assert.equal(isObservedActionConfirmation(
  "voice.previewPlay",
  { active: { speaking: false } },
  { active: { speaking: true }, settingsCommandResult: { command: "previewTts", operationId: "voice-op-1", ok: true, status: "completed" } },
  { text: "试听", operationId: "voice-op-1" }
), true);
assert.equal(isObservedActionConfirmation(
  "character.previewEmotion",
  { state: { currentEmotion: "normal" }, currentExpression: { id: "normal" } },
  { state: { currentEmotion: "normal" }, currentExpression: { id: "normal" }, settingsCommandResult: { command: "previewEmotion", operationId: "emotion-op-1", ok: true, status: "completed" } },
  { value: "normal", operationId: "emotion-op-1" }
), true);
assert.deepEqual(observedActionOutcome(
  "chat.send",
  { operationId: "chat-op-busy" },
  { settingsCommandResult: { command: "sendChatMessage", operationId: "chat-op-busy", ok: false, status: "busy", reason: "reply_in_progress" } }
), {
  ok: false,
  status: "busy",
  reason: "reply_in_progress"
});
assert.equal(isObservedActionConfirmation(
  "voice.stop",
  { active: { speaking: true } },
  { active: { speaking: false } }
), true);
assert.equal(isObservedActionConfirmation(
  "voice.setTtsEnabled",
  { state: { voiceEnabled: true } },
  { state: { voiceEnabled: false } },
  { value: false }
), true);
assert.equal(isObservedActionConfirmation(
  "voice.setTtsEnabled",
  { state: { voiceEnabled: true } },
  { state: {} },
  { value: false }
), false);
assert.equal(isObservedActionConfirmation(
  "voice.setVolume",
  { state: { voiceVolume: 0.82 } },
  { state: { voiceVolume: 0.64 } },
  { value: 0.64 }
), true);
assert.equal(isObservedActionConfirmation(
  "voice.setWakeWord",
  { state: { wakeWord: "Akane" } },
  { state: { wakeWord: "灵梦" } },
  { value: "灵梦" }
), true);
assert.equal(isObservedActionConfirmation(
  "advanced.setHitTestEnabled",
  { state: { hitTestEnabled: true } },
  { state: { hitTestEnabled: false } },
  { value: false }
), true);
assert.equal(isObservedActionConfirmation(
  "advanced.setHitboxOverlay",
  { state: { hitboxOverlay: false } },
  { state: { hitboxOverlay: true } },
  { value: true }
), true);
assert.equal(isObservedActionConfirmation(
  "chat.new",
  { state: { sessionId: "session-a" } },
  { state: { sessionId: "session-b" } }
), true);
assert.equal(isObservedActionConfirmation(
  "chat.stop",
  { active: { sending: true, replyDisplayActive: true } },
  { active: { sending: false, replyDisplayActive: false } }
), true);
assert.equal(isObservedActionConfirmation(
  "music.togglePlayback",
  { active: { musicPlaying: true, musicPaused: false } },
  { active: { musicPlaying: false, musicPaused: true } }
), true);
assert.equal(isObservedActionConfirmation(
  "music.togglePlayback",
  {
    active: { musicPlaying: false, musicPaused: false },
    music: { systemMedia: { playbackStatus: "playing", isPlaying: true } }
  },
  {
    active: { musicPlaying: false, musicPaused: false },
    music: { systemMedia: { playbackStatus: "paused", isPlaying: false } }
  }
), true);
assert.equal(isObservedActionConfirmation(
  "chat.new",
  { state: { sessionId: "session-a" } },
  { state: { sessionId: "session-a" } }
), false);

assert.equal(isObservedActionConfirmation(
  "character.selectPack",
  { state: { characterPackId: "test_character" } },
  { state: { characterPackId: "second_character" } },
  { value: "second_character" }
), true);
assert.equal(isObservedActionConfirmation(
  "character.setOutfit",
  { state: { outfit: "default" } },
  { state: { outfit: "casual" } },
  { value: "casual" }
), true);
assert.equal(isObservedActionConfirmation(
  "character.previewEmotion",
  { currentExpression: { id: "happy" } },
  { currentExpression: { id: "thinking" }, settingsCommandResult: { command: "previewEmotion", operationId: "emotion-op-2", ok: true, status: "completed" } },
  { value: "thinking", operationId: "emotion-op-2" }
), true);
assert.equal(isObservedActionConfirmation(
  "character.selectPack",
  { state: { characterPackId: "test_character" } },
  { state: { characterPackId: "unexpected_character" } },
  { value: "second_character" }
), false);
assert.equal(isObservedActionConfirmation(
  "settings.selectBot",
  { state: { boundBotId: "bot-main" } },
  { state: { boundBotId: "bot-work" } },
  { value: "bot-work" }
), true);

const appearanceHtml = renderCharacterAppearance({
  viewModel,
  actionStates: {},
  phase: "ready"
});
assert.match(appearanceHtml, /另一位角色/);
assert.match(appearanceHtml, /character\.selectPack/);
assert.match(appearanceHtml, /data-action-value="second_character"/);
assert.match(appearanceHtml, /日常服装/);
assert.match(appearanceHtml, /data-action-value="casual"/);
assert.match(appearanceHtml, /character\.previewEmotion/);
assert.match(appearanceHtml, /data-theme-mode="system"/);
assert.match(appearanceHtml, /data-theme-mode="light"/);
assert.match(appearanceHtml, /data-accent-preset="sakura"/);
assert.match(appearanceHtml, /data-font-preset="rounded"/);
assert.match(appearanceHtml, /data-presentation-range="surfaceOpacity"/);
assert.match(appearanceHtml, /data-presentation-range="backgroundDim"/);
assert.match(appearanceHtml, /data-presentation-range="blurAmount"/);
assert.match(appearanceHtml, /data-presentation-toggle="reducedMotion"/);
assert.match(appearanceHtml, /data-framing-stage/);
assert.match(appearanceHtml, /data-framing-target="portrait"/);
assert.doesNotMatch(appearanceHtml, /Akane Default/);
assert.doesNotMatch(appearanceHtml, /<html[^>]*data-theme-mode/);

const normalizedPresentation = normalizePresentationPreferences({
  themeMode: "LIGHT",
  accentPreset: "sakura",
  fontPreset: "serif",
  surfaceOpacity: 20,
  backgroundDim: 95,
  blurAmount: 12.4,
  reducedMotion: true,
  frames: {
    avatar: { x: -20, y: 125, scale: 4 },
    portrait: { x: 80, y: 40, scale: 1.35 }
  }
});
assert.deepEqual(normalizedPresentation.frames.avatar, { x: 0, y: 100, scale: 2 });
assert.deepEqual(normalizedPresentation.frames.portrait, { x: 80, y: 60, scale: 1.35 });
assert.deepEqual(normalizedPresentation.frames.background, { x: 50, y: 50, scale: 1 });
assert.equal(normalizedPresentation.themeMode, "light");
assert.equal(normalizedPresentation.accentPreset, "sakura");
assert.equal(normalizedPresentation.fontPreset, "serif");
assert.equal(normalizedPresentation.surfaceOpacity, 55);
assert.equal(normalizedPresentation.backgroundDim, 80);
assert.equal(normalizedPresentation.blurAmount, 12.4);
assert.equal(normalizedPresentation.reducedMotion, true);
assert.equal(resolveThemeMode("system", true), "light");
assert.equal(resolveThemeMode("system", false), "dark");
assert.equal(resolveThemeMode("invalid", false), "dark");
assert.deepEqual(presentationCssVariables(normalizedPresentation), {
  "--cc-surface-alpha": "0.55",
  "--cc-background-dim": "0.8",
  "--cc-backdrop-blur": "12px",
  "--cc-avatar-x": "0%",
  "--cc-avatar-y": "100%",
  "--cc-avatar-size": "200%",
  "--cc-portrait-shift-x": "15%",
  "--cc-portrait-shift-y": "-18%",
  "--cc-portrait-scale": "1.35",
  "--cc-background-x": "50%",
  "--cc-background-y": "50%",
  "--cc-background-scale": "1"
});
const movedPresentation = updatePresentationFrame(normalizedPresentation, "background", { x: 72.5, scale: 1.2 });
assert.deepEqual(movedPresentation.frames.background, { x: 72.5, y: 50, scale: 1.2 });
assert.deepEqual(resetPresentationFrame(movedPresentation, "background").frames.background, { x: 50, y: 50, scale: 1 });

const storageValues = new Map();
const fakeStorage = {
  getItem(key) { return storageValues.has(key) ? storageValues.get(key) : null; },
  setItem(key, value) { storageValues.set(key, String(value)); },
  removeItem(key) { storageValues.delete(key); }
};
bindInstanceStorage("presentation-smoke");
assert.equal(savePresentationPreferences("pack-a", movedPresentation, { storage: fakeStorage }), true);
assert.deepEqual(loadPresentationPreferences("pack-a", { storage: fakeStorage }), movedPresentation);
assert.equal(loadPresentationPreferences("pack-b", { storage: fakeStorage }).frames.background.x, 50);
assert.equal(loadPresentationPreferences("pack-b", { storage: fakeStorage }).themeMode, "light");
assert.equal(loadPresentationPreferences("pack-b", { storage: fakeStorage }).accentPreset, "sakura");
assert.equal(loadPresentationPreferences("pack-b", { storage: fakeStorage }).fontPreset, "serif");
assert.equal(loadPresentationPreferences("pack-b", { storage: fakeStorage }).reducedMotion, true);

const throwingStorage = {
  getItem() { return null; },
  setItem() { throw new Error("quota_exceeded"); }
};
assert.equal(savePresentationPreferences("pack-a", movedPresentation, { storage: throwingStorage }), false);

const chatHtml = renderChat({
  viewModel,
  actionStates: {},
  chatHistory: { phase: "idle", error: "", sessionId: "session-chat" },
  phase: "ready"
});
assert.match(chatHtml, /下午的对话/);
assert.match(chatHtml, /今天一起做什么/);
assert.match(chatHtml, /先把界面做舒服/);
assert.match(chatHtml, /data-chat-viewport/);
assert.match(chatHtml, /data-chat-form/);
assert.match(chatHtml, /data-action="chat\.new"/);
assert.match(chatHtml, /data-chat-load-older/);
assert.match(chatHtml, /加载更早消息/);
assert.match(chatHtml, /最近一次调用回执/);
assert.match(chatHtml, /akane\.sample\.gentle-checkin\.schedule/);
assert.match(chatHtml, /已完成/);
assert.match(chatHtml, /1 个产物/);
assert.match(chatHtml, /文件产物/);
assert.doesNotMatch(chatHtml, /must-not-reach-ui|local_path|_debug/);
assert.doesNotMatch(chatHtml, /假消息|演示消息/);

const controlledJobSnapshot = structuredClone(rawSnapshot);
controlledJobSnapshot.chatSession.latest_final_json = {
  status: "accepted",
  tool_events: [{
    type: "background_job_accepted",
    tool_type: "akane.sample.long-task",
    status: "accepted",
    job_id: "job-control-1",
    job_status: "queued",
    control_state: "running"
  }]
};
const controlledJobViewModel = createControlCenterViewModel(controlledJobSnapshot, runtimeSnapshot);
assert.equal(controlledJobViewModel.chat.latestOutcome.events[0].jobId, "job-control-1");
assert.equal(controlledJobViewModel.chat.latestOutcome.events[0].jobStatus, "queued");
assert.equal(controlledJobViewModel.chat.latestOutcome.events[0].controlState, "running");
const controlledJobHtml = renderChat({
  viewModel: controlledJobViewModel,
  actionStates: {},
  chatJobControls: {},
  chatHistory: { phase: "idle", error: "", sessionId: "session-chat" },
  phase: "ready"
});
assert.match(controlledJobHtml, /data-chat-job-control="pause"/);
assert.match(controlledJobHtml, /data-chat-job-control="stop"/);
const pausedJobHtml = renderChat({
  viewModel: controlledJobViewModel,
  actionStates: {},
  chatJobControls: { "job-control-1": { jobStatus: "paused", controlState: "paused" } },
  chatHistory: { phase: "idle", error: "", sessionId: "session-chat" },
  phase: "ready"
});
assert.match(pausedJobHtml, /data-chat-job-control="resume"/);
assert.doesNotMatch(pausedJobHtml, /data-chat-job-control="pause"/);
const terminalJobHtml = renderChat({
  viewModel: controlledJobViewModel,
  actionStates: {},
  chatJobControls: { "job-control-1": { jobStatus: "succeeded", controlState: "running" } },
  chatHistory: { phase: "idle", error: "", sessionId: "session-chat" },
  phase: "ready"
});
assert.doesNotMatch(terminalJobHtml, /data-chat-job-control=/);

const controlRequests = [];
const controlSource = createBackendControlCenterSource({
  baseUrl: "http://127.0.0.1:9999",
  expectedInstanceId: "desktop-local",
  sessionId: "session-chat",
  profileUserId: "master",
  fetchImpl: async (url, init = {}) => {
    controlRequests.push({ url: String(url), init });
    if (String(url).includes("/health")) {
      return Response.json({ status: "ok", root_binding: "valid", instance_id: "desktop-local" });
    }
    return Response.json({ ok: true, status: "paused", reason: "host_job_paused", job_id: "job-control-1", job_status: "paused", control_state: "paused" });
  }
});
const controlResult = await controlSource.runAction("chat.jobControl", { jobId: "job-control-1", action: "pause" });
assert.equal(controlResult.ok, true);
assert.equal(controlResult.jobStatus, "paused");
assert.equal(controlResult.controlState, "paused");
const controlRequest = controlRequests.find((item) => item.url.includes("/admin/plugins/jobs/job-control-1/control"));
assert.ok(controlRequest);
assert.match(controlRequest.url, /profile_user_id=master/);
assert.match(controlRequest.url, /session_id=session-chat/);
assert.equal(controlRequest.init.method, "POST");
assert.deepEqual(JSON.parse(controlRequest.init.body), { action: "pause" });

const rejectedSnapshot = structuredClone(rawSnapshot);
rejectedSnapshot.chatSession.latest_final_json = {
  status: "policy_rejected",
  reason: "capability_disabled_by_policy",
  tool_events: [{
    tool_type: "akane.sample.gentle-checkin.schedule",
    status: "policy_rejected",
    is_error: true,
    reason: "capability_disabled_by_policy"
  }],
  value: { scheduled: false },
  _debug: { token: "must-not-reach-ui", cached_path: "C:/private/cache" }
};
const rejectedViewModel = createControlCenterViewModel(rejectedSnapshot, runtimeSnapshot);
assert.equal(rejectedViewModel.chat.latestOutcome.statusLabel, "策略阻止");
assert.equal(rejectedViewModel.chat.latestOutcome.tone, "danger");
const rejectedChatHtml = renderChat({
  viewModel: rejectedViewModel,
  actionStates: {},
  chatHistory: { phase: "idle", error: "", sessionId: "session-chat" },
  phase: "ready"
});
assert.match(rejectedChatHtml, /策略阻止/);
assert.match(rejectedChatHtml, /当前能力策略没有允许这项调用/);
assert.doesNotMatch(rejectedChatHtml, /must-not-reach-ui|cached_path|_debug/);
assert.match(renderChat({
  viewModel,
  actionStates: {},
  chatHistory: { phase: "loading", error: "", sessionId: "session-chat" },
  phase: "ready"
}), /正在加载更早消息/);
assert.match(renderChat({
  viewModel,
  actionStates: {},
  chatHistory: { phase: "failed", error: "网络暂时不可用", sessionId: "session-chat" },
  phase: "ready"
}), /网络暂时不可用[\s\S]*重试/);
assert.match(renderChat({
  viewModel: { ...viewModel, chat: { ...viewModel.chat, history: { pageKnown: true, hasMore: false, nextBeforeSeq: 0 } } },
  actionStates: {},
  chatHistory: { phase: "idle", error: "", sessionId: "session-chat" },
  phase: "ready"
}), /已到这轮对话的最早消息/);

const abilitiesHtml = renderAbilities({ viewModel, actionStates: {}, phase: "ready" });
assert.match(abilitiesHtml, /她现在能做什么/);
assert.match(abilitiesHtml, /文件与工作区/);
assert.match(abilitiesHtml, /data-approval-mode="ask_each_time"/);
assert.match(abilitiesHtml, /data-approval-mode="trusted_auto_allow"/);
assert.match(abilitiesHtml, /硬安全边界始终保留/);
assert.match(abilitiesHtml, /AnySearch/);
assert.match(abilitiesHtml, /data-capability-form="provider"/);
assert.match(abilitiesHtml, /data-capability-form="mcp"/);
assert.match(abilitiesHtml, /data-capability-form="workflow"/);
assert.match(abilitiesHtml, /data-action="abilities\.provider\.healthCheck"/);
assert.match(abilitiesHtml, /data-action="abilities\.mcp\.discover"/);
assert.match(abilitiesHtml, /Akane Host/);
assert.match(abilitiesHtml, /Host 启动命令/);
assert.match(abilitiesHtml, /data-action="abilities\.workflow\.validate"/);
assert.match(abilitiesHtml, /data-approval-request="approval_001"/);
assert.match(abilitiesHtml, /Skill 操作手册/);
assert.match(abilitiesHtml, /INSTALLED PLUGINS/);
assert.match(abilitiesHtml, /DEPENDENCY SUPPLY/);
assert.match(abilitiesHtml, /离线 wheelhouse/);
assert.match(abilitiesHtml, /akane\.sample\.gentle-checkin/);
assert.match(abilitiesHtml, /akane\.sample\.dependency-wait/);
assert.match(abilitiesHtml, /等待依赖/);
assert.match(abilitiesHtml, /依赖未满足/);
assert.match(abilitiesHtml, /执行策略与资源限额/);
assert.match(abilitiesHtml, /safe\.default/);
assert.match(abilitiesHtml, /4 KB/);
assert.match(abilitiesHtml, /akane\.missing/);
assert.match(abilitiesHtml, /桌宠/);
assert.match(abilitiesHtml, /QQ/);
assert.match(abilitiesHtml, /运行中/);
assert.match(abilitiesHtml, /data-action="abilities\.plugin\.disable"/);
assert.match(abilitiesHtml, /data-action-value="akane\.sample\.gentle-checkin"/);
assert.match(abilitiesHtml, /查看贡献与权限/);
assert.match(abilitiesHtml, /data-capability-key="plugin:akane\.sample\.gentle-checkin"/);
assert.match(abilitiesHtml, /运行代次/);
assert.match(abilitiesHtml, /可回滚至有效版本/);
assert.match(abilitiesHtml, /data-action="abilities\.plugin\.rollback"/);
assert.match(abilitiesHtml, /回滚版本/);
assert.match(abilitiesHtml, /data-capability-form="plugin-connection"/);
assert.match(abilitiesHtml, /data-plugin-id="akane\.sample\.gentle-checkin"/);
assert.match(abilitiesHtml, /data-connection-name="query_api"/);
assert.match(abilitiesHtml, /type="password"/);
assert.match(abilitiesHtml, /已配置，留空保持不变/);
assert.doesNotMatch(abilitiesHtml, /real-secret|installed-private|credential-value/);
assert.equal(viewModel.actions["abilities.plugin.connection.save"].available, true);
assert.match(abilitiesHtml, /akane\.sample\.gentle-checkin\.schedule/);
assert.match(abilitiesHtml, /agent\.event\.submit/);
assert.match(abilitiesHtml, /安装本地插件/);
assert.match(abilitiesHtml, /data-capability-form="plugin-stage"/);
assert.match(abilitiesHtml, /data-plugin-path-picker="abilities\.plugin\.pickSource"/);
assert.match(abilitiesHtml, /data-plugin-path-picker="abilities\.plugin\.pickWheel"/);
assert.match(abilitiesHtml, /选择源码目录/);
assert.match(abilitiesHtml, /选择 wheel/);
assert.match(abilitiesHtml, /data-action="abilities\.plugin\.stageSource"/);
assert.match(abilitiesHtml, /data-action="abilities\.plugin\.stageWheel"/);
assert.match(abilitiesHtml, /可选插件市场/);
assert.match(abilitiesHtml, /本地发行目录/);
assert.match(abilitiesHtml, /FFmpeg 和 FFprobe/);
assert.match(abilitiesHtml, /data-plugin-market-stage="akane\.media-convert"/);
assert.equal(viewModel.actions["abilities.plugin.stageMarket"].available, true);
const marketPendingHtml = renderAbilities({ viewModel, actionStates: { "abilities.plugin.stageMarket": { phase: "pending" } } });
assert.match(marketPendingHtml, /data-plugin-market-stage="akane\.media-convert" disabled/);
assert.match(marketPendingHtml, /data-action="abilities\.plugin\.stageSource" disabled/);
const marketFailedHtml = renderAbilities({ viewModel, actionStates: { "abilities.plugin.stageMarket": { phase: "failed", detail: "文件校验失败" } } });
assert.match(marketFailedHtml, /role="status">文件校验失败/);
for (const [runtime, message] of [
  [{ status: "loading" }, "正在读取市场目录"],
  [{ status: "unavailable", reason: "market_index_unavailable" }, "尚无可读取的市场目录"],
  [{ status: "available", data: { ok: true, plugins: [] } }, "此目录当前没有可选插件"]
]) {
  const marketVm = createControlCenterViewModel({ ...rawSnapshot, pluginMarketRuntime: runtime }, runtimeSnapshot);
  const html = renderAbilities({ viewModel: marketVm, actionStates: {} });
  assert.ok(html.includes(message));
  assert.doesNotMatch(html, /data-plugin-market-stage/);
}
const disconnectedMarketVm = createControlCenterViewModel({ ...rawSnapshot, sourceKind: "mock" }, runtimeSnapshot);
assert.equal(disconnectedMarketVm.abilities.plugins.market.entries.length, 0);
assert.equal(disconnectedMarketVm.actions["abilities.plugin.stageMarket"].available, false);
assert.match(abilitiesHtml, /akane\.sample\.timer/);
assert.match(abilitiesHtml, /akane\.sample\.timer\.schedule/);
assert.match(abilitiesHtml, /storage\.write/);
assert.match(abilitiesHtml, /待确认/);
assert.match(abilitiesHtml, /data-plugin-stage-install="stage-review-1"/);
assert.match(abilitiesHtml, /确认权限并安装/);
assert.match(abilitiesHtml, /发布制品并原子切换插件代次/);
assert.match(abilitiesHtml, /data-plugin-stage-discard="stage-review-1"/);
assert.match(abilitiesHtml, /丢弃候选/);
assert.match(abilitiesHtml, /data-plugin-uninstall="akane\.sample\.gentle-checkin"/);
assert.match(abilitiesHtml, /确认卸载/);
assert.match(abilitiesHtml, /移除当前 Bot 的托管制品与贡献/);
assert.match(abilitiesHtml, /coding-project/);
assert.match(abilitiesHtml, /personal-workflow/);
assert.match(abilitiesHtml, /data-action="abilities\.skills\.openFolder"/);
assert.match(abilitiesHtml, /只有填写新命令并保存时才会替换现有配置/);
assert.doesNotMatch(abilitiesHtml, /api_key|cached_path|local_path/);

const bundledPluginHtml = renderAbilities({
  viewModel: {
    ...viewModel,
    abilities: {
      ...viewModel.abilities,
      plugins: {
        ...viewModel.abilities.plugins,
        entries: viewModel.abilities.plugins.entries.map((entry) => ({ ...entry, source: "bundled" }))
      }
    }
  },
  actionStates: {},
  phase: "ready"
});
assert.doesNotMatch(bundledPluginHtml, /data-plugin-uninstall=/);

const remotePluginHtml = renderAbilities({
  viewModel: {
    ...viewModel,
    abilities: {
      ...viewModel.abilities,
      plugins: {
        ...viewModel.abilities.plugins,
        localPickerAvailable: false
      }
    }
  },
  actionStates: {},
  phase: "ready"
});
assert.match(remotePluginHtml, /远端宿主路径/);
assert.doesNotMatch(remotePluginHtml, /data-plugin-path-picker=/);

const remoteAbilitiesHtml = renderAbilities({
  viewModel: {
    ...viewModel,
    abilities: {
      ...viewModel.abilities,
      mcpServers: [{
        serverId: "remote-search",
        title: "Remote Search",
        status: "ready",
        statusLabel: "可用",
        statusTone: "ready",
        reason: "工具已发现",
        enabled: true,
        configured: true,
        transport: "streamable_http",
        executionLocation: "remote",
        executionLocationLabel: "远程服务",
        executionLocationDetail: "通过网络连接独立 MCP 服务。",
        safeToolLabels: ["检索资料"],
        toolCount: 1,
        actionsEnabled: true
      }]
    }
  },
  actionStates: {},
  phase: "ready"
});
assert.match(remoteAbilitiesHtml, /远程地址与认证不会在控制中心回显或替换/);
assert.match(remoteAbilitiesHtml, /远程配置请通过受信配置文件或后端配置接口管理/);
assert.doesNotMatch(remoteAbilitiesHtml, /Host 启动命令/);

const modelDraft = createModelServiceDraft(viewModel.model);
const modelHtml = renderModelService({ viewModel, actionStates: {}, phase: "ready", modelDraft, modelModels: [] });
assert.match(modelHtml, /把她连接到真正的模型/);
assert.match(modelHtml, /data-model-field="apiKey"/);
assert.match(modelHtml, /已保存，留空表示继续使用原密钥/);
assert.match(modelHtml, /data-action="model\.models"/);
assert.match(modelHtml, /data-action="model\.test"/);
assert.match(modelHtml, /data-action="model\.save"/);
assert.doesNotMatch(modelHtml, /secret|sk-/i);
assert.equal(modelServicePayload({ ...modelDraft, standaloneVision: false }).visionApiKey, undefined);

const modelActionCalls = [];
const modelSource = {
  async runModelServiceAction(operation, payload) {
    modelActionCalls.push({ operation, payload });
    return { ok: true, status: operation === "save" ? "configured" : "available", models: operation === "models" ? ["model-a"] : undefined };
  }
};
const modelProbeResult = await runModelServiceBridgeAction(modelSource, MODEL_SERVICE_ACTIONS.models, modelServicePayload(modelDraft));
assert.equal(modelProbeResult.ok, true);
assert.equal(modelProbeResult.refresh, false);
assert.deepEqual(modelProbeResult.models, ["model-a"]);
const modelSaveResult = await runModelServiceBridgeAction(modelSource, MODEL_SERVICE_ACTIONS.save, modelServicePayload(modelDraft));
assert.equal(modelSaveResult.status, "configured");
assert.equal(modelSaveResult.refresh, true);
assert.deepEqual(modelActionCalls.map((item) => item.operation), ["models", "save"]);

const openingVoiceVm = createControlCenterViewModel(rawSnapshot, {
  ...runtimeSnapshot, runtimeStatus: "", active: { voiceInput: "opening", speaking: false, sending: false }
});
const openingVoiceHtml = renderVoice({ viewModel: openingVoiceVm, actionStates: {}, phase: "ready" });
assert.match(openingVoiceHtml, /正在打开麦克风/);
assert.equal(openingVoiceVm.activity.label, "正在打开麦克风");

const voiceHtml = renderVoice({ viewModel, actionStates: {}, phase: "ready" });
assert.match(voiceHtml, /让她听见，也让她说出来/);
assert.match(voiceHtml, /GPT-SoVITS/);
assert.match(voiceHtml, /data-action="voice\.setTtsEnabled"/);
assert.match(voiceHtml, /data-action-value-type="boolean"/);
assert.match(voiceHtml, /data-voice-range="volume"/);
assert.match(voiceHtml, /data-wake-word-form/);
assert.match(voiceHtml, /data-voice-preview-form/);
assert.match(voiceHtml, /打开角色工坊/);
assert.match(voiceHtml, /角色声线/);
assert.match(voiceHtml, /灵梦 · 主声线/);
assert.match(voiceHtml, /角色包已绑定 · 档案可用/);
assert.match(voiceHtml, /data-voice-profile-action="test"/);
assert.match(voiceHtml, /data-voice-profile-action="clear"/);
assert.match(voiceHtml, /data-capability-form="voice-profile"/);
assert.match(voiceHtml, /data-action="abilities\.provider\.voiceProfile\.inspectFolder"/);
assert.match(voiceHtml, /data-action="abilities\.provider\.voiceProfile\.save"/);
assert.match(voiceHtml, /保存只证明配置已写入/);
assert.match(voiceHtml, /高级推理参数/);
assert.match(voiceHtml, /name="topK"/);
assert.match(voiceHtml, /name="topP"/);
assert.match(voiceHtml, /权重不会自动切换/);
assert.match(voiceHtml, /情绪参考音频/);
assert.match(voiceHtml, /emotionId\.new/);
assert.match(voiceHtml, /1 个情绪样本/);
assert.doesNotMatch(voiceHtml, /C:\\voices\\reimu_ref\.wav/);
assert.doesNotMatch(voiceHtml, /portrait|hero-image|character-preview/);

const systemHtml = renderSystem({ viewModel, actionStates: {}, phase: "ready" });
assert.match(systemHtml, /先看结论，再处理问题/);
assert.match(systemHtml, /核心服务/);
assert.match(systemHtml, /data-action="perception\.runDiagnostics"/);
assert.match(systemHtml, /data-action="advanced\.setHitTestEnabled"/);
assert.match(systemHtml, /data-action="advanced\.setHitboxOverlay"/);
assert.doesNotMatch(systemHtml, /RECENT EVENTS|没有结构化事件来源|以后接入宿主事件流/);
assert.doesNotMatch(systemHtml, /Health check passed|Backend connected|Live2D|api_key|local_path|cached_path/);

const chatRequests = [];
// Backend selection failures reach the actual voice renderer without an Edge fallback claim.
for (const [status, reason, expected] of [
  ["disabled", "text_only_requested", "角色使用仅文字回复"],
  ["unavailable", "requested_voice_profile_missing", "角色声线档案未配置，保留文字回复"]
]) {
  const source = createBackendControlCenterSource({
    expectedInstanceId: "voice-selection-test",
    fetchImpl: async (url) => {
      const pathname = new URL(url).pathname;
      const data = pathname === "/health"
        ? { status: "ok", root_binding: "valid", instance_id: "voice-selection-test" }
        : pathname.endsWith("/capabilities") ? { ok: true, capabilities: [], resolutions: {
          "voice.tts.character": {
            capabilityId: "voice.tts.character", status, reason,
            requestedProviderId: status === "disabled" ? "provider.voice.text_only" : "provider.tts.gpt_sovits.local",
            activeProviderId: "", fallbackProviderId: ""
          }
        } } : null;
      return new Response(JSON.stringify(data || {}), { status: data ? 200 : 404 });
    }
  });
  const snapshot = await source.readSnapshot();
  const provider = snapshot.voiceRuntime.tts.providerStatus;
  assert.equal(provider.activeProviderId, "");
  assert.equal(provider.fallbackProviderId, "");
  const voiceVm = createControlCenterViewModel({ ...rawSnapshot, voiceRuntime: snapshot.voiceRuntime }, runtimeSnapshot);
  const html = renderVoice({ viewModel: voiceVm, actionStates: {} });
  assert.ok(html.includes(expected), html);
  assert.doesNotMatch(html, /先使用兜底通道/);
  assert.notEqual(voiceVm.voice.tts.provider.statusLabel, "待确认");
  assert.equal(voiceVm.voice.tts.provider.ready, false);
}
const chatSource = createBackendControlCenterSource({
  baseUrl: "http://127.0.0.1:9999",
  expectedInstanceId: "desktop-local",
  sessionId: "session-original",
  profileUserId: "master",
  characterPackId: "test_character",
  fetchImpl: async (url, init = {}) => {
    chatRequests.push({ url: String(url), init });
    if (String(url).includes("/health")) {
      return new Response(JSON.stringify({ status: "ok", root_binding: "valid", instance_id: "desktop-local" }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    }
    const payload = String(url).includes("/sessions/messages")
      ? {
          session_id: "session-live",
          messages: [{ source_id: "older-1", seq_no: 1, role: "user", content: "更早历史" }],
          message_page: { limit: 60, has_more: false, next_before_seq: null }
        }
      : {
          session: { session_id: "session-live" },
          messages: [{ role: "assistant", content: "真实历史" }]
        };
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  }
});
const liveChatSession = await chatSource.readChatSession({ sessionId: "session-live", characterPackId: "second_character" });
assert.equal(liveChatSession.messages[0].content, "真实历史");
const chatRequestBody = JSON.parse(chatRequests.find((item) => item.url.includes("/sessions/ensure")).init.body);
assert.equal(chatRequestBody.session_id, "session-live");
assert.equal(chatRequestBody.character_pack_id, "second_character");
const olderChatPage = await chatSource.readChatHistoryPage({
  sessionId: "session-live",
  characterPackId: "second_character",
  beforeSeq: 12,
  limit: 60
});
assert.equal(olderChatPage.messages[0].content, "更早历史");
const olderChatRequest = chatRequests.find((item) => item.url.includes("/sessions/messages"));
assert.match(olderChatRequest.url, /before_seq=12/);
assert.match(olderChatRequest.url, /limit=60/);
assert.match(olderChatRequest.url, /character_pack_id=second_character/);

const loadedHistory = {
  session: { session_id: "session-merge" },
  messages: Array.from({ length: 60 }, (_, index) => ({ source_id: `message-${index + 1}`, seq_no: index + 1 })),
  message_page: { has_more: false, next_before_seq: null }
};
const refreshedTail = {
  session: { session_id: "session-merge" },
  messages: Array.from({ length: 60 }, (_, index) => ({ source_id: `message-${index + 41}`, seq_no: index + 41 })),
  message_page: { has_more: true, next_before_seq: 41 }
};
const mergedAfterRefresh = mergeChatSessions(loadedHistory, refreshedTail);
assert.deepEqual(mergedAfterRefresh.messages.map((item) => item.seq_no), Array.from({ length: 100 }, (_, index) => index + 1));
assert.equal(mergedAfterRefresh.message_page.has_more, false, "live refresh must preserve the already loaded oldest window");
const mergedOlderPage = mergeChatSessions(refreshedTail, {
  session: { session_id: "session-merge" },
  messages: Array.from({ length: 40 }, (_, index) => ({ source_id: `message-${index + 1}`, seq_no: index + 1 })),
  message_page: { has_more: false, next_before_seq: null }
}, { preferIncomingPage: true });
assert.deepEqual(mergedOlderPage.messages.map((item) => item.seq_no), Array.from({ length: 100 }, (_, index) => index + 1));
assert.equal(mergedOlderPage.message_page.has_more, false);
assert.equal(prependedHistoryScrollTop({ scrollHeight: 800, scrollTop: 36 }, 1280), 516);

// Actual bridge scheduling: a slow market must neither block the main view nor
// publish an old Bot's catalog after a source change or bridge shutdown.
const savedWindow = globalThis.window;
const pendingMarkets = [];
globalThis.window = {
  location: { search: "?backend=http://market-a" }, setTimeout, clearTimeout,
  fetch: async (url) => {
    const path = new URL(url).pathname;
    if (path === "/plugins/market") return new Promise((resolve) => pendingMarkets.push(resolve));
    if (path === "/health") return Response.json({ status: "ok", root_binding: "valid", instance_id: "local-default" });
    if (path === "/plugins/catalog") return Response.json({ ok: true, status: "active", plugins: [] });
    if (path === "/admin/plugins/status") return Response.json({ management: { status: "ready", supports: ["stage_market"] }, artifacts: { stages: [] } });
    return Response.json({ ok: true });
  }
};
const marketBridge = createControlCenterBridge({ isTauri: false });
const marketPublished = [];
marketBridge.subscribe((vm) => marketPublished.push(vm.abilities.plugins.market));
const marketResponse = (name) => Response.json({ ok: true, source_kind: "https", plugins: [{
  plugin_id: name, version: "1", display_name: name, sha256: "b".repeat(64), size_bytes: 1
}] });
try {
  await marketBridge.start();
  assert.equal(pendingMarkets.length, 1);
  assert.equal(marketPublished.at(-1).status, "loading", "main snapshot must publish before the slow market");
  window.location.search = "?backend=http://market-b";
  await marketBridge.start();
  assert.equal(pendingMarkets.length, 2);
  pendingMarkets[0](marketResponse("old.bot"));
  await new Promise(setImmediate);
  assert.equal(marketPublished.at(-1).entries.length, 0, "old source result must not enter new Bot");
  pendingMarkets[1](marketResponse("new.bot"));
  await new Promise(setImmediate);
  assert.equal(marketPublished.at(-1).entries[0].pluginId, "new.bot");
  await marketBridge.refresh();
  marketBridge.stop();
  const beforeStop = marketPublished.length;
  pendingMarkets[2](marketResponse("late.bot"));
  await new Promise(setImmediate);
  assert.equal(marketPublished.length, beforeStop);
} finally {
  marketBridge.stop();
  if (savedWindow === undefined) delete globalThis.window;
  else globalThis.window = savedWindow;
}

console.log("control-center V2 smoke passed");
