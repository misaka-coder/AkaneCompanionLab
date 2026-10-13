use std::{
    collections::VecDeque,
    sync::{Mutex, OnceLock},
};

use crate::desktop_connection::contracts::SATELLITE_PROTOCOL_VERSION;

pub(crate) static PLUGIN_AGENT_EVENT_FRAMES: OnceLock<
    Mutex<VecDeque<(String, serde_json::Value)>>,
> = OnceLock::new();

pub(crate) const PLUGIN_AGENT_EVENT_FRAME_EVENT: &str = "akane:plugin-agent-event-frame";

pub(crate) fn validate_satellite_agent_event_frame(
    payload: &serde_json::Value,
    instance_id: &str,
    lease_epoch: &str,
) -> Option<(String, serde_json::Value)> {
    if payload.get("type").and_then(serde_json::Value::as_str) != Some("agent_event_frame")
        || payload
            .get("protocol_version")
            .and_then(serde_json::Value::as_u64)
            != Some(SATELLITE_PROTOCOL_VERSION)
        || payload
            .get("instance_id")
            .and_then(serde_json::Value::as_str)
            != Some(instance_id)
        || payload
            .get("lease_epoch")
            .and_then(serde_json::Value::as_str)
            != Some(lease_epoch)
    {
        return None;
    }
    let delivery_id = payload
        .get("delivery_id")
        .and_then(serde_json::Value::as_str)?
        .trim();
    let frame = payload.get("payload")?;
    if delivery_id.is_empty() || !frame.is_object() {
        return None;
    }
    Some((delivery_id.to_string(), frame.clone()))
}

pub(crate) fn queue_plugin_agent_event_frame(delivery_id: &str, frame: serde_json::Value) -> bool {
    let delivery_id = delivery_id.trim();
    if delivery_id.is_empty() || !frame.is_object() {
        return false;
    }
    let queue = PLUGIN_AGENT_EVENT_FRAMES.get_or_init(|| Mutex::new(VecDeque::new()));
    let Ok(mut pending) = queue.lock() else {
        return false;
    };
    if pending.iter().any(|(existing, _)| existing == delivery_id) {
        return true;
    }
    if pending.len() >= 64 {
        return false;
    }
    pending.push_back((delivery_id.to_string(), frame));
    true
}

#[tauri::command]
pub(crate) fn list_pending_plugin_agent_event_frames() -> Vec<serde_json::Value> {
    let queue = PLUGIN_AGENT_EVENT_FRAMES.get_or_init(|| Mutex::new(VecDeque::new()));
    let Ok(pending) = queue.lock() else {
        return Vec::new();
    };
    pending
        .iter()
        .map(|(delivery_id, payload)| {
            serde_json::json!({"deliveryId": delivery_id, "payload": payload})
        })
        .collect()
}

#[tauri::command]
pub(crate) fn acknowledge_plugin_agent_event_frame(delivery_id: String) -> bool {
    let delivery_id = delivery_id.trim();
    if delivery_id.is_empty() {
        return false;
    }
    let queue = PLUGIN_AGENT_EVENT_FRAMES.get_or_init(|| Mutex::new(VecDeque::new()));
    let Ok(mut pending) = queue.lock() else {
        return false;
    };
    let before = pending.len();
    pending.retain(|(existing, _)| existing != delivery_id);
    pending.len() != before
}
