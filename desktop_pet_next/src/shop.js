import { invoke } from "@tauri-apps/api/core";
import { emit, emitTo, listen } from "@tauri-apps/api/event";
import {
  SETTINGS_COMMAND_EVENT,
  SETTINGS_SNAPSHOT_EVENT
} from "./control-center/event-bridge.js";

import "./shop.css";

const SHOP_STATUS_EVENT = "akane-next-shop-status";

const els = {
  summary: document.querySelector("#shop-summary"),
  alert: document.querySelector("#shop-alert"),
  coins: document.querySelector("#care-coins"),
  hunger: document.querySelector("#care-hunger"),
  energy: document.querySelector("#care-energy"),
  affection: document.querySelector("#care-affection"),
  workPanel: document.querySelector("#work-panel"),
  workSummary: document.querySelector("#work-summary"),
  startWork: document.querySelector("#start-work"),
  allowancePanel: document.querySelector("#allowance-panel"),
  allowanceSummary: document.querySelector("#allowance-summary"),
  claimAllowance: document.querySelector("#claim-allowance"),
  shopCount: document.querySelector("#shop-count"),
  inventoryCount: document.querySelector("#inventory-count"),
  shopItems: document.querySelector("#shop-items"),
  inventoryItems: document.querySelector("#inventory-items"),
  createItem: document.querySelector("#create-item"),
  character: document.querySelector("#shop-character"),
  status: document.querySelector("#shop-status"),
  refresh: document.querySelector("#refresh-shop"),
  close: document.querySelector("#close-shop"),
  editor: document.querySelector("#food-editor"),
  form: document.querySelector("#food-form"),
  editorTitle: document.querySelector("#editor-title"),
  editorDescription: document.querySelector("#editor-description"),
  editorError: document.querySelector("#editor-error"),
  save: document.querySelector("#editor-save")
};

let snapshot = null;
let shopSettings = null;
let shopScope = "";
let shopLoading = false;
let editing = null;
let saving = false;
let requestSequence = 0;
let pendingAction = false;
let actionTimer = 0;

function currentScope() {
  return [snapshot?.state?.backendUrl, snapshot?.state?.boundBotId, snapshot?.character?.packId].join("|");
}
let workCountdownTimer = 0;
let allowanceCountdownTimer = 0;

init();

async function init() {
  bindActions();
  render();
  await bindStateSync();
}

function bindActions() {
  for (const id of ["editor-close", "editor-cancel"]) document.querySelector(`#${id}`).addEventListener("click", () => { if (!saving) els.editor.close(); });
  els.editor.addEventListener("cancel", (event) => { if (saving) event.preventDefault(); });
  els.editor.addEventListener("close", () => { editing = null; });
  els.form.addEventListener("submit", (event) => { event.preventDefault(); void saveFood(); });
  els.createItem?.addEventListener("click", () => openCreateEditor());
  els.refresh?.addEventListener("click", () => {
    setStatus("正在刷新");
    void sendCommand("requestSnapshot");
    void loadShopSettings();
  });
  els.close?.addEventListener("click", () => {
    void closeWindow();
  });
  els.startWork?.addEventListener("click", () => {
    if (!isCareFeatureAvailable()) {
      showCareDisabled();
      return;
    }
    setStatus("准备出门");
    void sendCommand("startCareWork");
  });
  els.claimAllowance?.addEventListener("click", () => {
    if (!isCareFeatureAvailable()) {
      showCareDisabled();
      return;
    }
    setStatus("领取补给");
    void sendCommand("claimCareAllowance");
  });
}

async function bindStateSync() {
  try {
    await listen(SETTINGS_SNAPSHOT_EVENT, (event) => {
      snapshot = event.payload || null;
      const scope = currentScope();
      if (scope !== shopScope) {
        shopScope = scope;
        shopSettings = null;
        saving = false;
        pendingAction = false;
        window.clearTimeout(actionTimer);
        els.editor.close();
        void loadShopSettings();
      }
      render();
    });
    await listen(SHOP_STATUS_EVENT, (event) => {
      const payload = event.payload || {};
      pendingAction = false;
      window.clearTimeout(actionTimer);
      render();
      showAlert(String(payload.message || ""), String(payload.tone || "info"));
    });
    await sendCommand("requestSnapshot");
  } catch (error) {
    showAlert(`商店同步失败：${formatError(error)}`, "error");
  }
}

