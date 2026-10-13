use crate::{computer_use, desktop_capture, personal_browser};
use std::sync::Mutex;

use crate::desktop_connection::browser::execute_satellite_browser_once;
use crate::desktop_connection::contracts::{
    DESKTOP_CONTEXT_SNAPSHOT_TOOL_ID, DESKTOP_SCREENSHOT_TOOL_ID, OPEN_BROWSER_TOOL_ID,
    SYSTEM_MEDIA_CONTROL_TOOL_ID, SYSTEM_MEDIA_SNAPSHOT_TOOL_ID, SYSTEM_PROCESS_SNAPSHOT_TOOL_ID,
    SYSTEM_PROCESS_TERMINATE_TOOL_ID, SYSTEM_VOLUME_TOOL_ID,
};
use crate::desktop_connection::ledger::{
    satellite_terminal_result, SatelliteInvocationLedger, SatelliteTerminalResult,
    SATELLITE_INVOCATION_LEDGER,
};
use crate::native_system::context::get_desktop_context_snapshot;
use crate::native_system::context_models::DesktopContextSnapshot;
use crate::native_system::media::commands::{control_system_media, get_current_system_media};
use crate::native_system::processes::{read_system_process_snapshot, terminate_system_process};
use crate::native_system::volume::{read_system_volume_result, set_system_volume_result};

