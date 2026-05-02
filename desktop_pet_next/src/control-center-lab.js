import { invoke } from "@tauri-apps/api/core";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";

import akaneNormal from "./assets/characters/猫娘/正常.png";
import akaneThinking from "./assets/characters/猫娘/思考中.png";
import akaneHappy from "./assets/characters/猫娘/开心.png";
import akaneListening from "./assets/characters/猫娘/侧耳听.png";
import akaneMusic from "./assets/characters/猫娘/听歌中.png";
import akaneShy from "./assets/characters/猫娘/脸红.png";
import akaneConfused from "./assets/characters/猫娘/困惑.png";
import akanePout from "./assets/characters/猫娘/气鼓鼓.png";
import akaneCute from "./assets/characters/猫娘/卖萌.png";
import skyCityBalcony from "./assets/control-center-lab/backgrounds/sky-city-balcony.png";
import akaneSakuraWide from "./assets/control-center-lab/heroes/akane-sakura-wide.png";
import akaneSkyWide from "./assets/control-center-lab/heroes/akane-sky-wide.png";
import akaneNightWindow from "./assets/control-center-lab/covers/akane-night-window.png";
import akaneSakuraClose from "./assets/control-center-lab/covers/akane-sakura-close.png";
import akaneSkyPaperPlane from "./assets/control-center-lab/covers/akane-sky-paper-plane.png";
import cloudLetter from "./assets/control-center-lab/covers/cloud-letter.png";
import moonBalcony from "./assets/control-center-lab/covers/moon-balcony.png";
import starryCloudCat from "./assets/control-center-lab/covers/starry-cloud-cat.png";
import {
  CONTROL_CENTER_ACTIONS,
  createControlCenterActionRouter
} from "./control-center/action-router.js";
import { createControlCenterSnapshot } from "./control-center/data-adapter.js";
import {
  CONTROL_CENTER_SOURCE_KIND,
  createControlCenterDataSource
} from "./control-center/data-sources.js";
import "./control-center-lab.css";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";
const isTauriRuntime = Boolean(window.__TAURI_INTERNALS__);
const root = document.querySelector("#app");
let dataSource = createControlCenterDataSource(createControlCenterDataSourceOptions());
let snapshot = createControlCenterSnapshot(dataSource.readInitialState());
let actionRouter = createControlCenterActionRouter({ dataSource });
let { labMeta, navItems, backgroundAsset } = snapshot.shell;
let {
  abilities: abilitiesPage,
  advanced: advancedPage,
  character: characterPage,
  music: musicPage,
  overview: overviewPage,
  perception: perceptionPage,
  voice: voicePage
} = snapshot.pages;
const images = {
  normal: akaneNormal,
  thinking: akaneThinking,
  happy: akaneHappy,
  listening: akaneListening,
  music: akaneMusic,
  shy: akaneShy,
  confused: akaneConfused,
  pout: akanePout,
  cute: akaneCute,
  skyCityBalcony,
  akaneSakuraWide,
  akaneSkyWide,
  akaneNightWindow,
  akaneSakuraClose,
  akaneSkyPaperPlane,
  cloudLetter,
  moonBalcony,
  starryCloudCat
};

const state = {
  activePage: resolveInitialPage(),
  activeOutfit: characterPage.outfits?.find((item) => item.current)?.id || characterPage.outfits?.[0]?.id || "",
  activeEmotion: characterPage.emotions?.find((item) => item.current)?.id || characterPage.emotions?.[0]?.id || "",
  switches: Object.fromEntries(perceptionPage.featureCards.map((card) => [card.id, card.enabled])),
  activeInterval: perceptionPage.featureCards.find((card) => card.id === "proactive")?.activeOption || "1 分钟",
  activeVoicePreset: voicePage.tts?.voice || "Akane Voice",
  activeMusicMode: musicPage.modes[0]
};

renderShell();
bindEvents();
renderActivePage();
void hydrateControlCenterSnapshot();

function createControlCenterDataSourceOptions(overrides = {}) {
  const params = new URLSearchParams(window.location.search);
  const source = String(params.get("source") || "").trim().toLowerCase();
  const petState = overrides.petState && typeof overrides.petState === "object" ? overrides.petState : {};
  if (source === CONTROL_CENTER_SOURCE_KIND.mock) {
    return { kind: CONTROL_CENTER_SOURCE_KIND.mock };
  }
  return {
    kind: CONTROL_CENTER_SOURCE_KIND.backend,
    baseUrl: params.get("backend") || params.get("backend_url") || petState.backendUrl || localStorage.getItem("akane.controlCenter.backendUrl") || DEFAULT_BACKEND_URL,
    fetchImpl: isTauriRuntime ? tauriFetch : typeof window.fetch === "function" ? window.fetch.bind(window) : undefined,
    sessionId: params.get("session_id") || params.get("user_id") || petState.sessionId || localStorage.getItem("akane.controlCenter.sessionId") || "control-center-lab",
    profileUserId: params.get("real_user_id") || params.get("profile_user_id") || petState.profileUserId || localStorage.getItem("akane.controlCenter.profileUserId") || "master",
    characterPackId: params.get("character_pack_id") || params.get("characterPackId") || petState.characterPackId || localStorage.getItem("akane.controlCenter.characterPackId") || "",
    outfit: params.get("outfit") || petState.outfit || "",
    emotion: params.get("emotion") || petState.currentEmotion || "",
    petState,
    availableCharacterPacks: overrides.availableCharacterPacks || []
  };
}

async function hydrateControlCenterSnapshot() {
  try {
    const runtimeOptions = await createRuntimeDataSourceOptions();
    if (runtimeOptions) {
      dataSource = createControlCenterDataSource(runtimeOptions);
      actionRouter = createControlCenterActionRouter({ dataSource });
    }
    if (!dataSource?.readSnapshot) return;
    const raw = await dataSource.readSnapshot();
    if (!raw) return;
    applyControlCenterSnapshot(createControlCenterSnapshot(raw));
  } catch (error) {
    console.info("[control-center] keep mock snapshot:", formatError(error));
  }
}

async function createRuntimeDataSourceOptions() {
  if (!isTauriRuntime) return null;
  try {
    const petState = await invoke("load_pet_state");
    let availableCharacterPacks = [];
    try {
      availableCharacterPacks = await invoke("list_character_packs");
    } catch {
      availableCharacterPacks = [];
    }
    return createControlCenterDataSourceOptions({ petState, availableCharacterPacks });
  } catch {
    return null;
  }
}

function applyControlCenterSnapshot(nextSnapshot) {
  if (!nextSnapshot || typeof nextSnapshot !== "object") return;
  snapshot = nextSnapshot;
  ({ labMeta, navItems, backgroundAsset } = snapshot.shell);
  ({
    abilities: abilitiesPage,
    advanced: advancedPage,
    character: characterPage,
    music: musicPage,
    overview: overviewPage,
    perception: perceptionPage,
    voice: voicePage
  } = snapshot.pages);
  if (!navItems.some((item) => item.id === state.activePage)) {
    state.activePage = labMeta.defaultPage;
  }
  syncInteractiveStateWithSnapshot();
  renderShell();
  renderActivePage();
}

function syncInteractiveStateWithSnapshot() {
  const outfits = Array.isArray(characterPage.outfits) ? characterPage.outfits : [];
  const emotions = Array.isArray(characterPage.emotions) ? characterPage.emotions : [];
  if (!outfits.some((item) => item.id === state.activeOutfit)) {
    state.activeOutfit = outfits.find((item) => item.current)?.id || outfits[0]?.id || "";
  }
  if (!emotions.some((item) => item.id === state.activeEmotion)) {
    state.activeEmotion = emotions.find((item) => item.current)?.id || emotions[0]?.id || "";
  }
}

