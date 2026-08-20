import { escapeHtml, safeStyleUrl } from "../dom.js";

const TARGET_LABELS = Object.freeze({
  background: "背景",
  portrait: "立绘",
  avatar: "头像"
});

export function renderPresentationControls(state, character) {
  const preferences = state.presentationPreferences || {};
  const target = TARGET_LABELS[state.framingTarget] ? state.framingTarget : "portrait";
  const frame = preferences.frames?.[target] || { x: 50, y: 50, scale: 1 };
  const backgroundStyle = safeStyleUrl(character.visuals?.hero);
  const portraitStyle = safeStyleUrl(character.visuals?.portrait);
  const avatarStyle = safeStyleUrl(character.visuals?.avatar);

  return `
    <section class="appearance-card presentation-card glass-panel">
      <div class="appearance-card-head">
        <div><p class="eyebrow">INTERFACE THEME</p><h3>界面主题</h3></div>
        <span class="mini-chip">仅这台设备</span>
      </div>
      <div class="theme-segment" role="group" aria-label="界面主题">
        ${renderThemeButton("system", "跟随系统", preferences.themeMode)}
        ${renderThemeButton("dark", "深色", preferences.themeMode)}
        ${renderThemeButton("light", "浅色", preferences.themeMode)}
      </div>
    </section>

    <section class="appearance-card presentation-card glass-panel">
      <div class="appearance-card-head">
        <div><p class="eyebrow">FRAME & CROP</p><h3>画面构图</h3></div>
        <button class="presentation-reset" type="button" data-framing-reset>恢复默认</button>
      </div>
      <p class="presentation-help">选择一个画面后，直接拖动预览调整取景；缩放只改变控制中心里的展示，不会移动桌宠本体。</p>
      <div class="framing-targets" role="group" aria-label="选择构图对象">
        ${Object.entries(TARGET_LABELS).map(([id, label]) => `<button type="button" data-framing-target="${id}" class="${id === target ? "is-selected" : ""}">${label}</button>`).join("")}
      </div>
      <div class="framing-editor" data-framing-stage data-target="${escapeHtml(target)}" tabindex="0" aria-label="拖动调整${TARGET_LABELS[target]}位置">
        ${backgroundStyle ? `<div class="framing-background" style="--framing-background:${backgroundStyle}"></div>` : ""}
        <div class="framing-grid" aria-hidden="true"></div>
        ${portraitStyle ? `<div class="framing-portrait" style="--framing-portrait:${portraitStyle}"></div>` : ""}
        ${avatarStyle ? `<div class="framing-avatar" style="--framing-avatar:${avatarStyle}"></div>` : ""}
        <span class="framing-crosshair" aria-hidden="true"></span>
        <span class="framing-tip">拖动调整 ${TARGET_LABELS[target]}</span>
      </div>
      <label class="framing-scale-row">
        <span><strong>缩放</strong><small data-framing-scale-label>${Math.round(Number(frame.scale || 1) * 100)}%</small></span>
        <input type="range" min="60" max="200" step="1" value="${Math.round(Number(frame.scale || 1) * 100)}" data-framing-scale aria-label="${TARGET_LABELS[target]}缩放" />
      </label>
    </section>`;
}

function renderThemeButton(mode, label, activeMode) {
  const selected = mode === activeMode;
  return `<button type="button" data-theme-mode="${mode}" class="${selected ? "is-selected" : ""}"${selected ? ' aria-pressed="true"' : ' aria-pressed="false"'}>${label}</button>`;
}
