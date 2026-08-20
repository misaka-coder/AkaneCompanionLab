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
        <span class="mini-chip">设备级 · 自动保存</span>
      </div>
      <div class="theme-segment" role="group" aria-label="界面主题">
        ${renderThemeButton("system", "跟随系统", preferences.themeMode)}
        ${renderThemeButton("dark", "深色", preferences.themeMode)}
        ${renderThemeButton("light", "浅色", preferences.themeMode)}
      </div>
      <div class="presentation-field">
        <span><strong>强调色</strong><small>按钮、状态和选中项</small></span>
        <div class="accent-options" role="group" aria-label="强调色">
          ${renderAccentButton("violet", "紫罗兰", preferences.accentPreset)}
          ${renderAccentButton("sakura", "樱粉", preferences.accentPreset)}
          ${renderAccentButton("sky", "晴空", preferences.accentPreset)}
          ${renderAccentButton("mint", "薄荷", preferences.accentPreset)}
        </div>
      </div>
      <div class="presentation-field">
        <span><strong>界面字体</strong><small>只影响控制中心</small></span>
        <div class="font-options" role="group" aria-label="界面字体">
          ${renderFontButton("system", "清晰", preferences.fontPreset)}
          ${renderFontButton("rounded", "圆润", preferences.fontPreset)}
          ${renderFontButton("serif", "文艺", preferences.fontPreset)}
        </div>
      </div>
      ${renderRange("surfaceOpacity", "面板透明度", preferences.surfaceOpacity, 55, 96, "%")}
      ${renderRange("backgroundDim", "背景压暗", preferences.backgroundDim, 0, 80, "%")}
      ${renderRange("blurAmount", "玻璃模糊", preferences.blurAmount, 0, 36, " px")}
      <button class="motion-toggle${preferences.reducedMotion ? " is-selected" : ""}" type="button" data-presentation-toggle="reducedMotion" aria-pressed="${preferences.reducedMotion ? "true" : "false"}">
        <span><strong>减弱动效</strong><small>减少转场、呼吸和位移动画</small></span><i aria-hidden="true"></i>
      </button>
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

function renderAccentButton(id, label, activeId) {
  const selected = id === activeId;
  return `<button type="button" data-accent-preset="${id}" class="accent-${id}${selected ? " is-selected" : ""}" aria-label="${label}" title="${label}" aria-pressed="${selected ? "true" : "false"}"><i></i></button>`;
}

function renderFontButton(id, label, activeId) {
  const selected = id === activeId;
  return `<button type="button" data-font-preset="${id}" class="${selected ? "is-selected" : ""}" aria-pressed="${selected ? "true" : "false"}">${label}</button>`;
}

function renderRange(id, label, value, min, max, unit) {
  const number = Number(value);
  return `<label class="presentation-range"><span><strong>${label}</strong><small data-presentation-value="${id}">${Math.round(number)}${unit}</small></span><input type="range" min="${min}" max="${max}" step="1" value="${Math.round(number)}" data-presentation-range="${id}" data-presentation-unit="${unit}" aria-label="${label}" /></label>`;
}
