<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from "vue";
import RoomIcon from "./RoomIcon.vue";
const props = defineProps<{ title: string; eyebrow: string; open: boolean }>();
const emit = defineEmits<{ close: [] }>();
const panel = ref<HTMLElement>();
let previous: HTMLElement | null = null;
function keys(event: KeyboardEvent) {
  if (!props.open) return;
  if (event.key === "Escape") {
    event.stopPropagation();
    emit("close");
  }
  if (event.key !== "Tab") return;
  const items = [
    ...(panel.value?.querySelectorAll<HTMLElement>('button:not(:disabled),input,select,[tabindex="0"]') || []),
  ];
  const first = items[0];
  const last = items.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last?.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first?.focus();
  }
}
watch(
  () => props.open,
  async (open) => {
    if (open) {
      previous = document.activeElement as HTMLElement;
      await nextTick();
      panel.value?.querySelector<HTMLButtonElement>("button")?.focus();
      document.addEventListener("keydown", keys);
    } else {
      document.removeEventListener("keydown", keys);
      await nextTick();
      previous?.focus();
    }
  },
);
onBeforeUnmount(() => document.removeEventListener("keydown", keys));
</script>
<template>
  <Transition name="drawer">
    <div v-if="open" class="drawer-scrim" @click.self="emit('close')">
      <aside ref="panel" class="room-drawer" role="dialog" aria-modal="true" :aria-label="title">
        <header class="drawer-heading">
          <div>
            <span class="eyebrow">{{ eyebrow }}</span>
            <h2>{{ title }}</h2>
          </div>
          <button class="icon-button" aria-label="关闭面板" @click="emit('close')"><RoomIcon name="close" /></button>
        </header>
        <div class="drawer-content"><slot /></div>
      </aside>
    </div>
  </Transition>
</template>