function renderShell() {
  root.innerHTML = `
    <div class="lab-sky" style="--lab-background-image: url(${imageFor(backgroundAsset)})" aria-hidden="true">
      <span class="sparkle sparkle-one"></span>
      <span class="sparkle sparkle-two"></span>
      <span class="sparkle sparkle-three"></span>
      <span class="skyline skyline-left"></span>
      <span class="skyline skyline-right"></span>
      <span class="flower-haze flower-left"></span>
      <span class="flower-haze flower-right"></span>
    </div>
    <main class="cc-shell" aria-label="Akane 控制中心 UI 原型">
      <aside class="cc-sidebar">
        <div class="brand-block">
          <div>
            <strong>Akane</strong>
            <span>控制中心</span>
          </div>
          <i aria-hidden="true">✦</i>
        </div>
        <nav class="side-nav" aria-label="控制中心导航">
          ${navItems.map(renderNavButton).join("")}
        </nav>
        <div class="sidebar-spacer"></div>
        <section class="companion-card">
          <span class="mini-sparkle" aria-hidden="true">✦</span>
          <p>与 Akane 一起<br />让每一天都更轻松</p>
          <span class="cat-mark" aria-hidden="true"></span>
        </section>
        <section class="online-card">
          <img src="${images.happy}" alt="" />
          <div>
            <strong><span></span>${labMeta.status}</strong>
            <p>${labMeta.statusDetail}</p>
          </div>
        </section>
      </aside>

      <section class="cc-main">
        <header class="window-chrome">
          <button class="chrome-icon" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.windowNotify}" aria-label="通知">${icon("bell")}</button>
          <div class="chrome-actions" aria-label="窗口操作">
            <button class="chrome-icon" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.windowMinimize}" aria-label="最小化">${icon("minus")}</button>
            <button class="chrome-icon" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.windowMaximize}" aria-label="最大化">${icon("square")}</button>
            <button class="chrome-icon" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.windowClose}" aria-label="关闭">${icon("x")}</button>
          </div>
        </header>
        <div id="page-content" class="page-content"></div>
        <footer class="cc-footer">
          <span>${icon("lock")} ${labMeta.footer} ✦</span>
          <span>${labMeta.version}</span>
        </footer>
      </section>
    </main>
  `;
}

function bindEvents() {
  root.addEventListener("click", (event) => {
    const actionButton = event.target.closest("[data-action-id]");
    if (actionButton) {
      void actionRouter.run(
        actionButton.dataset.actionId,
        { page: state.activePage },
        { source: "control-center-lab" }
      );
      return;
    }

    const navButton = event.target.closest("[data-page]");
    if (navButton) {
      state.activePage = navButton.dataset.page;
      const url = new URL(window.location.href);
      url.searchParams.set("page", state.activePage);
      window.history.replaceState({}, "", url);
      renderActivePage();
      return;
    }

    const switchButton = event.target.closest("[data-switch]");
    if (switchButton) {
      const key = switchButton.dataset.switch;
      state.switches[key] = !state.switches[key];
      switchButton.classList.toggle("is-on", state.switches[key]);
      switchButton.setAttribute("aria-checked", String(state.switches[key]));
      return;
    }

    const intervalButton = event.target.closest("[data-interval]");
    if (intervalButton) {
      state.activeInterval = intervalButton.dataset.interval;
      renderActivePage();
      return;
    }

    const outfitButton = event.target.closest("[data-character-outfit]");
    if (outfitButton) {
      state.activeOutfit = outfitButton.dataset.characterOutfit;
      renderActivePage();
      return;
    }

    const emotionButton = event.target.closest("[data-character-emotion]");
    if (emotionButton) {
      state.activeEmotion = emotionButton.dataset.characterEmotion;
      renderActivePage();
      return;
    }

    const voicePreset = event.target.closest("[data-voice-preset]");
    if (voicePreset) {
      state.activeVoicePreset = voicePreset.dataset.voicePreset;
      renderActivePage();
      return;
    }

    const musicMode = event.target.closest("[data-music-mode]");
    if (musicMode) {
      state.activeMusicMode = musicMode.dataset.musicMode;
      renderActivePage();
    }
  });
}

