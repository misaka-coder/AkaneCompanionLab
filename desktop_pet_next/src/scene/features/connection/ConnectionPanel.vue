<script setup lang="ts">
import { reactive, watch } from "vue";
import type { Connection } from "../../domain/types";
import { DEFAULT_SCENE_CONNECTION, normalizeConnection } from "../../bridges/connection";
const props = defineProps<{ value: Connection | null; busy: boolean }>();
const emit = defineEmits<{ connect: [connection: Connection] }>();
const form = reactive<Connection>({ ...DEFAULT_SCENE_CONNECTION });
watch(
  () => props.value,
  (value) => {
    if (value) {
      Object.assign(form, normalizeConnection(value));
    }
  },
  { immediate: true },
);
function resetToDefault() {
  Object.assign(form, DEFAULT_SCENE_CONNECTION);
}
function submit() {
  emit("connect", normalizeConnection(form));
}
</script>
<template>
  <p class="panel-intro">连接已运行的宿主，进入当前角色的房间。</p>
  <form class="connection-form" @submit.prevent="submit">
    <label>宿主地址<input v-model="form.backend" required :placeholder="DEFAULT_SCENE_CONNECTION.backend" /></label>
    <label>Bot<input v-model="form.botId" placeholder="留空使用宿主默认 Bot" /></label>
    <label>用户标识<input v-model="form.profileId" required placeholder="填写目标用户" /></label>
    <label>会话标识<input v-model="form.sessionId" required placeholder="填写目标会话" /></label>
    <label>角色包<input v-model="form.characterId" required :placeholder="DEFAULT_SCENE_CONNECTION.characterId" /></label>
    <div style="display: flex; gap: 8px; margin-top: 8px;">
      <button type="submit" class="primary-button" :disabled="busy" style="flex: 1;">{{ busy ? "连接中…" : "进入房间" }}</button>
      <button type="button" class="secondary-button" :disabled="busy" @click="resetToDefault" title="重置连接，重新填写用户与会话">重置连接</button>
    </div>
  </form>
</template>
