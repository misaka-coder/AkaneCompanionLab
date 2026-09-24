<script setup lang="ts">
import { ref } from "vue";
import type { StoryItemSummary, StoryRunState } from "../../domain/types";
import RoomIcon from "../../ui/RoomIcon.vue";
import { openCharacterStoriesFolder } from "../../bridges/native";

const props = defineProps<{
  stories: StoryItemSummary[];
  activeRun: StoryRunState | null;
  busy: boolean;
  asset: (url: string) => string;
  characterPackId?: string;
}>();

const emit = defineEmits<{
  start: [storyId: string, forceRestart: boolean];
  resume: [];
  reset: [storyId: string];
  refresh: [];
  import: [fileName: string, content: string];
}>();

const failedCovers = ref<Set<string>>(new Set());
const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

function onImgError(storyId: string) {
  failedCovers.value.add(storyId);
}

async function onFilePicked(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  try {
    const isImg = /\.(png|jpe?g|webp)$/i.test(file.name);
    if (isImg) {
      const reader = new FileReader();
      const b64 = await new Promise<string>((resolve, reject) => {
        reader.onload = () => resolve(reader.result as string);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      emit("import", file.name, b64);
    } else {
      const text = await file.text();
      emit("import", file.name, text);
    }
  } catch (err) {
    console.error("Failed to read story file", err);
  } finally {
    input.value = "";
  }
}

async function openStoriesFolder() {
  await openCharacterStoriesFolder(props.characterPackId || "akane_v1");
}
</script>

<template>
  <div class="panel-header-row">
    <p class="panel-intro">在专属的小剧场里，经历一段心动时光。不同的选择会引向不同的结局。</p>
    <div class="panel-header-actions">
      <label class="action-btn import-btn" :class="{ 'is-busy': busy }" title="导入剧本（.md/.json）或封面图片">
        <RoomIcon name="book" :size="14" /> 导入
        <input type="file" accept=".md,.markdown,.json,.yaml,.png,.jpg,.jpeg,.webp" hidden @change="onFilePicked" />
      </label>
      <button v-if="isTauri" class="action-btn" :disabled="busy" title="在文件夹中打开当前角色剧本目录" @click="openStoriesFolder">
        <RoomIcon name="compass" :size="14" /> 目录
      </button>
      <button class="action-btn refresh-btn" :disabled="busy" title="重新扫描剧本目录" @click="emit('refresh')">
        <RoomIcon name="history" :size="14" /> 刷新
      </button>
    </div>
  </div>
  <div v-if="!stories.length" class="story-empty-state">
    <RoomIcon name="leaf" :size="26" />
    <h4>暂无可演出的剧本</h4>
    <p>将 <code>.md</code> 剧本或封面图放入当前角色 <code>stories/</code> 目录，或点击上方「导入」即可自动加载。</p>
    <button class="secondary-button" :disabled="busy" @click="emit('refresh')">重新扫描</button>
  </div>
  <div v-else class="story-list">
    <div
      v-for="item in stories"
      :key="item.story_id"
      class="story-card"
      :class="{ 'is-active': activeRun?.story_id === item.story_id }"
    >
      <div class="story-cover">
        <img
          v-if="item.cover_image && !failedCovers.has(item.story_id)"
          :src="asset(item.cover_image)"
          :alt="item.title"
          @error="onImgError(item.story_id)"
        />
        <div v-else class="story-cover-fallback">
          <RoomIcon name="sun" :size="24" />
          <span>{{ item.title.slice(0, 4) }}</span>
        </div>
        <span v-if="item.completed_endings.length" class="story-badge">
          <RoomIcon name="check" :size="14" /> 已达成 {{ item.completed_endings.length }} 个结局
        </span>
      </div>
      <div class="story-info">
        <div class="story-header">
          <h3>{{ item.title }}</h3>
          <span v-if="activeRun?.story_id === item.story_id" class="status-pill in-progress">进行中</span>
          <span v-else-if="item.has_active_run" class="status-pill paused">有存档</span>
        </div>
        <p class="story-desc">{{ item.description }}</p>
        <div class="story-actions">
          <template v-if="activeRun?.story_id === item.story_id">
            <button class="primary-button" :disabled="busy" @click="emit('resume')">
              <RoomIcon name="play" :size="16" /> 返回当前剧场
            </button>
            <button
              class="secondary-button"
              :disabled="busy"
              @click="emit('start', item.story_id, true)"
            >
              重新开始
            </button>
          </template>
          <template v-else-if="item.has_active_run">
            <button
              class="primary-button"
              :disabled="busy"
              @click="emit('start', item.story_id, false)"
            >
              <RoomIcon name="play" :size="16" /> 继续未完故事
            </button>
            <button
              class="secondary-button"
              :disabled="busy"
              @click="emit('start', item.story_id, true)"
            >
              重新开始
            </button>
          </template>
          <template v-else>
            <button
              class="primary-button"
              :disabled="busy"
              @click="emit('start', item.story_id, false)"
            >
              <RoomIcon name="play" :size="16" /> 开启故事
            </button>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>