function resolveInitialPage() {
  const params = new URLSearchParams(window.location.search);
  const candidate = params.get("page") || window.location.hash.replace(/^#/, "");
  return navItems.some((item) => item.id === candidate) ? candidate : labMeta.defaultPage;
}

function renderActivePage() {
  document.documentElement.dataset.activePage = state.activePage;
  root.querySelectorAll("[data-page]").forEach((button) => {
    button.classList.toggle("active", button.dataset.page === state.activePage);
  });

  const content = root.querySelector("#page-content");
  const renderers = {
    overview: renderOverviewPage,
    character: renderCharacterPage,
    voice: renderVoicePage,
    music: renderMusicPage,
    context: renderPerceptionPage,
    abilities: renderAbilitiesPage,
    advanced: renderAdvancedPage
  };
  content.innerHTML = (renderers[state.activePage] || renderOverviewPage)();
}

function renderNavButton(item) {
  return `
    <button class="${item.id === state.activePage ? "active" : ""}" data-page="${item.id}" type="button">
      ${icon(item.icon)}
      <span>${escapeHtml(item.label)}</span>
    </button>
  `;
}

function renderPageTitle(page, extra = "") {
  return `
    <section class="page-title-row">
      <div>
        <h1>${escapeHtml(page.title)}${page.accent ? ` <span>${escapeHtml(page.accent)}</span>` : ""}</h1>
        <p>${escapeHtml(page.subtitle)}</p>
      </div>
      ${extra || `<div class="title-orbit" aria-hidden="true">${Array.from({ length: 18 }, (_, index) => `<i style="--bar: ${((index * 5) % 20) + 8}px"></i>`).join("")}</div>`}
    </section>
  `;
}

function renderOverviewPage() {
  return `
    ${renderPageTitle(overviewPage)}
    <section class="overview-dashboard-page">
      <div class="overview-top-grid">
        <article class="glass-card overview-status-card">
          <div class="overview-status-art">
            <img src="${imageFor(overviewPage.status.hero || overviewPage.status.image)}" alt="" />
          </div>
          <div class="overview-status-content">
            <div class="card-heading">
              <h2>${escapeHtml(overviewPage.status.title)}</h2>
              <span class="connected-badge">${icon("checkCircle")} ${escapeHtml(overviewPage.status.badge)}</span>
            </div>
            <div class="status-detail-list">
              ${overviewPage.status.items.map(renderOverviewStatusRow).join("")}
            </div>
          </div>
        </article>

        <article class="glass-card overview-diagnostic-card">
          <div class="card-heading">
            <h2>${escapeHtml(overviewPage.connection.title)}</h2>
            <span class="connected-badge">${icon("checkCircle")} ${escapeHtml(overviewPage.connection.badge)}</span>
          </div>
          <div class="connection-list">
            ${overviewPage.connection.rows.map(renderConnectionRow).join("")}
          </div>
        </article>

        <article class="glass-card overview-actions-card">
          <h2>快捷操作</h2>
          <div class="overview-action-stack">
            ${overviewPage.quickActions.map(renderOverviewAction).join("")}
          </div>
        </article>
      </div>

      <div class="overview-mid-grid">
        ${renderOverviewPackCard()}
        ${renderOverviewEmotionCard()}
        ${renderOverviewVoiceCard()}
        ${renderOverviewMusicCard()}
      </div>

      <div class="overview-feature-grid">
        <article class="glass-card overview-sense-card">
          <h2>${icon("eye")} ${escapeHtml(overviewPage.sense.title)}</h2>
          <div class="sense-toggle-row">
            ${overviewPage.sense.toggles.map((item) => `
              <span>${icon("clipboard")} ${escapeHtml(item)} <i></i></span>
            `).join("")}
          </div>
          <p>${icon("shield")} ${escapeHtml(overviewPage.sense.note)}</p>
        </article>
        <article class="glass-card overview-abilities-card">
          <h2>${icon("star")} 能力一览</h2>
          <div class="overview-ability-chip-row">
            ${overviewPage.abilities.map((item, index) => `
              <span class="tone-${index % 6}">${escapeHtml(item)}</span>
            `).join("")}
          </div>
        </article>
      </div>

      <article class="glass-card overview-health-card">
        <h2>${icon("equalizer")} 应用健康诊断</h2>
        <div class="health-grid">
          ${overviewPage.health.map(renderHealthTile).join("")}
        </div>
      </article>
    </section>
  `;
}

function renderOverviewStatusRow(item) {
  return `
    <div>
      <span>${icon(item.icon)} ${escapeHtml(item.label)}:</span>
      <strong>${escapeHtml(item.value)}</strong>
    </div>
  `;
}

function renderConnectionRow(item) {
  return `
    <div>
      <span>${icon(item.icon)}</span>
      <b>${escapeHtml(item.label)}:</b>
      <strong class="${item.tone || ""}">${escapeHtml(item.value)}</strong>
    </div>
  `;
}

function renderOverviewAction(item) {
  return `
    <button class="${item.tone}" type="button" data-action-id="${escapeAttr(item.commandId)}">
      ${icon(item.icon)}
      <span>${escapeHtml(item.label)}</span>
    </button>
  `;
}

function renderOverviewPackCard() {
  return `
    <article class="glass-card overview-pack-card">
      <h2>${escapeHtml(overviewPage.pack.title)}</h2>
      <div class="pack-folder-visual">${icon("folder")}<span>✿</span></div>
      <div>
        <strong>${escapeHtml(overviewPage.pack.name)}</strong>
        <p>版本：${escapeHtml(overviewPage.pack.version)}</p>
        <p>发布时间：${escapeHtml(overviewPage.pack.publishedAt)}</p>
        <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.characterOpenPackFolder}">${escapeHtml(overviewPage.pack.action)} ${icon("chevron")}</button>
      </div>
    </article>
  `;
}

function renderOverviewEmotionCard() {
  return `
    <article class="glass-card overview-emotion-card">
      <h2>${escapeHtml(overviewPage.emotion.title)}</h2>
      <img src="${imageFor(overviewPage.emotion.image)}" alt="" />
      <strong>${escapeHtml(overviewPage.emotion.name)}</strong>
      <div class="preview-dots"><span></span><span class="active"></span><span></span></div>
    </article>
  `;
}

function renderOverviewVoiceCard() {
  return `
    <article class="glass-card overview-voice-card">
      <h2>${icon("equalizer")} ${escapeHtml(overviewPage.voice.title)}</h2>
      <div class="overview-voice-toggles">
        ${overviewPage.voice.rows.map((row) => `
          <div><span>${escapeHtml(row.label)}</span>${renderStaticSwitch(row.enabled)}</div>
        `).join("")}
      </div>
      <p>${icon("checkCircle")} ${escapeHtml(overviewPage.voice.status)} <i></i></p>
    </article>
  `;
}

function renderOverviewMusicCard() {
  return `
    <article class="glass-card overview-music-card">
      <h2>${icon("music")} ${escapeHtml(overviewPage.music.title)}</h2>
      <div class="overview-mini-player">
        <div class="mini-album" style="--cover-image: url(${imageFor(overviewPage.music.cover)})"></div>
        <div>
          <strong>${escapeHtml(overviewPage.music.song)}</strong>
          <p>${escapeHtml(overviewPage.music.artist)}</p>
        </div>
        <span class="mini-bars" aria-hidden="true">${Array.from({ length: 12 }, (_, index) => `<i style="--bar: ${((index * 7) % 24) + 8}px"></i>`).join("")}</span>
      </div>
      <div class="mini-control-row">
        ${overviewPage.music.controls.map((item, index) => `
          <button class="${index === 2 ? "active" : ""}" type="button">${index === 0 ? icon("previous") : index === 1 ? icon("next") : index === 2 ? icon("pause") : index === 3 ? icon("stop") : icon("trash")} ${escapeHtml(item)}</button>
        `).join("")}
      </div>
    </article>
  `;
}

function renderHealthTile(item) {
  return `
    <div class="health-tile">
      <span>${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.value)}</strong>
      ${typeof item.progress === "number" ? `<i><b style="width: ${item.progress}%"></b></i>` : `<em class="${item.detail || ""}"></em>`}
      ${item.note ? `<small>${escapeHtml(item.note)}</small>` : ""}
    </div>
  `;
}

function renderStaticSwitch(isOn) {
  return `<span class="static-switch ${isOn ? "is-on" : ""}"><i></i></span>`;
}

function renderCharacterPage() {
  const activeOutfit =
    characterPage.outfits.find((item) => item.id === state.activeOutfit) || characterPage.outfits[0];
  const activeEmotion =
    characterPage.emotions.find((item) => item.id === state.activeEmotion) || characterPage.emotions[0];

  return `
    <section class="character-lab-page">
      <div class="character-top-grid">
        <article class="glass-card character-hero-panel">
          <img src="${imageFor(characterPage.hero || "happy")}" alt="" />
          <div>
            <h1>${escapeHtml(characterPage.title)}</h1>
            <p>${escapeHtml(characterPage.subtitle)}</p>
          </div>
        </article>
        <article class="glass-card pack-select-panel">
          <h2>${icon("folder")} 当前角色包选择</h2>
          <button class="select-like" type="button">
            <span>${escapeHtml(characterPage.selectedPack)}</span>
            ${icon("chevronDown")}
          </button>
          <div class="pack-action-row">
            <button class="pink-action" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.characterImportZip}">${icon("cloudUpload")} 导入 zip</button>
            <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.characterOpenPackFolder}">${icon("folder")} 打开角色包目录</button>
          </div>
        </article>
        <article class="glass-card pack-info-panel">
          <h2>当前包信息</h2>
          <dl>
            ${characterPage.packInfo
              .map((item) => `<div><dt>${escapeHtml(item.label)}:</dt><dd>${escapeHtml(item.value)}</dd></div>`)
              .join("")}
          </dl>
          <div class="pack-completeness">
            <div><span>资源完整度</span><strong>${characterPage.completeness}%</strong></div>
            <i><b style="width: ${characterPage.completeness}%"></b></i>
            <p>${icon("checkCircle")} 资源状态良好</p>
          </div>
        </article>
      </div>

      <div class="character-middle-grid">
        <article class="glass-card outfit-panel">
          <div class="card-heading">
            <h2>${icon("shirt")} 服装选择</h2>
            <button type="button">管理服装</button>
          </div>
          <div class="outfit-strip">
            ${characterPage.outfits.map((item) => renderOutfitTile(item, activeOutfit)).join("")}
            <button class="outfit-next" type="button" aria-label="更多服装">${icon("chevron")}</button>
          </div>
        </article>
        <article class="glass-card expression-panel">
          <div class="card-heading">
            <h2>${icon("smile")} 表情预览</h2>
            <button type="button">更多表情 ${icon("chevron")}</button>
          </div>
          <div class="expression-layout">
            <div class="expression-strip">
              ${characterPage.emotions.map((item) => renderExpressionTile(item, activeEmotion)).join("")}
            </div>
            <div class="expression-preview">
              <img src="${imageFor(activeEmotion.image)}" alt="" />
              <div>
                <strong>${escapeHtml(activeEmotion.name)}</strong>
                <span>${icon("circle")} 当前预览</span>
              </div>
            </div>
          </div>
        </article>
      </div>

      <div class="character-bottom-grid">
        <article class="glass-card resource-warning-panel">
          <h2>${icon("alert")} ${escapeHtml(characterPage.warning.title)}</h2>
          <div>
            <div>
              <strong>${escapeHtml(characterPage.warning.headline)}</strong>
              <p>${escapeHtml(characterPage.warning.body)}</p>
            </div>
            <button type="button">${escapeHtml(characterPage.warning.action)}</button>
            <span aria-hidden="true">${icon("folder")}</span>
          </div>
        </article>
        <article class="glass-card resource-status-panel">
          <h2>${icon("folder")} 资源与包状态</h2>
          <div class="resource-list">
            ${characterPage.resources.map(renderResourceRow).join("")}
          </div>
        </article>
        <article class="glass-card character-tip-panel">
          <h2>${icon("help")} 小贴士</h2>
          ${characterPage.tip.map((line) => `<p>${escapeHtml(line)}</p>`).join("")}
          <span aria-hidden="true">✦</span>
        </article>
      </div>

      <div class="character-action-bar">
        <button class="apply-button" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.characterApply}">${icon("checkCircle")} ${escapeHtml(characterPage.actions[0])}</button>
        <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.characterRefresh}">${icon("refresh")} ${escapeHtml(characterPage.actions[1])}</button>
        <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.characterRestoreDefaults}">${icon("undo")} ${escapeHtml(characterPage.actions[2])}</button>
      </div>
    </section>
  `;
}

function renderOutfitTile(item, activeOutfit) {
  const active = item.id === activeOutfit.id;
  return `
    <button class="outfit-tile ${active ? "active" : ""}" data-character-outfit="${escapeAttr(item.id)}" type="button">
      <img src="${imageFor(item.image)}" alt="" />
      ${active ? `<span class="check-corner">${icon("check")}</span>` : ""}
      <strong>${escapeHtml(item.name)}</strong>
      ${item.badge ? `<small>${escapeHtml(item.badge)}</small>` : ""}
    </button>
  `;
}

function renderExpressionTile(item, activeEmotion) {
  const active = item.id === activeEmotion.id;
  return `
    <button class="expression-tile ${active ? "active" : ""}" data-character-emotion="${escapeAttr(item.id)}" type="button">
      <img src="${imageFor(item.image)}" alt="" />
      <span>${escapeHtml(item.name)}</span>
    </button>
  `;
}

function renderResourceRow(item) {
  return `
    <div class="${item.tone}">
      <span>${icon(item.tone === "pink" ? "shirt" : item.tone === "blue" ? "sparkle" : "image")} ${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.value)}</strong>
    </div>
  `;
}

function imageFor(key) {
  if (isImageUrl(key)) return String(key);
  return images[key] || images.normal;
}

function isImageUrl(value) {
  const raw = String(value || "").trim();
  return /^(https?:|data:|blob:|\/)/i.test(raw);
}

function renderVoicePage() {
  return `
    ${renderPageTitle(voicePage)}
    <section class="voice-lab-page">
      <div class="voice-top-grid">
        ${renderTtsCard()}
        ${renderAsrCard()}
        ${renderVoicePreviewCard()}
      </div>

      <div class="voice-action-row">
        <button class="voice-test-button" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.voiceTest}">${icon("mic")} <span>测试语音<small>检测麦克风与识别效果</small></span></button>
        <button class="voice-stop-button" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.voiceStop}">${icon("stop")} <span>停止语音<small>停止当前语音会话</small></span></button>
      </div>

      <div class="voice-bottom-grid">
        ${renderVoiceRecordsCard()}
        ${renderVoiceQueueCard()}
        ${renderVoiceProcessingCard()}
        ${renderVoiceDiagnosticsCard()}
      </div>

      <footer class="voice-footer-note">${icon("sparkle")} ${escapeHtml(voicePage.footer)}</footer>
    </section>
  `;
}

function renderTtsCard() {
  return `
    <article class="glass-card voice-config-card tts-card">
      <div class="voice-card-title">
        <div>
          <h2>${icon("volume")} ${escapeHtml(voicePage.tts.title)}</h2>
          <p>${escapeHtml(voicePage.tts.subtitle)}</p>
        </div>
        <span>${icon("checkCircle")} 已启用</span>
      </div>
      <div class="voice-form-grid">
        <label><span>${icon("user")} 选择音色</span><button type="button">${icon("equalizer")} ${escapeHtml(voicePage.tts.voice)} ${icon("chevronDown")}</button></label>
        <label><span>${icon("volume")} 输出音量</span>${renderRangeBar(voicePage.tts.volume)}<strong>${voicePage.tts.volume}%</strong></label>
        <label><span>${icon("clock")} 语速调节</span>${renderRangeBar(52)}<strong>${escapeHtml(voicePage.tts.speed)}</strong></label>
      </div>
    </article>
  `;
}

function renderAsrCard() {
  return `
    <article class="glass-card voice-config-card asr-card">
      <div class="voice-card-title">
        <div>
          <h2>${icon("mic")} ${escapeHtml(voicePage.asr.title)}</h2>
          <p>${escapeHtml(voicePage.asr.subtitle)}</p>
        </div>
        <span>${icon("checkCircle")} 已启用</span>
      </div>
      <div class="voice-form-grid">
        <label><span>${icon("mic")} 麦克风设备</span><button type="button">${escapeHtml(voicePage.asr.device)} ${icon("chevronDown")}</button></label>
        <label><span>${icon("settings")} 识别语言</span><button type="button">${escapeHtml(voicePage.asr.language)} ${icon("chevronDown")}</button></label>
        <label><span>${icon("equalizer")} 输入灵敏度</span>${renderRangeBar(voicePage.asr.sensitivity)}<strong>${voicePage.asr.sensitivity}%</strong></label>
        <label><span>实时输入</span><em class="voice-live-wave">${Array.from({ length: 24 }, (_, index) => `<i style="--bar: ${((index * 7) % 24) + 8}px"></i>`).join("")}</em></label>
      </div>
    </article>
  `;
}

function renderVoicePreviewCard() {
  return `
    <article class="glass-card voice-preview-card">
      <h2>${icon("equalizer")} ${escapeHtml(voicePage.preview.title)}</h2>
      <p>${escapeHtml(voicePage.preview.subtitle)}</p>
      <div class="voice-preview-body">
        <img src="${imageFor("happy")}" alt="" />
        <div class="speech-bubble">
          ${voicePage.preview.text.map((line) => `<span>${escapeHtml(line)}</span>`).join("")}
        </div>
      </div>
      <div class="voice-preview-player">
        <button type="button" aria-label="播放试听">${icon("play")}</button>
        <span>${Array.from({ length: 28 }, (_, index) => `<i style="--bar: ${((index * 5) % 28) + 8}px"></i>`).join("")}</span>
        <time>${escapeHtml(voicePage.preview.duration)}</time>
      </div>
    </article>
  `;
}

function renderVoiceRecordsCard() {
  return `
    <article class="glass-card voice-list-card">
      <div class="card-heading"><h2>${icon("clock")} 识别记录</h2><button type="button">清空记录</button></div>
      <p>最近识别到的语音内容</p>
      <div class="voice-record-list">
        ${voicePage.records.map((item) => `
          <div><strong>${escapeHtml(item.text)}</strong><time>${escapeHtml(item.time)}</time><span>${escapeHtml(item.score)}</span></div>
        `).join("")}
      </div>
    </article>
  `;
}

function renderVoiceQueueCard() {
  return `
    <article class="glass-card voice-list-card">
      <div class="card-heading"><h2>${icon("equalizer")} 合成队列 / 最近朗读</h2><button type="button">清空队列</button></div>
      <p>Akane 最近为你朗读的内容</p>
      <div class="voice-queue-list">
        ${voicePage.queue.map((item, index) => `
          <div><span>${index + 1}</span><strong>${escapeHtml(item.text)}</strong><time>${escapeHtml(item.duration)}</time></div>
        `).join("")}
      </div>
    </article>
  `;
}

function renderVoiceProcessingCard() {
  return `
    <article class="glass-card voice-processing-card">
      <h2>${icon("equalizer")} 语音处理</h2>
      <div class="processing-list">
        ${voicePage.processing.map((item) => `
          <div>
            <span>${icon("plusCircle")}</span>
            <strong>${escapeHtml(item.label)}<small>${escapeHtml(item.detail)}</small></strong>
            ${renderStaticSwitch(item.enabled)}
          </div>
        `).join("")}
      </div>
      <label><span>唤醒词设置</span><button type="button">Akane</button></label>
      <label><span>唤醒灵敏度</span><button type="button">中等（推荐） ${icon("chevronDown")}</button></label>
    </article>
  `;
}

function renderVoiceDiagnosticsCard() {
  return `
    <article class="glass-card voice-diagnostics-card">
      <div class="card-heading">
        <h2>${icon("shield")} ASR / TTS 状态诊断</h2>
        <span>${icon("checkCircle")} 一切正常</span>
      </div>
      <p>实时检测语音服务状态</p>
      <div class="voice-diagnostic-list">
        ${voicePage.diagnostics.map((item) => `
          <div><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.value)}</strong><b>${icon("checkCircle")} 正常</b></div>
        `).join("")}
      </div>
      <footer>语音服务运行良好，陪伴随时在线</footer>
    </article>
  `;
}

