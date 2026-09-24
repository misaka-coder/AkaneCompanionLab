import { escapeHtml } from "../dom.js";
import { actionPhase, renderActionButton } from "./action-button.js";

const SPEEDS = ["0.85x", "1.00x", "1.15x", "1.30x"];
const SENSITIVITIES = ["低", "中等", "高"];

export function renderVoice(state) {
  const vm = state.viewModel;
  const voice = vm?.voice;
  if (!voice?.available) {
    return `
      <section class="empty-state glass-panel">
        <span aria-hidden="true">◖</span>
        <h2>语音状态还没有同步</h2>
        <p>${escapeHtml(vm?.shell?.connected ? "后端已连接，但暂时没有返回语音运行状态。" : "连接桌宠后，这里会显示真实语音能力和控制项。")}</p>
        <button class="action-button is-primary" type="button" data-refresh><span>↻</span><b>重新同步</b></button>
      </section>`;
  }

  const controlsDisabled = !voice.controlsAvailable;
  return `
    <section class="ccv2-voice" aria-labelledby="ccv2-page-title">
      <div class="voice-heading">
        <div><p class="eyebrow">VOICE & LISTENING</p><h2>让她听见，也让她说出来</h2><p>日常开关留在上面，角色声线在下方完成检查、试听与绑定。</p></div>
        <span class="voice-live-pill is-${voice.speaking ? "speaking" : voice.inputState === "recording" ? "listening" : "idle"}"><i></i>${escapeHtml(voiceStateLabel(voice))}</span>
      </div>

      ${controlsDisabled ? `<div class="voice-readonly-note"><span>只读状态</span><p>${escapeHtml(voice.controlsUnavailableReason)}</p></div>` : ""}

      <div class="voice-layout">
        <div class="voice-primary-column">
          <section class="voice-console glass-panel">
            <div class="voice-console-head">
              <div><p class="eyebrow">SPEECH OUTPUT</p><h3>回复朗读</h3></div>
              ${renderVoiceToggle(state, vm, "voice.setTtsEnabled", voice.tts.enabled, "TTS")}
            </div>
            <div class="voice-wave" data-active="${voice.speaking ? "true" : "false"}" aria-label="${voice.speaking ? "正在播放语音" : "语音当前空闲"}">
              ${Array.from({ length: 28 }, (_, index) => `<i style="--wave-index:${index};--wave-height:${10 + (index % 7) * 3}px"></i>`).join("")}
            </div>
            <div class="voice-runtime-summary">
              <span><small>当前通道</small><strong>${escapeHtml(voice.tts.provider.name)}</strong></span>
              <span><small>播放状态</small><strong>${escapeHtml(voice.speaking ? "正在说话" : voice.tts.enabled ? "等待文本" : "已关闭")}</strong></span>
              <span><small>待播队列</small><strong>${escapeHtml(String(voice.queueLength))}</strong></span>
            </div>
            <label class="voice-range-row">
              <span><strong>播放音量</strong><small data-voice-range-value="volume">${voice.tts.volume}%</small></span>
              <input type="range" min="0" max="100" step="1" value="${voice.tts.volume}" data-voice-range="volume" ${controlsDisabled ? "disabled" : ""}>
            </label>
            <div class="voice-choice-row">
              <span><strong>语速</strong><small>保存后应用于下一段语音</small></span>
              <div class="voice-segments" role="group" aria-label="语速">${SPEEDS.map((speed) => renderChoiceButton("voice.setSpeed", speed, speed === voice.tts.speed, controlsDisabled)).join("")}</div>
            </div>
          </section>

          <section class="voice-listen-card glass-panel">
            <div class="voice-console-head">
              <div><p class="eyebrow">SPEECH INPUT</p><h3>语音输入与唤醒</h3></div>
              ${renderVoiceToggle(state, vm, "voice.setAsrEnabled", voice.asr.enabled, "ASR")}
            </div>
            <div class="voice-provider-inline ${voice.asr.provider.ready ? "is-ready" : ""}"><i></i><span><strong>${escapeHtml(voice.asr.provider.name)}</strong><small>${escapeHtml(voice.asr.provider.reason || voice.asr.provider.statusLabel)}</small></span><em>${escapeHtml(inputStateLabel(voice.inputState))}</em></div>
            <form class="wake-word-form" data-wake-word-form>
              <label><span>唤醒词</span><input type="text" maxlength="32" value="${escapeHtml(voice.wakeWord)}" data-wake-word-input ${controlsDisabled ? "disabled" : ""}></label>
              <button class="action-button" type="submit" ${controlsDisabled ? "disabled" : ""}><span>✓</span><b>保存唤醒词</b></button>
            </form>
            <div class="voice-choice-row">
              <span><strong>唤醒灵敏度</strong><small>环境嘈杂时建议使用中等或低</small></span>
              <div class="voice-segments" role="group" aria-label="唤醒灵敏度">${SENSITIVITIES.map((value) => renderChoiceButton("voice.setWakeSensitivity", value, value === voice.wakeSensitivity, controlsDisabled)).join("")}</div>
            </div>
          </section>
        </div>

        <aside class="voice-side-column">
          <section class="voice-preview-card glass-panel">
            <div class="voice-panel-head"><div><p class="eyebrow">QUICK TEST</p><h3>听一下现在的声音</h3></div><span class="mini-chip">真实播放</span></div>
            <p>输入一小段文字，确认当前服务、声线和播放链路是否真的可用。</p>
            <form data-voice-preview-form>
              <textarea rows="4" maxlength="160" data-voice-preview-input placeholder="例如：你好呀，今天也请多关照。" ${controlsDisabled ? "disabled" : ""}></textarea>
              <div class="voice-preview-actions">
                ${renderActionButton(state, vm, "voice.previewPlay", voice.speaking ? "◼" : "▶", voice.speaking ? "正在播放" : "播放试听", "primary")}
                ${renderActionButton(state, vm, "voice.stop", "■", "停止播放", "soft")}
              </div>
            </form>
            <div class="voice-profile-route"><span>角色声线</span><p>当前角色的语音绑定仍写回角色包；这里提供同一条真实配置链路，不复制角色包权威。</p><a class="voice-inline-link" href="#voice-profile-center">管理角色声线 ↓</a></div>
          </section>

          <section class="voice-health-card glass-panel">
            <div class="voice-panel-head"><div><p class="eyebrow">SERVICE HEALTH</p><h3>语音链路</h3></div><button class="round-button" type="button" data-refresh aria-label="刷新语音状态">↻</button></div>
            <div class="voice-provider-grid">
              ${renderProvider("语音输出", voice.tts.provider)}
              ${renderProvider("语音输入", voice.asr.provider)}
            </div>
            ${voice.diagnostics.length ? `<dl class="voice-diagnostics">${voice.diagnostics.map((item) => `<div><dt>${escapeHtml(item.label)}</dt><dd class="is-${escapeHtml(item.tone)}">${escapeHtml(item.value)}</dd></div>`).join("")}</dl>` : `<div class="empty-inline"><span>没有更多诊断数据</span><small>刷新后仍为空时，请检查本地语音服务。</small></div>`}
          </section>
        </aside>
      </div>

      ${renderVoiceProfileCenter(state, vm)}
    </section>`;
}

