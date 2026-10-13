import { escapeHtml } from "../dom.js";
import { createModelServiceDraft, MODEL_SERVICE_ACTIONS } from "../model-service.js";
import { actionPhase, renderActionButton } from "./action-button.js";

export function renderModelService(state) {
  const vm = state.viewModel;
  const model = vm?.model;
  if (!model?.connected) {
    return `
      <section class="empty-state glass-panel">
        <span aria-hidden="true">◌</span>
        <h2>模型服务暂时不可读取</h2>
        <p>连接桌宠后，这里会读取本机模型配置；密钥不会从后端返回到界面。</p>
        <button class="action-button is-primary" type="button" data-refresh><span>↻</span><b>重新连接</b></button>
      </section>`;
  }
  if (!model.available) {
    return `
      <section class="empty-state glass-panel">
        <span aria-hidden="true">!</span>
        <h2>模型配置接口暂时不可用</h2>
        <p>桌宠已经连接，但没有读取到模型服务配置。刷新后仍失败时，请检查后端版本。</p>
        <button class="action-button is-primary" type="button" data-refresh><span>↻</span><b>重新读取</b></button>
      </section>`;
  }

  const draft = state.modelDraft || createModelServiceDraft(model);
  const provider = model.providers.find((item) => item.id === draft.providerId) || model.providers[0];
  const busy = Object.values(MODEL_SERVICE_ACTIONS).some((actionId) => ["pressed", "pending"].includes(actionPhase(state, actionId)));
  const models = Array.from(new Set([...(state.modelModels || []), draft.chatModel].filter(Boolean)));
  const visionModels = Array.from(new Set([...(draft.standaloneVision ? state.modelVisionModels || [] : state.modelModels || []), draft.visionModel].filter(Boolean)));
  const subagentDraft = draft.subagent || { mode: "inherit" };
  const subagentProvider = model.providers.find((item) => item.id === subagentDraft.providerId) || model.providers[0];
  const subagentModels = Array.from(new Set([...(state.modelSubagentModels || []), subagentDraft.model].filter(Boolean)));

  const statusDetail = model.configured
    ? `${provider?.label || draft.providerId} · ${draft.chatModel || "未选择模型"}`
    : "填写并测试后即可开始对话";
  const apiKeyPlaceholder = model.hasApiKey
    ? "已保存，留空表示继续使用原密钥"
    : provider?.apiKeyRequired === false
      ? "本地服务通常无需填写"
      : "粘贴服务商提供的 API Key";

  const subagentApiKeyPlaceholder = model.subagent?.hasApiKey
    ? "已保存，留空表示继续使用原密钥"
    : subagentProvider?.apiKeyRequired === false
      ? "本地服务通常无需填写"
      : "粘贴服务商提供的 API Key";

  return `
    <section class="ccv2-model" aria-labelledby="ccv2-page-title">
      <div class="model-heading">
        <div><p class="eyebrow">MODEL SERVICE</p><h2>把她连接到真正的模型</h2><p>读取和保存都走现有本机配置接口；密钥输入仅用于本次提交，不会被读取接口回显。</p></div>
        <span class="model-status-chip is-${model.configured ? "ready" : "warning"}">${model.configured ? "已配置" : "等待配置"}</span>
      </div>

      <section class="model-health glass-panel">
        <span class="model-health-mark">${model.configured ? "✓" : "◇"}</span>
        <div><p class="eyebrow">CURRENT CONNECTION</p><h3>${model.configured ? "模型服务已配置" : "还需要完成配置"}</h3><p>${escapeHtml(statusDetail)}</p></div>
        <dl><div><dt>配置来源</dt><dd>${model.source === "local_file" ? "控制中心" : "系统默认"}</dd></div><div><dt>协议</dt><dd>${escapeHtml(draft.protocol || "openai")}</dd></div></dl>
      </section>

      <div class="model-layout">
        <form class="model-config glass-panel" data-model-form>
          <div class="model-panel-head"><div><p class="eyebrow">BASIC CONFIG</p><h3>连接主模型服务</h3></div><span class="mini-chip">本机配置</span></div>
          <div class="model-form-grid">
            <label class="is-wide"><span>服务商</span><select data-model-field="providerId" ${busy ? "disabled" : ""}>${model.providers.map((item) => `<option value="${escapeHtml(item.id)}"${item.id === draft.providerId ? " selected" : ""}>${escapeHtml(item.label)}</option>`).join("")}</select><small>${escapeHtml(provider?.description || "兼容 OpenAI Chat Completions 的服务也可以使用。")}</small></label>
            <label class="is-wide"><span>Base URL</span><input data-model-field="baseUrl" type="url" value="${escapeHtml(draft.baseUrl)}" placeholder="https://api.example.com/v1" autocomplete="url" ${busy ? "disabled" : ""}/><small>填写服务根地址或 /v1，不要填到 /chat/completions。</small></label>
            <label class="is-wide"><span>API Key</span><input data-model-field="apiKey" type="password" value="${escapeHtml(draft.apiKey)}" placeholder="${escapeHtml(apiKeyPlaceholder)}" autocomplete="new-password" ${busy ? "disabled" : ""}/><small>读取接口只返回“是否已保存”，不会返回密钥内容。</small></label>
            <label><span>聊天模型</span><input data-model-field="chatModel" type="text" list="ccv2-model-options" value="${escapeHtml(draft.chatModel)}" placeholder="检测后选择，或手动填写" ${busy ? "disabled" : ""}/><datalist id="ccv2-model-options">${models.map((item) => `<option value="${escapeHtml(item)}"></option>`).join("")}</datalist></label>
            <label><span>请求超时</span><input data-model-field="timeoutSeconds" data-model-value-type="number" type="number" min="5" max="600" value="${escapeHtml(draft.timeoutSeconds)}" ${busy ? "disabled" : ""}/><small>单位：秒</small></label>
            <label><span>主服务协议</span><select data-model-field="protocol" ${busy ? "disabled" : ""}>${protocolOptions(draft.protocol)}</select></label>
            <label><span>主模型思考模式</span><select data-model-field="chatThinkingMode" ${busy ? "disabled" : ""}>${thinkingOptions(draft.chatThinkingMode)}</select><small>启用或关闭适用于支持此控制的服务。</small></label>
            <label><span>主模型思考强度</span><select data-model-field="chatReasoningEffort" ${busy ? "disabled" : ""}>${reasoningOptions(draft.chatReasoningEffort)}</select><small>由服务商支持的档位决定；与子代理分别保存。</small></label>
            <label><span>主模型最大输出 Token</span><input data-model-field="chatMaxOutputTokens" data-model-value-type="number" type="number" min="0" max="131072" value="${escapeHtml(draft.chatMaxOutputTokens || 0)}" ${busy ? "disabled" : ""}/><small>0 表示使用默认预算。</small></label>
          </div>

          <div class="model-toggle-list">
            ${model.hasApiKey ? renderToggle("clearApiKey", "清除已保存的 API Key", "仅在确定要移除现有密钥时开启。", draft.clearApiKey, busy, "danger") : ""}
          </div>

          ${renderVisionSection(draft, model, visionModels, busy, state, vm)}

          <div class="model-actions">
            ${renderActionButton(state, vm, MODEL_SERVICE_ACTIONS.models, "↻", "检测主模型", "default")}
            ${renderActionButton(state, vm, MODEL_SERVICE_ACTIONS.test, "✓", "测试主模型 API", "default")}
          </div>

          <div class="model-panel-head" style="margin-top: 24px; padding-top: 16px; border-top: 1px solid rgba(255, 255, 255, 0.08);">
            <div><p class="eyebrow">SUBAGENT MODEL</p><h3>子代理通用模型配置</h3></div>
            <span class="mini-chip">${subagentDraft.mode === "independent" ? "独立服务" : subagentDraft.mode === "shared_service" ? "复用主服务" : "跟随主模型"}</span>
          </div>

          <div class="model-form-grid">
            <label class="is-wide"><span>子模型模式</span><select data-subagent-field="mode" ${busy ? "disabled" : ""}>
              <option value="inherit"${subagentDraft.mode === "inherit" ? " selected" : ""}>跟随主模型（继承主服务及思考设置）</option>
              <option value="shared_service"${subagentDraft.mode === "shared_service" ? " selected" : ""}>复用主服务（独立指定子模型与思考参数）</option>
              <option value="independent"${subagentDraft.mode === "independent" ? " selected" : ""}>独立服务商（使用专属的 API 路由与密钥）</option>
            </select><small>${escapeHtml(subagentModeDescription(subagentDraft.mode))}</small></label>
          </div>

          ${renderSubagentSection(subagentDraft, model, subagentProvider, subagentApiKeyPlaceholder, subagentModels, busy, state, vm)}

          <div class="model-actions" style="margin-top: 24px; padding-top: 16px; border-top: 1px solid rgba(255, 255, 255, 0.08);">
            ${renderActionButton(state, vm, MODEL_SERVICE_ACTIONS.save, "✓", "保存并应用配置", "primary")}
          </div>
          <p class="model-action-hint">${escapeHtml(modelActionHint(state))}</p>
        </form>

        <aside class="model-guide glass-panel">
          <p class="eyebrow">FIRST SETUP</p><h3>只需要三样东西</h3>
          <ol><li><strong>服务商</strong><span>选择官方、本地 Ollama 或兼容中转。</span></li><li><strong>API Key</strong><span>Ollama 以外通常需要。</span></li><li><strong>模型名</strong><span>优先检测，不支持列表接口时手动填写。</span></li></ol>
          <div class="model-privacy-note"><i>✓</i><span><strong>密钥不会出现在快照和日志里</strong><small>保存后，控制中心只知道“已经配置”，不会重新读回密钥。</small></span></div>
        </aside>
      </div>
    </section>`;
}