function render() {
  const character = snapshot?.character || {};
  if (!isCareFeatureAvailable()) {
    window.clearTimeout(workCountdownTimer);
    window.clearTimeout(allowanceCountdownTimer);
    els.coins.textContent = "—";
    els.hunger.textContent = "—";
    els.energy.textContent = "—";
    els.affection.textContent = "—";
    els.character.textContent = `当前角色：${character.name || character.packId || "—"}`;
    els.summary.textContent = "养成模块未启用";
    els.shopCount.textContent = "0 件商品";
    els.inventoryCount.textContent = "0 件物品";
    if (els.createItem) els.createItem.disabled = true;
    els.workPanel.hidden = true;
    els.allowancePanel.hidden = true;
    els.shopItems.innerHTML = `<div class="empty-state">当前实例未启用养成模块。</div>`;
    els.inventoryItems.innerHTML = `<div class="empty-state">养成模块启用后可查看背包。</div>`;
    return;
  }
  const careConfig = normalizeCareConfig(character.care);
  const care = normalizeCareState(snapshot?.state?.care, careConfig);
  const items = careConfig.enabled ? mergeShopItems(careConfig.shopItems, shopSettings?.items) : [];
  if (els.createItem) {
    els.createItem.disabled = !careConfig.enabled || !shopSettings || shopLoading || saving || pendingAction;
  }

  els.coins.textContent = String(care.coins);
  els.hunger.textContent = String(care.hunger);
  els.energy.textContent = String(care.energy);
  els.affection.textContent = String(care.affection);
  els.character.textContent = `当前角色：${character.name || character.packId || "—"}`;
  els.summary.textContent = careConfig.enabled
    ? `${character.name || "角色"}的小卖部`
    : "这个角色还没有配置商店";
  els.shopCount.textContent = `${items.length} 件商品`;

  renderWork(careConfig, care);
  renderAllowance(careConfig, care);
  renderShopItems(items, care);
  renderInventory(items, care);
}

function isCareFeatureAvailable() {
  const feature = snapshot?.resource?.features?.care;
  return Boolean(feature && feature.enabled === true && feature.status === "enabled");
}

function showCareDisabled() {
  showAlert("养成模块未启用。", "disabled");
  setStatus("养成模块未启用");
}

function renderWork(config, care) {
  const work = config.work || { enabled: false };
  els.workPanel.hidden = !config.enabled || !work.enabled;
  window.clearTimeout(workCountdownTimer);
  if (els.workPanel.hidden) return;

  const task = care.workTask;
  if (task) {
    const remainMs = Math.max(0, task.completeAt - Date.now());
    const remainSeconds = Math.ceil(remainMs / 1000);
    els.workSummary.textContent = remainSeconds > 0
      ? `外出中，约 ${remainSeconds} 秒后回来。`
      : "差不多该回来了，正在结算。";
    els.startWork.textContent = "外出中";
    els.startWork.disabled = true;
    workCountdownTimer = window.setTimeout(render, Math.min(1000, Math.max(250, remainMs)));
    return;
  }

  els.workSummary.textContent =
    `外出 ${work.durationSeconds} 秒，消耗 饥饿 ${work.hungerCost} / 精力 ${work.energyCost}，` +
    `回来可获得 ${work.rewardCoinsMin}-${work.rewardCoinsMax} 金币。`;
  els.startWork.textContent = "出门";
  els.startWork.disabled = pendingAction || saving || care.hunger < work.minHunger || care.energy < work.minEnergy;
}

function renderAllowance(config, care) {
  const allowance = config.allowance || { enabled: false };
  els.allowancePanel.hidden = !config.enabled || !allowance.enabled;
  window.clearTimeout(allowanceCountdownTimer);
  if (els.allowancePanel.hidden) return;

  const now = Date.now();
  const cooldownMs = allowance.cooldownSeconds * 1000;
  const nextAt = care.lastAllowanceAt + cooldownMs;
  const remainMs = Math.max(0, nextAt - now);
  const lowEnough = care.coins < allowance.maxCoins;
  const ready = lowEnough && remainMs <= 0;

  if (!lowEnough) {
    els.allowanceSummary.textContent = `金币低于 ${allowance.maxCoins} 时可领取 ${allowance.coins} 金币应急补给。`;
  } else if (remainMs > 0) {
    const remainSeconds = Math.ceil(remainMs / 1000);
    els.allowanceSummary.textContent = `补给冷却中，约 ${remainSeconds} 秒后可领取。`;
    allowanceCountdownTimer = window.setTimeout(render, Math.min(1000, Math.max(250, remainMs)));
  } else {
    els.allowanceSummary.textContent = `可领取 ${allowance.coins} 金币，最多补到 ${allowance.maxCoins} 金币。`;
  }
  els.claimAllowance.textContent = "领取";
  els.claimAllowance.disabled = pendingAction || saving || !ready;
}

