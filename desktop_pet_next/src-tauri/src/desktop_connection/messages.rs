use crate::desktop_connection::contracts::SATELLITE_PROTOCOL_VERSION;

pub(crate) fn satellite_message_json(
    message: tokio_tungstenite::tungstenite::Message,
) -> Option<serde_json::Value> {
    match message {
        tokio_tungstenite::tungstenite::Message::Text(text) if text.len() <= 16 * 1024 => {
            serde_json::from_str(text.as_ref()).ok()
        }
        _ => None,
    }
}

pub(crate) fn satellite_execution_message(
    message_type: &str,
    instance_id: &str,
    lease_epoch: &str,
    offer_id: &str,
    invocation_id: &str,
    tool_id: &str,
    status: &str,
    reason: &str,
    data: Option<&serde_json::Value>,
) -> serde_json::Value {
    let mut value = serde_json::json!({
        "type": message_type,
        "protocol_version": SATELLITE_PROTOCOL_VERSION,
        "instance_id": instance_id,
        "lease_epoch": lease_epoch,
        "offer_id": offer_id,
        "invocation_id": invocation_id,
        "tool_id": tool_id,
    });
    if !status.is_empty() {
        value["status"] = serde_json::Value::String(status.to_string());
    }
    if !reason.is_empty() {
        value["reason"] = serde_json::Value::String(reason.to_string());
    }
    if let Some(data) = data {
        if data.is_object() {
            value["data"] = data.clone();
        }
    }
    value
}
