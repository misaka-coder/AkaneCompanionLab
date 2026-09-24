<script setup lang="ts">
import { ref } from "vue";
import type { Snapshot } from "../../domain/types";
import RoomIcon from "../../ui/RoomIcon.vue";
defineProps<{ snapshot: Snapshot; busy: boolean }>();
const emit = defineEmits<{
  action: [kind: "buy" | "feed" | "claim_allowance", target: string, count?: number];
  addItem: [
    value: { name: string; price: number; description?: string; effects: Record<string, number>; icon?: string; item_id?: string },
    callback?: (ok: boolean) => void,
  ];
  openShop: [];
}>();
const tab = ref("inventory");
const adding = ref(false);
const editingItemId = ref("");
const newName = ref("");
const newPrice = ref(6);
const selectedPreset = ref<"sweet" | "drink" | "meal" | "custom">("sweet");
const newHunger = ref(18);
const newEnergy = ref(4);
const newAffection = ref(3);
const newIcon = ref("🍰");

const PRESETS = {
  sweet: { hunger: 18, energy: 4, affection: 3, icon: "🍰", label: "点心" },
  drink: { hunger: 5, energy: 15, affection: 2, icon: "🧋", label: "饮品" },
  meal: { hunger: 32, energy: 10, affection: 4, icon: "🍱", label: "正餐" },
};

function onPresetChange() {
  if (selectedPreset.value in PRESETS) {
    const preset = PRESETS[selectedPreset.value as keyof typeof PRESETS];
    newHunger.value = preset.hunger;
    newEnergy.value = preset.energy;
    newAffection.value = preset.affection;
    newIcon.value = preset.icon;
  }
}

function onCustomStatInput() {
  selectedPreset.value = "custom";
}

function startEditItem(item: Snapshot["shop"][number]) {
  editingItemId.value = item.id;
  newName.value = item.name;
  newPrice.value = item.price;
  newHunger.value = item.effects.hunger ?? 0;
  newEnergy.value = item.effects.energy ?? 0;
  newAffection.value = item.effects.affection ?? 0;
  selectedPreset.value = "custom";
  adding.value = true;
}

function cancelEdit() {
  adding.value = false;
  editingItemId.value = "";
  newName.value = "";
  newPrice.value = 6;
  selectedPreset.value = "sweet";
  onPresetChange();
}