function renderShopItems(items, care) {
  if (!items.length) {
    els.shopItems.innerHTML = `<div class="empty-state">货架上还没有商品，可以用「新增商品」加一个。</div>`;
    return;
  }

  els.shopItems.replaceChildren(...items.map((item) => createShopItem(item, care)));
}

function mergeShopItems(baseItems, savedItems) {
  const saved = Array.isArray(savedItems) ? savedItems : [];
  const merged = baseItems.map((item) => {
    const update = saved.find((value) => value.id === item.id);
    if (!update) return item;
    return { ...item, ...update, description: update.description || item.description };
  });
  const known = new Set(baseItems.map((item) => item.id));
  for (const item of saved) if (!known.has(item.id)) merged.push(item);
  return merged;
}

function createShopItem(item, care) {
  const element = document.createElement("article");
  element.className = "shop-item";

  const canBuy = care.coins >= item.price && !pendingAction && !saving && !shopLoading;
  element.innerHTML = `
    <div class="item-mark">${foodIcon(item.name)}</div>
    <div class="item-body">
      <div class="item-topline">
        <h3>${escapeHtml(item.name)}</h3>
        <span>${item.price} 金币</span>
      </div>
      <p class="item-description">${escapeHtml(item.description || "给今天添一点好心情。")}</p>
      <div class="effect-badges">${effectBadges(item.effects)}</div>
      <div class="item-actions"></div>
    </div>
  `;

  const actions = element.querySelector(".item-actions");
  const buyButton = document.createElement("button");
  buyButton.type = "button";
  buyButton.className = canBuy ? "primary-button" : "";
  buyButton.textContent = "购买";
  buyButton.disabled = !canBuy;
  buyButton.addEventListener("click", () => {
    if (!isCareFeatureAvailable()) {
      showCareDisabled();
      return;
    }
    setStatus(`购买 ${item.name}`);
    void sendCommand("buyShopItem", item.id);
  });
  const editButton = document.createElement("button");
  editButton.type = "button";
  editButton.className = "edit-button";
  editButton.textContent = shopLoading ? "读取中" : "调整食物";
  editButton.disabled = !shopSettings || shopLoading || saving || pendingAction;
  editButton.addEventListener("click", () => openFoodEditor(item));
  const deleteButton = document.createElement("button");
  deleteButton.type = "button";
  deleteButton.className = "delete-button";
  deleteButton.textContent = "下架";
  deleteButton.disabled = !shopSettings || shopLoading || saving || pendingAction;
  deleteButton.addEventListener("click", () => { void removeShopItem(item); });
  actions.append(deleteButton, editButton, buyButton);
  return element;
}

async function removeShopItem(item) {
  if (!shopSettings || saving || shopLoading || pendingAction) return;
  if (!window.confirm(`把「${item.name}」从货架上撤下来吗？已有的存货还能继续使用。`)) return;
  const scope = currentScope();
  const revision = shopSettings.revision;
  saving = true;
  render();
  try {
    shopSettings = await shopRequest("delete", { revision, item_id: item.id });
    if (scope !== currentScope()) return;
    showAlert(`已下架「${item.name}」。`, "ok");
    await loadShopSettings();
    await sendCommand("refreshCharacterPacks", { apply: false });
  } catch (error) {
    if (scope === currentScope()) showAlert(formatError(error), "error");
  } finally {
    if (scope === currentScope()) {
      saving = false;
      render();
    }
  }
}

function renderInventory(items, care) {
  const entries = items
    .map((item) => ({ item, count: Math.max(0, Math.round(Number(care.inventory[item.id]) || 0)) }))
    .filter((entry) => entry.count > 0);
  const total = entries.reduce((sum, entry) => sum + entry.count, 0);
  els.inventoryCount.textContent = `${total} 件物品`;

  if (!entries.length) {
    els.inventoryItems.innerHTML = `<div class="empty-state">背包是空的。</div>`;
    return;
  }

  els.inventoryItems.replaceChildren(...entries.map(({ item, count }) => createInventoryItem(item, count)));
}

