<script setup lang="ts">
import { onMounted, onBeforeUnmount } from "vue";
import type { StoryChoiceOption } from "../../domain/types";
import RoomIcon from "../../ui/RoomIcon.vue";

const props = defineProps<{
  title: string;
  options: StoryChoiceOption[];
  busy: boolean;
}>();

const emit = defineEmits<{
  choose: [choiceId: string];
}>();

function handleKeydown(e: KeyboardEvent) {
  if (props.busy) return;
  const num = parseInt(e.key, 10);
  if (num >= 1 && num <= props.options.length) {
    e.preventDefault();
    emit("choose", props.options[num - 1].id);
  }
}

onMounted(() => {
  window.addEventListener("keydown", handleKeydown);
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", handleKeydown);
});
</script>

<template>
  <div class="choice-backdrop">
    <div class="choice-modal">
      <div v-if="title" class="choice-prompt">
        <span class="prompt-icon"><RoomIcon name="tea" :size="18" /></span>
        <span class="prompt-text">{{ title }}</span>
      </div>
      <div class="choice-options">
        <button
          v-for="(opt, idx) in options"
          :key="opt.id"
          class="choice-card"
          :disabled="busy"
          @click="emit('choose', opt.id)"
        >
          <span class="choice-index">{{ idx + 1 }}</span>
          <div class="choice-text-group">
            <span class="choice-label">{{ opt.label }}</span>
            <span v-if="opt.cost_label" class="choice-badge">
              {{ opt.cost_label }}
            </span>
          </div>
          <span class="choice-arrow"><RoomIcon name="arrow" :size="16" /></span>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.choice-backdrop {
  position: absolute;
  inset: 0;
  background: linear-gradient(to top, rgba(20, 16, 14, 0.42) 0%, rgba(20, 16, 14, 0.12) 45%, transparent 80%);
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding-bottom: 125px;
  pointer-events: none;
  z-index: 80;
  animation: fadeIn 0.25s ease;
}

@keyframes fadeIn {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

.choice-modal {
  pointer-events: auto;
  width: min(640px, 86%);
  background: rgba(255, 252, 248, 0.98);
  border: 1px solid rgba(189, 156, 103, 0.28);
  border-radius: 16px;
  padding: 20px 24px;
  box-shadow: 0 16px 48px rgba(35, 25, 18, 0.28);
  display: flex;
  flex-direction: column;
  gap: 14px;
  animation: slideUp 0.28s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes slideUp {
  from {
    opacity: 0;
    transform: translateY(16px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.choice-prompt {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 500;
  color: var(--room-primary, #b35c39);
  padding-bottom: 12px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}

.choice-options {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.choice-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  background: #ffffff;
  border: 1.5px solid rgba(0, 0, 0, 0.08);
  border-radius: 12px;
  cursor: pointer;
  text-align: left;
  transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.choice-card:hover:not(:disabled) {
  border-color: var(--room-primary, #b35c39);
  background: #fffdfb;
  transform: translateX(4px);
  box-shadow: 0 4px 12px rgba(179, 92, 57, 0.12);
}

.choice-card:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.choice-index {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.06);
  font-size: 12px;
  font-weight: 600;
  color: var(--room-text-muted, #7c726d);
}

.choice-card:hover:not(:disabled) .choice-index {
  background: var(--room-primary, #b35c39);
  color: #fff;
}

.choice-text-group {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.choice-label {
  font-size: 14px;
  color: var(--room-text-main, #2c2523);
  line-height: 1.45;
}

.choice-badge {
  font-size: 11px;
  padding: 2px 9px;
  border-radius: 999px;
  background: rgba(179, 92, 57, 0.1);
  color: var(--room-primary, #b35c39);
  font-weight: 500;
  border: 1px solid rgba(179, 92, 57, 0.22);
  white-space: nowrap;
}

.choice-arrow {
  color: var(--room-text-muted, #7c726d);
  opacity: 0.4;
  transition: opacity 0.2s, transform 0.2s;
}

.choice-card:hover:not(:disabled) .choice-arrow {
  opacity: 1;
  color: var(--room-primary, #b35c39);
  transform: translateX(2px);
}
</style>
