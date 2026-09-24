<script setup lang="ts">
import { reactive, watch } from "vue";
import type { Connection } from "../../domain/types";
const props = defineProps<{ value: Connection | null; busy: boolean }>();
const emit = defineEmits<{ connect: [connection: Connection] }>();
const DEFAULT_SCENE_CONNECTION: Connection = {
  backend: "http://127.0.0.1:14321",
  botId: "local-default",
  profileId: "scene-qa",
  sessionId: "scene-ui-live",
  characterId: "akane_v1",
  instanceId: "",
};
const form = reactive<Connection>({ ...DEFAULT_SCENE_CONNECTION });
watch(
  () => props.value,
  (value) => {
    if (value) {
      form.backend = value.backend || DEFAULT_SCENE_CONNECTION.backend;
      form.botId = value.botId || DEFAULT_SCENE_CONNECTION.botId;
      form.profileId = value.profileId || DEFAULT_SCENE_CONNECTION.profileId;
      form.sessionId = value.sessionId || DEFAULT_SCENE_CONNECTION.sessionId;
      form.characterId = value.characterId || DEFAULT_SCENE_CONNECTION.characterId;
      form.instanceId = value.instanceId || "";
    }
  },
  { immediate: true },
);
function resetToDefault() {
  Object.assign(form, DEFAULT_SCENE_CONNECTION);
}
function submit() {
  emit("connect", {
    backend: form.backend.trim() || DEFAULT_SCENE_CONNECTION.backend,
    botId: form.botId.trim() || DEFAULT_SCENE_CONNECTION.botId,
    profileId: form.profileId.trim() || DEFAULT_SCENE_CONNECTION.profileId,
    sessionId: form.sessionId.trim() || DEFAULT_SCENE_CONNECTION.sessionId,
    characterId: form.characterId.trim() || DEFAULT_SCENE_CONNECTION.characterId,
    instanceId: form.instanceId.trim(),
  });
}
</script>
<template>
  <p class="panel-intro">连接你正在使用的宿主，让房间和桌宠共享角色与养成状态。</p>
  <form class="connection-form" @submit.prevent="submit">
    <label>宿主地址<input v-model="form.backend" required placeholder="http://127.0.0.1:14321" /></label>
    <label>Bot<input v-model="form.botId" placeholder="默认 local-default" /></label>
    <label>用户标识<input v-model="form.profileId" placeholder="默认 scene-qa" /></label>
    <label>会话标识<input v-model="form.sessionId" placeholder="默认 scene-ui-live" /></label>
    <label>角色包<input v-model="form.characterId" placeholder="默认 akane_v1" /></label>
    <div style="display: flex; gap: 8px; margin-top: 8px;">
      <button type="submit" class="primary-button" :disabled="busy" style="flex: 1;">{{ busy ? "连接中…" : "进入房间" }}</button>
      <button type="button" class="secondary-button" :disabled="busy" @click="resetToDefault" title="恢复默认独立小屋存档配置">恢复默认</button>
    </div>
  </form>
</template>