function renderVoiceProfileCenter(state, vm) {
  const providers = vm.abilities.providers.filter((item) => item.adapter === "gpt_sovits" || item.type === "tts_provider" || item.voiceProfiles.length);
  const profiles = providers.flatMap((provider) => provider.voiceProfiles.map((profile) => ({ ...profile, provider })));
  const binding = vm.character.voice;
  const boundProfile = profiles.find((item) => item.voiceProfileId === binding.profileId);
  const suggestion = state.voiceProfileSuggestion || {};
  const activeProvider = providers.find((item) => item.id === suggestion.providerId) || providers[0];
  const currentLabel = binding.profileId ? (boundProfile?.name || binding.profileId) : "跟随默认语音";
  return `
    <section class="voice-profile-center" id="voice-profile-center" aria-labelledby="voice-profile-title">
      <div class="voice-profile-heading">
        <div><p class="eyebrow">CHARACTER VOICE</p><h3 id="voice-profile-title">角色声线</h3><p>先确认服务和声线真的可用，再绑定给当前角色。保存、试听、绑定是三个独立状态。</p></div>
        <span class="mini-chip">${escapeHtml(profiles.length ? `${profiles.length} 个档案` : "等待配置")}</span>
      </div>

      <div class="voice-profile-overview">
        <article class="voice-current-binding glass-panel">
          <div class="voice-current-mark" aria-hidden="true">♪</div>
          <span><small>${escapeHtml(vm.character.displayName)} 当前使用</small><strong>${escapeHtml(currentLabel)}</strong><em>${escapeHtml(binding.profileId ? (boundProfile?.configured ? "角色包已绑定 · 档案可用" : "角色包已绑定 · 档案需要检查") : "没有角色专属绑定，将使用可用的默认通道")}</em></span>
          ${binding.profileId ? renderProfileActionButton(state, vm, "abilities.provider.voiceProfile.clearCurrentCharacter", "清除绑定", { action: "clear", tone: "soft" }) : ""}
        </article>
        <article class="voice-service-summary glass-panel">
          <span><small>声线服务</small><strong>${escapeHtml(activeProvider?.title || "GPT-SoVITS 尚未接入")}</strong></span>
          <em class="is-${escapeHtml(activeProvider?.statusTone || "warning")}">${escapeHtml(activeProvider?.statusLabel || "待配置")}</em>
          <p>${escapeHtml(activeProvider?.reason || activeProvider?.description || "请先在能力页配置真实的本地语音服务。")}</p>
          ${renderActionButton(state, vm, "character.openWorkshop", "↗", "打开角色工坊", "soft")}
        </article>
      </div>

      ${profiles.length ? `<div class="voice-profile-grid">${profiles.map((item) => renderVoiceProfileCard(state, vm, item, binding.profileId)).join("")}</div>` : `<div class="voice-profile-empty glass-panel"><span>还没有声线档案</span><p>可以从 GPT-SoVITS 模型目录读取建议配置，也可以手动填写参考音频和提示文本。</p></div>`}

      ${activeProvider ? renderVoiceProfileEditor(state, vm, activeProvider, suggestion) : `
        <div class="voice-profile-missing glass-panel">
          <div><strong>当前没有可配置的语音 Provider</strong><p>先在“能力与权限”中接通本地 GPT-SoVITS，Voice 页不会制造一个看似可用的假入口。</p></div>
          <button class="action-button is-soft" type="button" data-page="abilities"><span>↗</span><b>前往能力配置</b></button>
        </div>`}
    </section>`;
}

