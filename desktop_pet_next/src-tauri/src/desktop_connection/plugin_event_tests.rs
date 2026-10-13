use crate::desktop_connection::contracts::SATELLITE_PROTOCOL_VERSION;
use crate::desktop_connection::plugin_events::{
    acknowledge_plugin_agent_event_frame, list_pending_plugin_agent_event_frames,
    queue_plugin_agent_event_frame, validate_satellite_agent_event_frame,
};

#[test]
fn satellite_agent_event_frame_is_bound_to_instance_and_lease() {
    let value = serde_json::json!({
        "type": "agent_event_frame",
        "protocol_version": SATELLITE_PROTOCOL_VERSION,
        "instance_id": "instance-a",
        "lease_epoch": "lease-a",
        "delivery_id": "delivery-a",
        "payload": {"speech": "到点了", "emotion": "happy"},
    });
    let (delivery_id, frame) =
        validate_satellite_agent_event_frame(&value, "instance-a", "lease-a").expect("valid frame");
    assert_eq!(delivery_id, "delivery-a");
    assert_eq!(
        frame.get("speech").and_then(serde_json::Value::as_str),
        Some("到点了")
    );
    assert!(validate_satellite_agent_event_frame(&value, "instance-b", "lease-a").is_none());
    assert!(validate_satellite_agent_event_frame(&value, "instance-a", "lease-b").is_none());
    assert!(queue_plugin_agent_event_frame("delivery-test", frame));
    assert!(list_pending_plugin_agent_event_frames().iter().any(|item| {
        item.get("deliveryId").and_then(serde_json::Value::as_str) == Some("delivery-test")
    }));
    assert!(acknowledge_plugin_agent_event_frame(
        "delivery-test".to_string()
    ));
}
