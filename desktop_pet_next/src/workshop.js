import { invoke } from "@tauri-apps/api/core";
import { emit, listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";

import "./workshop.css";

const SETTINGS_COMMAND_EVENT = "akane-next-settings-command";
const SETTINGS_SNAPSHOT_EVENT = "akane-next-settings-snapshot";

const isTauriRuntime = Boolean(window.__TAURI_INTERNALS__);
const appWindow = isTauriRuntime ? getCurrentWindow() : null;

const els = {
  summary: document.querySelector("#workshop-summary"),
  status: document.querySelector("#workshop-status"),
  activeCharacter: document.querySelector("#active-character"),
  activePack: document.querySelector("#active-pack"),
  activeSession: document.querySelector("#active-session"),
  packCount: document.querySelector("#pack-count"),
  packDetailList: document.querySelector("#pack-detail-list"),
  packList: document.querySelector("#pack-list"),
  refreshPacks: document.querySelector("#refresh-packs"),
  openPacksFolder: document.querySelector("#open-packs-folder"),
  closeWindow: document.querySelector("#close-window")
};

const view = {
  packs: [],
  activePackId: "",
  activeSessionId: "",
  activeCharacterName: ""
};

boot();

async function boot() {
  bindUi();
  if (!isTauriRuntime) {
    setStatus("浏览器预览模式。");
    render();
    return;
  }

  await listen(SETTINGS_SNAPSHOT_EVENT, (event) => {
    applySnapshot(event.payload);
  });
  await refreshPacks();
  await emit(SETTINGS_COMMAND_EVENT, { command: "requestSnapshot", value: null });
}

function bindUi() {
  els.refreshPacks.addEventListener("click", () => refreshPacks());
  els.openPacksFolder.addEventListener("click", () => openPacksFolder());
  els.closeWindow.addEventListener("click", () => {
    void appWindow?.close?.();
  });
  els.packList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-apply-pack]");
    if (!button) return;
    const packId = String(button.dataset.applyPack || "").trim();
    if (packId) void applyPack(packId);
  });
}

async function refreshPacks() {
  if (!isTauriRuntime) return;
  setStatus("正在刷新角色包。");
  try {
    const [packs, petState] = await Promise.all([
      invoke("list_character_packs"),
      invoke("load_pet_state")
    ]);
    view.packs = normalizePacks(packs);
    view.activePackId = String(petState?.characterPackId || view.activePackId || "").trim();
    view.activeSessionId = String(petState?.sessionId || view.activeSessionId || "").trim();
    view.activeCharacterName = getPackName(findPack(view.activePackId)) || view.activeCharacterName;
    render();
    setStatus("角色包已刷新。");
  } catch (error) {
    setStatus(`刷新失败：${formatError(error)}`);
  }
}

function applySnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return;
  const state = snapshot.state || {};
  const character = snapshot.character || {};
  view.activePackId = String(character.packId || state.characterPackId || view.activePackId || "").trim();
  view.activeSessionId = String(state.sessionId || view.activeSessionId || "").trim();
  view.activeCharacterName = String(character.appName || character.name || view.activeCharacterName || "").trim();
  const available = Array.isArray(character.availablePacks) ? character.availablePacks : [];
  if (available.length) {
    view.packs = normalizePacks(available);
  }
  render();
}

async function applyPack(packId) {
  const pack = findPack(packId);
  setStatus(`正在应用：${getPackName(pack) || packId}`);
  try {
    await emit(SETTINGS_COMMAND_EVENT, { command: "setCharacterPack", value: packId });
    view.activePackId = packId;
    view.activeCharacterName = getPackName(pack) || view.activeCharacterName;
    render();
  } catch (error) {
    setStatus(`应用失败：${formatError(error)}`);
  }
}

async function openPacksFolder() {
  try {
    await invoke("open_character_packs_folder");
    setStatus("已打开角色包目录。");
  } catch (error) {
    setStatus(`打开失败：${formatError(error)}`);
  }
}