function renderVoiceProfileCard(state, vm, item, boundProfileId) {
  const bound = item.voiceProfileId === boundProfileId;
  const meta = [item.referenceAudioName, item.emotionSampleCount ? `${item.emotionSampleCount} 个情绪样本` : "", item.textLang && `${item.textLang}/${item.promptLang}`, item.mediaType.toUpperCase()].filter(Boolean);
  return `<article class="voice-profile-card glass-panel${bound ? " is-bound" : ""}">
    <div class="voice-profile-card-head"><span><small>${escapeHtml(item.provider.title)}</small><strong>${escapeHtml(item.name)}</strong></span><em class="is-${escapeHtml(item.statusTone)}">${escapeHtml(bound ? "当前绑定" : item.statusLabel)}</em></div>
    <p>${escapeHtml(meta.join(" · ") || item.reason || "声线资料等待同步")}</p>
    <div class="voice-profile-card-actions">
      ${renderProfileActionButton(state, vm, "abilities.provider.ttsTest", "试听", { action: "test", providerId: item.provider.id, voiceProfileId: item.voiceProfileId, disabled: !item.enabled })}
      ${bound ? "" : renderProfileActionButton(state, vm, "abilities.provider.voiceProfile.assignToCurrentCharacter", "绑定角色", { action: "bind", providerId: item.provider.id, voiceProfileId: item.voiceProfileId, disabled: !item.enabled })}
      <button class="action-button is-quiet" type="button" data-voice-profile-edit="${escapeHtml(item.voiceProfileId)}" data-provider-id="${escapeHtml(item.provider.id)}"><span>✎</span><b>调整</b></button>
    </div>
  </article>`;
}

