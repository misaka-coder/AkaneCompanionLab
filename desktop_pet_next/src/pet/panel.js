import { MEDIA_CONTROL_TARGETS } from "../media-control.js";
import { CHARACTER_NAME } from "../character-profile.js";
import { mediaPosition, mediaKind } from "../media-awareness.js";
import { readJsonResponse } from "../desktop-voice-session.js";
import { getProfileIdentityText, getProfileDefaultEmotion, getProfileCareConfig } from './profile.js';

/** Owns panel behavior and its temporary runtime state. */
export function createPanelController({
  state,
  els,
  platform: platformPort,
  lifecycle,
  chat: chatPort,
  window: windowPort,
  voice: voicePort,
  persistence: statePort,
  systemMedia: systemMediaPort,
  status: statusPort,
  music: musicPort,
  care: carePort,
  backend: backendPort
}) {
  let recentTracksHistory = [];

  const PANEL_MUSIC_CONTROLS = Object.freeze(["pause", "next", "prev", "recommend"]);

  let panelMusicController = "model";

  let panelSyncTimer = null;

  async function registerPanelBridge() {
    if (!platformPort.isTauriRuntime) return;

    lifecycle.track(await platformPort.listen("panel:ready", () => {
      schedulePanelStateSync(50);
      if (recentTracksHistory.length) {
        void emitPanelEvent("panel:recent-update", recentTracksHistory.slice());
      }
      void refreshPanelCoListenSummary();
      void refreshPanelMusicController();
    }));

    lifecycle.track(await platformPort.listen("panel:action", (event) => {
      const { action, muted, value, controller, mediaAction, operationId } = event.payload || {};
      if (action === "open-workspace") {
        void windowPort.openWorkspaceWindow();
      } else if (action === "open-gomoku") {
        void platformPort.tauriCall("open_gomoku_window", {});
      } else if (action === "open-workshop") {
        void windowPort.openWorkshopWindow();
      } else if (action === "open-shop") {
        void windowPort.openShopWindow();
      } else if (action === "toggle-mute" && typeof muted === "boolean") {
        voicePort.setVoiceEnabled(!muted);
      } else if (action === "stop-reply") {
        chatPort.interruptReply({ announce: true });
      } else if (action === "quit") {
        void windowPort.quitAkane();
      } else if (action === "set-scale" && typeof value === "number") {
        windowPort.updateVisualScale(value);
        schedulePanelStateSync(80);
      } else if (action === "set-opacity" && typeof value === "number") {
        windowPort.updateVisualOpacity(value);
        schedulePanelStateSync(80);
      } else if (action === "refresh-co-listen") {
        void refreshPanelCoListenSummary();
      } else if (action === "refresh-music-controller") {
        void refreshPanelMusicController();
      } else if (action === "set-music-controller") {
        void setPanelMusicController(controller === "user" ? "user" : "model");
      } else if (action === "media-control" && mediaAction) {
        void systemMediaPort.controlActiveMusic(mediaAction, { operationId });
      }
    }));
  }

  async function openPanelWindow() {
    windowPort.closeMenu();
    if (!platformPort.isTauriRuntime) {
      statusPort.setStatus("面板仅 Tauri 可用");
      return;
    }
    await platformPort.tauriCall("open_panel_window", {});
    schedulePanelStateSync(80);
  }

  function buildPanelStatePayload() {
    const control = musicPort.buildActiveMediaControlSnapshot();
    const local = control.target === MEDIA_CONTROL_TARGETS.local;
    const media = local ? {
      title: musicPort.getMusicDisplayName(), artist: "", positionSeconds: els.musicPlayer?.currentTime,
      durationSeconds: els.musicPlayer?.duration, playbackRate: els.musicPlayer?.playbackRate,
    } : systemMediaPort.systemMedia || {};
    const playing = control.isPlaying;
    return {
      instanceId: state.instanceId,
      hostId: state.hostId,
      boundBotId: state.boundBotId,
      backendUrl: state.backendUrl,
      characterName: getProfileIdentityText("name", CHARACTER_NAME),
      emotion: state.currentEmotion || getProfileDefaultEmotion(),
      avatarSrc: els.petImage?.src || "",
      musicPlaying: playing,
      musicTitle: media.title || "",
      musicArtist: media.artist || "",
      musicPosition: local ? Number(media.positionSeconds) || 0 : mediaPosition(media) || 0,
      musicPlaybackStatus: control.playbackStatus,
      musicPlaybackRate: Number.isFinite(media.playbackRate) ? media.playbackRate : 1,
      musicDuration: Number(media.durationSeconds) || 0,
      musicPositionAt: playing ? Date.now() : 0,
      musicController: panelMusicController,
      muted: !state.voiceEnabled,
      scale: state.scale,
      opacity: state.opacity,
      careFeature: { ...carePort.getCareFeatureStatus() },
      shopAvailable: carePort.isCareRuntimeActive() && getProfileCareConfig().shopItems.length > 0,
    };
  }

  function schedulePanelStateSync(delayMs = 500) {
    clearTimeout(panelSyncTimer);
    panelSyncTimer = setTimeout(pushPanelStateUpdate, delayMs);
  }

  async function pushPanelStateUpdate() {
    if (!platformPort.isTauriRuntime) return;
    try {
      await emitPanelEvent("panel:state-update", buildPanelStatePayload());
    } catch {
      // Panel may not be open; silent failure is fine
    }
  }

  async function emitPanelEvent(eventName, payload) {
    try {
      await platformPort.emitTo("panel", eventName, payload);
    } catch {
      await platformPort.emit(eventName, payload);
    }
  }

  function getPanelProfileUserId() {
    return String(statePort.getProfileUserId() || state.profileUserId || "master").trim() || "master";
  }

  function panelMusicControllerFromControls(controls) {
    if (!controls || typeof controls !== "object") return panelMusicController;
    const allEnabled = PANEL_MUSIC_CONTROLS.every((name) => controls[name] !== false);
    return allEnabled ? "model" : "user";
  }

  function updatePanelMusicController(controller) {
    panelMusicController = controller === "user" ? "user" : "model";
    schedulePanelStateSync(0);
  }

  async function refreshPanelMusicController() {
    if (!platformPort.isTauriRuntime) return;
    const response = await platformPort.backendFetch(
      backendPort.buildBackendEndpointUrl("music_control_permissions", "/capabilities/music/control_permissions", {
        user_id: state.sessionId || "desktop_pet_next",
        real_user_id: getPanelProfileUserId(),
        t: Date.now()
      }),
      {
        method: "GET",
        cache: "no-store",
        connectTimeout: 3000
      }
    ).catch(() => null);
    const payload = response?.ok ? await readJsonResponse(response) : null;
    if (payload?.ok && payload.controls && typeof payload.controls === "object") {
      updatePanelMusicController(panelMusicControllerFromControls(payload.controls));
    }
  }

  async function setPanelMusicController(controller) {
    if (!platformPort.isTauriRuntime) return;
    const normalized = controller === "user" ? "user" : "model";
    const previous = panelMusicController;
    const enabled = normalized === "model";
    const response = await platformPort.backendFetch(
      backendPort.buildBackendEndpointUrl("music_control_permissions", "/capabilities/music/control_permissions", {
        user_id: state.sessionId || "desktop_pet_next",
        real_user_id: getPanelProfileUserId(),
        t: Date.now()
      }),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        connectTimeout: 3000,
        body: JSON.stringify({
          controls: Object.fromEntries(PANEL_MUSIC_CONTROLS.map((name) => [name, enabled]))
        })
      }
    ).catch(() => null);
    const payload = response?.ok ? await readJsonResponse(response) : null;
    if (payload?.ok && payload.controls && typeof payload.controls === "object") {
      updatePanelMusicController(panelMusicControllerFromControls(payload.controls));
    } else {
      updatePanelMusicController(previous);
    }
  }

  function formatPanelRecentTrack(item) {
    const title = String(item?.title || "").trim() || "某首歌";
    const artist = String(item?.artist || "").trim();
    const label = String(item?.last_listened_label || item?.timestamp_display || "").trim();
    const main = artist ? `${title} · ${artist}` : title;
    return label ? `${main}（${label}）` : main;
  }

  async function refreshPanelCoListenSummary() {
    if (!platformPort.isTauriRuntime) return;
    const media = mediaKind(systemMediaPort.systemMedia) === "music" ? systemMediaPort.systemMedia : {};
    const response = await platformPort.backendFetch(
      backendPort.buildBackendEndpointUrl("music_co_listen_summary", "/capabilities/music/co_listen_summary", {
        user_id: state.sessionId || "desktop_pet_next",
        real_user_id: getPanelProfileUserId(),
        t: Date.now()
      }),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        connectTimeout: 5000,
        body: JSON.stringify({
          title: String(media.title || "").trim(),
          artist: String(media.artist || "").trim(),
          album: String(media.album || "").trim(),
          source_kind: media.ok ? "system_media" : "",
          source_app: String(media.sourceApp || "").trim(),
          system_media: Boolean(media.ok),
          recent_limit: 5
        })
      }
    ).catch(() => null);
    const payload = response?.ok ? await readJsonResponse(response) : null;
    if (!payload?.ok) return;
    const recent = Array.isArray(payload.recent) ? payload.recent : [];
    if (recent.length) {
      const items = recent.map(formatPanelRecentTrack).filter(Boolean).slice(0, 5);
      if (items.length) {
        void emitPanelEvent("panel:recent-update", items);
      }
    }
    if (Array.isArray(payload.enabled_music_controls)) {
      const enabledSet = new Set(payload.enabled_music_controls.map((name) => String(name)));
      updatePanelMusicController(
        PANEL_MUSIC_CONTROLS.every((name) => enabledSet.has(name)) ? "model" : "user"
      );
    }
  }

  function recordRecentTrack(title, artist = "") {
    const entry = `${title}${artist ? " · " + artist : ""}`;
    recentTracksHistory = [entry, ...recentTracksHistory.filter(item => item !== entry)].slice(0, 5);
    void emitPanelEvent("panel:recent-update", [...recentTracksHistory]);
  }

  return {
    recordRecentTrack,
    registerPanelBridge,
    openPanelWindow,
    schedulePanelStateSync,
    emitPanelEvent
  };
}
