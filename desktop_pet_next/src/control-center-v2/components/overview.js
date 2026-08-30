import { escapeHtml, initial, safeStyleUrl } from "../dom.js";
import { renderActionButton } from "./action-button.js";

export function renderOverview(state) {
  const vm = state.viewModel;
  if (!vm) return renderUnavailable(state);
  const character = vm.character;
  const portraitStyle = safeStyleUrl(character.visuals.portrait);
  const heroStyle = safeStyleUrl(character.visuals.hero);
  const activityTone = vm.activity.phase === "failed" ? "danger" : vm.activity.phase === "idle" ? "quiet" : "active";

  return `
    <section class="ccv2-overview" aria-labelledby="ccv2-page-title">
      <div class="ccv2-hero glass-panel">
        ${heroStyle ? `<div class="hero-background" style="--hero-image:${heroStyle}"></div>` : ""}
        <div class="ccv2-hero-copy">
          <div class="identity-row">
            ${renderAvatar(character)}
            <div>
              <p class="eyebrow">ACTIVE CHARACTER</p>
              <h2>${escapeHtml(character.displayName)}</h2>
              <p class="subtle">${escapeHtml([character.outfit, character.emotion].filter(Boolean).join(" · ") || "角色资源已连接")}</p>
            </div>
          </div>
          <div class="activity-card" data-tone="${activityTone}">
            <span class="activity-mark" aria-hidden="true">✦</span>
            <span><small>当前活动</small><strong>${escapeHtml(vm.activity.label)}</strong>${vm.activity.detail ? `<em>${escapeHtml(vm.activity.detail)}</em>` : ""}</span>
            <i aria-hidden="true"></i>
          </div>
          ${renderBotSelector(state, vm)}
          <div class="quick-actions">
            ${renderActionButton(state, vm, "chat.new", "＋", "新对话", "primary")}
            ${renderActionButton(state, vm, "workspace.open", "▱", "打开手边")}
            ${renderActionButton(state, vm, "character.openWorkshop", "◇", "角色工坊")}
            ${renderActionButton(state, vm, "character.openPackFolder", "⌁", "角色文件夹")}
          </div>
        </div>
        <div class="portrait-stage">
          <span class="portrait-halo"></span>
          ${portraitStyle
            ? `<div class="portrait-image" role="img" aria-label="${escapeHtml(character.displayName)} 当前立绘" style="--portrait-image:${portraitStyle}"></div>`
            : `<div class="portrait-fallback" aria-label="当前角色暂无立绘">${initial(character.displayName)}</div>`}
          <span class="portrait-caption">${portraitStyle ? "当前角色资源" : "等待角色立绘"}</span>
        </div>
      </div>

      ${renderSetupCenter(state, vm)}

      <div class="overview-grid">
        ${renderMusic(state, vm)}
        ${renderRecent(vm)}
        ${renderResourceState(character)}
      </div>
    </section>`;
}

function renderSetupCenter(state, vm) {
  const setup = vm.setup;
  if (!setup?.items?.length) return "";
  const remaining = setup.coreTotal - setup.coreReadyCount;
  const progress = setup.coreTotal ? Math.round((setup.coreReadyCount / setup.coreTotal) * 100) : 0;
  return `
    <details class="setup-center glass-panel${setup.coreComplete ? " is-complete" : ""}"${(state.setupExpanded ?? !setup.coreComplete) ? " open" : ""}>
      <summary>
        <span class="setup-progress" style="--setup-progress:${progress}%"><strong>${escapeHtml(`${setup.coreReadyCount}/${setup.coreTotal}`)}</strong></span>
        <span class="setup-summary-copy"><small>QUICK START</small><strong>${escapeHtml(setup.coreComplete ? "基础设置已就绪" : `还差 ${remaining} 项基础设置`)}</strong><em>${escapeHtml(setup.coreComplete ? `${setup.optionalReadyCount}/${setup.optionalTotal} 项可选增强已启用` : "按真实状态逐项处理，不会自动改动配置")}</em></span>
        <span class="setup-summary-action">${setup.coreComplete ? "查看入口" : "继续设置"}⌄</span>
      </summary>
      <div class="setup-item-grid">
        ${setup.items.map((item) => `<button class="setup-item is-${escapeHtml(item.status)}" type="button" data-page="${escapeHtml(item.page)}"><i aria-hidden="true">${item.status === "ready" ? "✓" : item.optional ? "＋" : "!"}</i><span><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.detail)}</small></span><em>${escapeHtml(item.statusLabel)} →</em></button>`).join("")}
      </div>
    </details>`;
}

