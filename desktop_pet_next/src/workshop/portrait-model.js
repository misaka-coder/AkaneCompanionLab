import { formatError } from "./shared.js";

export function normalizePortraitCutoutCapability(workflow) {
  const status = String(workflow?.status || "").trim() || "unknown";
  const reason = String(workflow?.reason || "").trim();
  const reasonDetail = describePortraitCutoutReason(reason);
  const labels = {
    configured: "已绑定，等待执行器",
    validated_config: "已绑定，等待执行器",
    ready: "自动抠图已可用",
    missing_config: "需要配置本地 ComfyUI",
    missing_workflow: "需要绑定抠图工作流",
    missing_slot_mapping: "需要补齐输入输出槽位",
    disabled: "自动抠图绑定未启用",
    unreachable: "ComfyUI 暂时未连接",
    invalid_config: "本地能力配置异常",
    invalid_workflow_config: "工作流绑定异常",
  };
  const details = {
    configured: "配置已经保存，但后端还没有绑定真实执行器。",
    validated_config: "配置已经通过基础校验，但后端还没有绑定真实执行器。",
    ready: "先在右侧预览一张表情图，再点击自动抠图生成透明背景版本。",
    missing_config: "可以先到设置的能力页填写本地 ComfyUI 地址并做探活。",
    missing_workflow: "本地服务已配置，下一步是在能力页绑定透明背景处理工作流。",
    missing_slot_mapping: "工作流引用已保存，还需要补齐输入图片和输出图片的槽位名。",
    disabled: "绑定存在但未启用，可以到能力页重新启用。",
    unreachable: "请确认 ComfyUI 正在运行，然后在能力页重新探活。",
    invalid_config: "能力配置文件需要修复，立绘管理本身不会受影响。",
    invalid_workflow_config: "工作流绑定需要修复，立绘管理本身不会受影响。",
  };
  return {
    status,
    label: labels[status] || "能力状态待确认",
    detail: reasonDetail || details[status] || "立绘管理可继续使用；自动处理入口会等真实执行边界完成后开放。",
    configured: Boolean(workflow?.configured),
    enabled: Boolean(workflow?.enabled),
    executionReady: Boolean(workflow?.executionReady),
    canConfigure: workflow?.configurable !== false,
    reason,
  };
}

export function portraitCutoutTone(status) {
  if (status === "ready") return "ready";
  if (status === "configured" || status === "validated_config") return "configured";
  if (status === "invalid_config" || status === "invalid_workflow_config") return "error";
  if (status === "missing_config" || status === "missing_workflow" || status === "missing_slot_mapping" || status === "disabled" || status === "unreachable") {
    return "attention";
  }
  return "unknown";
}

export function buildGeneratedCutoutEmotionId(emotionId) {
  const base = String(emotionId || "normal").trim() || "normal";
  return base.endsWith("_cutout") ? base : `${base}_cutout`;
}

export function formatPortraitCutoutError(error) {
  const raw = formatError(error);
  return describePortraitCutoutReason(raw) || raw;
}

export function describePortraitCutoutReason(reason) {
  const key = String(reason || "").trim();
  const messages = {
    workflow_file_missing: "找不到工作流 JSON。请把文件放到当前用户能力目录下，例如 capabilities/workflows/comfyui/portrait_cutout.json。",
    workflow_file_invalid_json: "工作流 JSON 解析失败，请确认导出的是 ComfyUI API workflow JSON。",
    workflow_file_invalid_encoding: "工作流文件编码无法读取，请保存为 UTF-8 JSON。",
    workflow_file_too_large: "工作流文件过大，请检查是否误放了图片或模型文件。",
    workflow_file_unreadable: "工作流文件暂时无法读取，请检查文件是否被占用或权限不足。",
    workflow_json_invalid: "工作流内容不是有效的 ComfyUI 工作流对象。",
    workflow_path_must_be_safe_relative_json: "工作流引用只能填写能力目录内的相对 JSON 路径。",
    workflow_path_required: "还没有填写工作流 JSON 引用。",
    required_slot_mapping_missing: "还需要填写输入图片和输出文件名的 ComfyUI 节点槽位。",
    slot_mapping_path_invalid: "槽位需要填写成 ComfyUI 节点输入路径，例如 12.inputs.image。",
    slot_mapping_node_missing: "槽位指向的节点在工作流 JSON 里不存在。",
    slot_mapping_inputs_missing: "槽位指向的节点没有 inputs 区域，请换成可写入的输入节点。",
    slot_mapping_target_missing: "槽位指向的输入字段不存在，请核对节点编号和字段名。",
    input_image_bytes_required: "没有读到当前预览立绘的图片数据，请重新点选一张表情图。",
    workflow_slot_mapping_invalid: "工作流槽位映射失败，请核对节点路径是否仍然匹配当前 JSON。",
    comfyui_request_failed: "ComfyUI 请求失败，请确认服务正在运行，且工作流依赖节点和模型可用。",
    workflow_runtime_config_invalid: "工作流运行配置无效，请回到能力页检查 ComfyUI 地址、JSON 文件和槽位。",
    workflow_runner_failed: "本地工作流执行失败，请检查 ComfyUI 控制台输出。",
    workflow_job_failed: "工作流任务失败，请检查能力配置和 ComfyUI 状态。",
    workflow_job_timeout: "工作流处理超时，可以稍后重试或检查 ComfyUI 是否卡住。",
    workflow_output_not_found: "工作流完成了，但没有找到可导入的输出图片。",
    workflow_job_output_not_ready: "输出还没准备好，请稍后再试。",
    connection_failed: "无法连接本地 ComfyUI，请确认它正在运行。",
    provider_unavailable: "本地 ComfyUI 暂时不可用，请在能力页重新探活。",
  };
  return messages[key] || "";
}

export function inferEmotionNameFromFileName(fileName, fallback = "normal") {
  const baseName = String(fileName || "")
    .replace(/\\/g, "/")
    .split("/")
    .pop()
    .replace(/\.[^.]+$/, "")
    .trim();
  return baseName || fallback;
}

export function portraitMimeType(path) {
  const extension = String(path || "").split(".").pop().toLowerCase();
  if (extension === "jpg" || extension === "jpeg") return "image/jpeg";
  if (extension === "webp") return "image/webp";
  return "image/png";
}