function renderVisionSection(draft, model, models, busy, state, vm) {
  return `
    <div class="model-panel-head" style="margin-top: 24px; padding-top: 16px; border-top: 1px solid rgba(255, 255, 255, 0.08);">
      <div><p class="eyebrow">VISION MODEL</p><h3>图片与屏幕理解</h3></div>
      <span class="mini-chip">${!draft.useForVision ? "已关闭" : draft.standaloneVision ? "独立视觉服务" : "复用主服务"}</span>
    </div>
    <div class="model-toggle-list">
      ${renderToggle("useForVision", "启用视觉模型", "看图时使用下方选择的模型和连接。", draft.useForVision, busy)}
      ${draft.useForVision ? renderToggle("standaloneVision", "使用独立视觉服务", "关闭时复用主模型的地址、协议和密钥。", draft.standaloneVision, busy) : ""}
    </div>
    ${draft.useForVision ? `
      <div class="model-form-grid">
        ${draft.standaloneVision ? `
          <label class="is-wide"><span>视觉 Base URL</span><input data-model-field="visionBaseUrl" type="url" value="${escapeHtml(draft.visionBaseUrl)}" placeholder="https://api.example.com/v1" ${busy ? "disabled" : ""}/></label>
          <label><span>视觉协议</span><select data-model-field="visionApiProtocol" ${busy ? "disabled" : ""}>${["openai", "responses", "anthropic", "gemini", "ollama"].map(item => `<option value="${item}"${item === draft.visionApiProtocol ? " selected" : ""}>${item}</option>`).join("")}</select></label>
          <label class="is-wide"><span>视觉 API Key</span><input data-model-field="visionApiKey" type="password" value="${escapeHtml(draft.visionApiKey)}" placeholder="${model.hasVisionApiKey ? "已保存，地址与协议不变时可留空" : "填写视觉服务密钥"}" autocomplete="new-password" ${busy ? "disabled" : ""}/></label>
        ` : `<p class="model-action-hint">当前复用：${escapeHtml(draft.baseUrl)} · ${escapeHtml(draft.protocol)}</p>`}
        <label class="is-wide"><span>视觉模型${draft.standaloneVision ? "" : "（可选）"}</span><input data-model-field="visionModel" type="text" list="ccv2-vision-model-options" value="${escapeHtml(draft.visionModel)}" placeholder="${draft.standaloneVision ? "检测视觉服务后选择，或手动填写" : "留空则使用聊天模型"}" ${busy ? "disabled" : ""}/><datalist id="ccv2-vision-model-options">${models.map(item => `<option value="${escapeHtml(item)}"></option>`).join("")}</datalist><small>模型名必须由当前视觉服务提供。</small></label>
      </div>
      <div class="model-actions">
        ${renderActionButton(state, vm, MODEL_SERVICE_ACTIONS.visionModels, "↻", "检测视觉模型", "default")}
        ${renderActionButton(state, vm, MODEL_SERVICE_ACTIONS.visionTest, "✓", "测试视觉 API", "default")}
      </div>
      <p class="model-action-hint">视觉测试会发送一张小色块图片，验证实际看图链路。</p>
    ` : ""}`;
}