function submitNewItem() {
  const trimmed = newName.value.trim();
  if (!trimmed) return;
  const hunger = Math.max(-100, Math.min(Number(newHunger.value) || 0, 100));
  const energy = Math.max(-100, Math.min(Number(newEnergy.value) || 0, 100));
  const affection = Math.max(-100, Math.min(Number(newAffection.value) || 0, 100));
  const payload: {
    name: string;
    price: number;
    description: string;
    effects: Record<string, number>;
    icon: string;
    item_id?: string;
  } = {
    name: trimmed,
    price: Math.max(0, Math.min(Number(newPrice.value) || 0, 9999)),
    description: `饱食 ${hunger >= 0 ? "+" + hunger : hunger} · 精力 ${energy >= 0 ? "+" + energy : energy} · 好感 ${affection >= 0 ? "+" + affection : affection}`,
    effects: { hunger, energy, affection },
    icon: newIcon.value,
  };
  if (editingItemId.value) {
    payload.item_id = editingItemId.value;
  }
  emit("addItem", payload, (ok?: boolean) => {
    if (ok) {
      cancelEdit();
    }
  });
}
</script>
<template>
  <div v-if="!snapshot.care.enabled" class="empty-state">这个角色尚未开启养成。可在角色工坊中配置。</div>
  <template v-else>
    <div class="care-summary">
      <div>
        <RoomIcon name="heart" /><span
          >好感 <b>{{ snapshot.care.affection }}</b></span
        >
      </div>
      <div>
        <RoomIcon name="tea" /><span
          >饱食 <b>{{ snapshot.care.hunger }}</b></span
        >
      </div>
      <div>
        <RoomIcon name="sun" /><span
          >精力 <b>{{ snapshot.care.energy }}</b></span
        >
      </div>
    </div>
    <div class="segmented">
      <button :class="{ active: tab === 'inventory' }" @click="tab = 'inventory'">随身口袋</button
      ><button :class="{ active: tab === 'shop' }" @click="tab = 'shop'">小卖铺</button>
    </div>
    <div class="balance">
      <span>慢慢积攒的小确幸</span><b>◈ {{ snapshot.care.coins }}</b>
    </div>
    <p v-if="tab === 'inventory' && !Object.keys(snapshot.care.inventory).length" class="empty-state">
      口袋还是空的。<br /><button class="text-button" @click="tab = 'shop'">去小卖铺挑点好吃的 →</button>
    </p>
    <div class="food-list">
      <article
        v-for="item in snapshot.shop.filter((i) => tab === 'shop' || snapshot.care.inventory[i.id])"
        :key="item.id"
        class="food-card"
      >
        <div class="food-symbol">
          {{ /茶|tea/i.test(item.name + item.id) ? "🍵" : /团子|dango/i.test(item.name + item.id) ? "🍡" : /咖啡|coffee/i.test(item.name + item.id) ? "☕" : /蛋糕|cake/i.test(item.name + item.id) ? "🍰" : /奶茶|boba/i.test(item.name + item.id) ? "🧋" : "🍪" }}
        </div>
        <div class="food-copy">
          <h3>{{ item.name }}</h3>
          <p>{{ item.description || `饱食 +${item.effects.hunger || 0} · 精力 +${item.effects.energy || 0}` }}</p>
          <small>{{ tab === "shop" ? `◈ ${item.price}` : `口袋里还有 ${snapshot.care.inventory[item.id]} 份` }}</small>
        </div>
        <div class="food-controls">
          <template v-if="tab === 'shop'">
            <button
              class="soft-button"
              :disabled="busy || snapshot.care.coins < item.price"
              @click="emit('action', 'buy', item.id)"
            >
              买一份
            </button>
            <button
              type="button"
              class="icon-action-button"
              title="调整这个食物"
              :disabled="busy"
              @click="startEditItem(item)"
            >
              ✎
            </button>
          </template>
          <template v-else>
            <button
              class="soft-button"
              :disabled="busy"
              @click="emit('action', 'feed', item.id, 1)"
            >
              {{ (snapshot.care.inventory[item.id] || 0) > 1 ? "吃 1 份" : "给她吃" }}
            </button>
            <button
              v-if="(snapshot.care.inventory[item.id] || 0) > 1"
              class="soft-button secondary"
              :disabled="busy"
              :title="`全部吃掉 (${snapshot.care.inventory[item.id]} 份)`"
              @click="emit('action', 'feed', item.id, snapshot.care.inventory[item.id])"
            >
              全吃 (x{{ snapshot.care.inventory[item.id] }})
            </button>
          </template>
        </div>
      </article>
      <div v-if="tab === 'shop' && !adding" class="add-item-trigger">
        <button class="soft-button secondary" :disabled="busy" @click="adding = true">
          + 添置新品小食
        </button>
      </div>
      <form v-else-if="tab === 'shop' && adding" class="add-item-card" @submit.prevent="submitNewItem">
        <div class="add-item-header">
          <strong>{{ editingItemId ? `调整小食「${newName || '商品'}」` : "添置小食到小卖铺" }}</strong>
          <button type="button" class="text-button" @click="cancelEdit">取消</button>
        </div>
        <div class="add-item-row">
          <label>美食名称
            <input v-model="newName" maxlength="30" placeholder="例如：草莓大福 / 抹茶拿铁" required />
          </label>
        </div>
        <div class="add-item-row split">
          <label>效果模板
            <select v-model="selectedPreset" @change="onPresetChange">
              <option value="sweet">🍰 甜品点心模板</option>
              <option value="drink">🧋 温热饮品模板</option>
              <option value="meal">🍱 丰盛正餐模板</option>
              <option value="custom">✨ 自定义数值</option>
            </select>
          </label>
          <label>售价(金币)
            <input v-model.number="newPrice" type="number" min="0" max="999" />
          </label>
        </div>
        <div class="add-item-row split">
          <label>饱食恢复
            <input v-model.number="newHunger" type="number" min="-100" max="100" @input="onCustomStatInput" />
          </label>
          <label>精力恢复
            <input v-model.number="newEnergy" type="number" min="-100" max="100" @input="onCustomStatInput" />
          </label>
          <label>好感增加
            <input v-model.number="newAffection" type="number" min="-100" max="100" @input="onCustomStatInput" />
          </label>
        </div>
        <div class="add-item-actions">
          <button type="submit" class="primary-button" :disabled="busy || !newName.trim()">
            {{ editingItemId ? "保存设置" : "立刻上架" }}
          </button>
        </div>
      </form>
    </div>
    <button class="text-button" :disabled="busy" @click="emit('action', 'claim_allowance', '')">领取零花钱</button>
    <div class="panel-link-footer">
      <button class="text-button" @click="emit('openShop')">
        管理全部商品与外出打工 (片刻小卖铺) →
      </button>
    </div>
  </template>
</template>
