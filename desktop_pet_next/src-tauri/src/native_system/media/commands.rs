use crate::native_system::media::actions::normalize_system_media_control_action;
use crate::native_system::media::models::{SystemMediaControlResult, SystemMediaSnapshot};
use crate::native_system::media::outcomes::{
    system_media_control_unavailable, system_media_unavailable,
};
use crate::native_system::media::platform::{
    control_system_media_platform, read_current_system_media,
};

#[tauri::command]
pub(crate) async fn get_current_system_media() -> SystemMediaSnapshot {
    tauri::async_runtime::spawn_blocking(read_current_system_media)
        .await
        .unwrap_or_else(|error| system_media_unavailable("join_failed", error.to_string()))
}

#[tauri::command]
pub(crate) async fn control_system_media(action: String) -> SystemMediaControlResult {
    tauri::async_runtime::spawn_blocking(move || control_system_media_blocking(action))
        .await
        .unwrap_or_else(|error| {
            system_media_control_unavailable("", "join_failed", error.to_string())
        })
}

pub(crate) fn control_system_media_blocking(action: String) -> SystemMediaControlResult {
    let normalized = normalize_system_media_control_action(&action);
    if normalized.is_empty() {
        return system_media_control_unavailable(&action, "invalid_action", "");
    }
    control_system_media_platform(&normalized)
}