function renderRangeBar(value) {
  return `<i class="range-visual"><b style="width: ${Number(value) || 0}%"></b><span style="left: ${Number(value) || 0}%"></span></i>`;
}

function renderMusicPage() {
  return `
    <section class="music-lab-page">
      <header class="music-title-row">
        <div>
          <h1>${escapeHtml(musicPage.title)} <span>${escapeHtml(musicPage.accent)}</span></h1>
          <p>${escapeHtml(musicPage.subtitle)}</p>
        </div>
        <div class="music-equalizer" aria-hidden="true">
          ${Array.from({ length: 34 }, (_, index) => `<i style="--bar: ${((index * 7) % 24) + 8}px"></i>`).join("")}
        </div>
      </header>

      <div class="music-top-grid">
        <article class="glass-card now-playing-panel">
          <h2>${icon("equalizer")} 当前播放</h2>
          <div class="now-playing-body">
            <div class="album-art">
              <img src="${imageFor(musicPage.nowPlaying.cover)}" alt="" />
              <span>Starry<br />Days</span>
            </div>
            <div class="track-main">
              <h3>${escapeHtml(musicPage.nowPlaying.title)} <span>♥</span></h3>
              <p>${escapeHtml(musicPage.nowPlaying.artist)} <b>${icon("sparkle")} ${escapeHtml(musicPage.nowPlaying.quality)}</b></p>
              <div class="time-row">
                <span>${escapeHtml(musicPage.nowPlaying.elapsed)}</span>
                <span>${escapeHtml(musicPage.nowPlaying.duration)}</span>
              </div>
              <div class="pink-progress"><span style="width: ${musicPage.nowPlaying.progress}%"></span></div>
              <div class="volume-row">
                ${icon("volume")}
                <div class="volume-track"><span style="width: ${musicPage.nowPlaying.volume}%"></span></div>
                <strong>${musicPage.nowPlaying.volume}%</strong>
              </div>
            </div>
          </div>
          <div class="play-mode-row">
            <span>${icon("repeat")} 播放模式</span>
            <button type="button">${icon("repeat")} 列表循环 ${icon("chevronDown")}</button>
          </div>
          <div class="music-control-row">
            <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.musicPrevious}" title="上一首" aria-label="上一首">${icon("previous")}</button>
            <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.musicNext}" title="下一首" aria-label="下一首">${icon("next")}</button>
            <button class="pause" type="button" data-action-id="${CONTROL_CENTER_ACTIONS.musicPause}" title="暂停" aria-label="暂停">${icon("pause")}</button>
            <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.musicStop}" title="停止" aria-label="停止">${icon("stop")}</button>
            <button type="button" data-action-id="${CONTROL_CENTER_ACTIONS.musicClear}" title="清空" aria-label="清空">${icon("trash")}</button>
          </div>
        </article>

        <article class="glass-card lyric-panel">
          <div class="card-heading">
            <h2>${icon("file")} 歌词</h2>
            <span>正在播放</span>
          </div>
          <div class="lyric-lines">
            ${musicPage.lyrics.map((line, index) => `
              <p class="${index === musicPage.activeLyric ? "active" : ""}">
                ${index === musicPage.activeLyric ? icon("equalizer") : ""}
                ${escapeHtml(line)}
              </p>
            `).join("")}
          </div>
        </article>

        <article class="glass-card queue-panel">
          <div class="card-heading">
            <h2>${icon("music")} 播放队列</h2>
            <span>${musicPage.playlist.length} 首</span>
          </div>
          <div class="queue-list">
            ${musicPage.playlist.map(renderQueueItem).join("")}
          </div>
        </article>
      </div>

      <div class="music-bottom-grid">
        <article class="glass-card play-info-panel">
          <h2>${icon("music")} 播放信息</h2>
          ${musicPage.info.map((item) => `
            <div><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.value)}</strong></div>
          `).join("")}
          <div class="mini-wave" aria-hidden="true">${Array.from({ length: 42 }, (_, index) => `<i style="--bar: ${((index * 5) % 18) + 4}px"></i>`).join("")}</div>
        </article>
        <article class="glass-card mood-panel">
          <h2>${icon("star")} 音乐心情</h2>
          <p>选择心情，Akane 为你匹配氛围音乐</p>
          <div class="mood-grid">
            ${musicPage.modes.map((mode) => `
              <button class="${mode === state.activeMusicMode ? "active" : ""}" data-music-mode="${escapeAttr(mode)}" type="button">
                ${renderMoodIcon(mode)} ${escapeHtml(mode)}
              </button>
            `).join("")}
          </div>
        </article>
        <article class="glass-card recommend-panel">
          <div class="card-heading">
            <h2>${icon("sparkle")} Akane 推荐</h2>
            <button type="button">${icon("refresh")} 换一批</button>
          </div>
          <div class="recommend-body">
            <img src="${imageFor(musicPage.recommendations[0]?.cover || musicPage.nowPlaying.cover)}" alt="" />
            <div>
              ${musicPage.recommendations.map((item) => `
                <div class="recommend-row">
                  <span>${icon("play")}</span>
                  <strong>${escapeHtml(item.title)}</strong>
                  <small>${escapeHtml(item.artist)}</small>
                  <time>${escapeHtml(item.duration)}</time>
                </div>
              `).join("")}
            </div>
          </div>
        </article>
      </div>

      <footer class="music-bottom-bar">
        <span>${icon("refresh")} ${escapeHtml(musicPage.bottomStatus)} ✦</span>
        <div>
          <b>音量均衡</b>
          <button class="tiny-switch is-on" type="button" aria-label="音量均衡"><span></span></button>
          <strong>${icon("volume")} 设备输出：扬声器 ${icon("chevronDown")}</strong>
        </div>
      </footer>
    </section>
  `;
}

