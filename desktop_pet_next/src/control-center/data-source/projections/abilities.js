import {
  asArray,
  asObject,
  formatTimeOfDay,
  normalizeStringList,
  numberOrFallback,
  positiveNumber,
  stringValue
} from "../shared.js";
import {
  capabilityEntryText,
  mergeWorkspaceCounts,
  normalizeCapabilityCatalogEntries,
  summarizeCapabilityCatalogEntries,
  summarizeCapabilityModuleStatus
} from "./capability-common.js";
import {
  buildAbilitySafetyPanel,
  buildSafetyDescription,
  countPendingSafetyItems,
  normalizeApprovalPolicyEntry,
  normalizeApprovalRequestsCatalog
} from "./approval.js";
import { buildAbilityProviderCards } from "./providers.js";
import { buildAbilityMcpServerCards } from "./mcp.js";
import { buildCatalogWorkflowCards } from "./workflows.js";
import { inferLatencyLabel } from "./metrics.js";

export function buildAbilitiesRuntimePatch({ diagnostics, workspace, capabilitiesCatalog, voiceProfilesCatalog, approvalRequestsCatalog, executionPolicy = null, resourcePolicy = null, connected }) {
  const diagnosticsData = asObject(diagnostics);
  const capabilities = asObject(diagnosticsData.capabilities);
  const catalogEntries = normalizeCapabilityCatalogEntries(capabilitiesCatalog);
  const approvalPolicy = normalizeApprovalPolicyEntry(capabilitiesCatalog?.approvalPolicy);
  const approvalRequests = normalizeApprovalRequestsCatalog(approvalRequestsCatalog);
  const catalogSummary = summarizeCapabilityCatalogEntries(catalogEntries);
  const safety = asObject(diagnosticsData.safety);
  const runtime = asObject(diagnosticsData.runtime);
  const runtimeMetrics = asObject(runtime.metrics);
  const workspaceCounts = asObject(diagnosticsData.workspace);
  const workspaceDataCounts = asObject(workspace?.counts);
  const tools = normalizeStringList(capabilities.tool_names || capabilities.toolNames);
  const effectiveModules = normalizeStringList(capabilities.effective_modules || capabilities.effectiveModules);
  const declared = normalizeStringList(capabilities.declared);
  const serviceOk = Boolean(connected) || stringValue(diagnosticsData.status) === "ok";
  const moduleCards = catalogEntries.length
    ? buildCapabilityCatalogModuleCards({ entries: catalogEntries, workspaceCounts, workspaceDataCounts, safety })
    : buildAbilityModuleCards({ tools, workspaceCounts, workspaceDataCounts, safety });
  const availableModuleCount = moduleCards.length;
  const toolCount = catalogEntries.length ? catalogSummary.total : tools.length;
  const pendingApprovalCount = catalogEntries.length ? catalogSummary.needsAttention : countPendingSafetyItems(safety);
  const activeApprovalCount = positiveNumber(approvalRequests.pendingCount);
  const availability = serviceOk
    ? (catalogEntries.length ? catalogSummary.availability : Math.min(100, Math.max(0, toolCount ? 98 : 72)))
    : 0;
  const syncedAt = diagnosticsData.server_time
    ? formatTimeOfDay(new Date(Number(diagnosticsData.server_time) * 1000))
    : formatTimeOfDay(new Date());
  const overviewStats = catalogEntries.length
    ? [
        { label: "能力模块", value: String(availableModuleCount) },
        { label: "可用能力", value: String(catalogSummary.ready) },
        { label: activeApprovalCount ? "待确认" : "待完善", value: String(activeApprovalCount || catalogSummary.needsAttention) }
      ]
    : [
        { label: "可用模块", value: String(availableModuleCount || effectiveModules.length || declared.length || 0) },
        { label: "可用能力", value: String(toolCount) },
        { label: "需审批权限", value: String(pendingApprovalCount) }
      ];

  return {
    overview: {
      stats: overviewStats,
      availability,
      note: buildAbilityOverviewNote({ serviceOk, catalogEntries, catalogSummary })
    },
    modules: moduleCards,
    providers: buildAbilityProviderCards(catalogEntries, capabilitiesCatalog, voiceProfilesCatalog),
    mcpServers: buildAbilityMcpServerCards(catalogEntries),
    workflows: buildCatalogWorkflowCards(catalogEntries).slice(0, 3),
    skills: buildAbilitySkillCatalog(capabilitiesCatalog?.skills),
    calls: buildAbilityStatusRows({
      syncedAt,
      serviceOk,
      toolCount,
      moduleCount: availableModuleCount,
      catalogSummary,
      workspaceCounts: mergeWorkspaceCounts(workspaceCounts, workspaceDataCounts),
      safety,
      approvalRequests,
      approvalPolicy,
      runtimeMetrics
    }),
    safety: buildAbilitySafetyPanel(safety, serviceOk, approvalRequests, approvalPolicy),
    execution: {
      policy: executionPolicy,
      resource: resourcePolicy
    }
  };
}

