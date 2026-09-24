import { defineStore } from "pinia";
import { ref } from "vue";
import type { Connection, Drawer, Snapshot, StoryItemSummary, StoryRunState } from "../domain/types";

export const useRoom = defineStore("scene-room", () => {
  const snapshot = ref<Snapshot | null>(null);
  const connection = ref<Connection | null>(null);
  const phase = ref<"connecting" | "ready" | "offline">("connecting");
  const drawer = ref<Drawer>(null);
  const error = ref("");
  const busy = ref(false);
  const toast = ref("");
  const previewOutfit = ref("");
  const emotion = ref("normal");
  const muted = ref(true);
  const hideUi = ref(false);
  const stories = ref<StoryItemSummary[]>([]);
  const activeStoryRun = ref<StoryRunState | null>(null);
  const storyBackground = ref("");
  const storyMusic = ref("");
  const storyOutfit = ref("");
  return {
    snapshot,
    connection,
    phase,
    drawer,
    error,
    busy,
    toast,
    previewOutfit,
    emotion,
    muted,
    hideUi,
    stories,
    activeStoryRun,
    storyBackground,
    storyMusic,
    storyOutfit,
  };
});
