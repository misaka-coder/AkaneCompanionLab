<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { StageFrame, StageRenderer } from "./renderer";
import type { TouchRegion } from "../domain/types";
const props = defineProps<{
  frame: StageFrame | null;
  motion?: string;
  beatId?: string;
  reaction?: { motion: TouchRegion; id: number } | null;
}>();
const emit = defineEmits<{ touch: [region: TouchRegion]; error: [message: string] }>();
const host = ref<HTMLElement>();
const loading = ref(false);
let renderer: StageRenderer | undefined;
let disposed = false;
let sequence = 0;
async function load() {
  const id = ++sequence;
  if (!renderer) return;
  if (!props.frame) {
    loading.value = false;
    renderer.clear();
    return;
  }
  loading.value = true;
  try {
    await renderer.load(props.frame);
  } catch (error) {
    console.warn("scene_stage_load_failed", error);
    if (!disposed && id === sequence) emit("error", "画面资源未能加载，请检查资源或重新连接。");
  } finally {
    if (id === sequence) loading.value = false;
  }
}
async function visibility() {
  if (document.hidden) {
    renderer?.pause(true);
    return;
  }
  const w = window as unknown as { __TAURI__?: { window?: { getCurrentWindow?: () => { isMinimized: () => Promise<boolean> } } } };
  if (w.__TAURI__?.window?.getCurrentWindow) {
    try {
      const isMin = await w.__TAURI__.window.getCurrentWindow().isMinimized();
      renderer?.pause(isMin);
      return;
    } catch {
      /* ignore */
    }
  }
  renderer?.pause(false);
}
onMounted(async () => {
  try {
    const { createRoomRenderer } = await import("./pixi/room-renderer");
    renderer = await createRoomRenderer(host.value!, (region) => emit("touch", region));
    if (disposed) {
      renderer.dispose();
      return;
    }
    await load();
    document.addEventListener("visibilitychange", visibility);
    window.addEventListener("resize", visibility);
  } catch {
    emit("error", "舞台初始化失败，请检查图形加速是否可用。");
  }
});
watch(() => props.frame, load, { deep: true });
watch(
  () => props.reaction,
  (value) => {
    if (value) renderer?.react(value.motion);
  },
);
watch(
  () => [props.motion, props.beatId],
  ([value]) => {
    if (value === "nod" || value === "shake" || value === "bounce") renderer?.react(value);
  },
);
onBeforeUnmount(() => {
  disposed = true;
  sequence++;
  renderer?.dispose();
  document.removeEventListener("visibilitychange", visibility);
  window.removeEventListener("resize", visibility);
});
defineExpose({ react: (region: TouchRegion) => renderer?.react(region) });
</script>
<template>
  <div ref="host" class="room-stage" aria-label="角色与房间"></div>
  <div v-if="loading" class="stage-loading" role="status">正在布置房间<span class="loading-dots">…</span></div>
</template>