function buildAbilitySkillCatalog(value) {
  const payload = asObject(value);
  const summary = asObject(payload.summary);
  const entries = asArray(payload.entries).map((item) => {
    const entry = asObject(item);
    return {
      name: stringValue(entry.name),
      description: stringValue(entry.description),
      source: stringValue(entry.source),
      revision: stringValue(entry.revision),
      requiredTools: normalizeStringList(entry.requiredTools),
      resourceCount: positiveNumber(entry.resourceCount)
    };
  }).filter((item) => item.name && item.description);
  const diagnostics = asArray(payload.diagnostics).map((item) => {
    const entry = asObject(item);
    return {
      name: stringValue(entry.name),
      source: stringValue(entry.source),
      reason: stringValue(entry.reason),
      fallback: stringValue(entry.fallback)
    };
  }).filter((item) => item.name || item.reason);
  return {
    status: stringValue(payload.status || "unavailable"),
    catalogRevision: stringValue(payload.catalogRevision),
    total: positiveNumber(summary.total ?? entries.length),
    bundled: positiveNumber(summary.bundled ?? entries.filter((item) => item.source === "bundled").length),
    managed: positiveNumber(summary.managed ?? entries.filter((item) => item.source === "managed").length),
    entries,
    diagnostics
  };
}

function buildAbilityOverviewNote({ serviceOk, catalogEntries, catalogSummary }) {
  if (!serviceOk) return "等待后端连接，能力状态暂不可用";
  if (!catalogEntries.length) return "能力注册表已同步，当前模块可正常使用";
  if (catalogSummary.needsAttention > 0) {
    return `能力目录已同步，${catalogSummary.needsAttention} 项能力等待配置或执行环境`;
  }
  return "能力目录已同步，当前能力状态良好";
}

function buildCapabilityCatalogModuleCards({ entries, workspaceCounts, workspaceDataCounts, safety }) {
  const definitions = [
    {
      title: "记忆与人设",
      description: "长期记忆 / 人设资料",
      permission: "个人记忆与资料",
      tone: "blue",
      icon: "sparkle",
      match: (entry) => capabilityEntryText(entry).match(/memory|persona|retrieve/)
    },
    {
      title: "文件与工作区",
      description: "材料整理 / 文件读写 / 文件交付",
      permission: "工作区文件访问",
      tone: "orange",
      icon: "folder",
      match: (entry) => capabilityEntryText(entry).match(/workspace|attachment|generated_files|file_handoff|gift|artifact|inventory|send_file|inspect_attachment/)
    },
    {
      title: "文档处理",
      description: "阅读资料 / 生成报告 / 修改文稿",
      permission: "文档读写",
      tone: "purple",
      icon: "file",
      match: (entry) => capabilityEntryText(entry).match(/documents|compose|revise|style|section|document/)
    },
    {
      title: "音频与语音",
      description: "朗读 / 识别 / 音频处理",
      permission: "麦克风与音频",
      tone: "green",
      icon: "mic",
      match: (entry) => capabilityEntryText(entry).match(/tts|asr|audio|voice|media|transcribe|stems|ffmpeg|ffprobe|faster_whisper/)
    },
    {
      title: "本地模型与执行器",
      description: "转码 / 抠图 / 本地模型工具",
      permission: "本地运行环境",
      tone: "pink",
      icon: "cube",
      match: (entry) => entry.source === "external_executor" || entry.executionMode === "external" || capabilityEntryText(entry).match(/comfyui|gpt_sovits|rvc|demucs|ffmpeg|ffprobe/)
    },
    {
      title: "桌面感知",
      description: "窗口上下文 / 看屏幕 / 主动唤醒",
      permission: "桌面上下文",
      tone: "blue",
      icon: "eye",
      match: (entry) => capabilityEntryText(entry).match(/desktop|screen|vision|clipboard|context|proactive/)
    },
    {
      title: "安全与契约",
      description: "权限确认 / 风险隔离 / 能力契约",
      permission: "安全与确认",
      tone: "pink",
      icon: "shield",
      match: (entry) => entry.requiresConfirmation || entry.approvalMode === "ask_each_time" || entry.risk === "high" || capabilityEntryText(entry).match(/guard|safe|security|approval|sandbox/)
    }
  ];

  const cards = [];
  for (const definition of definitions) {
    const matched = entries.filter((entry) => definition.match(entry));
    const fallbackCount = definition.title === "文件与工作区"
      ? positiveNumber(workspaceCounts.files) + positiveNumber(workspaceDataCounts.files)
      : definition.title === "安全与契约"
        ? countPendingSafetyItems(safety)
        : 0;
    if (!matched.length && !fallbackCount) continue;
    const status = summarizeCapabilityModuleStatus(matched);
    const count = matched.length || fallbackCount;
    cards.push({
      title: definition.title,
      description: definition.description,
      permission: definition.permission,
      count: `${count} 项能力`,
      tone: definition.tone,
      icon: definition.icon,
      statusLabel: status.label,
      statusTone: status.tone
    });
  }

  if (!cards.length) {
    return buildAbilityModuleCards({
      tools: entries.map((entry) => entry.toolType || entry.id || entry.name).filter(Boolean),
      workspaceCounts,
      workspaceDataCounts,
      safety
    });
  }
  return cards.slice(0, 8);
}