pub(crate) async fn execute_satellite_invocation_once(
    invocation_id: &str,
    tool_id: &str,
    arguments: &serde_json::Value,
) -> SatelliteTerminalResult {
    if matches!(
        tool_id.as_ref(),
        computer_use::protocol::TOOL_ID | personal_browser::TOOL_ID
    ) {
        let id = invocation_id.to_string();
        let args = arguments.clone();
        let personal = tool_id == personal_browser::TOOL_ID;
        let data = tauri::async_runtime::spawn_blocking(move || if personal {personal_browser::execute(&id, &args)} else {computer_use::execute(&id, &args)}).await
            .unwrap_or_else(|_| serde_json::json!({"ok":false,"action_state":"unknown","observation_state":"failed","reason":"executor_worker_failed"}));
        let status = if data["ok"] == true {
            "succeeded"
        } else if data["action_state"] == "unknown" {
            "execution_unknown"
        } else {
            "failed"
        };
        let reason = data["reason"].as_str().unwrap_or("").to_string();
        return satellite_terminal_result(tool_id, status, &reason, data);
    }
    if tool_id == OPEN_BROWSER_TOOL_ID {
        let raw_url = arguments
            .get("url")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("");
        return execute_satellite_browser_once(invocation_id, raw_url);
    }

    let ledger = SATELLITE_INVOCATION_LEDGER
        .get_or_init(|| Mutex::new(SatelliteInvocationLedger::default()));
    if let Ok(current) = ledger.lock() {
        if let Some(existing) = current.get(invocation_id) {
            if existing.tool_id == tool_id {
                return existing;
            }
            return satellite_terminal_result(
                tool_id,
                "rejected",
                "invocation_id_tool_conflict",
                serde_json::Value::Null,
            );
        }
    }

    let result = match tool_id {
        DESKTOP_SCREENSHOT_TOOL_ID => match desktop_capture::capture() {
            Ok(data) => satellite_terminal_result(tool_id, "succeeded", "", data),
            Err(reason) => {
                satellite_terminal_result(tool_id, "failed", reason, serde_json::Value::Null)
            }
        },
        DESKTOP_CONTEXT_SNAPSHOT_TOOL_ID => {
            desktop_context_terminal_result(get_desktop_context_snapshot())
        }
        SYSTEM_MEDIA_SNAPSHOT_TOOL_ID => {
            match serde_json::to_value(get_current_system_media().await) {
                Ok(data) => satellite_terminal_result(tool_id, "succeeded", "", data),
                Err(_) => satellite_terminal_result(
                    tool_id,
                    "failed",
                    "result_serialization_failed",
                    serde_json::Value::Null,
                ),
            }
        }
        SYSTEM_PROCESS_SNAPSHOT_TOOL_ID => {
            let snapshot = read_system_process_snapshot(arguments);
            let status = if snapshot.ok { "succeeded" } else { "failed" };
            let reason = snapshot.reason.clone();
            match serde_json::to_value(snapshot) {
                Ok(data) => satellite_terminal_result(tool_id, status, &reason, data),
                Err(_) => satellite_terminal_result(
                    tool_id,
                    "failed",
                    "result_serialization_failed",
                    serde_json::Value::Null,
                ),
            }
        }
        SYSTEM_PROCESS_TERMINATE_TOOL_ID => {
            let pid = arguments
                .get("pid")
                .and_then(serde_json::Value::as_u64)
                .unwrap_or(0);
            let control = terminate_system_process(pid as u32);
            let status = if control.ok { "succeeded" } else { "failed" };
            let reason = if control.ok {
                String::new()
            } else if control.reason.trim().is_empty() {
                "terminate_failed".to_string()
            } else {
                control.reason.clone()
            };
            let data = serde_json::to_value(&control).unwrap_or(serde_json::Value::Null);
            satellite_terminal_result(tool_id, status, &reason, data)
        }
        SYSTEM_VOLUME_TOOL_ID => {
            let action = arguments
                .get("action")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("");
            let control = if action == "set" {
                set_system_volume_result(
                    action,
                    arguments
                        .get("value")
                        .and_then(serde_json::Value::as_u64)
                        .unwrap_or(0) as u32,
                )
            } else {
                read_system_volume_result(action)
            };
            let status = if control.ok { "succeeded" } else { "failed" };
            let reason = if control.ok {
                String::new()
            } else if control.reason.trim().is_empty() {
                "volume_control_failed".to_string()
            } else {
                control.reason.clone()
            };
            let data = serde_json::to_value(&control).unwrap_or(serde_json::Value::Null);
            satellite_terminal_result(tool_id, status, &reason, data)
        }
        SYSTEM_MEDIA_CONTROL_TOOL_ID => {
            let action = arguments
                .get("action")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("");
            if !matches!(action, "play" | "pause" | "stop" | "previous" | "next") {
                satellite_terminal_result(
                    tool_id,
                    "rejected",
                    "invalid_action",
                    serde_json::Value::Null,
                )
            } else {
                let control = control_system_media(action.to_string()).await;
                let status = if control.ok {
                    "succeeded"
                } else if control.status == "execution_unknown" {
                    "execution_unknown"
                } else {
                    "failed"
                };
                let reason = if control.ok {
                    String::new()
                } else if control.reason.trim().is_empty() {
                    "media_control_failed".to_string()
                } else {
                    control.reason.clone()
                };
                match serde_json::to_value(control) {
                    Ok(data) => satellite_terminal_result(tool_id, status, &reason, data),
                    Err(_) => satellite_terminal_result(
                        tool_id,
                        "failed",
                        "result_serialization_failed",
                        serde_json::Value::Null,
                    ),
                }
            }
        }
        _ => {
            satellite_terminal_result(tool_id, "rejected", "unknown_tool", serde_json::Value::Null)
        }
    };
    if let Ok(mut current) = ledger.lock() {
        if tool_id == DESKTOP_SCREENSHOT_TOOL_ID {
            current
                .entries
                .retain(|_, prior| prior.tool_id != DESKTOP_SCREENSHOT_TOOL_ID);
        }
        current.insert(invocation_id.to_string(), result.clone());
    }
    result
}

pub(crate) fn desktop_context_terminal_result(
    snapshot: DesktopContextSnapshot,
) -> SatelliteTerminalResult {
    let (status, reason) = if snapshot.ok {
        ("succeeded", "")
    } else {
        (
            "failed",
            match snapshot.foreground.source.as_str() {
                "self" => "foreground_is_desktop_pet",
                "unsupported_platform" => "unsupported_platform",
                _ => "foreground_unavailable",
            },
        )
    };
    match serde_json::to_value(snapshot) {
        Ok(data) => {
            satellite_terminal_result(DESKTOP_CONTEXT_SNAPSHOT_TOOL_ID, status, reason, data)
        }
        Err(_) => satellite_terminal_result(
            DESKTOP_CONTEXT_SNAPSHOT_TOOL_ID,
            "failed",
            "result_serialization_failed",
            serde_json::Value::Null,
        ),
    }
}
