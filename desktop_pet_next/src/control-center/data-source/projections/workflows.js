import { asObject, stringValue } from "../shared.js";
import { mapCapabilityStatus } from "./capability-common.js";

export function buildCatalogWorkflowCards(entries) {
  return entries
    .filter((entry) => entry.kind === "workflow")
    .map((entry) => {
      const status = mapWorkflowStatus(entry.status);
      const slotMapping = asObject(entry.slotMapping);
      return {
        id: entry.id,
        workflowId: entry.id,
        steps: workflowSteps(entry),
        title: workflowTitle(entry),
        detail: workflowDetail(entry),
        statusLabel: status.label,
        statusTone: status.tone,
        enabled: Boolean(entry.enabled),
        configured: Boolean(entry.configured),
        executionReady: Boolean(entry.executionReady),
        workflowPath: entry.workflowPath,
        defaultWorkflowPath: entry.defaultWorkflowPath,
        inputImageSlot: stringValue(slotMapping.input_image_handle),
        outputImageSlot: stringValue(slotMapping.output_image_handle),
        actionsEnabled: entry.configurable !== false
      };
    });
}

function workflowSteps(entry) {
  if (entry.capabilityId === "workshop.portrait.cutout") {
    return ["立绘", "处理", "透明PNG"];
  }
  if (entry.type === "tts_provider") {
    return ["文本", "声线", "语音"];
  }
  return ["输入", "处理", "输出"];
}

function workflowTitle(entry) {
  if (entry.capabilityId === "workshop.portrait.cutout") return "透明背景处理";
  return entry.name || "本地工作流";
}

function workflowDetail(entry) {
  const status = entry.status || "";
  if (status === "missing_config") return "需要先配置本地执行环境";
  if (status === "missing_workflow") return "本地服务已保存，等待绑定具体工作流";
  if (status === "missing_slot_mapping") return "工作流已选择，仍需补齐输入输出槽位";
  if (status === "unreachable") return "本地服务暂时未连接";
  if (status === "invalid_config") return "配置需要修复后才能使用";
  if (status === "invalid_workflow_config") return "工作流绑定配置需要修复";
  if (status === "disabled") return "已配置但未启用";
  if (status === "configured") return "工作流绑定已保存，执行入口尚未开放";
  if (status === "ready") return "工作流已可供相关页面调用";
  return entry.description || "等待本地能力同步";
}

function mapWorkflowStatus(status) {
  const normalized = stringValue(status);
  if (normalized === "configured" || normalized === "validated_config") {
    return { label: "已绑定", tone: "warning" };
  }
  if (normalized === "missing_slot_mapping") {
    return { label: "待补齐", tone: "warning" };
  }
  if (normalized === "invalid_workflow_config") {
    return { label: "配置异常", tone: "danger" };
  }
  return mapCapabilityStatus(status);
}
