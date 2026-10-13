use serde::{Deserialize, Serialize};
use std::collections::HashMap;

use crate::runtime::config::{
    DEFAULT_BACKEND_URL, DEFAULT_CHARACTER_PACK_ID, DEFAULT_EMOTION, DEFAULT_OUTFIT,
    DEFAULT_PROFILE_USER_ID, LOCAL_DEFAULT_INSTANCE_ID,
};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CharacterRuntimeState {
    pub(crate) version: u32,
    pub(crate) character_pack_id: String,
    pub(crate) session_id: String,
    pub(crate) outfit: String,
    pub(crate) current_emotion: String,
    pub(crate) x: Option<i32>,
    pub(crate) y: Option<i32>,
    pub(crate) width: Option<u32>,
    pub(crate) height: Option<u32>,
    pub(crate) scale: f64,
    pub(crate) opacity: f64,
    pub(crate) care: serde_json::Value,
    pub(crate) updated_at: u64,
}

impl Default for CharacterRuntimeState {
    fn default() -> Self {
        Self {
            version: 1,
            character_pack_id: String::new(),
            session_id: String::new(),
            outfit: String::new(),
            current_emotion: String::new(),
            x: None,
            y: None,
            width: None,
            height: None,
            scale: 1.0,
            opacity: 1.0,
            care: serde_json::Value::Null,
            updated_at: 0,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PetState {
    pub(crate) instance_id: String,
    pub(crate) host_id: String,
    pub(crate) bound_bot_id: String,
    pub(crate) x: Option<i32>,
    pub(crate) y: Option<i32>,
    pub(crate) width: Option<u32>,
    pub(crate) height: Option<u32>,
    pub(crate) scale: f64,
    pub(crate) opacity: f64,
    pub(crate) skip_taskbar: bool,
    pub(crate) always_on_top: bool,
    pub(crate) click_through: bool,
    pub(crate) backend_url: String,
    pub(crate) profile_user_id: String,
    #[serde(default = "default_character_pack_id")]
    pub(crate) character_pack_id: String,
    pub(crate) characters: HashMap<String, CharacterRuntimeState>,
    pub(crate) session_id: String,
    pub(crate) outfit: String,
    pub(crate) current_emotion: String,
    #[serde(default = "default_restore_latest_on_startup")]
    pub(crate) restore_latest_on_startup: bool,
    #[serde(default)]
    pub(crate) voice_enabled: bool,
    #[serde(default = "default_voice_input_enabled")]
    pub(crate) voice_input_enabled: bool,
    #[serde(default = "default_voice_volume")]
    pub(crate) voice_volume: f64,
    #[serde(default)]
    pub(crate) screen_vision_enabled: bool,
    // Frontend owns normalization; native persistence only preserves these values.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) screen_vision_sample_interval_sec: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) screen_vision_window_sec: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) screen_vision_max_edge: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) screen_vision_packing: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) screen_vision_sheet_count: Option<u32>,
    #[serde(default)]
    pub(crate) proactive_wake_enabled: bool,
    #[serde(default = "default_proactive_wake_interval_sec")]
    pub(crate) proactive_wake_interval_sec: u32,
    #[serde(default = "default_screen_vision_frame_count")]
    pub(crate) screen_vision_frame_count: u32,
    #[serde(default = "default_hit_test_enabled")]
    pub(crate) hit_test_enabled: bool,
    #[serde(default)]
    pub(crate) hitbox_overlay: bool,
}

impl Default for PetState {
    fn default() -> Self {
        Self {
            instance_id: LOCAL_DEFAULT_INSTANCE_ID.to_string(),
            host_id: String::new(),
            bound_bot_id: String::new(),
            x: None,
            y: None,
            width: None,
            height: None,
            scale: 1.0,
            opacity: 1.0,
            skip_taskbar: true,
            always_on_top: true,
            click_through: false,
            backend_url: DEFAULT_BACKEND_URL.to_string(),
            profile_user_id: DEFAULT_PROFILE_USER_ID.to_string(),
            character_pack_id: DEFAULT_CHARACTER_PACK_ID.to_string(),
            characters: HashMap::new(),
            session_id: String::new(),
            outfit: DEFAULT_OUTFIT.to_string(),
            current_emotion: DEFAULT_EMOTION.to_string(),
            restore_latest_on_startup: true,
            voice_enabled: false,
            voice_input_enabled: true,
            voice_volume: 0.85,
            screen_vision_enabled: false,
            screen_vision_sample_interval_sec: None,
            screen_vision_window_sec: None,
            screen_vision_max_edge: None,
            screen_vision_packing: None,
            screen_vision_sheet_count: None,
            proactive_wake_enabled: false,
            proactive_wake_interval_sec: default_proactive_wake_interval_sec(),
            screen_vision_frame_count: default_screen_vision_frame_count(),
            hit_test_enabled: true,
            hitbox_overlay: false,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ClientLaunchBinding {
    pub(crate) instance_id: String,
    pub(crate) host_id: String,
    pub(crate) bound_bot_id: String,
    pub(crate) backend_url: String,
    pub(crate) has_backend_override: bool,
}

pub(crate) fn default_hit_test_enabled() -> bool {
    true
}

pub(crate) fn default_voice_volume() -> f64 {
    0.85
}

pub(crate) fn default_restore_latest_on_startup() -> bool {
    true
}

pub(crate) fn default_voice_input_enabled() -> bool {
    true
}

pub(crate) fn default_character_pack_id() -> String {
    DEFAULT_CHARACTER_PACK_ID.to_string()
}

pub(crate) fn default_proactive_wake_interval_sec() -> u32 {
    30
}

pub(crate) fn default_screen_vision_frame_count() -> u32 {
    4
}