function thinkingOptions(selected) {
  return [["", "默认（系统设置）"], ["auto", "自动"], ["enabled", "启用"], ["disabled", "关闭"]]
    .concat(selected === "default" ? [["default", "default"]] : [])
    .map(([value, label]) => `<option value="${value}"${(selected || "") === value ? " selected" : ""}>${label}</option>`).join("");
}

function protocolOptions(selected) {
  return ["openai", "responses", "anthropic", "gemini", "ollama"]
    .map(value => `<option value="${value}"${selected === value ? " selected" : ""}>${value}</option>`).join("");
}

function reasoningOptions(selected) {
  return ["", "none", "minimal", "low", "medium", "high", "xhigh", "max"]
    .map(value => `<option value="${value}"${(selected || "") === value ? " selected" : ""}>${value || "默认（系统设置）"}</option>`).join("");
}

function subagentModeDescription(mode) {
  if (mode === "independent") return "子代理任务将通过完全独立的 provider、地址和 API Key 执行。";
  if (mode === "shared_service") return "子代理复用当前主模型的 API 密钥和网关，但可以使用独立的子模型与思考预算。";
  return "子代理默认继承主模型的地址、密钥、模型标识和思考预算。";
}

function renderSubagentSection(draft, model, provider, apiKeyPlaceholder, models, busy, state, vm) {
  if (draft.mode === "inherit") {
    return `
      <div class="model-subagent-inherited-note" style="padding: 12px 14px; background: rgba(255, 255, 255, 0.03); border-radius: 8px; font-size: 0.85rem; color: rgba(255, 255, 255, 0.7);">
        <span>✓ 已设为跟随主模型。所有子任务将继承主模型的 API 连接与思考参数，无需额外配置。</span>
      </div>`;
  }

  const isIndependent = draft.mode === "independent";

  return `
    <div class="model-subagent-fields">
      ${isIndependent ? `
        <div class="model-form-grid" style="margin-top: 12px;">
          <label class="is-wide"><span>子代理服务商</span><select data-subagent-field="providerId" ${busy ? "disabled" : ""}>${model.providers.map((item) => `<option value="${escapeHtml(item.id)}"${item.id === draft.providerId ? " selected" : ""}>${escapeHtml(item.label)}</option>`).join("")}</select><small>${escapeHtml(provider?.description || "")}</small></label>
          <label class="is-wide"><span>子代理 Base URL</span><input data-subagent-field="baseUrl" type="url" value="${escapeHtml(draft.baseUrl)}" placeholder="https://api.example.com/v1" autocomplete="url" ${busy ? "disabled" : ""}/></label>
          <label><span>子服务协议</span><select data-subagent-field="protocol" ${busy ? "disabled" : ""}>${protocolOptions(draft.protocol)}</select></label>
          <label class="is-wide"><span>子代理 API Key</span><input data-subagent-field="apiKey" type="password" value="${escapeHtml(draft.apiKey)}" placeholder="${escapeHtml(apiKeyPlaceholder)}" autocomplete="new-password" ${busy ? "disabled" : ""}/></label>
        </div>` : ""}

      <div class="model-form-grid" style="margin-top: 12px;">
        <label><span>子模型名称</span><input data-subagent-field="model" type="text" list="ccv2-subagent-model-options" value="${escapeHtml(draft.model)}" placeholder="检测后选择或手动填写" ${busy ? "disabled" : ""}/><datalist id="ccv2-subagent-model-options">${models.map((item) => `<option value="${escapeHtml(item)}"></option>`).join("")}</datalist></label>
        <label><span>子模型思考模式</span><select data-subagent-field="thinkingMode" ${busy ? "disabled" : ""}>${thinkingOptions(draft.thinkingMode)}</select></label>
        <label><span>子模型思考强度</span><select data-subagent-field="reasoningEffort" ${busy ? "disabled" : ""}>${reasoningOptions(draft.reasoningEffort)}</select></label>
        <label><span>最大输出 Token 预算</span><input data-subagent-field="maxOutputTokens" data-model-value-type="number" type="number" min="0" max="131072" value="${escapeHtml(draft.maxOutputTokens || "")}" placeholder="0 (默认)" ${busy ? "disabled" : ""}/><small>0 表示使用服务商默认值</small></label>
        <label><span>单次请求超时</span><input data-subagent-field="timeoutSeconds" data-model-value-type="number" type="number" min="5" max="600" value="${escapeHtml(draft.timeoutSeconds)}" ${busy ? "disabled" : ""}/><small>单位：秒</small></label>
      </div>

      ${isIndependent && model.subagent?.hasApiKey ? `
        <div class="model-toggle-list" style="margin-top: 12px;">
          <button class="model-toggle${draft.clearApiKey ? " is-on is-danger" : ""}" type="button" data-subagent-toggle="clearApiKey" aria-pressed="${draft.clearApiKey ? "true" : "false"}" ${busy ? "disabled" : ""}><span><strong>清除子代理已保存的 API Key</strong><small>仅在确定要移除独立密钥时开启。</small></span><i></i></button>
        </div>` : ""}

      <div class="model-actions" style="margin-top: 12px;">
        ${renderActionButton(state, vm, MODEL_SERVICE_ACTIONS.subagentModels, "↻", isIndependent ? "检测独立模型" : "检测主服务子模型", "default")}
        ${renderActionButton(state, vm, MODEL_SERVICE_ACTIONS.subagentTest, "✓", "测试子模型 API", "default")}
      </div>
    </div>`;
}