function renderVoiceProfileEditor(state, vm, provider, draft) {
  const selectedId = String(draft.voiceProfileId || "").trim();
  const existing = provider.voiceProfiles.find((item) => item.voiceProfileId === selectedId);
  const enabled = draft.enabled ?? existing?.enabled ?? true;
  const emotionSamples = Array.isArray(draft.emotionSamples) ? draft.emotionSamples : (existing?.emotionSamples || []);
  return `<details class="voice-profile-editor glass-panel" data-capability-key="voice-profile:editor"${state.voiceProfileEditorOpen ? " open" : ""}>
    <summary><span><small>低频配置</small><strong>${escapeHtml(selectedId ? `调整 ${draft.displayName || existing?.name || selectedId}` : "添加声线档案")}</strong></span><em>展开配置⌄</em></summary>
    <form data-capability-form="voice-profile" data-provider-id="${escapeHtml(provider.id)}">
      <fieldset class="voice-folder-inspector">
        <legend>从声线目录读取</legend>
        <label class="is-wide"><span>声线资料目录</span><input type="text" name="folderPath" value="${escapeHtml(draft.folderPath || "")}" placeholder="例如：D:\\GPT-SoVITS\\voices\\reimu"><small>读取参考音频与配置建议；检测到的权重不会自动切换正在运行的 GPT-SoVITS 服务。</small></label>
        ${renderProfileSubmitButton(state, vm, "abilities.provider.voiceProfile.inspectFolder", "检查目录", "⌕")}
      </fieldset>
      ${state.voiceProfileInspection ? renderInspectionSummary(state.voiceProfileInspection) : ""}
      <div class="voice-profile-form-grid">
        <label><span>档案 ID</span><input type="text" name="voiceProfileId" value="${escapeHtml(selectedId)}" maxlength="80" required placeholder="reimu_main"></label>
        <label><span>显示名称</span><input type="text" name="displayName" value="${escapeHtml(draft.displayName || existing?.name || "")}" maxlength="80" placeholder="灵梦 · 主声线"></label>
        <label><span>正文语言</span><input type="text" name="textLang" value="${escapeHtml(draft.textLang || existing?.textLang || "zh")}" maxlength="20"></label>
        <label><span>参考语言</span><input type="text" name="promptLang" value="${escapeHtml(draft.promptLang || existing?.promptLang || "zh")}" maxlength="20"></label>
        <label><span>音频格式</span><select name="mediaType">${["wav", "ogg", "mp3"].map((value) => `<option value="${value}"${value === (draft.mediaType || existing?.mediaType || "wav") ? " selected" : ""}>${value.toUpperCase()}</option>`).join("")}</select></label>
        <label class="voice-enabled-field"><span>启用档案</span><input type="checkbox" name="voiceProfileEnabled" ${enabled ? "checked" : ""}></label>
        <label class="is-wide"><span>参考音频</span><input type="text" name="refAudioPath" value="${escapeHtml(draft.refAudioPath || "")}" placeholder="检查目录后自动填写，或粘贴本机音频路径"><small>${existing?.referenceAudioName ? `已保存 ${escapeHtml(existing.referenceAudioName)}；留空不会清除原值。` : "需要真实可读的本机音频文件。"}</small></label>
        <label class="is-wide"><span>参考文本</span><textarea name="promptText" rows="3" maxlength="1000" placeholder="与参考音频一致的文字；调整已有档案时留空可保留原值。">${escapeHtml(draft.promptText || "")}</textarea></label>
      </div>
      <details class="voice-inference-settings">
        <summary><span>高级推理参数</span><small>留空沿用服务默认值</small></summary>
        <div class="voice-profile-form-grid">
          <label><span>文本切分</span><input type="text" name="textSplitMethod" value="${escapeHtml(profileValue(draft, existing, "textSplitMethod"))}" maxlength="40" placeholder="例如 cut1"></label>
          <label><span>批大小</span><input type="number" name="batchSize" value="${escapeHtml(profileValue(draft, existing, "batchSize"))}" min="1" max="32" step="1" placeholder="1"></label>
          <label><span>Top K</span><input type="number" name="topK" value="${escapeHtml(profileValue(draft, existing, "topK"))}" min="1" max="100" step="1" placeholder="8"></label>
          <label><span>Top P</span><input type="number" name="topP" value="${escapeHtml(profileValue(draft, existing, "topP"))}" min="0" max="1" step="0.01" placeholder="0.85"></label>
          <label><span>Temperature</span><input type="number" name="temperature" value="${escapeHtml(profileValue(draft, existing, "temperature"))}" min="0" max="2" step="0.05" placeholder="0.6"></label>
          <label><span>语速</span><input type="number" name="speedFactor" value="${escapeHtml(profileValue(draft, existing, "speedFactor"))}" min="0.5" max="2" step="0.05" placeholder="1.0"></label>
          <label><span>片段间隔</span><input type="number" name="fragmentInterval" value="${escapeHtml(profileValue(draft, existing, "fragmentInterval"))}" min="0" max="2" step="0.05" placeholder="0.3"></label>
          <label><span>并行推理</span><select name="parallelInfer">${booleanSettingOptions(profileValue(draft, existing, "parallelInfer"))}</select></label>
          <label><span>分桶处理</span><select name="splitBucket">${booleanSettingOptions(profileValue(draft, existing, "splitBucket"))}</select></label>
        </div>
      </details>
      <details class="voice-emotion-settings">
        <summary><span>情绪参考音频</span><small>${escapeHtml(emotionSamples.length ? `已配置 ${emotionSamples.length} 条` : "可选配置")}</small></summary>
        <div class="voice-emotion-settings-body">
          <p>模型输出的情绪会按标识或别名选择参考音频；音频与对应文本必须成对保存。未命中时安全回到基础参考。</p>
          <div class="voice-emotion-sample-list">
            ${emotionSamples.map((sample, index) => renderEmotionSampleRow(state, vm, provider.id, selectedId, sample, String(index), true)).join("")}
            ${renderEmotionSampleRow(state, vm, provider.id, selectedId, {}, "new", false)}
          </div>
        </div>
      </details>
      <div class="voice-profile-editor-actions">
        ${renderProfileSubmitButton(state, vm, "abilities.provider.voiceProfile.save", "保存档案", "✓", "primary")}
        <small>保存只证明配置已写入；请再试听，确认服务和音频链路真实可用后再绑定。</small>
      </div>
    </form>
  </details>`;
}

