

export const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";

export const LOCAL_DEFAULT_INSTANCE_ID = "local-default";

export const PROFILE_USER_ID = "master";

export const CLIENT_MODE = "desktop_pet";

export const DESKTOP_HEALTH_PATH = "/desktop-pet/health";

export const PUBLIC_HEALTH_PATH = "/health";

export const BASE_CAPABILITIES = ["speech_segments", "speech_units_v1", "tts", "file_drop", "tool_actions"];

export const AUDIO_PLAYBACK_CAPABILITY = "audio_playback";

export const THINK_IDLE_TIMEOUT_MS = 5 * 60 * 1000;

export const TTS_TIMEOUT_MS = 45 * 1000;

export const TTS_SLOW_REQUEST_MS = 1200;

export const TTS_CHUNK_SOFT_LIMIT = 24;

export const TTS_SHORT_SEGMENT_MAX_CHARS = 4;

export const TTS_SHORT_SEGMENT_HOLD_MS = 420;

export const TTS_PREWARM_TEXT = "嗯。";

export const TTS_PREWARM_DELAY_MS = 650;

export const TTS_PREWARM_TIMEOUT_MS = 12 * 1000;

export const TTS_PREWARM_COOLDOWN_MS = 10 * 60 * 1000;

export const ASR_TIMEOUT_MS = 2 * 60 * 1000;

export const REALTIME_VOICE_FINAL_TIMEOUT_MS = 15 * 1000;

export const REALTIME_VOICE_STABLE_LISTENER_MS = 5 * 1000;

export const SCREEN_VISION_JPEG_QUALITY = 0.8;

export const PROACTIVE_WAKE_DEFAULT_SEC = 30;

export const PROACTIVE_WAKE_MIN_SEC = 15;

export const PROACTIVE_WAKE_MAX_SEC = 600;

export const PROACTIVE_WAKE_RETRY_MS = 15000;

export const MUSIC_TIMELINE_POLL_MS = 8000;

export const MUSIC_TIMELINE_RETRY_MS = 30000;

export const MUSIC_TIMELINE_INITIAL_DELAY_MS = 6500;

export const SYSTEM_MEDIA_POLL_MS = 2000;

export const SYSTEM_MEDIA_MAX_AGE_MS = 10000;

export const SYSTEM_MEDIA_LYRICS_RETRY_MS = 30000;

export const SYSTEM_MEDIA_LYRICS_TURN_WAIT_MS = 1200;

export const SYSTEM_MEDIA_LYRICS_TURN_WAIT_FOCUSED_MS = 2400;

export const BACKEND_RETRY_MS = 30 * 1000;

export const MUSIC_PLAY_MODES = Object.freeze(["列表循环", "单曲循环", "随机播放"]);

export const MIN_RECORDING_MS = 700;

export const CLIENT_SEGMENT_SOFT_LIMIT = 56;

export const LOCAL_CLICK_DELAY_MS = 240;

export const INPUT_HISTORY_LIMIT = 24;

export const CHAT_INPUT_IDLE_HIDE_MS = 5000;

export const PET_MOTION_RESTORE_MS = 1800;

export const PET_IDLE_JUMP_AFTER_MS = 90000;

export const PET_IDLE_JUMP_COOLDOWN_MS = 150000;

export const MUSIC_EMOTION_RESTORE_DELAY_MS = 1800;

export const SCALE_MIN = 0.75;

export const SCALE_MAX = 1.45;

export const SCALE_PRESETS = [0.85, 1, 1.15, 1.3];

export const OPACITY_PRESETS = [1, 0.85, 0.7, 0.55];

export const WORKSPACE_REFRESH_EVENT = "akane-next-workspace-refresh";

export const SHOP_STATUS_EVENT = "akane-next-shop-status";

export const CHARACTER_PACK_ACTIVATED_EVENT = "akane-next-character-pack-activated";

export const PLUGIN_AGENT_EVENT_FRAME_EVENT = "akane:plugin-agent-event-frame";