function buildAbilityModuleCards({ tools, workspaceCounts, workspaceDataCounts, safety }) {
  const definitions = [
    {
      title: "文件处理",
      description: "读取 / 整理 / 转换",
      permission: "受限文件访问",
      tone: "blue",
      icon: "folder",
      pattern: /file|attachment|document|read|inspect|sync_attachment/i
    },
    {
      title: "生成文件交付",
      description: "文档 / 报告 / 表格",
      permission: "生成与导出",
      tone: "purple",
      icon: "file",
      pattern: /compose|send_file|generated|delivery|handoff/i
    },
    {
      title: "手边物品",
      description: "材料 / 成果",
      permission: "工作区管理",
      tone: "orange",
      icon: "gift",
      pattern: /workspace|gift|clipboard/i,
      extraCount: numberOrFallback(workspaceCounts.files, workspaceDataCounts.files, 0)
    },
    {
      title: "媒体工具",
      description: "转写 / 分离 / 转码",
      permission: "多媒体操作",
      tone: "green",
      icon: "play",
      pattern: /media|audio|voice|transcribe|stems|clean/i
    },
    {
      title: "记忆检索",
      description: "长期记忆 / 上下文",
      permission: "记忆读取",
      tone: "blue",
      icon: "sparkle",
      pattern: /memory|retrieve/i
    },
    {
      title: "安全边界",
      description: "权限 / 审批 / 保护",
      permission: "安全与隔离",
      tone: "pink",
      icon: "shield",
      pattern: /guard|safe|security|approval/i,
      fallbackCount: countPendingSafetyItems(safety)
    }
  ];

  const cards = [];
  for (const definition of definitions) {
    const count = tools.filter((name) => definition.pattern.test(name)).length + positiveNumber(definition.extraCount);
    const fallbackCount = positiveNumber(definition.fallbackCount);
    const abilityCount = count || fallbackCount;
    if (!abilityCount && definition.title !== "安全边界") continue;
    cards.push({
      title: definition.title,
      description: definition.description,
      permission: definition.permission,
      count: `${abilityCount || 1} 项能力`,
      tone: definition.tone,
      icon: definition.icon
    });
  }

  if (!cards.length) {
    cards.push({
      title: "能力注册表",
      description: "等待后端同步可用能力模块。",
      permission: "待连接",
      count: "0 项能力",
      tone: "blue",
      icon: "sparkle"
    });
  }
  return cards.slice(0, 8);
}

function buildAbilityStatusRows({ syncedAt, serviceOk, toolCount, moduleCount, catalogSummary, workspaceCounts, safety, approvalRequests, approvalPolicy, runtimeMetrics }) {
  const hasCatalog = Boolean(catalogSummary?.total);
  const pendingApprovalCount = positiveNumber(approvalRequests?.pendingCount);
  const rows = [
    {
      time: syncedAt,
      module: "能力注册表",
      description: serviceOk
        ? (hasCatalog
            ? `已同步 ${moduleCount} 个模块、${catalogSummary.ready}/${catalogSummary.total} 项能力可用`
            : `已同步 ${moduleCount} 个模块、${toolCount} 项能力`)
        : "等待后端同步能力注册表",
      status: serviceOk ? (catalogSummary?.needsAttention ? "需配置" : "成功") : "待连接",
      duration: inferLatencyLabel(runtimeMetrics, ["capabilities.catalog"]),
      method: hasCatalog ? "能力目录" : "后端诊断"
    }
  ];
  const files = numberOrFallback(workspaceCounts.files, 0);
  const outputs = numberOrFallback(workspaceCounts.outputs, 0);
  if (files || outputs) {
    rows.push({
      time: syncedAt,
      module: "手边物品",
      description: `当前工作区：${files} 个文件、${outputs} 个生成文件`,
      status: "成功",
      duration: "-",
      method: "状态同步"
    });
  }
  rows.push({
    time: syncedAt,
    module: "安全边界",
    description: buildSafetyDescription(safety, approvalRequests, approvalPolicy),
    status: pendingApprovalCount ? "待确认" : safety?.secrets_exposed ? "已拦截" : "成功",
    duration: "-",
    method: pendingApprovalCount ? "审批队列" : "策略检查"
  });
  return rows;
}

export function buildRecentOutputsPatch(workspace) {
  const sections = asObject(workspace?.data?.sections || workspace?.sections);
  const outputs = Array.isArray(sections.outputs) ? sections.outputs : [];
  return outputs
    .map((item) => {
      const title = String(item.title || item.name || "").trim();
      const status = String(item.status || "").trim();
      if (!title || status === "failed") return null;
      return {
        id: String(item.handle || item.id || "").trim(),
        handle: String(item.handle || "").trim(),
        title,
        subtitle: String(item.subtitle || item.format || "").trim(),
        kind: String(item.kind || "file").trim().toLowerCase(),
        format: String(item.format || item.file_ext || "").trim(),
        status,
        updatedAt: Number(item.updated_at || item.updatedAt || 0)
      };
    })
    .filter(Boolean)
    .slice(0, 3);
}
