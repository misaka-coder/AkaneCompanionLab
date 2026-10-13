use crate::{computer_use, personal_browser};
use std::collections::HashMap;

use crate::desktop_connection::contracts::{
    satellite_tool_contract, DESKTOP_CONTEXT_SNAPSHOT_TOOL_ID, DESKTOP_SCREENSHOT_TOOL_ID,
    OPEN_BROWSER_TOOL_ID, SATELLITE_PROTOCOL_VERSION, SYSTEM_MEDIA_CONTROL_TOOL_ID,
    SYSTEM_MEDIA_SNAPSHOT_TOOL_ID, SYSTEM_PROCESS_SNAPSHOT_TOOL_ID,
    SYSTEM_PROCESS_TERMINATE_TOOL_ID, SYSTEM_VOLUME_TOOL_ID,
};

pub(crate) fn validate_satellite_invocation(
    value: &serde_json::Value,
    instance_id: &str,
    lease_epoch: &str,
    offer_ids: &HashMap<String, String>,
) -> Option<(String, String, serde_json::Value)> {
    let tool_id = value
        .get("tool_id")
        .and_then(serde_json::Value::as_str)?
        .trim();
    let (spec_version, schema_version, schema_hash) = satellite_tool_contract(tool_id)?;
    let expected_offer_id = offer_ids.get(tool_id)?;
    if value
        .get("protocol_version")
        .and_then(serde_json::Value::as_u64)
        != Some(SATELLITE_PROTOCOL_VERSION)
        || value.get("instance_id").and_then(serde_json::Value::as_str) != Some(instance_id)
        || value.get("lease_epoch").and_then(serde_json::Value::as_str) != Some(lease_epoch)
        || value.get("offer_id").and_then(serde_json::Value::as_str)
            != Some(expected_offer_id.as_str())
        || value
            .get("spec_version")
            .and_then(serde_json::Value::as_str)
            != Some(spec_version)
        || value
            .get("schema_version")
            .and_then(serde_json::Value::as_u64)
            != Some(schema_version)
        || value.get("schema_hash").and_then(serde_json::Value::as_str) != Some(schema_hash)
    {
        return None;
    }
    let invocation_id = value
        .get("invocation_id")
        .and_then(serde_json::Value::as_str)?
        .trim();
    if invocation_id.is_empty()
        || invocation_id.len() > 128
        || !invocation_id
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | '-'))
    {
        return None;
    }
    let arguments = value.get("arguments")?.as_object()?;
    let valid_arguments = match tool_id {
        computer_use::protocol::TOOL_ID => computer_use::protocol::valid(arguments),
        personal_browser::TOOL_ID => personal_browser::valid(arguments),
        OPEN_BROWSER_TOOL_ID => {
            arguments.len() == 1
                && arguments
                    .get("url")
                    .and_then(serde_json::Value::as_str)
                    .is_some()
        }
        DESKTOP_CONTEXT_SNAPSHOT_TOOL_ID
        | SYSTEM_MEDIA_SNAPSHOT_TOOL_ID
        | DESKTOP_SCREENSHOT_TOOL_ID => arguments.is_empty(),
        SYSTEM_PROCESS_SNAPSHOT_TOOL_ID => {
            arguments.iter().all(|(key, value)| match key.as_str() {
                "query" => value
                    .as_str()
                    .map(|v| v.chars().count() <= 100)
                    .unwrap_or(false),
                "offset" => value.as_u64().map(|v| v <= 1_000_000).unwrap_or(false),
                "limit" => value
                    .as_u64()
                    .map(|v| (1..=128).contains(&v))
                    .unwrap_or(false),
                _ => false,
            })
        }
        SYSTEM_PROCESS_TERMINATE_TOOL_ID => {
            arguments.len() == 1
                && arguments
                    .get("pid")
                    .and_then(serde_json::Value::as_u64)
                    .map(|pid| pid > 0 && pid <= u32::MAX as u64)
                    .unwrap_or(false)
        }
        SYSTEM_VOLUME_TOOL_ID => {
            let action = arguments
                .get("action")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("");
            if action == "get" {
                arguments.len() == 1
            } else if action == "set" {
                arguments.len() == 2
                    && arguments
                        .get("value")
                        .and_then(serde_json::Value::as_u64)
                        .map(|value| value <= 100)
                        .unwrap_or(false)
            } else {
                false
            }
        }
        SYSTEM_MEDIA_CONTROL_TOOL_ID => {
            arguments.len() == 1
                && arguments
                    .get("action")
                    .and_then(serde_json::Value::as_str)
                    .map(|action| matches!(action, "play" | "pause" | "stop" | "previous" | "next"))
                    .unwrap_or(false)
        }
        _ => false,
    };
    if !valid_arguments {
        return None;
    }
    let mut arguments = arguments.clone();
    if matches!(
        tool_id.as_ref(),
        computer_use::protocol::TOOL_ID | personal_browser::TOOL_ID
    ) {
        let scope = value
            .get("control_scope")
            .and_then(serde_json::Value::as_str)?;
        if scope.len() != 64 || !scope.chars().all(|ch| ch.is_ascii_hexdigit()) {
            return None;
        }
        arguments.insert("_control_scope".into(), serde_json::json!(scope));
        arguments.insert("_device_epoch".into(), serde_json::json!(lease_epoch));
        if let Some(control) = value
            .get("control_dispatch")
            .and_then(serde_json::Value::as_object)
        {
            if let Some(mode) = control.get("permission_mode") {
                let mode = mode.as_str()?;
                if !matches!(mode, "trusted_auto_allow" | "ask_each_time") {
                    return None;
                }
                arguments.insert("_permission_mode".into(), serde_json::json!(mode));
            }
            if let Some(phase) = control.get("phase").and_then(serde_json::Value::as_str) {
                if !matches!(phase, "prepare" | "execute") {
                    return None;
                }
                arguments.insert("_phase".into(), serde_json::json!(phase));
            }
            if let Some(binding) = control
                .get("approval_binding")
                .and_then(serde_json::Value::as_str)
            {
                if binding.len() != 64 || !binding.chars().all(|ch| ch.is_ascii_hexdigit()) {
                    return None;
                }
                arguments.insert("_approval_binding".into(), serde_json::json!(binding));
            }
        }
    }
    Some((
        invocation_id.to_string(),
        tool_id.to_string(),
        serde_json::Value::Object(arguments),
    ))
}