function render() {
  const activePack = findPack(view.activePackId);
  const activeName = view.activeCharacterName || getPackName(activePack) || "-";
  els.activeCharacter.textContent = activeName;
  els.activePack.textContent = view.activePackId || "-";
  els.activeSession.textContent = shortId(view.activeSessionId);
  els.summary.textContent = view.packs.length
    ? `${activeName} · ${view.packs.length} 个角色包`
    : "还没有读到角色包。";
  els.packCount.textContent = String(view.packs.length);
  renderPackDetails(activePack);

  if (!view.packs.length) {
    els.packList.textContent = "暂无角色包。";
    return;
  }

  els.packList.replaceChildren(
    ...view.packs.map((pack) => {
      const card = document.createElement("article");
      card.className = "pack-card";
      card.classList.toggle("active", pack.id === view.activePackId || Boolean(pack.selected));

      const heading = document.createElement("div");
      heading.className = "pack-card-heading";
      heading.append(buildText("strong", getPackName(pack) || pack.id), buildText("span", pack.id));

      const meta = document.createElement("p");
      meta.textContent = buildPackMeta(pack);

      const button = document.createElement("button");
      button.type = "button";
      button.dataset.applyPack = pack.id;
      button.disabled = pack.id === view.activePackId || Boolean(pack.selected);
      button.textContent = button.disabled ? "已启用" : "应用";

      card.append(heading, meta, button);
      return card;
    })
  );
}

function renderPackDetails(pack) {
  const rows = [
    ["名称", getPackName(pack) || "-"],
    ["Pack ID", pack?.id || "-"],
    ["角色 ID", pack?.characterId || "-"],
    ["Schema", pack?.schemaVersion || "-"],
    ["默认服装", pack?.defaultOutfit || "-"],
    ["默认表情", pack?.defaultEmotion || "-"],
    ["资源数", pack?.assetCount ? String(pack.assetCount) : "-"],
    ["来源", pack?.source || "-"]
  ];
  els.packDetailList.replaceChildren(
    ...rows.map(([label, value]) => {
      const row = document.createElement("div");
      const key = document.createElement("dt");
      const data = document.createElement("dd");
      key.textContent = label;
      data.textContent = value;
      row.append(key, data);
      return row;
    })
  );
}

function normalizePacks(value) {
  return (Array.isArray(value) ? value : [])
    .map((item) => {
      const pack = item && typeof item === "object" ? item : {};
      const profile = pack.profile && typeof pack.profile === "object" ? pack.profile : {};
      const identity = profile.identity && typeof profile.identity === "object" ? profile.identity : {};
      const appearance = profile.appearance && typeof profile.appearance === "object" ? profile.appearance : {};
      return {
        id: String(pack.id || pack.packId || "").trim(),
        name: String(pack.appName || pack.name || identity.app_name || identity.appName || identity.name || "").trim(),
        characterId: String(pack.characterId || identity.id || "").trim(),
        characterName: String(pack.characterName || identity.name || "").trim(),
        schemaVersion: String(pack.schemaVersion || profile.schema_version || profile.schemaVersion || "").trim(),
        defaultOutfit: String(pack.defaultOutfit || appearance.default_outfit || appearance.defaultOutfit || "").trim(),
        defaultEmotion: String(pack.defaultEmotion || appearance.default_emotion || appearance.defaultEmotion || "").trim(),
        assetCount: Number(pack.assetCount || 0),
        source: String(pack.source || "").trim(),
        selected: Boolean(pack.selected)
      };
    })
    .filter((pack) => pack.id)
    .sort((left, right) => getPackName(left).localeCompare(getPackName(right), "zh-Hans-CN"));
}

function findPack(packId) {
  const id = String(packId || "").trim();
  return view.packs.find((pack) => pack.id === id) || null;
}

function getPackName(pack) {
  return String(pack?.name || pack?.characterName || pack?.characterId || "").trim();
}

function buildPackMeta(pack) {
  const parts = [
    pack.characterName || pack.characterId || "",
    pack.defaultOutfit ? `服装 ${pack.defaultOutfit}` : "",
    pack.defaultEmotion ? `默认 ${pack.defaultEmotion}` : "",
    pack.schemaVersion || "",
    pack.assetCount ? `${pack.assetCount} 个文件` : ""
  ].filter(Boolean);
  return parts.join(" · ") || "角色包";
}

function buildText(tagName, text) {
  const element = document.createElement(tagName);
  element.textContent = text;
  return element;
}

function shortId(value) {
  const text = String(value || "").trim();
  if (!text) return "-";
  return text.length <= 14 ? text : `${text.slice(0, 7)}...${text.slice(-5)}`;
}

function setStatus(message) {
  els.status.textContent = String(message || "");
}

function formatError(error) {
  return error instanceof Error ? error.message : String(error || "unknown error");
}