function createInventoryItem(item, count) {
  const element = document.createElement("article");
  element.className = "shop-item";
  element.innerHTML = `
    <div class="item-mark">${count}</div>
    <div class="item-body">
      <div class="item-topline">
        <h3>${escapeHtml(item.name)}</h3>
        <span>持有 ${count}</span>
      </div>
      <p>${escapeHtml(formatEffects(item.effects))}</p>
      <div class="item-actions"></div>
    </div>
  `;

  const feedButton = document.createElement("button");
  feedButton.type = "button";
  feedButton.className = "primary-button";
  feedButton.textContent = "投喂";
  feedButton.disabled = pendingAction || saving || shopLoading;
  feedButton.addEventListener("click", () => {
    if (!isCareFeatureAvailable()) {
      showCareDisabled();
      return;
    }
    setStatus(`投喂 ${item.name}`);
    void sendCommand("feedInventoryItem", item.id);
  });
  element.querySelector(".item-actions").append(feedButton);
  return element;
}

function normalizeCareConfig(value) {
  const source = value && typeof value === "object" ? value : {};
  return {
    enabled: Boolean(source.enabled),
    initialCoins: toInteger(source.initialCoins, 0),
    initialHunger: toInteger(source.initialHunger, 50),
    initialEnergy: toInteger(source.initialEnergy, 50),
    initialAffection: toInteger(source.initialAffection, 0),
    work: normalizeCareWork(source.work),
    allowance: normalizeCareAllowance(source.allowance),
    shopItems: Array.isArray(source.shopItems) ? source.shopItems.map(normalizeShopItem).filter((item) => item.id) : []
  };
}

function normalizeCareWork(value) {
  const source = value && typeof value === "object" ? value : {};
  return {
    enabled: Boolean(source.enabled),
    durationSeconds: Math.max(1, toInteger(source.durationSeconds, 20)),
    rewardCoinsMin: Math.max(0, toInteger(source.rewardCoinsMin, 5)),
    rewardCoinsMax: Math.max(0, toInteger(source.rewardCoinsMax, 10)),
    minHunger: clamp(toInteger(source.minHunger, 20), 0, 100),
    minEnergy: clamp(toInteger(source.minEnergy, 25), 0, 100),
    hungerCost: clamp(toInteger(source.hungerCost, 12), 0, 100),
    energyCost: clamp(toInteger(source.energyCost, 25), 0, 100)
  };
}

function normalizeCareAllowance(value) {
  const source = value && typeof value === "object" ? value : {};
  return {
    enabled: Boolean(source.enabled),
    coins: Math.max(1, toInteger(source.coins, 4)),
    cooldownSeconds: Math.max(0, toInteger(source.cooldownSeconds, 300)),
    maxCoins: Math.max(1, toInteger(source.maxCoins, 6))
  };
}

function normalizeShopItem(value) {
  const source = value && typeof value === "object" ? value : {};
  const effects = source.effects && typeof source.effects === "object" ? source.effects : {};
  return {
    id: String(source.id || "").trim(),
    name: String(source.name || source.id || "").trim(),
    description: String(source.description || "").trim(),
    price: Math.max(0, toInteger(source.price, 0)),
    category: String(source.category || "").trim(),
    preferenceTags: normalizeStringArray(source.preferenceTags || source.preference_tags),
    usableIn: normalizeStringArray(source.usableIn || source.usable_in),
    feedbackTone: String(source.feedbackTone || source.feedback_tone || "").trim(),
    effects: {
      hunger: toInteger(effects.hunger, 0),
      affection: toInteger(effects.affection, 0),
      energy: toInteger(effects.energy, 0)
    }
  };
}

function normalizeStringArray(value) {
  return Array.isArray(value) ? value.map((item) => String(item || "").trim()).filter(Boolean) : [];
}

function normalizeCareState(value, config) {
  const source = value && typeof value === "object" ? value : {};
  const inventory = source.inventory && typeof source.inventory === "object" ? source.inventory : {};
  return {
    coins: toInteger(source.coins, config.initialCoins),
    hunger: clamp(toInteger(source.hunger, config.initialHunger), 0, 100),
    energy: clamp(toInteger(source.energy, config.initialEnergy), 0, 100),
    affection: clamp(toInteger(source.affection, config.initialAffection), 0, 100),
    inventory,
    workTask: normalizeWorkTask(source.workTask || source.work_task),
    lastAllowanceAt: Math.max(0, toInteger(source.lastAllowanceAt || source.last_allowance_at, 0))
  };
}

function normalizeWorkTask(value) {
  const source = value && typeof value === "object" ? value : {};
  const completeAt = Math.max(0, toInteger(source.completeAt || source.complete_at, 0));
  if (!completeAt) return null;
  return {
    completeAt,
    rewardCoins: Math.max(0, toInteger(source.rewardCoins || source.reward_coins, 0))
  };
}