function renderToggle(field, title, description, enabled, busy, tone = "") {
  return `<button class="model-toggle${enabled ? " is-on" : ""}${tone ? ` is-${tone}` : ""}" type="button" data-model-toggle="${field}" aria-pressed="${enabled ? "true" : "false"}" ${busy ? "disabled" : ""}><span><strong>${escapeHtml(title)}</strong><small>${escapeHtml(description)}</small></span><i></i></button>`;
}

function modelActionHint(state) {
  const entries = Object.entries(MODEL_SERVICE_ACTIONS).map(([operation, actionId]) => [operation, state.actionStates?.[actionId]]).filter(([, value]) => value);
  const [operation, latest] = entries.at(-1) || [];
  if (!latest) return "建议先检测模型，选好后测试 API，最后保存并应用。";
  if (["pressed", "pending"].includes(latest.phase)) {
    if (operation === "subagentModels") return "正在读取子代理模型列表…";
    if (operation === "subagentTest") return "正在验证子代理连接…";
    if (operation === "visionModels") return "正在读取视觉模型列表…";
    if (operation === "visionTest") return "正在验证图片理解…";
    if (operation === "models") return "正在读取主模型列表…";
    if (operation === "test") return "正在验证主模型连接…";
    if (operation === "save") return "正在保存并刷新模型服务…";
    return "正在执行操作…";
  }
  return latest.detail || latest.label || "操作已结束";
}
