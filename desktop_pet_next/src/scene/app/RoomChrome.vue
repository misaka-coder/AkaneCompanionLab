<script setup lang="ts">
import { useRoom } from "../stores/room";
import RoomIcon from "../ui/RoomIcon.vue";
import { panels } from "./panels";
import type { TouchRegion } from "../domain/types";
defineProps<{ backgroundName: string }>();
const emit = defineEmits<{ touch: [region: TouchRegion] }>();
const room = useRoom();
</script>
<template>
  <header class="room-topbar" :inert="room.hideUi || !!room.drawer">
    <a class="room-brand" href="#" @click.prevent="room.drawer = null"
      ><span class="brand-mark"><RoomIcon name="home" :size="21" /></span
      ><span>片刻小屋<small>A LITTLE SPACE FOR US</small></span></a
    >
    <div class="room-location">
      <span class="location-line"></span><RoomIcon name="sun" :size="16" /><span>{{ backgroundName }}</span>
    </div>
    <div class="top-actions">
      <span class="connection-status" :class="room.phase"
        ><i></i>{{ room.phase === "ready" ? "已连接" : room.phase === "connecting" ? "连接中" : "未连接" }}</span
      ><button class="icon-button" :aria-label="room.muted ? '开启声音' : '静音'" @click="room.muted = !room.muted">
        <RoomIcon :name="room.muted ? 'mute' : 'sound'" :size="19" /></button
      ><button class="icon-button" aria-label="连接设置" @click="room.drawer = 'connection'">
        <RoomIcon name="settings" :size="20" />
      </button>
    </div>
  </header>
  <section
    v-if="room.phase === 'ready'"
    class="room-note"
    :class="{ 'is-dimmed': !!room.activeStoryRun || !!room.drawer }"
  >
    <span class="eyebrow">OUR EVERYDAY</span>
    <h1>把时间，<br />留在这里。</h1>
    <p>一杯茶，一点闲话。<br />还有刚好在身边的她。</p>
    <span class="note-divider"></span
    ><span class="note-name">{{ room.snapshot?.character_name }} <span>与你</span></span>
  </section>
  <section v-else class="welcome-note">
    <span class="eyebrow">WELCOME HOME</span>
    <h1>给陪伴，<br />留一个位置。</h1>
    <p>{{ room.phase === "connecting" ? "正在等你的角色回家…" : "连接宿主后，角色与房间会在这里出现。" }}</p>
    <button v-if="room.phase === 'offline'" class="primary-button" @click="room.drawer = 'connection'">
      连接我的角色 <RoomIcon name="arrow" :size="18" />
    </button>
  </section>
  <nav class="room-rail" :inert="room.hideUi || !!room.drawer" aria-label="房间功能">
    <button
      v-for="item in panels"
      :key="item.id!"
      :class="{ active: room.drawer === item.id }"
      :disabled="room.phase !== 'ready'"
      @click="room.drawer = item.id"
    >
      <RoomIcon :name="item.icon" /><span>{{ item.short }}</span></button
    ><span class="rail-divider"></span
    ><button aria-label="添加资源" :disabled="room.phase !== 'ready'" @click="room.drawer = 'resources'">
      <RoomIcon name="upload" /><span>添加</span>
    </button>
  </nav>
  <div
    v-if="room.phase === 'ready'"
    class="touch-shortcuts"
    :inert="room.hideUi || !!room.drawer"
    aria-label="和她互动"
  >
    <span>靠近一点</span><button @click="emit('touch', 'head')">摸摸头</button
    ><button @click="emit('touch', 'hand')">碰一下手</button><button @click="emit('touch', 'shoulder')">拍拍肩</button>
  </div>
  <footer class="room-footer" :inert="!!room.drawer">
    <span>在一起的平凡日常，也值得珍藏。</span
    ><button @click="room.hideUi = !room.hideUi">
      <RoomIcon name="eye" :size="15" />{{ room.hideUi ? "显示界面" : "沉浸一下" }}<kbd>Esc</kbd>
    </button>
  </footer>
</template>
