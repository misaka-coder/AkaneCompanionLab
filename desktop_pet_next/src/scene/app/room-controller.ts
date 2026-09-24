import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { SceneClient } from "../bridges/client";
import { readConnection, rememberConnection } from "../bridges/connection";
import { enterNativeRoom, openShopWindow, openWorkshopWindow, releaseNativePlayback, shareOutfit } from "../bridges/native";
import { friendlyError } from "../bridges/errors";
import { useRoom } from "../stores/room";
import type { ActionRequest, Connection, Snapshot, TouchRegion } from "../domain/types";
import type { StageFrame } from "../stage/renderer";
import { RoomMusic } from "../audio/music";
import { RoomEffects } from "../audio/effects";
import { validate } from "../contracts/validate";
import { usePlayback } from "../presentation/playback";
import { useStoryController } from "./story-controller";

type Action = { kind: ActionRequest["kind"]; target: string; count?: number; id: string; revision: number };
export function useRoomController() {
  const room = useRoom();
  const playback = usePlayback(room);
  const reaction = ref<{ motion: TouchRegion; id: number } | null>(null);
  const retryAction = ref<Action | null>(null);
  let client: SceneClient | null = null;
  let scope = 0;
  let animation = 0;
  let lastTick = 0;
  let touchAt = 0;
  let touchResponded = 0;
  let toastTimer: ReturnType<typeof setTimeout>;
  let feedDebounceTimer: ReturnType<typeof setTimeout> | null = null;
  const pendingFeedQueue: { target: string; count: number; eventId?: string; itemName?: string }[] = [];
  const music = new RoomMusic();
  const effects = new RoomEffects();
  const notify = (text: string) => {
    room.toast = text;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      room.toast = "";
    }, 3500);
  };
  const story = useStoryController(() => client, room, playback, notify);
  const asset = (url: string) => client?.asset(url) || "";
  const frame = computed<StageFrame | null>(() => {
    const s = playback.pinned.value || room.snapshot;
    if (!s || !client) return null;
    const outfits = room.previewOutfit ? room.snapshot?.catalog.outfits : s.catalog.outfits;
    const targetOutfitId = room.previewOutfit || room.storyOutfit || s.room.outfit_id;
    let outfit = outfits?.find((o) => o.id === targetOutfitId || o.name === targetOutfitId);
    if (!outfit) {
      outfit = outfits?.find((o) => o.id === s.room.outfit_id) || outfits?.[0];
    }
    const activeBgId = room.storyBackground || s.room.background_id;
    let bgUrl = "";
    const background = s.catalog.backgrounds.find(
      (b) => b.id === activeBgId || b.name === activeBgId || b.url === activeBgId || b.url.endsWith(activeBgId),
    );
    if (background) {
      bgUrl = background.url;
    } else if (activeBgId && (activeBgId.includes("/") || activeBgId.endsWith(".png") || activeBgId.endsWith(".jpg"))) {
      bgUrl = activeBgId;
    } else {
      const fallback = s.catalog.backgrounds.find((b) => b.id === s.room.background_id) || s.catalog.backgrounds[0];
      if (fallback) bgUrl = fallback.url;
    }
    const portrait =
      outfit?.emotions.find((e) => e.id === room.emotion) ||
      outfit?.emotions.find((e) => e.id === "normal") ||
      outfit?.emotions[0];
    if (!bgUrl || !portrait || !outfit) return null;
    return {
      background: asset(bgUrl),
      portrait: asset(portrait.url),
      position: s.room.position,
      scale: outfit.scale,
      anchorX: outfit.anchor_x,
      anchorY: outfit.anchor_y,
    };
  });
  async function connect(connection: Connection) {
    const current = ++scope;
    story.finishStory();
    playback.stop();
    music.dispose();
    room.busy = false;
    retryAction.value = null;
    room.previewOutfit = "";
    room.snapshot = null;
    room.connection = { ...connection };
    room.phase = "connecting";
    room.error = "";
    try {
      const next = new SceneClient(connection);
      let snapshot = await next.snapshot();
      if (current !== scope) return;
      snapshot = await enterNativeRoom(next, snapshot);
      if (current !== scope) return;
      client = next;
      room.snapshot = snapshot;
      await playback.bind(next);
      if (current !== scope) return;
      room.phase = "ready";
      room.drawer = null;
      rememberConnection(connection);
      void story.loadCatalog();
    } catch (error) {
      if (current === scope) {
        client = null;
        room.phase = "offline";
        room.error = friendlyError(error);
        room.drawer = "connection";
      }
    }
  }
  async function execute(value: Action) {
    if (!client || room.busy) return;
    room.busy = true;
    room.error = "";
    const current = scope;
    let confirmed = false;
    try {
      const result = await client.action(value.kind, value.target, value.revision, value.id, value.count ?? 1);
      if (current !== scope) return;
      confirmed = true;
      retryAction.value = null;
      room.snapshot = result.snapshot;
      if (!result.ok) throw Error(result.reason);
      if (value.kind !== "touch") effects.play("success");
      room.previewOutfit = "";
      const kind = value.kind;
      if (["equip", "background", "position"].includes(kind)) playback.stop();
      if (kind === "equip") await shareOutfit(client, result.snapshot.room.outfit_id);
      notify(
        {
          equip: "换好啦",
          feed: "点心已送到她手里",
          buy: "已经放入口袋",
          background: "换一个心情",
          music: "音乐已切换",
          position: "位置已调整",
          touch: "",
          claim_allowance: "零花钱已到账",
        }[kind],
      );
      if (kind === "feed") {
        pendingFeedQueue.push({
          target: value.target,
          count: result.event?.quantity || value.count || 1,
          eventId: result.event?.event_id,
          itemName: result.event?.item_name,
        });
        if (feedDebounceTimer) clearTimeout(feedDebounceTimer);
        feedDebounceTimer = setTimeout(async () => {
          if (!pendingFeedQueue.length || playback.pending.value) return;
          const items = [...pendingFeedQueue];
          pendingFeedQueue.length = 0;
          const lastEventId = items[items.length - 1]?.eventId;
          if (items.length === 1) {
            await playback.send("", lastEventId);
          } else {
            const summary = items.map((e) => `${e.itemName || "点心"} x${e.count}`).join("、");
            await playback.send(`刚才发生的互动：我连续投喂了你${summary}。`, lastEventId);
          }
        }, 1200);
      } else if (["equip", "touch"].includes(kind) && !playback.pending.value) {
        if (kind !== "touch" || Date.now() - touchResponded > 10000) {
          touchResponded = Date.now();
          await playback.send("", result.event?.event_id);
        }
      }
    } catch (error) {
      if (current === scope) {
        room.error = friendlyError(error);
        if (!confirmed) retryAction.value = value;
      }
    } finally {
      if (current === scope) room.busy = false;
    }
  }
  function action(kind: ActionRequest["kind"], target: string, count = 1) {
    if (!room.snapshot || room.busy) return;
    if (retryAction.value) {
      room.error = "上次动作的结果还未取回，请先点击“重试同步”。";
      return;
    }
    return execute({ kind, target, count, revision: room.snapshot.room.revision, id: crypto.randomUUID() });
  }
  async function importResource(value: { kind: "background" | "outfit" | "music"; name: string; data: string }) {
    if (!client || room.busy) return;
    const current = scope;
    room.busy = true;
    room.error = "";
    try {
      const result = await client.request("/scene/resources/import", { identity: client.identity, ...value });
      if (current !== scope) return;
      room.snapshot = validate<Snapshot>("Snapshot", result);
      room.drawer = value.kind === "outfit" ? "wardrobe" : value.kind === "music" ? "music" : "places";
      notify("已加入资源库，选择后即可使用");
    } catch (error) {
      if (current === scope) room.error = friendlyError(error);
    } finally {
      if (current === scope) room.busy = false;
    }
  }
  async function addShopItem(
    value: { name: string; price: number; description?: string; effects: Record<string, number>; icon?: string },
    callback?: (ok: boolean) => void,
  ) {
    if (!client || room.busy) {
      callback?.(false);
      return;
    }
    const current = scope;
    room.busy = true;
    room.error = "";
    try {
      const result = await client.request("/scene/shop/items/add", { identity: client.identity, ...value });
      if (current !== scope) {
        callback?.(false);
        return;
      }
      room.snapshot = validate<Snapshot>("Snapshot", result);
      notify(`新品「${value.name}」已上架小卖铺`);
      callback?.(true);
    } catch (error) {
      if (current === scope) room.error = friendlyError(error);
      callback?.(false);
    } finally {
      if (current === scope) room.busy = false;
    }
  }
  function touch(region: TouchRegion) {
    if (Date.now() - touchAt < 800 || room.busy) return;
    touchAt = Date.now();
    effects.play(region);
    reaction.value = { motion: region, id: touchAt };
    void action("touch", region);
  }
  watch(
    () => [room.storyMusic || room.snapshot?.room.music_id, room.muted],
    () => {
      if (room.muted) playback.mute();
      music.setMuted(room.muted);
      effects.setMuted(room.muted);
      const activeMusicId = room.storyMusic || room.snapshot?.room.music_id;
      const track = activeMusicId
        ? room.snapshot?.catalog.music.find(
            (m) => m.id === activeMusicId || m.name === activeMusicId || m.url === activeMusicId || m.url.endsWith(activeMusicId),
          )
        : undefined;
      const current = scope;
      const trackUrl = track
        ? asset(track.url)
        : activeMusicId && (activeMusicId.includes("/") || activeMusicId.endsWith(".mp3") || activeMusicId.endsWith(".ogg") || activeMusicId.endsWith(".flac") || activeMusicId.endsWith(".wav") || activeMusicId.endsWith(".m4a"))
          ? asset(activeMusicId)
          : "";
      void music.change(trackUrl).catch(() => {
        if (current === scope) room.error = "音乐播放失败；请检查文件并点击声音按钮后重试。";
      });
    },
  );
  function keyboard(event: KeyboardEvent) {
    if (room.drawer) return;
    if (event.key === "Escape") {
      room.hideUi = !room.hideUi;
      return;
    }
    if ((event.target as HTMLElement).closest("input,textarea,button")) return;
    if (event.code === "Space") {
      event.preventDefault();
      playback.advance();
    }
  }
  watch(playback.speaking, (speaking) => music.setDucked(speaking));
  onMounted(async () => {
    document.addEventListener("keydown", keyboard);
    function tick(now: number) {
      if (!document.hidden) playback.tick(lastTick ? now - lastTick : 0);
      lastTick = now;
      animation = requestAnimationFrame(tick);
    }
    animation = requestAnimationFrame(tick);
    try {
      await connect(await readConnection());
    } catch (error) {
      room.phase = "offline";
      room.error = friendlyError(error);
    }
  });
  onBeforeUnmount(() => {
    scope++;
    story.stopStory();
    playback.stop();
    music.dispose();
    cancelAnimationFrame(animation);
    effects.dispose();
    clearTimeout(toastTimer);
    if (feedDebounceTimer) clearTimeout(feedDebounceTimer);
    document.removeEventListener("keydown", keyboard);
    window.removeEventListener("beforeunload", unload);
    void releaseNativePlayback();
  });
  const unload = () => void releaseNativePlayback();
  window.addEventListener("beforeunload", unload);
  async function openWorkshop() {
    const opened = await openWorkshopWindow();
    if (!opened) {
      window.open("/workshop.html", "_blank");
    }
  }
  async function openShop() {
    const opened = await openShopWindow();
    if (!opened) {
      window.open("/shop.html", "_blank");
    }
  }
  async function send(message: string) {
    if (
      room.activeStoryRun &&
      (room.activeStoryRun.current_node.kind === "agent" ||
        room.activeStoryRun.current_node.kind === "conversation")
    ) {
      await story.stepStory("", message);
      return;
    }
    return playback.send(message);
  }
  return {
    room,
    frame,
    asset,
    connect,
    action,
    touch,
    reaction,
    importResource,
    addShopItem,
    openWorkshop,
    openShop,
    startStory: story.startStory,
    stepStory: story.stepStory,
    advanceStory: story.advanceStory,
    finishStory: story.finishStory,
    loadStoryCatalog: story.loadCatalog,
    importStory: story.importStory,
    ...playback,
    stop: () => (room.activeStoryRun ? story.stopStory() : playback.stop()),
    send,
    retryLabel: computed(() => (retryAction.value ? "重试同步" : playback.retryable.value ? "重试回应" : "")),
    retry: () => (retryAction.value ? execute(retryAction.value) : playback.retry()),
  };
}
