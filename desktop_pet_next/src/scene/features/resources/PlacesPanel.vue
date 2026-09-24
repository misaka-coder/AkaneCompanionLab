<script setup lang="ts">
import type { ActionRequest, Snapshot } from "../../domain/types";
import RoomIcon from "../../ui/RoomIcon.vue";
defineProps<{
  snapshot: Snapshot;
  busy: boolean;
  asset: (url: string) => string;
  action: (kind: ActionRequest["kind"], target: string) => unknown;
}>();
const emit = defineEmits<{ openAdd: [] }>();
</script>
<template>
  <p class="panel-intro">换一扇窗外的风景，或让她坐得近一点。</p>
  <div class="segmented">
    <button :class="{ active: snapshot.room.position === 'center' }" @click="action('position', 'center')">
      在你面前</button
    ><button :class="{ active: snapshot.room.position === 'right' }" @click="action('position', 'right')">
      坐在身旁
    </button>
  </div>
  <div class="places-grid">
    <button
      v-for="place in snapshot.catalog.backgrounds"
      :key="place.id"
      class="place-card"
      :class="{ selected: place.id === snapshot.room.background_id }"
      :disabled="busy"
      @click="action('background', place.id)"
    >
      <img :src="asset(place.url)" :alt="place.name" /><span>{{ place.name }}</span>
    </button>
    <button class="place-card add-card" :disabled="busy" @click="emit('openAdd')">
      <RoomIcon name="upload" :size="24" />
      <span>+ 带她去新地方</span>
      <small>添加新风景背景</small>
    </button>
  </div>
</template>
