<script setup lang="ts">
import RoomIcon from "../../ui/RoomIcon.vue";

defineProps<{
  title: string;
  summary: string;
  busy: boolean;
}>();

const emit = defineEmits<{
  finish: [];
  replay: [];
}>();
</script>

<template>
  <div class="ending-banner">
    <div class="ending-badge">
      <RoomIcon name="tea" :size="16" />
      <span>剧场终章</span>
    </div>
    <h2 class="ending-title">{{ title }}</h2>
    <p class="ending-summary">{{ summary }}</p>
    <div class="ending-actions">
      <button class="primary-button" :disabled="busy" @click="emit('finish')">
        <RoomIcon name="home" :size="16" /> 返回日常房间
      </button>
      <button class="secondary-button" :disabled="busy" @click="emit('replay')">
        重新体验其他分支
      </button>
    </div>
  </div>
</template>

<style scoped>
.ending-banner {
  position: absolute;
  top: 72px;
  left: 50%;
  transform: translateX(-50%);
  width: 90%;
  max-width: 520px;
  background: rgba(255, 252, 248, 0.95);
  border: 1.5px solid var(--room-primary, #b35c39);
  border-radius: 16px;
  padding: 20px 24px;
  box-shadow: 0 16px 36px rgba(179, 92, 57, 0.16);
  z-index: 60;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  animation: dropIn 0.35s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes dropIn {
  from {
    opacity: 0;
    transform: translate(-50%, -20px);
  }
  to {
    opacity: 1;
    transform: translate(-50%, 0);
  }
}

.ending-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(179, 92, 57, 0.1);
  color: var(--room-primary, #b35c39);
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
}

.ending-title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  color: var(--room-text-main, #2c2523);
}

.ending-summary {
  margin: 0;
  font-size: 13px;
  color: var(--room-text-muted, #7c726d);
  line-height: 1.55;
  max-width: 440px;
}

.ending-actions {
  display: flex;
  gap: 12px;
  margin-top: 8px;
}

.secondary-button {
  padding: 6px 14px;
  border-radius: 8px;
  border: 1px solid rgba(0, 0, 0, 0.15);
  background: transparent;
  color: var(--room-text-muted, #7c726d);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}

.secondary-button:hover:not(:disabled) {
  border-color: var(--room-text-main, #2c2523);
  color: var(--room-text-main, #2c2523);
}
</style>
