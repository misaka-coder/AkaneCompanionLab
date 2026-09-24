<script setup lang="ts">
import type { Outfit } from "../../domain/types";
import RoomIcon from "../../ui/RoomIcon.vue";
defineProps<{ outfits: Outfit[]; selected: string; preview: string; busy: boolean; asset: (url: string) => string }>();
const emit = defineEmits<{ preview: [id: string]; equip: [id: string]; cancel: []; openAdd: []; openWorkshop: [] }>();
</script>
<template>
  <p class="panel-intro">选一套今天喜欢的。先试试看，确认后她会知道你的选择。</p>
  <div class="outfit-grid">
    <button
      v-for="outfit in outfits"
      :key="outfit.id"
      class="outfit-card"
      :class="{ selected: (preview || selected) === outfit.id }"
      @click="emit('preview', outfit.id)"
    >
      <div class="outfit-image">
        <img
          :src="asset((outfit.emotions.find((e) => e.id === 'normal') || outfit.emotions[0]).url)"
          :alt="outfit.name"
        />
      </div>
      <span>{{ outfit.name }}</span
      ><small>{{ selected === outfit.id ? "正在穿着" : `${outfit.emotions.length} 种表情` }}</small>
    </button>
    <button class="outfit-card add-card" @click="emit('openAdd')">
      <RoomIcon name="upload" :size="24" />
      <span>+ 添置新衣服</span>
      <small>拖入透明立绘</small>
    </button>
  </div>
  <div v-if="preview && preview !== selected" class="panel-footer">
    <span>试穿中 · 尚未换装</span
    ><button class="primary-button" :disabled="busy" @click="emit('equip', preview)">就穿这套</button
    ><button class="text-button" @click="emit('cancel')">取消试穿</button>
  </div>
  <div class="panel-link-footer">
    <button class="text-button" @click="emit('openWorkshop')">
      管理差分立绘与校准 (角色工坊) →
    </button>
  </div>
</template>