function renderQueueItem(item) {
  return `
    <button class="queue-item ${item.active ? "active" : ""}" type="button">
      <span class="queue-cover"><img src="${imageFor(item.cover || musicPage.nowPlaying.cover)}" alt="" /></span>
      <span>
        <strong>${escapeHtml(item.title)}</strong>
        <small>${escapeHtml(item.artist)}</small>
      </span>
      <time>${escapeHtml(item.duration)}</time>
      ${item.active ? icon("equalizer") : icon("menu")}
    </button>
  `;
}

function renderMoodIcon(mode) {
  const iconMap = {
    放松: "leaf",
    专注: "target",
    治愈: "leaf",
    活力: "sun",
    思念: "heart",
    睡前: "moon"
  };
  return icon(iconMap[mode] || "sparkle");
}

function renderPerceptionPage() {
  return `
    ${renderPageTitle(perceptionPage, `<button class="privacy-help" type="button">${icon("help")} ${escapeHtml(perceptionPage.helpLabel)}</button>`)}
    <section class="perception-page">
      <div class="perception-feature-grid">
        ${perceptionPage.featureCards.map(renderPerceptionFeatureCard).join("")}
      </div>
      <div class="perception-info-row">
        ${renderPrivacyCard()}
        ${renderPermissionCard()}
      </div>
      <div class="perception-bottom-grid">
        ${renderEventCard()}
        ${renderSuggestionCard()}
        ${renderDiagnosticCard()}
      </div>
    </section>
  `;
}

function renderAbilitiesPage() {
  return `
    ${renderPageTitle(abilitiesPage)}
    <section class="abilities-page">
      <div class="ability-top-grid">
        <article class="ability-portrait-card">
          <img src="${images.normal}" alt="" />
        </article>
        <article class="glass-card ability-overview-card">
          <h2>能力概览 ${icon("info")}</h2>
          <div class="ability-stats">
            ${abilitiesPage.overview.stats.map(renderAbilityStat).join("")}
          </div>
          <div class="availability-row">
            <span>整体可用性</span>
            <strong>${abilitiesPage.overview.availability}%</strong>
          </div>
          <div class="availability-bar">
            <span style="width: ${abilitiesPage.overview.availability}%"></span>
          </div>
          <p>${icon("check")} ${escapeHtml(abilitiesPage.overview.note)}</p>
        </article>
        <article class="glass-card quick-actions-card">
          <h2>快捷操作</h2>
          <p>常用能力一键直达</p>
          <div class="quick-action-grid">
            ${abilitiesPage.quickActions.map(renderQuickAction).join("")}
          </div>
        </article>
      </div>
      <div class="ability-body-grid">
        <div class="ability-left-stack">
          <article class="glass-card modules-card">
            <div class="card-heading">
              <h2>能力模块 ${icon("info")}</h2>
              <button type="button">管理模块与权限 ${icon("chevron")}</button>
            </div>
            <div class="module-grid">
              ${abilitiesPage.modules.map(renderAbilityModule).join("")}
            </div>
          </article>
          <article class="glass-card workflow-card">
            <h2>能力工作流示例 ${icon("info")}</h2>
            <div class="workflow-list">
              ${abilitiesPage.workflows.map(renderWorkflow).join("")}
              <button class="more-workflow" type="button">更多示例 ${icon("chevron")}</button>
            </div>
          </article>
          <article class="glass-card calls-card">
            <div class="card-heading">
              <h2>最近能力调用</h2>
              <button type="button">查看全部日志 ${icon("chevron")}</button>
            </div>
            ${renderCallsTable()}
          </article>
        </div>
        <aside class="ability-right-stack">
          ${renderSafetyPanel()}
          ${renderLive2dPanel()}
        </aside>
      </div>
    </section>
  `;
}

