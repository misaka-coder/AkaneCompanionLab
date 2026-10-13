use crate::character_packs::storage::write_text_atomic;
use serde::Serialize;
use std::{
    fs,
    path::PathBuf,
    sync::atomic::{AtomicBool, Ordering},
};

use crate::runtime::config::{runtime_bound_bot_id, runtime_instance_id};
use crate::runtime::paths::akane_data_root;
use crate::runtime::time::current_time_millis;

pub(crate) const UI_LAUNCH_STATE_FILE: &str = "desktop-ui-state.json";

pub(crate) const UI_LAUNCH_STATE_SCHEMA_VERSION: u64 = 1;

pub(crate) const DEVICE_RUNTIME_STATE_FILE: &str = "desktop-device-state.json";

pub(crate) const DEVICE_RUNTIME_STATE_SCHEMA_VERSION: u64 = 1;

// Until the page can present its UI, the transparent main window stays hidden.
// Activation must not reveal a WebView that never finished initializing.
static UI_PRESENTATION_READY: AtomicBool = AtomicBool::new(false);

pub(crate) fn ui_presentation_ready() -> bool {
    UI_PRESENTATION_READY.load(Ordering::Acquire)
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) struct UiLaunchState {
    pub(crate) schema_version: u64,
    pub(crate) status: String,
    pub(crate) phase: String,
    pub(crate) reason: String,
    pub(crate) instance_id: String,
    pub(crate) host_id: String,
    pub(crate) bot_id: String,
    pub(crate) process_id: u32,
    pub(crate) at: u128,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) struct DeviceRuntimeState {
    pub(crate) schema_version: u64,
    pub(crate) status: String,
    pub(crate) phase: String,
    pub(crate) reason: String,
    pub(crate) instance_id: String,
    pub(crate) host_id: String,
    pub(crate) bot_id: String,
    pub(crate) process_id: u32,
    pub(crate) at: u128,
}

pub(crate) fn ui_launch_state_path() -> Result<PathBuf, String> {
    let directory = akane_data_root()?.join("run");
    fs::create_dir_all(&directory).map_err(|error| error.to_string())?;
    Ok(directory.join(UI_LAUNCH_STATE_FILE))
}

pub(crate) fn device_runtime_state_path() -> Result<PathBuf, String> {
    let directory = akane_data_root()?.join("run");
    fs::create_dir_all(&directory).map_err(|error| error.to_string())?;
    Ok(directory.join(DEVICE_RUNTIME_STATE_FILE))
}

pub(crate) fn write_device_runtime_state(
    status: &str,
    phase: &str,
    reason: &str,
) -> Result<DeviceRuntimeState, String> {
    let normalized_status = match status.trim().to_ascii_lowercase().as_str() {
        "online" => "online",
        "connecting" => "connecting",
        "offline" => "offline",
        _ => "starting",
    };
    let clean_phase: String = phase
        .chars()
        .filter(|value| !value.is_control())
        .take(80)
        .collect();
    let clean_reason: String = reason
        .chars()
        .filter(|value| !value.is_control())
        .take(240)
        .collect();
    let instance_id = runtime_instance_id()?;
    let state = DeviceRuntimeState {
        schema_version: DEVICE_RUNTIME_STATE_SCHEMA_VERSION,
        status: normalized_status.to_string(),
        phase: clean_phase,
        reason: clean_reason,
        host_id: instance_id.clone(),
        bot_id: runtime_bound_bot_id()?,
        instance_id,
        process_id: std::process::id(),
        at: current_time_millis(),
    };
    let raw = serde_json::to_string(&state).map_err(|error| error.to_string())?;
    write_text_atomic(&device_runtime_state_path()?, &raw)?;
    Ok(state)
}

#[tauri::command]
pub(crate) fn report_ui_ready(
    window: tauri::Window,
    status: String,
    phase: String,
    reason: String,
) -> Result<UiLaunchState, String> {
    if window.label() != "main" {
        return Err("main_window_required".to_string());
    }
    let normalized_status = match status.trim().to_ascii_lowercase().as_str() {
        "ready" => "ready",
        "failed" => "failed",
        _ => "starting",
    };
    let clean_phase: String = phase
        .chars()
        .filter(|value| !value.is_control())
        .take(80)
        .collect();
    let clean_reason: String = reason
        .chars()
        .filter(|value| !value.is_control())
        .take(240)
        .collect();
    let instance_id = runtime_instance_id()?;
    let state = UiLaunchState {
        schema_version: UI_LAUNCH_STATE_SCHEMA_VERSION,
        status: normalized_status.to_string(),
        phase: clean_phase,
        reason: clean_reason,
        host_id: instance_id.clone(),
        bot_id: runtime_bound_bot_id()?,
        instance_id,
        process_id: std::process::id(),
        at: current_time_millis(),
    };
    let raw = serde_json::to_string(&state).map_err(|error| error.to_string())?;
    if normalized_status != "starting" {
        window.show().map_err(|error| error.to_string())?;
    }
    write_text_atomic(&ui_launch_state_path()?, &raw)?;
    UI_PRESENTATION_READY.store(normalized_status != "starting", Ordering::Release);
    Ok(state)
}
