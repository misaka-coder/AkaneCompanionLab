const root = document.getElementById("settings-root");

const DEFAULT_SETTINGS = {
  backendUrl: "http://127.0.0.1:9999",
  outfit: "猫娘",
  opacity: 1,
  petScale: 1,
  voiceEnabled: false,
  voiceInputEnabled: true,
  desktopContextEnabled: true,
  clipboardContextEnabled: false,
};

let settings = { ...DEFAULT_SETTINGS };
let saving = false;

function normalizeSettings(value) {
  return {
    ...DEFAULT_SETTINGS,
    ...(value && typeof value === "object" ? value : {}),
  };
}

function render() {
  const opacity = Number(settings.opacity || 1);
  const petScale = normalizePetScale(settings.petScale);
  const desktopContextEnabled = settings.desktopContextEnabled !== false;
  const clipboardDisabled = !desktopContextEnabled;
  root.innerHTML = `
    <section class="settings-panel" role="dialog" aria-label="Akane 设置">
      <header class="settings-panel__header">
        <div>
          <p class="settings-panel__eyebrow">Akane Control</p>
          <h1>桌宠设置</h1>
          <p>把低频配置收在这里，右键菜单只保留常用动作。</p>
        </div>
        <button class="settings-panel__icon-btn" type="button" data-action="close" aria-label="关闭">×</button>
      </header>

      <main class="settings-panel__body">
        <section class="settings-section">
          <div class="settings-section__title-row">
            <h2>连接与角色</h2>
          </div>
          <label class="settings-field">
            <span>后端地址</span>
            <input type="text" data-field="backendUrl" value="${escapeAttr(settings.backendUrl)}" spellcheck="false" />
          </label>
          <label class="settings-field">
            <span>服装 / 角色名</span>
            <input type="text" data-field="outfit" value="${escapeAttr(settings.outfit)}" spellcheck="false" />
          </label>
          <button class="settings-primary-btn" type="button" data-action="save-basic">${saving ? "保存中……" : "保存基础设置"}</button>
        </section>

        <section class="settings-section">
          <div class="settings-section__title-row">
            <h2>透明度</h2>
            <span>${Math.round(opacity * 100)}%</span>
          </div>
          <div class="settings-chip-row">
            ${[1, 0.85, 0.7].map((value) => `
              <button type="button" class="${opacity === value ? "active" : ""}" data-action="set-opacity" data-value="${value}">
                ${Math.round(value * 100)}%
              </button>
            `).join("")}
          </div>
        </section>

        <section class="settings-section">
          <div class="settings-section__title-row">
            <h2>大小与占用</h2>
            <span id="pet-scale-label">${Math.round(petScale * 100)}%</span>
          </div>
          <label class="settings-field">
            <span>桌宠整体大小</span>
            <input type="range" data-field="petScale" min="75" max="145" step="5" value="${Math.round(petScale * 100)}" />
          </label>
          <p class="settings-note">会同时调整窗口范围、立绘、气泡和输入框。</p>
        </section>

        <section class="settings-section">
          <div class="settings-section__title-row">
            <h2>语音</h2>
          </div>
          ${renderToggle("voiceEnabled", "语音播放", "Akane 正式回复时调用后端 TTS。")}
          ${renderToggle("voiceInputEnabled", "语音输入", "麦克风按钮与 Ctrl+Shift+Space 录音。")}
        </section>

        <section class="settings-section">
          <div class="settings-section__title-row">
            <h2>桌面上下文</h2>
          </div>
          ${renderToggle("desktopContextEnabled", "前台窗口感知", "只在发送消息时临时附带，不进长期记忆。")}
          ${renderToggle("clipboardContextEnabled", "剪贴板文本", "默认关闭；开启后随消息临时附带。", clipboardDisabled)}
        </section>

        <section class="settings-section">
          <div class="settings-section__title-row">
            <h2>工具入口</h2>
          </div>
          <div class="settings-shortcuts">
            <button type="button" data-action="workspace-panel">手边物品</button>
            <button type="button" data-action="debug-panel">状态预览器</button>
            <button type="button" data-action="reload-sprite">重载立绘</button>
          </div>
        </section>
      </main>
    </section>
  `;
}

function renderToggle(key, title, description, disabled = false) {
  const on = settings[key] === true;
  return `
    <button type="button" class="settings-toggle${on ? " on" : " off"}${disabled ? " disabled" : ""}"
      data-action="toggle-setting" data-key="${escapeAttr(key)}" ${disabled ? "disabled" : ""}>
      <span>
        <strong>${escapeHtml(title)}</strong>
        <small>${escapeHtml(description)}</small>
      </span>
      <em>${on ? "开" : "关"}</em>
    </button>
  `;
}

async function saveSettings(partial) {
  saving = true;
  render();
  try {
    const next = await window.akaneAPI?.setSettings?.(partial);
    if (next) settings = normalizeSettings(next);
  } finally {
    saving = false;
    render();
  }
}

async function saveBasicSettings() {
  const backendUrl = root.querySelector('[data-field="backendUrl"]')?.value?.trim() || DEFAULT_SETTINGS.backendUrl;
  const outfit = root.querySelector('[data-field="outfit"]')?.value?.trim() || DEFAULT_SETTINGS.outfit;
  await saveSettings({ backendUrl, outfit });
}

root.addEventListener("click", (event) => {
  const target = event.target?.closest?.("[data-action]");
  if (!target) return;
  event.preventDefault();

  const action = target.dataset.action;
  if (action === "close") {
    window.close();
    return;
  }
  if (action === "save-basic") {
    void saveBasicSettings();
    return;
  }
  if (action === "set-opacity") {
    void saveSettings({ opacity: Number(target.dataset.value) || 1 });
    return;
  }
  if (action === "toggle-setting") {
    const key = target.dataset.key;
    if (!key) return;
    void saveSettings({ [key]: !(settings[key] === true) });
    return;
  }
  if (["workspace-panel", "debug-panel", "reload-sprite"].includes(action)) {
    window.akaneAPI?.menuAction?.(action);
  }
});

root.addEventListener("input", (event) => {
  const target = event.target;
  if (!target?.matches?.('[data-field="petScale"]')) return;
  const label = root.querySelector("#pet-scale-label");
  if (label) label.textContent = `${Number(target.value) || 100}%`;
});

root.addEventListener("change", (event) => {
  const target = event.target;
  if (!target?.matches?.('[data-field="petScale"]')) return;
  void saveSettings({ petScale: normalizePetScale((Number(target.value) || 100) / 100) });
});

root.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.target?.matches?.("input")) {
    event.preventDefault();
    void saveBasicSettings();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") window.close();
});

window.akaneAPI?.onSettingsInit?.((initialSettings) => {
  settings = normalizeSettings(initialSettings);
  render();
});

window.akaneAPI?.onSettingsChanged?.((nextSettings) => {
  settings = normalizeSettings(nextSettings);
  render();
});

async function init() {
  const loaded = await window.akaneAPI?.getSettings?.();
  settings = normalizeSettings(loaded);
  render();
}

void init();

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/'/g, "&#39;");
}

function normalizePetScale(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return DEFAULT_SETTINGS.petScale;
  return Math.max(0.75, Math.min(1.45, numeric));
}