function renderEmotionSampleRow(state, vm, providerId, profileId, sample, index, configured) {
  const aliases = Array.isArray(sample.aliases) ? sample.aliases.join("，") : "";
  return `<fieldset class="voice-emotion-sample" data-emotion-sample-row="${escapeHtml(index)}" data-emotion-configured="${configured ? "true" : "false"}">
    <legend>${escapeHtml(configured ? sample.emotionId : "新增情绪样本")}</legend>
    <div class="voice-profile-form-grid">
      <label><span>情绪标识</span><input type="text" name="emotionId.${escapeHtml(index)}" value="${escapeHtml(sample.emotionId || "")}" maxlength="80" placeholder="happy 或 开心" ${configured ? "readonly" : ""}></label>
      <label><span>匹配别名</span><input type="text" name="emotionAliases.${escapeHtml(index)}" value="${escapeHtml(aliases)}" maxlength="240" placeholder="开心，高兴，happy"></label>
      <label class="is-wide"><span>参考音频</span><input type="text" name="emotionRefAudioPath.${escapeHtml(index)}" value="" placeholder="${escapeHtml(configured && sample.referenceAudioName ? `已保存 ${sample.referenceAudioName}；留空保留` : "本机参考音频路径")}"></label>
      <label class="is-wide"><span>对应文本</span><textarea name="emotionPromptText.${escapeHtml(index)}" rows="2" maxlength="300" placeholder="${escapeHtml(configured && sample.promptTextLength ? `已保存 ${sample.promptTextLength} 字；留空保留` : "必须与这段参考音频逐字一致")}"></textarea></label>
      ${configured ? `<div class="voice-emotion-row-actions">${renderProfileActionButton(state, vm, "abilities.provider.ttsTest", "试听这条", { action: "test", providerId, voiceProfileId: profileId, emotion: sample.emotionId })}<label class="voice-emotion-remove"><input type="checkbox" name="emotionRemove.${escapeHtml(index)}"><span>删除这条情绪样本</span></label></div>` : ""}
    </div>
  </fieldset>`;
}

function renderInspectionSummary(inspection) {
  const detected = inspection.detected || {};
  const details = [detected.configFileName, detected.referenceAudioName, detected.gptWeightName, detected.sovitsWeightName].filter(Boolean);
  const warnings = Array.isArray(inspection.warnings) ? inspection.warnings : [];
  return `<div class="voice-inspection-result ${warnings.length ? "has-warning" : "is-ready"}"><strong>${warnings.length ? "目录已读取，但还需补充" : "目录结构已读取"}</strong><span>${escapeHtml(details.join(" · ") || "已生成建议配置")}</span><small>${warnings.length ? escapeHtml(warnings.map(inspectionWarningLabel).join("；")) : "权重仅完成检测；服务模型需在 GPT-SoVITS 侧切换。"}</small></div>`;
}

function profileValue(draft, existing, key) {
  const value = draft?.[key] ?? existing?.[key];
  return value === null || value === undefined ? "" : String(value);
}