async function sendCommand(command, value = null) {
  const scoped = ["buyShopItem", "feedInventoryItem", "startCareWork", "claimCareAllowance"].includes(command);
  if (scoped && pendingAction) return;
  const payload = { command, value, operationId: crypto.randomUUID(),
    ...(scoped ? { characterPackId: snapshot?.character?.packId, boundBotId: snapshot?.state?.boundBotId } : {}) };
  if (scoped) {
    pendingAction = true;
    render();
    actionTimer = window.setTimeout(() => {
      pendingAction = false;
      showAlert("操作回执尚未确认，请先刷新查看结果，避免重复购买或投喂。", "warn");
      render();
    }, 20000);
  }
  try {
    await emitTo("main", SETTINGS_COMMAND_EVENT, payload);
  } catch (error) {
    try {
      await emit(SETTINGS_COMMAND_EVENT, payload);
    } catch {
      showAlert(`命令发送失败：${formatError(error)}`, "error");
    }
  }
}

async function closeWindow() {
  try {
    await invoke("close_window");
  } catch {
    window.close();
  }
}

function showAlert(message, tone = "info") {
  if (!message) return;
  els.alert.hidden = false;
  els.alert.dataset.status = tone;
  els.alert.textContent = message;
  setStatus(message);
  window.clearTimeout(showAlert.timer);
  showAlert.timer = window.setTimeout(() => {
    els.alert.hidden = true;
  }, 2200);
}

function setStatus(message) {
  els.status.textContent = message || "Ready";
}

function formatEffects(effects = {}) {
  const parts = [];
  if (effects.hunger) parts.push(`饥饿 ${formatSigned(effects.hunger)}`);
  if (effects.energy) parts.push(`精力 ${formatSigned(effects.energy)}`);
  if (effects.affection) parts.push(`好感 ${formatSigned(effects.affection)}`);
  return parts.join(" / ") || "普通小物件";
}

function formatSigned(value) {
  const number = Number(value) || 0;
  return number > 0 ? `+${number}` : String(number);
}