function renderAdvancedPage() {
  return `
    ${renderPageTitle(advancedPage, renderAdvancedSystemStrip())}
    <section class="advanced-lab-page">
      <div class="advanced-main-grid">
        <div class="advanced-left-stack">
          <article class="glass-card advanced-core-card">
            <h2>${icon("cube")} 核心渲染与交互设置</h2>
            <div class="advanced-toggle-list">
              ${advancedPage.coreSettings.map(renderAdvancedCoreItem).join("")}
            </div>
          </article>

          <article class="glass-card advanced-run-card">
            <h2>${icon("zap")} 运行操作</h2>
            <div class="advanced-operation-list">
              ${advancedPage.operations.map(renderAdvancedOperation).join("")}
            </div>
          </article>
        </div>

        <article class="glass-card advanced-diagnostics-card">
          <h2>${icon("stethoscope")} 诊断信息</h2>
          <div class="advanced-metric-grid">
            ${advancedPage.diagnostics.metrics.map(renderAdvancedMetric).join("")}
          </div>
          <section class="advanced-log-panel">
            <div class="card-heading">
              <h3>运行日志 <span>最近 20 条</span></h3>
              <button type="button">${icon("trash")} 清空日志</button>
            </div>
            <div class="advanced-log-list">
              ${advancedPage.diagnostics.logs.map(renderAdvancedLog).join("")}
            </div>
            <button class="advanced-more-log" type="button">查看更多日志 ${icon("chevronDown")}</button>
          </section>
        </article>

        <div class="advanced-right-stack">
          <article class="glass-card advanced-live2d-card">
            <h2>${icon("star")} Live2D 预留状态</h2>
            <div class="advanced-live2d-body">
              <div class="advanced-live2d-ring">
                <img src="${imageFor(advancedPage.live2d.image)}" alt="" />
              </div>
              <div class="advanced-live2d-list">
                ${advancedPage.live2d.rows.map(renderAdvancedLive2dRow).join("")}
              </div>
            </div>
          </article>

          <article class="glass-card advanced-ability-card">
            <h2>${icon("star")} 能力概览</h2>
            <div class="advanced-ability-grid">
              ${advancedPage.abilityOverview.map(renderAdvancedAbility).join("")}
            </div>
          </article>
        </div>
      </div>

      <article class="glass-card advanced-expert-card">
        <h2>${icon("sparkle")} 专家选项</h2>
        <div class="advanced-expert-grid">
          ${advancedPage.expertOptions.map(renderExpertOption).join("")}
        </div>
        <p>${icon("star")} ${escapeHtml(advancedPage.expertNote)}</p>
      </article>
    </section>
  `;
}

function renderAdvancedSystemStrip() {
  return `
    <div class="advanced-system-strip">
      ${advancedPage.systemStrip.map((item) => `
        <span class="${escapeAttr(item.tone)}">
          ${icon(item.icon)}
          <b>${escapeHtml(item.label)}</b>
          ${item.value ? `<strong>${escapeHtml(item.value)}</strong>` : ""}
        </span>
      `).join("")}
    </div>
  `;
}

function renderAdvancedCoreItem(item) {
  return `
    <div class="advanced-toggle-item ${escapeAttr(item.tone)}">
      <span class="advanced-item-icon">${icon(item.icon)}</span>
      <span>
        <strong>${escapeHtml(item.title)}</strong>
        <small>${escapeHtml(item.description)}</small>
      </span>
      ${renderStaticSwitch(item.enabled)}
    </div>
  `;
}

function renderAdvancedOperation(item) {
  return `
    <div class="advanced-operation ${escapeAttr(item.tone)}">
      <span class="advanced-item-icon">${icon(item.icon)}</span>
      <span>
        <strong>${escapeHtml(item.title)}</strong>
        <small>${escapeHtml(item.description)}</small>
      </span>
      <button type="button">${escapeHtml(item.action)}</button>
    </div>
  `;
}

function renderAdvancedMetric(item) {
  return `
    <div class="advanced-metric ${escapeAttr(item.tone)} ${item.spark ? "has-spark" : ""}">
      <span class="advanced-item-icon">${icon(item.icon)}</span>
      <span>
        <small>${escapeHtml(item.label)}</small>
        <strong>${escapeHtml(item.value)}</strong>
      </span>
      ${item.spark ? `<i>${Array.from({ length: 12 }, (_, index) => `<b style="--bar:${((index * 7) % 20) + 8}px"></b>`).join("")}</i>` : ""}
    </div>
  `;
}

function renderAdvancedLog(item) {
  return `
    <div>
      <time>${escapeHtml(item.time)}</time>
      <span>${escapeHtml(item.level)}</span>
      <strong>${escapeHtml(item.message)}</strong>
    </div>
  `;
}

function renderAdvancedLive2dRow(row) {
  return `
    <div>
      <span>${escapeHtml(row.label)}：</span>
      <strong>${icon("checkCircle")} ${escapeHtml(row.value)}</strong>
    </div>
  `;
}

function renderAdvancedAbility(item) {
  return `
    <button class="${escapeAttr(item.tone)}" type="button">
      ${icon(item.icon)}
      <span>${escapeHtml(item.label)}</span>
    </button>
  `;
}

function renderExpertOption(item) {
  return `
    <div class="advanced-expert-option">
      <span class="advanced-item-icon">${icon(item.icon)}</span>
      <span>
        <strong>${escapeHtml(item.title)}</strong>
        <small>${escapeHtml(item.description)}</small>
      </span>
      ${renderStaticSwitch(item.enabled)}
    </div>
  `;
}

function renderPerceptionFeatureCard(card) {
  const isOn = Boolean(state.switches[card.id]);
  return `
    <article class="glass-card feature-card">
      <header>
        <div>
          <h2>${icon(card.icon)} ${escapeHtml(card.title)}</h2>
          <p>${escapeHtml(card.description)}</p>
        </div>
        ${renderSwitch(card.id, isOn)}
      </header>
      ${renderFeaturePreview(card)}
    </article>
  `;
}

function renderFeaturePreview(card) {
  if (card.previewType === "window") {
    return `
      <div class="feature-preview window-preview">
        <strong>${escapeHtml(card.label)}</strong>
        <div class="window-row">
          <div class="mini-code-window">
            <span></span><span></span><span></span><i></i><i></i><i></i>
          </div>
          <div>
            <b>${escapeHtml(card.appName)}</b>
            <small>${escapeHtml(card.appDetail)}</small>
            <small>${escapeHtml(card.version)}</small>
          </div>
        </div>
        <button type="button">${escapeHtml(card.action)}</button>
      </div>
    `;
  }

  if (card.previewType === "code") {
    return `
      <div class="feature-preview clipboard-preview">
        <strong>${escapeHtml(card.label)}</strong>
        <pre>${card.code.map(escapeHtml).join("\n")}</pre>
        <div class="preview-footer">
          <span>${escapeHtml(card.source)}</span>
          <button type="button">${escapeHtml(card.action)}</button>
        </div>
      </div>
    `;
  }

  if (card.previewType === "settings") {
    return `
      <div class="feature-preview settings-preview">
        <strong>${escapeHtml(card.label)}</strong>
        <label>
          <span>截图间隔</span>
          <button type="button">${escapeHtml(card.frequency)} ${icon("chevronDown")}</button>
        </label>
        <label>
          <span>保留帧数</span>
          <div class="stepper">
            <button type="button">−</button>
            <b>${escapeHtml(card.frames)}</b>
            <button type="button">＋</button>
          </div>
          <small>${escapeHtml(card.hint)}</small>
        </label>
        <p>${icon("shield")} ${escapeHtml(card.note)}</p>
      </div>
    `;
  }

  return `
    <div class="feature-preview interval-preview">
      <strong>${escapeHtml(card.label)}</strong>
      <div class="interval-grid">
        ${card.options.map((option) => `
          <button
            class="${option === state.activeInterval ? "active" : ""}"
            data-interval="${escapeAttr(option)}"
            type="button"
          >${escapeHtml(option)}</button>
        `).join("")}
      </div>
      <p>${icon("heart")} ${escapeHtml(card.note)}</p>
    </div>
  `;
}

function renderPrivacyCard() {
  return `
    <article class="glass-card privacy-card">
      <div class="privacy-shield">${icon("lock")}</div>
      <div>
        <h2>隐私与安全说明</h2>
        <ul>
          ${perceptionPage.privacy.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
        </ul>
        <button type="button">了解更多隐私保护细节 ${icon("arrowRight")}</button>
      </div>
    </article>
  `;
}

function renderPermissionCard() {
  return `
    <article class="glass-card permission-card">
      <div class="card-heading">
        <h2>权限状态</h2>
        <button type="button">管理权限</button>
      </div>
      <div class="permission-grid">
        ${perceptionPage.permissions.map((item) => `
          <div class="permission-tile ${item.tone}">
            ${icon(item.icon)}
            <span>${escapeHtml(item.label)}</span>
            <strong>${icon("checkCircle")} ${escapeHtml(item.status)}</strong>
          </div>
        `).join("")}
      </div>
    </article>
  `;
}