function renderBotSelector(state, vm) {
  const bots = vm.bots?.items || [];
  if (bots.length < 2) return "";
  const actionState = state.actionStates["settings.selectBot"];
  const pending = ["pressed", "pending"].includes(actionState?.phase);
  return `
    <label class="bot-selector">
      <span><small>当前桌宠 Bot</small><em>聊天、记忆与能力随 Bot 切换</em></span>
      <select data-bound-bot-select${pending ? " disabled" : ""}>
        ${bots.map((bot) => `<option value="${escapeHtml(bot.id)}"${bot.selected ? " selected" : ""}${!bot.available && !bot.selected ? " disabled" : ""}>${escapeHtml(bot.displayName)}${bot.isDefault ? " · 默认" : !bot.available ? " · 不可用" : ""}</option>`).join("")}
      </select>
    </label>`;
}

function renderAvatar(character) {
  const style = safeStyleUrl(character.visuals.avatar);
  return style
    ? `<span class="avatar" role="img" aria-label="${escapeHtml(character.displayName)} 头像" style="--avatar-image:${style}"></span>`
    : `<span class="avatar avatar-fallback" aria-hidden="true">${initial(character.displayName)}</span>`;
}

function renderMusic(state, vm) {
  if (!vm.music.available) return "";
  const playing = vm.music.playback === "playing";
  const moodLine = moodPhraseFromEmotion(vm.character.emotion);
  return `
    <section class="music-card glass-panel">
      <div class="card-head"><div><p class="eyebrow">NOW PLAYING</p><h3>陪伴播放</h3></div><span class="mini-chip">${playing ? "正在播放" : vm.music.playback === "paused" ? "已暂停" : "已停止"}</span></div>
      <div class="music-row">
        <span class="cover-fallback" aria-hidden="true">♫</span>
        <span class="track"><strong>${escapeHtml(vm.music.title)}</strong><small>${escapeHtml(vm.music.artist || vm.music.detail || "当前媒体")}</small>${moodLine ? `<small class="mood-line">${escapeHtml(moodLine)}</small>` : ""}</span>
        ${renderActionButton(state, vm, "music.togglePlayback", playing ? "Ⅱ" : "▶", playing ? "暂停" : "播放", "media")}
      </div>
    </section>`;
}

function moodPhraseFromEmotion(name) {
  const normalized = String(name || "").toLowerCase().trim();
  return {
    开心: "她好像挺开心的～", 高兴: "她好像挺高兴的～", 兴奋: "她好像很兴奋", 开朗: "她心情看起来不错",
    温柔: "她好像很温柔", 平静: "她安静地在听", 默然: "她安静地在听", 思考: "她好像在想什么",
    沉思: "她好像在想什么", 好奇: "她好像很好奇", 害羞: "她有点害羞", 难过: "她好像有点难过",
    委屈: "她好像有点委屈", 无聊: "她好像有点无聊"
  }[normalized] || "";
}

function renderRecent(vm) {
  const items = vm.recentOutputs;
  return `
    <section class="recent-card glass-panel">
      <div class="card-head"><div><p class="eyebrow">RECENT</p><h3>最近生成</h3></div></div>
      ${items.length ? `<ol class="recent-list">${items.map((item) => `<li><i></i><span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.detail || item.status)}</small></span></li>`).join("")}</ol>` : `<div class="empty-inline"><span>暂时没有最近文件</span><small>完成并登记的输出会出现在这里</small></div>`}
    </section>`;
}

function renderResourceState(character) {
  if (!character.resourceWarnings.length) return "";
  return `<section class="resource-card glass-panel"><p class="eyebrow">RESOURCE</p><h3>角色资源需要处理</h3><ul>${character.resourceWarnings.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></section>`;
}

function renderUnavailable(state) {
  const failed = state.phase === "failed";
  return `<section class="empty-state glass-panel"><span aria-hidden="true">${failed ? "!" : "◌"}</span><h2>${failed ? "暂时没连上桌宠" : "正在读取真实状态"}</h2><p>${escapeHtml(state.error || "控制中心外壳已经可用，状态卡会在连接后分别出现。")}</p><button class="action-button is-primary" type="button" data-refresh><span aria-hidden="true">↻</span><b>重新连接</b></button></section>`;
}
