<script setup lang="ts">
import { computed } from "vue";
import { useRoomController } from "./room-controller";
import RoomStage from "../stage/RoomStage.vue";
import RoomIcon from "../ui/RoomIcon.vue";
import RoomDrawer from "../ui/RoomDrawer.vue";
import ConnectionPanel from "../features/connection/ConnectionPanel.vue";
import WardrobePanel from "../features/wardrobe/WardrobePanel.vue";
import PantryPanel from "../features/inventory/PantryPanel.vue";
import DialoguePanel from "../features/dialogue/DialoguePanel.vue";
import PlacesPanel from "../features/resources/PlacesPanel.vue";
import MusicPanel from "../features/resources/MusicPanel.vue";
import ResourcePanel from "../features/resources/ResourcePanel.vue";
import StoryPanel from "../features/story/StoryPanel.vue";
import ChoiceOverlay from "../features/story/ChoiceOverlay.vue";
import EndingCard from "../features/story/EndingCard.vue";
import { panels } from "./panels";
import RoomChrome from "./RoomChrome.vue";
const c = useRoomController();
const { room, frame, view, pending, history, reaction, retryLabel } = c;
const panel = computed(
  () =>
    panels.find((p) => p.id === room.drawer) || {
      name: room.drawer === "connection" ? "回到你的房间" : room.drawer === "history" ? "我们的对话" : "添一点新鲜感",
      label: "A LITTLE SPACE FOR US",
    },
);
const backgroundName = computed(
  () =>
    room.snapshot?.catalog.backgrounds.find((b) => b.id === (room.storyBackground || room.snapshot?.room.background_id))?.name ||
    "等你回来的地方",
);
function closeDrawer() {
  room.drawer = null;
  room.previewOutfit = "";
}
</script>
<template>
  <main class="room-app" :class="{ 'ui-hidden': room.hideUi, 'is-night': /夜|月色|night/i.test(backgroundName) }">
    <RoomStage
      :frame="frame"
      :motion="view.beat?.motion_id"
      :beat-id="view.beat?.beat_id"
      :reaction="reaction"
      @touch="c.touch"
      @error="room.error = $event"
    />
    <div class="room-vignette"></div>
    <RoomChrome :background-name="backgroundName" @touch="c.touch" />
    <DialoguePanel
      :inert="room.hideUi || !!room.drawer"
      :name="room.activeStoryRun?.current_node.speaker || room.snapshot?.character_name || ''"
      :view="view"
      :pending="pending"
      :online="room.phase === 'ready' && (!room.activeStoryRun || ['agent', 'conversation'].includes(room.activeStoryRun.current_node.kind))"
      :placeholder="room.activeStoryRun ? (['agent', 'conversation'].includes(room.activeStoryRun.current_node.kind) ? '自由回应，你的话会推进剧情…' : '点击对白继续故事，返回日常后可以聊天') : ''"
      :active-story-title="room.activeStoryRun ? (room.stories.find(s => s.story_id === room.activeStoryRun?.story_id)?.title || '小剧场') : ''"
      :story-node="room.activeStoryRun?.current_node" :story-run="room.activeStoryRun"
      @send="c.send" @advance="c.advance" @advance-story="c.advanceStory"
      @auto="c.auto" @stop="c.stop" @history="room.drawer = 'history'" @exit-story="c.finishStory"
    />

    <Transition name="toast"
      ><div v-if="room.toast" class="room-toast" role="status">
        <RoomIcon name="leaf" :size="17" />{{ room.toast }}
      </div></Transition
    >
    <div v-if="room.error" class="room-error" role="alert">
      <span>{{ room.error }}</span
      ><button v-if="retryLabel" :disabled="pending || room.busy" @click="c.retry">{{ retryLabel }}</button
      ><button aria-label="关闭提示" @click="room.error = ''">×</button>
    </div>
    <RoomDrawer :open="!!room.drawer" :title="panel.name" :eyebrow="panel.label" @close="closeDrawer">
      <ConnectionPanel
        v-if="room.drawer === 'connection'"
        :value="room.connection"
        :busy="room.phase === 'connecting'"
        @connect="c.connect"
      />
      <WardrobePanel
        v-else-if="room.drawer === 'wardrobe' && room.snapshot"
        :outfits="room.snapshot.catalog.outfits"
        :selected="room.snapshot.room.outfit_id"
        :preview="room.previewOutfit"
        :busy="room.busy"
        :asset="c.asset"
        @preview="room.previewOutfit = $event"
        @equip="c.action('equip', $event)"
        @cancel="room.previewOutfit = ''"
        @open-add="room.drawer = 'resources'"
        @open-workshop="c.openWorkshop"
      />
      <PantryPanel
        v-else-if="room.drawer === 'pantry' && room.snapshot"
        :snapshot="room.snapshot"
        :busy="room.busy"
        @action="c.action"
        @add-item="c.addShopItem" @open-shop="c.openShop"
      />
      <PlacesPanel
        v-else-if="room.drawer === 'places' && room.snapshot"
        :snapshot="room.snapshot"
        :busy="room.busy"
        :asset="c.asset"
        :action="c.action"
        @open-add="room.drawer = 'resources'"
      />
      <MusicPanel v-else-if="room.drawer === 'music' && room.snapshot" :snapshot="room.snapshot" :busy="room.busy" :action="c.action" @import-music="c.importResource" />
      <template v-else-if="room.drawer === 'history'"
        ><div v-if="!history.length" class="empty-state">这次相聚的对话会留在这里。</div>
        <article v-for="(line, index) in history" :key="index" class="history-line">
          <b>{{ line.name }}</b>
          <p>{{ line.text }}</p>
        </article></template
      >
      <StoryPanel
        v-else-if="room.drawer === 'story'"
        :stories="room.stories"
        :active-run="room.activeStoryRun"
        :busy="room.busy" :asset="c.asset"
        :character-pack-id="room.snapshot?.identity.character_pack_id"
        @start="c.startStory"
        @resume="room.drawer = null"
        @reset="c.startStory($event, true)"
        @refresh="c.loadStoryCatalog" @import="c.importStory"
      />
      <ResourcePanel v-else-if="room.drawer === 'resources'" :busy="room.busy" @publish="c.importResource" />
    </RoomDrawer>
    <ChoiceOverlay
      v-if="room.activeStoryRun?.current_node.kind === 'choice'"
      :title="room.activeStoryRun.current_node.title"
      :options="room.activeStoryRun.current_node.options"
      :busy="room.busy"
      @choose="c.stepStory"
    />
    <EndingCard
      v-if="room.activeStoryRun?.current_node.kind === 'ending'"
      :title="room.activeStoryRun.current_node.ending_title"
      :summary="room.activeStoryRun.current_node.ending_summary"
      :busy="room.busy"
      @finish="c.finishStory"
      @replay="c.startStory(room.activeStoryRun.story_id, true)"
    />
  </main>
</template>
