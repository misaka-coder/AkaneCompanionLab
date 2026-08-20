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
        <div><p class="eyebrow">VOICE & LISTENING</p><h2>让她听见，也让她说出来</h2><p>日常开关、试听和唤醒设置都在这里；角色声线仍由角色工坊统一管理。</p></div>
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
            <div class="voice-profile-route"><span>角色声线</span><p>绑定和更换声线档案请在角色工坊完成，避免同一角色出现两套权威配置。</p>${renderActionButton(state, vm, "character.openWorkshop", "↗", "打开角色工坊", "soft")}</div>
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
    </section>`;
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
  if (voice.inputState === "recording") return "正在聆听";
  if (voice.inputState === "processing") return "正在识别";
  return "语音空闲";
}

function inputStateLabel(value) {
  return { recording: "聆听中", processing: "识别中", disabled: "已关闭", idle: "待命" }[value] || value || "待确认";
}