function toInteger(value, fallback) {
  const number = Math.round(Number(value));
  return Number.isFinite(number) ? number : fallback;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatError(error) {
  return error?.message || String(error || "unknown");
}

function foodIcon(name) {
  if (/茶|咖啡|奶|饮|水/.test(name)) return "🍵";
  if (/饭|团|寿司|便当/.test(name)) return "🍙";
  if (/果|莓|桃|苹/.test(name)) return "🍎";
  if (/蛋糕|甜|布丁|巧克力/.test(name)) return "🍰";
  if (/面|包|饼/.test(name)) return "🥐";
  return "🍬";
}

function effectBadges(effects) {
  return [["hunger", "饥饿"], ["energy", "精力"], ["affection", "好感"]]
    .map(([key, label]) => `<span>${label} <b>${formatSigned(effects[key])}</b></span>`).join("");
}

async function shopRequest(action, values = {}) {
  const base = snapshot?.state?.backendUrl;
  const bot = snapshot?.state?.boundBotId;
  const pack = snapshot?.character?.packId;
  if (!base || !bot || !pack) throw new Error("商店尚未连接，请稍后刷新。");
  const url = new URL(`/api/bots/${encodeURIComponent(bot)}/desktop-pet/care/shop`, base).href;
  const reply = await invoke("backend_admin_request", { request: { url, method: "POST",
    body: JSON.stringify({ action, character_pack_id: pack, ...values }) } });
  let result;
  try { result = JSON.parse(reply.body || "{}"); } catch { throw new Error("商店返回了无法读取的结果。"); }
  if (!reply.ok || !result.ok) {
    if (result.reason === "shop_revision_conflict") throw new Error("食物配置已在其他地方更新。请取消编辑、刷新后再修改。");
    if (result.reason === "shop_item_limit_reached") throw new Error("货架上的商品已达上限，先整理一些再新增。");
    if (result.reason === "invalid_shop_name") throw new Error("商品名需要 1～40 个字符，且不能包含换行。");
    if (result.reason === "invalid_shop_price" || result.reason === "invalid_shop_effects") throw new Error("价格或效果超出可设置的范围。");
    if (reply.httpStatus === 401 || reply.httpStatus === 403) throw new Error("当前连接没有商店管理权限。");
    throw new Error("食物设置未能保存或读取，请检查连接及输入范围。");
  }
  return result;
}

async function loadShopSettings() {
  if (!snapshot || !isCareFeatureAvailable()) return;
  const scope = currentScope();
  const sequence = ++requestSequence;
  shopLoading = true;
  render();
  try {
    const result = await shopRequest("read");
    if (sequence !== requestSequence || scope !== currentScope()) return;
    shopSettings = result;
    setStatus("食物设置已同步");
  } catch (error) {
    if (sequence === requestSequence && scope === currentScope()) showAlert(formatError(error), "error");
  } finally {
    if (sequence === requestSequence) { shopLoading = false; render(); }
  }
}

const EFFECT_KEYS = ["hunger", "energy", "affection"];

function editorInputs() {
  return {
    name: els.form.querySelector("#food-name"),
    price: els.form.querySelector("#food-price"),
    description: els.form.querySelector("#food-description"),
    qq: els.form.querySelector("#food-qq")
  };
}

function setEditorDisabled(disabled) {
  for (const input of els.form.querySelectorAll("input")) input.disabled = disabled;
}

function saveLabel(mode) {
  return mode === "create" ? "新增到货架" : "保存设置";
}

function showEditor() {
  els.editorError.hidden = true;
  setEditorDisabled(false);
  els.save.disabled = false;
  els.editor.showModal();
}

function openFoodEditor(item) {
  if (!shopSettings || saving) return;
  const modes = item.usable_in || item.usableIn;
  editing = { mode: "edit", id: item.id, scope: currentScope(), revision: shopSettings.revision };
  els.editorTitle.textContent = item.name;
  els.editorDescription.textContent = "修改会保存到当前角色，之后的购买和投喂都会使用这些数值。";
  const inputs = editorInputs();
  inputs.name.value = item.name;
  inputs.price.value = item.price;
  inputs.description.value = item.description || "";
  inputs.qq.checked = Array.isArray(modes) ? modes.includes("qq") : true;
  for (const key of EFFECT_KEYS) document.querySelector(`#food-${key}`).value = item.effects[key];
  els.save.textContent = saveLabel("edit");
  showEditor();
}

function openCreateEditor() {
  if (!shopSettings || saving || shopLoading || pendingAction) return;
  editing = { mode: "create", scope: currentScope(), revision: shopSettings.revision };
  els.editorTitle.textContent = "新增商品";
  els.editorDescription.textContent = "新商品会加进当前角色的货架，名字、价格和效果都由你来定。";
  const inputs = editorInputs();
  const defaults = { hunger: 15, energy: 0, affection: 0 };
  inputs.name.value = "";
  inputs.price.value = 8;
  inputs.description.value = "";
  inputs.qq.checked = true;
  for (const key of EFFECT_KEYS) document.querySelector(`#food-${key}`).value = defaults[key];
  els.save.textContent = saveLabel("create");
  showEditor();
}

async function saveFood() {
  if (!editing || saving || !els.form.reportValidity()) return;
  const edit = editing;
  const inputs = editorInputs();
  const name = inputs.name.value.trim();
  const price = Number(inputs.price.value);
  const description = inputs.description.value.trim();
  const usableIn = inputs.qq.checked ? ["desktop_pet", "qq"] : ["desktop_pet"];
  const effects = Object.fromEntries(EFFECT_KEYS.map(key => [key, Number(document.querySelector(`#food-${key}`).value)]));
  saving = true;
  setEditorDisabled(true);
  els.save.disabled = true;
  els.save.textContent = "正在保存…";
  els.editorError.hidden = true;
  try {
    const shared = { revision: edit.revision, name, price, effects, description, usable_in: usableIn };
    const result = edit.mode === "create"
      ? await shopRequest("create", shared)
      : await shopRequest("update", { ...shared, item_id: edit.id });
    if (edit !== editing || edit.scope !== currentScope()) return;
    shopSettings = result;
    els.editor.close();
    showAlert(edit.mode === "create"
      ? `已新增「${name}」，货架上就能看到了。`
      : "已保存。之后的购买和投喂会使用新的数值。", "ok");
    await loadShopSettings();
    await sendCommand("refreshCharacterPacks", { apply: false });
  } catch (error) {
    if (edit === editing && edit.scope === currentScope()) {
      els.editorError.textContent = formatError(error);
      els.editorError.hidden = false;
    }
  } finally {
    if (edit.scope === currentScope()) {
      saving = false;
      setEditorDisabled(false);
      els.save.disabled = false;
      els.save.textContent = saveLabel(edit.mode);
      render();
    }
  }
}