function renderEventCard() {
  return `
    <article class="glass-card event-card">
      <div class="card-heading">
        <h2>近期感知事件</h2>
        <button type="button">查看全部</button>
      </div>
      <div class="event-list">
        ${perceptionPage.events.map((item) => `
          <div class="event-row">
            <span class="event-icon">${icon(item.icon)}</span>
            <div>
              <strong>${escapeHtml(item.title)}</strong>
              <p>${escapeHtml(item.detail)}</p>
            </div>
            <time>${escapeHtml(item.time)}</time>
          </div>
        `).join("")}
      </div>
    </article>
  `;
}

function renderSuggestionCard() {
  return `
    <article class="glass-card suggestion-card">
      <div class="card-heading">
        <h2>${icon("sparkle")} Akane 的发现与建议</h2>
        <span>${icon("sparkle")} ${escapeHtml(perceptionPage.suggestion.badge)}</span>
      </div>
      <div class="suggestion-body">
        <div>
          <h3>${escapeHtml(perceptionPage.suggestion.title)}</h3>
          <p>${escapeHtml(perceptionPage.suggestion.body)}</p>
          <p>${escapeHtml(perceptionPage.suggestion.prompt)}</p>
          <div class="suggestion-actions">
            ${perceptionPage.suggestion.actions.map((action) => `<button type="button">${escapeHtml(action)}</button>`).join("")}
          </div>
        </div>
        <img src="${images.thinking}" alt="" />
      </div>
    </article>
  `;
}

function renderDiagnosticCard() {
  return `
    <article class="glass-card diagnostic-card">
      <h2>感知诊断</h2>
      <div class="diagnostic-list">
        ${perceptionPage.diagnostics.map((item) => `
          <div>
            <span>${icon("plusCircle")} ${escapeHtml(item.label)}</span>
            <strong class="${item.tone}">${escapeHtml(item.value)}</strong>
            <small>${escapeHtml(item.detail)}</small>
          </div>
        `).join("")}
      </div>
      <button type="button">${icon("refresh")} 运行诊断</button>
    </article>
  `;
}

function renderAbilityStat(item) {
  return `
    <div>
      <strong>${escapeHtml(item.value)}</strong>
      <span>${escapeHtml(item.label)}</span>
    </div>
  `;
}

function renderQuickAction(item) {
  return `
    <button class="quick-action ${item.tone}" type="button">
      ${icon(item.icon)}
      <span>${escapeHtml(item.label)}</span>
    </button>
  `;
}

function renderAbilityModule(item) {
  return `
    <article class="module-tile ${item.tone}">
      <div class="module-icon">${icon(item.icon)}</div>
      <div>
        <div class="module-title-row">
          <h3>${escapeHtml(item.title)}</h3>
          <span>运行中</span>
        </div>
        <p>${escapeHtml(item.description)}</p>
      </div>
      <footer>
        <span>${icon("circle")} 权限：${escapeHtml(item.permission)}</span>
        <strong>${escapeHtml(item.count)}</strong>
      </footer>
    </article>
  `;
}

function renderWorkflow(item) {
  return `
    <article class="workflow-tile">
      <div class="workflow-icons">
        ${item.steps.map((step, index) => `
          <span>${icon(index === 0 ? "folder" : index === 1 ? "file" : "upload")}</span>
        `).join("<i>›</i>")}
      </div>
      <div>
        <strong>${escapeHtml(item.title)}</strong>
        <p>${escapeHtml(item.detail)}</p>
      </div>
    </article>
  `;
}

function renderCallsTable() {
  return `
    <div class="calls-table" role="table" aria-label="最近能力调用">
      <div class="calls-row calls-head" role="row">
        <span>时间</span>
        <span>能力模块</span>
        <span>操作描述</span>
        <span>状态</span>
        <span>耗时</span>
        <span>调用方式</span>
      </div>
      ${abilitiesPage.calls.map((item) => `
        <div class="calls-row" role="row">
          <span>${escapeHtml(item.time)}</span>
          <strong>${escapeHtml(item.module)}</strong>
          <span>${escapeHtml(item.description)}</span>
          <b class="${item.status === "成功" ? "success" : "blocked"}">${escapeHtml(item.status)}</b>
          <span>${escapeHtml(item.duration)}</span>
          <span>${escapeHtml(item.method)}</span>
        </div>
      `).join("")}
    </div>
  `;
}

function renderSafetyPanel() {
  return `
    <article class="glass-card side-status-panel safety-panel">
      <div class="card-heading">
        <h2>${icon("shield")} 安全边界</h2>
        <span>${escapeHtml(abilitiesPage.safety.status)}</span>
      </div>
      <div class="side-list">
        ${abilitiesPage.safety.items.map((item) => `
          <div>
            <span>${icon("checkCircle")} ${escapeHtml(item.label)}</span>
            <strong>${icon("clock")} ${escapeHtml(item.status)}</strong>
          </div>
        `).join("")}
      </div>
      <button type="button">查看详细策略 ${icon("chevron")}</button>
    </article>
  `;
}

function renderLive2dPanel() {
  return `
    <article class="glass-card side-status-panel live-panel">
      <div class="card-heading">
        <h2>${icon("sparkle")} Live2D 预留状态</h2>
        <span>${escapeHtml(abilitiesPage.live2d.status)}</span>
      </div>
      <div class="side-list live-list">
        ${abilitiesPage.live2d.items.map((item) => `
          <div>
            <span>${escapeHtml(item.label)}</span>
            <strong>${escapeHtml(item.value)}</strong>
            ${icon("star")}
          </div>
        `).join("")}
      </div>
      <button type="button">打开 Live2D 设置面板</button>
    </article>
  `;
}

function renderSwitch(key, isOn) {
  return `
    <button class="switch ${isOn ? "is-on" : ""}" data-switch="${escapeAttr(key)}" type="button" role="switch" aria-checked="${isOn}">
      <span></span>
    </button>
  `;
}

