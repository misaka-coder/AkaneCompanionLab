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
  const statusDetail = model.configured
    ? `${provider?.label || draft.providerId} · ${draft.chatModel || "未选择模型"}`
    : "填写并测试后即可开始对话";
  const apiKeyPlaceholder = model.hasApiKey
    ? "已保存，留空表示继续使用原密钥"
    : provider?.apiKeyRequired === false
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
          <div class="model-panel-head"><div><p class="eyebrow">BASIC CONFIG</p><h3>连接模型服务</h3></div><span class="mini-chip">本机配置</span></div>
          <div class="model-form-grid">
            <label class="is-wide"><span>服务商</span><select data-model-field="providerId" ${busy ? "disabled" : ""}>${model.providers.map((item) => `<option value="${escapeHtml(item.id)}"${item.id === draft.providerId ? " selected" : ""}>${escapeHtml(item.label)}</option>`).join("")}</select><small>${escapeHtml(provider?.description || "兼容 OpenAI Chat Completions 的服务也可以使用。")}</small></label>
            <label class="is-wide"><span>Base URL</span><input data-model-field="baseUrl" type="url" value="${escapeHtml(draft.baseUrl)}" placeholder="https://api.example.com/v1" autocomplete="url" ${busy ? "disabled" : ""}/><small>填写服务根地址或 /v1，不要填到 /chat/completions。</small></label>
            <label class="is-wide"><span>API Key</span><input data-model-field="apiKey" type="password" value="${escapeHtml(draft.apiKey)}" placeholder="${escapeHtml(apiKeyPlaceholder)}" autocomplete="new-password" ${busy ? "disabled" : ""}/><small>读取接口只返回“是否已保存”，不会返回密钥内容。</small></label>
            <label><span>聊天模型</span><input data-model-field="chatModel" type="text" list="ccv2-model-options" value="${escapeHtml(draft.chatModel)}" placeholder="检测后选择，或手动填写" ${busy ? "disabled" : ""}/><datalist id="ccv2-model-options">${models.map((item) => `<option value="${escapeHtml(item)}"></option>`).join("")}</datalist></label>
            <label><span>请求超时</span><input data-model-field="timeoutSeconds" data-model-value-type="number" type="number" min="5" max="600" value="${escapeHtml(draft.timeoutSeconds)}" ${busy ? "disabled" : ""}/><small>单位：秒</small></label>
          </div>

          <div class="model-toggle-list">
            ${renderToggle("useForVision", "同时用于图片与屏幕理解", "主模型支持视觉时开启；也可以单独指定视觉模型。", draft.useForVision, busy)}
            ${draft.useForVision ? `<label class="model-inline-field"><span>视觉模型（可选）</span><input data-model-field="visionModel" type="text" list="ccv2-model-options" value="${escapeHtml(draft.visionModel)}" placeholder="留空则使用聊天模型" ${busy ? "disabled" : ""}/></label>` : ""}
            ${renderToggle("standaloneVision", "使用独立视觉服务", "聊天和看图走不同服务时再开启。", draft.standaloneVision, busy)}
            ${draft.useForVision && draft.standaloneVision ? `
              <div class="model-vision-grid">
                <label><span>视觉 Base URL</span><input data-model-field="visionBaseUrl" type="url" value="${escapeHtml(draft.visionBaseUrl)}" placeholder="https://api.example.com/v1" ${busy ? "disabled" : ""}/></label>
                <label><span>视觉协议</span><select data-model-field="visionApiProtocol" ${busy ? "disabled" : ""}>${["openai", "responses", "anthropic", "gemini", "ollama"].map((item) => `<option value="${item}"${item === draft.visionApiProtocol ? " selected" : ""}>${item}</option>`).join("")}</select></label>
                <label class="is-wide"><span>视觉 API Key</span><input data-model-field="visionApiKey" type="password" value="${escapeHtml(draft.visionApiKey)}" placeholder="${model.hasVisionApiKey ? "已保存，留空表示继续使用" : "填写视觉服务密钥"}" autocomplete="new-password" ${busy ? "disabled" : ""}/></label>
              </div>` : ""}
            ${model.hasApiKey ? renderToggle("clearApiKey", "清除已保存的 API Key", "仅在确定要移除现有密钥时开启。", draft.clearApiKey, busy, "danger") : ""}
          </div>

          <div class="model-actions">
            ${renderActionButton(state, vm, MODEL_SERVICE_ACTIONS.models, "↻", "检测模型", "default")}
            ${renderActionButton(state, vm, MODEL_SERVICE_ACTIONS.test, "✓", "测试 API", "default")}
            ${renderActionButton(state, vm, MODEL_SERVICE_ACTIONS.save, "✓", "保存并应用", "primary")}
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

function renderToggle(field, title, description, enabled, busy, tone = "") {
  return `<button class="model-toggle${enabled ? " is-on" : ""}${tone ? ` is-${tone}` : ""}" type="button" data-model-toggle="${field}" aria-pressed="${enabled ? "true" : "false"}" ${busy ? "disabled" : ""}><span><strong>${escapeHtml(title)}</strong><small>${escapeHtml(description)}</small></span><i></i></button>`;
}

function modelActionHint(state) {
  const entries = Object.entries(MODEL_SERVICE_ACTIONS).map(([operation, actionId]) => [operation, state.actionStates?.[actionId]]).filter(([, value]) => value);
  const [operation, latest] = entries.at(-1) || [];
  if (!latest) return "建议先检测模型，选好后测试 API，最后保存并应用。";
  if (["pressed", "pending"].includes(latest.phase)) return operation === "models" ? "正在读取模型列表…" : operation === "test" ? "正在验证连接…" : "正在保存并刷新模型服务…";
  return latest.detail || latest.label || "操作已结束";
}
