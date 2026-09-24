<script setup lang="ts">
import { ref } from "vue";
import type { ActionRequest, Snapshot } from "../../domain/types";
import RoomIcon from "../../ui/RoomIcon.vue";

defineProps<{
  snapshot: Snapshot;
  busy?: boolean;
  action: (kind: ActionRequest["kind"], target: string) => unknown;
}>();

const emit = defineEmits<{
  (e: "import-music", value: { kind: "music"; name: string; data: string }): void;
}>();

const showImport = ref(false);
const reading = ref(false);
const error = ref("");
const name = ref("");
const data = ref("");

function clear() {
  reading.value = false;
  error.value = "";
  name.value = "";
  data.value = "";
}

async function choose(file?: File) {
  if (!file) return;
  clear();
  const validExts = [".mp3", ".wav", ".ogg", ".flac", ".m4a"];
  const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
  if (!validExts.includes(ext) || file.size > 20 * 1024 * 1024) {
    error.value = "请选择不超过 20 MB 的 MP3、WAV、OGG、FLAC 或 M4A 音频。";
    return;
  }
  reading.value = true;
  try {
    const encoded = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
    data.value = encoded.split(",")[1];
    name.value = file.name.replace(/\.[^.]+$/, "").slice(0, 60);
  } catch {
    error.value = "音频读取失败，请重新选择。";
  } finally {
    reading.value = false;
  }
}

function submitImport() {
  if (!name.value.trim() || !data.value) return;
  emit("import-music", { kind: "music", name: name.value.trim(), data: data.value });
  clear();
  showImport.value = false;
}
</script>
<template>
  <p class="panel-intro">让熟悉的旋律，轻轻填满房间。</p>
  <div style="display: flex; gap: 8px; margin-bottom: 12px;">
    <button class="soft-button" style="flex: 1;" @click="action('music', '')">安静一会儿</button>
    <button class="soft-button" style="flex: 1;" @click="showImport = !showImport">
      {{ showImport ? "返回列表" : "导入音乐" }}
    </button>
  </div>

  <div v-if="showImport" class="connection-form resource-form">
    <div class="resource-drop">
      <RoomIcon name="music" :size="32" />
      <label>
        {{ reading ? "读取中…" : name ? "已选: " + name : "选择或拖入音频文件" }}
        <input
          type="file"
          accept="audio/mp3,audio/wav,audio/ogg,audio/flac,audio/aac,audio/m4a,audio/*,.mp3,.wav,.ogg,.flac,.m4a"
          :disabled="busy || reading"
          @change="choose(($event.target as HTMLInputElement).files?.[0])"
        />
      </label>
      <small>MP3 / WAV / OGG / FLAC / M4A · 最大 20 MB</small>
    </div>
    <label v-if="name">
      曲目名称
      <input v-model="name" maxlength="60" placeholder="曲目显示名称" />
    </label>
    <button
      v-if="data"
      class="primary-button"
      :disabled="busy || reading || !name.trim()"
      @click="submitImport"
    >
      {{ busy ? "正在导入…" : "加入音乐库" }}
    </button>
    <p v-if="error" class="resource-error" role="alert">{{ error }}</p>
  </div>

  <div v-else class="track-list">
    <button
      v-for="track in snapshot.catalog.music"
      :key="track.id"
      :class="{ active: track.id === snapshot.room.music_id }"
      @click="action('music', track.id)"
    >
      <RoomIcon name="music" :size="18" /><span>{{ track.name }}</span>
    </button>
  </div>
  <p v-if="!showImport && !snapshot.catalog.music.length" class="empty-state">还没有可用音乐。</p>
</template>