function icon(name) {
  const paths = {
    home: '<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10.5V20h5v-5.5h3V20h5v-9.5"/>',
    user: '<path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/>',
    mic: '<path d="M12 14a3.5 3.5 0 0 0 3.5-3.5v-4a3.5 3.5 0 0 0-7 0v4A3.5 3.5 0 0 0 12 14Z"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>',
    music: '<path d="M9 18V5l10-2v13"/><path d="M9 18a3 3 0 1 1-3-3 3 3 0 0 1 3 3Z"/><path d="M19 16a3 3 0 1 1-3-3 3 3 0 0 1 3 3Z"/>',
    monitor: '<path d="M4 5h16v11H4Z"/><path d="M9 20h6"/><path d="M12 16v4"/>',
    sparkle: '<path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8Z"/><path d="m19 15 .8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8Z"/>',
    settings: '<path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"/><path d="M19.4 15a1.8 1.8 0 0 0 .36 2l.05.05-2.1 2.1-.05-.05a1.8 1.8 0 0 0-2-.36 1.8 1.8 0 0 0-1.1 1.66V20.5h-3V20.4a1.8 1.8 0 0 0-1.1-1.66 1.8 1.8 0 0 0-2 .36l-.05.05-2.1-2.1.05-.05a1.8 1.8 0 0 0 .36-2 1.8 1.8 0 0 0-1.66-1.1H5v-3h.06a1.8 1.8 0 0 0 1.66-1.1 1.8 1.8 0 0 0-.36-2l-.05-.05 2.1-2.1.05.05a1.8 1.8 0 0 0 2 .36A1.8 1.8 0 0 0 11.56 4V3.5h3V4a1.8 1.8 0 0 0 1.1 1.66 1.8 1.8 0 0 0 2-.36l.05-.05 2.1 2.1-.05.05a1.8 1.8 0 0 0-.36 2 1.8 1.8 0 0 0 1.66 1.1H21v3h-.06A1.8 1.8 0 0 0 19.4 15Z"/>',
    wifi: '<path d="M5 9.5a10 10 0 0 1 14 0"/><path d="M8.5 13a5 5 0 0 1 7 0"/><path d="M12 17h.01"/>',
    focus: '<path d="M8 4H5a1 1 0 0 0-1 1v3"/><path d="M16 4h3a1 1 0 0 1 1 1v3"/><path d="M8 20H5a1 1 0 0 1-1-1v-3"/><path d="M16 20h3a1 1 0 0 0 1-1v-3"/><path d="M9 12h6"/>',
    code: '<path d="m8 8-4 4 4 4"/><path d="m16 8 4 4-4 4"/><path d="m14 5-4 14"/>',
    cpu: '<path d="M8 8h8v8H8Z"/><path d="M4 10h4"/><path d="M4 14h4"/><path d="M16 10h4"/><path d="M16 14h4"/><path d="M10 4v4"/><path d="M14 4v4"/><path d="M10 16v4"/><path d="M14 16v4"/>',
    zap: '<path d="m13 2-8 12h6l-1 8 9-13h-6Z"/>',
    cube: '<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9Z"/><path d="M12 12 4 7.5"/><path d="m12 12 8-4.5"/><path d="M12 12v9"/>',
    stethoscope: '<path d="M6 4v5a4 4 0 0 0 8 0V4"/><path d="M4 4h4"/><path d="M12 4h4"/><path d="M10 15a5 5 0 0 0 10 0v-2"/><path d="M20 13a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z"/>',
    shirt: '<path d="M8 4 5 6.5l-2 4L6 12l1-2v10h10V10l1 2 3-1.5-2-4L16 4l-4 2Z"/><path d="M9 4a3 3 0 0 0 6 0"/>',
    smile: '<path d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"/><path d="M8.5 10h.01"/><path d="M15.5 10h.01"/><path d="M8.5 14a5 5 0 0 0 7 0"/>',
    cloudUpload: '<path d="M16 17h2.5a3.5 3.5 0 0 0 .4-7 5.5 5.5 0 0 0-10.6-1.7A4.5 4.5 0 0 0 8 17h2"/><path d="M12 18V10"/><path d="m8.5 13.5 3.5-3.5 3.5 3.5"/>',
    alert: '<path d="m12 4 9 16H3Z"/><path d="M12 9v5"/><path d="M12 17h.01"/>',
    undo: '<path d="M9 7H4v5"/><path d="M4 12a8 8 0 1 0 2.4-5.7Z"/>',
    image: '<path d="M4 5h16v14H4Z"/><path d="m7 15 3-3 2.5 2.5L15 12l2 3"/><path d="M8.5 9h.01"/>',
    bell: '<path d="M18 16H6l1.2-1.6V10a4.8 4.8 0 0 1 9.6 0v4.4Z"/><path d="M10 19a2 2 0 0 0 4 0"/>',
    minus: '<path d="M6 12h12"/>',
    square: '<path d="M7 7h10v10H7Z"/>',
    x: '<path d="m7 7 10 10"/><path d="m17 7-10 10"/>',
    lock: '<path d="M7 11V8a5 5 0 0 1 10 0v3"/><path d="M5.5 11h13v9h-13Z"/><path d="M12 15v2"/>',
    clipboard: '<path d="M9 4h6l1 2h3v15H5V6h3Z"/><path d="M9 4v3h6V4"/><path d="M8 11h8"/><path d="M8 15h6"/>',
    eye: '<path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z"/><path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"/>',
    chat: '<path d="M5 6h14v9H9l-4 4Z"/><path d="M8 10h8"/><path d="M8 13h5"/>',
    shield: '<path d="M12 3 19 6v5c0 4.5-2.8 7.8-7 10-4.2-2.2-7-5.5-7-10V6Z"/><path d="m9 12 2 2 4-5"/>',
    folder: '<path d="M3.5 6.5h6l2 2h9v9.5h-17Z"/><path d="M3.5 9h17"/>',
    file: '<path d="M7 3.5h7l4 4V20H7Z"/><path d="M14 3.5V8h4"/><path d="M9.5 12h5"/><path d="M9.5 15h5"/>',
    doc: '<path d="M7 3.5h7l4 4V20H7Z"/><path d="M14 3.5V8h4"/><path d="M10 11h4"/><path d="M10 14h6"/><path d="M10 17h4"/>',
    gift: '<path d="M4 10h16v10H4Z"/><path d="M12 10v10"/><path d="M3.5 7h17v3h-17Z"/><path d="M8.5 7C6 7 6 4 8.3 4 10 4 12 7 12 7s2-3 3.7-3C18 4 18 7 15.5 7"/>',
    play: '<path d="M8 5v14l11-7Z"/>',
    previous: '<path d="M6 5v14"/><path d="m18 6-9 6 9 6Z"/>',
    next: '<path d="M18 5v14"/><path d="m6 6 9 6-9 6Z"/>',
    pause: '<path d="M8 5h3v14H8Z"/><path d="M13 5h3v14h-3Z"/>',
    stop: '<path d="M7 7h10v10H7Z"/>',
    trash: '<path d="M4 7h16"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M6 7l1 13h10l1-13"/><path d="M9 7V4h6v3"/>',
    repeat: '<path d="M17 2l4 4-4 4"/><path d="M3 11V9a3 3 0 0 1 3-3h15"/><path d="M7 22l-4-4 4-4"/><path d="M21 13v2a3 3 0 0 1-3 3H3"/>',
    volume: '<path d="M4 10v4h4l5 4V6l-5 4Z"/><path d="M16 9a5 5 0 0 1 0 6"/><path d="M18.5 6.5a8 8 0 0 1 0 11"/>',
    equalizer: '<path d="M4 14v4"/><path d="M8 10v8"/><path d="M12 6v12"/><path d="M16 9v9"/><path d="M20 13v5"/>',
    menu: '<path d="M5 7h14"/><path d="M5 12h14"/><path d="M5 17h14"/>',
    leaf: '<path d="M5 19c10 0 14-8 14-14-8 0-14 4-14 14Z"/><path d="M5 19c3-5 7-8 14-14"/>',
    target: '<path d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"/><path d="M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z"/><path d="M12 12h.01"/>',
    sun: '<path d="M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.9 4.9 1.4 1.4"/><path d="m17.7 17.7 1.4 1.4"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m4.9 19.1 1.4-1.4"/><path d="m17.7 6.3 1.4-1.4"/>',
    moon: '<path d="M20 14.5A7.5 7.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5Z"/>',
    log: '<path d="M7 4h10v16H7Z"/><path d="M10 8h4"/><path d="M10 12h4"/><path d="M10 16h3"/>',
    panel: '<path d="M4 5h16v14H4Z"/><path d="M4 10h16"/><path d="M10 10v9"/>',
    upload: '<path d="M12 16V4"/><path d="m8 8 4-4 4 4"/><path d="M5 16v4h14v-4"/>',
    window: '<path d="M4 5h16v14H4Z"/><path d="M4 9h16"/><path d="M8 7h.01"/><path d="M11 7h.01"/>',
    camera: '<path d="M5 8h3l1.5-2h5L16 8h3v10H5Z"/><path d="M12 16a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"/>',
    message: '<path d="M4 5h16v11H9l-5 4Z"/><path d="M8 10h8"/><path d="M8 13h5"/>',
    help: '<path d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"/><path d="M9.8 9.5a2.3 2.3 0 1 1 3.7 1.8c-.9.6-1.5 1-1.5 2.2"/><path d="M12 16h.01"/>',
    info: '<path d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"/><path d="M12 11v5"/><path d="M12 8h.01"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
    chevronDown: '<path d="m7 10 5 5 5-5"/>',
    arrowRight: '<path d="M5 12h14"/><path d="m13 6 6 6-6 6"/>',
    check: '<path d="m5 12 4 4 10-10"/>',
    heart: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.7A4 4 0 0 1 19 10c0 5.6-7 10-7 10Z"/>',
    plusCircle: '<path d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"/><path d="M12 8v8"/><path d="M8 12h8"/>',
    checkCircle: '<path d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"/><path d="m8.5 12 2.2 2.2 4.8-5"/>',
    refresh: '<path d="M20 12a8 8 0 0 1-13.6 5.7"/><path d="M4 12A8 8 0 0 1 17.6 6.3"/><path d="M17 3v4h-4"/><path d="M7 21v-4h4"/>',
    circle: '<path d="M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z"/>',
    star: '<path d="m12 4 2.2 4.5 5 .7-3.6 3.5.9 5-4.5-2.4-4.5 2.4.9-5-3.6-3.5 5-.7Z"/>',
    clock: '<path d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"/><path d="M12 8v5l3 2"/>'
  };
  return `<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.sparkle}</svg>`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
    return entities[char];
  });
}

function escapeAttr(value) {
  return escapeHtml(value);
}

function formatError(error) {
  return error instanceof Error ? error.message : String(error || "unknown");
}
