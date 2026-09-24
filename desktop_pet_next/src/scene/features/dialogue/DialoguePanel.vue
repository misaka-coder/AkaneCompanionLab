<script setup lang="ts">
import { ref } from "vue";
import type { PlaybackView } from "../../presentation/queue";
import type { StoryNode, StoryRunState } from "../../domain/types";
import RoomIcon from "../../ui/RoomIcon.vue";

const props = defineProps<{
  name: string;
  view: PlaybackView;
  pending: boolean;
  online: boolean;
  placeholder?: string;
  activeStoryTitle?: string;
  storyNode?: StoryNode | null;
  storyRun?: StoryRunState | null;
}>();

const emit = defineEmits<{
  send: [text: string];
  advance: [];
  advanceStory: [];
  auto: [];
  stop: [];
  history: [];
  exitStory: [];
}>();

const message = ref("");

function submit() {
  const text = message.value.trim();
  if (text) {
    emit("send", text);
    message.value = "";
  }
}

function sendQuick(text: string) {
  if (text) {
    emit("send", text);
  }
}
</script>
<template>
  <section class="dialogue-shell" aria-label="对话">
    <header class="dialogue-heading">
      <span class="speaker-name">{{ name || "片刻小屋" }}<i></i></span
      ><span class="dialogue-caption">{{
        activeStoryTitle
          ? (pending
              ? "茜茜正在想怎么回答你…"
              : storyNode?.kind === "conversation"
                ? `自由交谈 · 第 ${(storyRun?.conversation_turns?.length ?? 0) + 1} 轮${storyNode.suggested_turns ? `（建议 ${storyNode.suggested_turns} 轮）` : ""}`
                : `正在演出：《${activeStoryTitle}》`)
          : pending
            ? "正在想怎么回答你…"
            : view.beat
              ? "和你在一起的片刻"
              : "让今天慢一点"
      }}</span>
      <div class="dialogue-tools">
        <button v-if="activeStoryTitle" class="exit-story-btn" title="离开剧场，返回日常模式" @click="emit('exitStory')">返回日常</button>
        <button :class="{ active: view.auto }" :aria-pressed="view.auto" @click="emit('auto')">AUTO</button
        ><button aria-label="对话记录" @click="emit('history')"><RoomIcon name="history" :size="17" /></button>
      </div>
    </header>
    <button class="dialogue-text" :disabled="!view.beat" @click="emit('advance')">
      <span v-if="view.beat">{{ view.visibleText }}<span v-if="!view.revealed" class="type-caret">▎</span></span
      ><span v-else class="dialogue-empty">{{
        pending
          ? "稍等片刻，她的回复正在路上。"
          : online
            ? (storyNode?.kind === "conversation"
                ? "茜茜正在认真听你说，你可以自由回应，也可以随时点击“继续故事”。"
                : "说点什么，或从一份小点心开始。")
            : "连接你的角色，让故事从这里开始。"
      }}</span
      ><span v-if="view.revealed" class="next-beat">{{ view.index + 1 }} / {{ view.total }} <span>▾</span></span>
    </button>
    <div
      v-if="storyNode?.kind === 'conversation' && storyNode.quick_reactions && storyNode.quick_reactions.length > 0"
      class="quick-reactions"
    >
      <button
        v-for="qr in storyNode.quick_reactions"
        :key="qr"
        type="button"
        class="quick-chip"
        :disabled="pending"
        @click="sendQuick(qr)"
      >
        {{ qr }}
      </button>
    </div>
    <form class="dialogue-input" @submit.prevent="submit">
      <span class="input-mark">✧</span
      ><input
        v-model="message"
        aria-label="想对她说的话"
        :placeholder="placeholder || (storyNode?.kind === 'conversation' ? '想对茜茜说点什么…' : '今天有什么想分享的？')"
        :disabled="!online || pending"
        maxlength="6000"
      /><button
        v-if="storyNode?.kind === 'conversation'"
        type="button"
        class="advance-story-btn"
        title="聊得差不多了，推进下一段剧情"
        :disabled="pending"
        @click="emit('advanceStory')"
      >
        继续故事 ▸
      </button
      ><button v-if="pending" type="button" class="send-button" aria-label="停止回复" @click="emit('stop')">■</button
      ><button v-else class="send-button" aria-label="发送" :disabled="!online || !message.trim()">
        <RoomIcon name="arrow" :size="20" />
      </button>
    </form>
  </section>
</template>