function booleanSettingOptions(value) {
  const normalized = value === true || value === "true" ? "true" : value === false || value === "false" ? "false" : "";
  return [
    ["", "跟随服务"],
    ["true", "开启"],
    ["false", "关闭"]
  ].map(([option, label]) => `<option value="${option}"${normalized === option ? " selected" : ""}>${label}</option>`).join("");
}

function inspectionWarningLabel(value) {
  return {
    tts_infer_yaml_missing: "未发现推理配置",
    reference_audio_missing: "未发现参考音频",
    prompt_text_missing: "未读取到参考文本",
    gpt_weight_missing: "未发现 GPT 权重",
    sovits_weight_missing: "未发现 SoVITS 权重"
  }[value] || value;
}

function renderProfileActionButton(state, vm, actionId, label, options = {}) {
  const action = vm.actions[actionId] || { available: false, reason: "当前不可用" };
  const actionState = state.actionStates[actionId];
  const pending = ["pressed", "pending"].includes(actionState?.phase);
  const disabled = options.disabled || !action.available || pending;
  return `<button class="action-button is-${escapeHtml(options.tone || "soft")}${pending ? " is-pending" : ""}" type="button" data-voice-profile-action="${escapeHtml(options.action || "")}" data-provider-id="${escapeHtml(options.providerId || "")}" data-voice-profile-id="${escapeHtml(options.voiceProfileId || "")}" data-emotion="${escapeHtml(options.emotion || "")}" title="${escapeHtml(action.available ? label : action.reason)}" ${disabled ? "disabled" : ""}><span>${options.action === "test" ? "▶" : options.action === "clear" ? "×" : "＋"}</span><b>${escapeHtml(pending ? "处理中…" : actionState?.phase === "confirmed" ? actionState.label : label)}</b></button>`;
}

function renderProfileSubmitButton(state, vm, actionId, label, icon, tone = "soft") {
  const action = vm.actions[actionId] || { available: false, reason: "当前不可用" };
  const actionState = state.actionStates[actionId];
  const pending = ["pressed", "pending"].includes(actionState?.phase);
  return `<button class="action-button is-${escapeHtml(tone)}${pending ? " is-pending" : ""}" type="submit" data-action="${escapeHtml(actionId)}" title="${escapeHtml(action.available ? label : action.reason)}" ${action.available && !pending ? "" : "disabled"}><span>${escapeHtml(icon)}</span><b>${escapeHtml(pending ? "处理中…" : actionState?.phase === "confirmed" ? actionState.label : label)}</b></button>`;
}

function renderVoiceToggle(state, vm, actionId, enabled, label) {
  const pending = ["pressed", "pending"].includes(actionPhase(state, actionId));
  const available = vm.actions[actionId]?.available;
  return `<button class="voice-toggle${enabled ? " is-on" : ""}${pending ? " is-pending" : ""}" type="button" data-action="${escapeHtml(actionId)}" data-action-value="${enabled ? "false" : "true"}" data-action-value-type="boolean" aria-pressed="${enabled ? "true" : "false"}" ${available && !pending ? "" : "disabled"}><i></i><span>${escapeHtml(label)} ${pending ? "保存中" : enabled ? "已开启" : "已关闭"}</span></button>`;
}

function renderChoiceButton(actionId, value, selected, disabled) {
  return `<button type="button" class="${selected ? "is-selected" : ""}" data-action="${escapeHtml(actionId)}" data-action-value="${escapeHtml(value)}" aria-pressed="${selected ? "true" : "false"}" ${selected || disabled ? "disabled" : ""}>${escapeHtml(value)}</button>`;
}

function renderProvider(label, provider) {
  return `<article class="voice-provider-tile ${provider.ready ? "is-ready" : ""}"><i></i><span><small>${escapeHtml(label)}</small><strong>${escapeHtml(provider.name)}</strong><em>${escapeHtml(provider.reason || provider.statusLabel)}</em></span></article>`;
}

function voiceStateLabel(voice) {
  if (voice.speaking) return "正在说话";
  if (voice.inputState === "opening") return "正在打开麦克风";
  if (voice.inputState === "recording") return "正在聆听";
  if (voice.inputState === "processing") return "正在识别";
  return "语音空闲";
}

function inputStateLabel(value) {
  return { opening: "打开麦克风中", recording: "聆听中", processing: "识别中", disabled: "已关闭", idle: "待命" }[value] || value || "待确认";
}
