import { invoke } from "@tauri-apps/api/core";
import { emit, emitTo, listen } from "@tauri-apps/api/event";

import "./shop.css";

const SETTINGS_COMMAND_EVENT = "akane-next-settings-command";
const SETTINGS_SNAPSHOT_EVENT = "akane-next-settings-snapshot";
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
  character: document.querySelector("#shop-character"),
  status: document.querySelector("#shop-status"),
  refresh: document.querySelector("#refresh-shop"),
  close: document.querySelector("#close-shop")
};

let snapshot = null;
let workCountdownTimer = 0;
let allowanceCountdownTimer = 0;

init();

async function init() {
  bindActions();
  render();
  await bindStateSync();
}

function bindActions() {
  els.refresh?.addEventListener("click", () => {
    setStatus("正在刷新");
    void sendCommand("requestSnapshot");
  });
  els.close?.addEventListener("click", () => {
    void closeWindow();
  });
  els.startWork?.addEventListener("click", () => {
    setStatus("准备出门");
    void sendCommand("startCareWork");
  });
  els.claimAllowance?.addEventListener("click", () => {
    setStatus("领取补给");
    void sendCommand("claimCareAllowance");
  });
}

async function bindStateSync() {
  try {
    await listen(SETTINGS_SNAPSHOT_EVENT, (event) => {
      snapshot = event.payload || null;
      render();
    });
    await listen(SHOP_STATUS_EVENT, (event) => {
      const payload = event.payload || {};
      showAlert(String(payload.message || ""), String(payload.tone || "info"));
    });
    await sendCommand("requestSnapshot");
  } catch (error) {
    showAlert(`商店同步失败：${formatError(error)}`, "error");
  }
}

function render() {
  const character = snapshot?.character || {};
  const careConfig = normalizeCareConfig(character.care);
  const care = normalizeCareState(snapshot?.state?.care, careConfig);
  const items = careConfig.enabled ? careConfig.shopItems : [];

  els.coins.textContent = String(care.coins);
  els.hunger.textContent = String(care.hunger);
  els.energy.textContent = String(care.energy);
  els.affection.textContent = String(care.affection);
  els.character.textContent = `Character: ${character.name || character.packId || "-"}`;
  els.summary.textContent = careConfig.enabled
    ? `${character.name || "角色"}的小卖部`
    : "这个角色还没有配置商店";
  els.shopCount.textContent = `${items.length} 件商品`;

  renderWork(careConfig, care);
  renderAllowance(careConfig, care);
  renderShopItems(items, care);
  renderInventory(items, care);
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
  els.startWork.disabled = care.hunger < work.minHunger || care.energy < work.minEnergy;
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
  els.claimAllowance.disabled = !ready;
}

function renderShopItems(items, care) {
  if (!items.length) {
    els.shopItems.innerHTML = `<div class="empty-state">这个角色还没有配置商店。</div>`;
    return;
  }

  els.shopItems.replaceChildren(...items.map((item) => createShopItem(item, care)));
}

function createShopItem(item, care) {
  const element = document.createElement("article");
  element.className = "shop-item";

  const canBuy = care.coins >= item.price;
  element.innerHTML = `
    <div class="item-mark">${escapeHtml(item.name.slice(0, 1) || "物")}</div>
    <div class="item-body">
      <div class="item-topline">
        <h3>${escapeHtml(item.name)}</h3>
        <span>${item.price} 金币</span>
      </div>
      <p>${escapeHtml(item.description || formatEffects(item.effects))}</p>
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
    setStatus(`购买 ${item.name}`);
    void sendCommand("buyShopItem", item.id);
  });
  actions.append(buyButton);
  return element;
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
  feedButton.addEventListener("click", () => {
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
  const payload = { command, value };
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
