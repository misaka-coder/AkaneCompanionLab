<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";
import RoomIcon from "../../ui/RoomIcon.vue";
defineProps<{ busy: boolean }>();
const emit = defineEmits<{ publish: [value: { kind: "background" | "outfit"; name: string; data: string }] }>();
const kind = ref<"background" | "outfit">("background");
const name = ref("");
const preview = ref("");
const data = ref("");
const error = ref("");
const reading = ref(false);
let sequence = 0;
function clear() {
  if (preview.value) URL.revokeObjectURL(preview.value);
  preview.value = "";
  data.value = "";
}
async function choose(file?: File) {
  if (!file) return;
  const current = ++sequence;
  clear();
  error.value = "";
  if (!["image/png", "image/jpeg", "image/webp"].includes(file.type) || file.size > 12 * 1024 * 1024) {
    error.value = "请选择不超过 12 MB 的 PNG、JPEG 或 WebP 图片。";
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
    if (current !== sequence) return;
    data.value = encoded.split(",")[1];
    name.value = file.name.replace(/\.[^.]+$/, "").slice(0, 60);
    preview.value = URL.createObjectURL(file);
  } catch {
    if (current === sequence) error.value = "图片读取失败，请重新选择。";
  } finally {
    if (current === sequence) reading.value = false;
  }
}
onBeforeUnmount(() => {
  sequence++;
  clear();
});
</script>
<template>
  <p class="panel-intro">把喜欢的风景和立绘带进房间。导入后可以立即选择，角色也会知道新增了什么。</p>
  <div class="resource-drop" @dragover.prevent @drop.prevent="choose($event.dataTransfer?.files[0])">
    <img v-if="preview" :src="preview" alt="待导入图片预览" />
    <RoomIcon v-else name="upload" :size="32" />
    <label
      >{{ reading ? "读取中…" : preview ? "换一张图片" : "拖入图片，或点击选择"
      }}<input
        type="file"
        accept="image/png,image/jpeg,image/webp"
        :disabled="busy || reading"
        @change="choose(($event.target as HTMLInputElement).files?.[0])"
    /></label>
    <small>PNG / JPEG / WebP · 最大 12 MB</small>
  </div>
  <div v-if="preview" class="connection-form resource-form">
    <label>资源名称<input v-model="name" maxlength="60" /></label
    ><label
      >用途<select v-model="kind">
        <option value="background">房间背景</option>
        <option value="outfit">一套新服装（透明立绘）</option>
      </select></label
    >
    <p class="panel-intro">
      {{
        kind === "outfit"
          ? "这张图作为常态表情。缺少的表情会保留常态立绘；后续可在角色工坊补齐。"
          : "加入风景列表后，仍由你选择什么时候使用。"
      }}
    </p>
    <button
      class="primary-button"
      :disabled="busy || reading || !name.trim()"
      @click="emit('publish', { kind, name: name.trim(), data })"
    >
      {{ busy ? "正在导入…" : "加入资源库" }}
    </button>
  </div>
  <p v-if="error" class="resource-error" role="alert">{{ error }}</p>
</template>
