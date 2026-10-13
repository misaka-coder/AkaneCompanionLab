use crate::desktop_connection::contracts::{
    DESKTOP_CONTEXT_SNAPSHOT_SCHEMA_HASH, DESKTOP_CONTEXT_SNAPSHOT_SCHEMA_VERSION,
    DESKTOP_CONTEXT_SNAPSHOT_SPEC_VERSION, DESKTOP_CONTEXT_SNAPSHOT_TOOL_ID,
    OPEN_BROWSER_SCHEMA_HASH, OPEN_BROWSER_SCHEMA_VERSION, OPEN_BROWSER_SPEC_VERSION,
    OPEN_BROWSER_TOOL_ID, SATELLITE_PROTOCOL_VERSION, SYSTEM_MEDIA_CONTROL_SCHEMA_HASH,
    SYSTEM_MEDIA_CONTROL_SCHEMA_VERSION, SYSTEM_MEDIA_CONTROL_SPEC_VERSION,
    SYSTEM_MEDIA_CONTROL_TOOL_ID, SYSTEM_MEDIA_SNAPSHOT_SCHEMA_HASH,
    SYSTEM_MEDIA_SNAPSHOT_SCHEMA_VERSION, SYSTEM_MEDIA_SNAPSHOT_SPEC_VERSION,
    SYSTEM_MEDIA_SNAPSHOT_TOOL_ID, SYSTEM_PROCESS_SNAPSHOT_SCHEMA_HASH,
    SYSTEM_PROCESS_SNAPSHOT_SCHEMA_VERSION, SYSTEM_PROCESS_SNAPSHOT_SPEC_VERSION,
    SYSTEM_PROCESS_SNAPSHOT_TOOL_ID, SYSTEM_PROCESS_TERMINATE_SCHEMA_HASH,
    SYSTEM_PROCESS_TERMINATE_SCHEMA_VERSION, SYSTEM_PROCESS_TERMINATE_SPEC_VERSION,
    SYSTEM_PROCESS_TERMINATE_TOOL_ID, SYSTEM_VOLUME_SCHEMA_HASH, SYSTEM_VOLUME_SCHEMA_VERSION,
    SYSTEM_VOLUME_SPEC_VERSION, SYSTEM_VOLUME_TOOL_ID,
};
use crate::desktop_connection::validation::validate_satellite_invocation;
use std::collections::HashMap;

#[test]
fn satellite_invocation_rejects_instance_or_schema_mismatch() {
    let offer_ids = HashMap::from([(OPEN_BROWSER_TOOL_ID.to_string(), "offer-a".to_string())]);
    let valid = serde_json::json!({
        "type": "invoke",
        "protocol_version": SATELLITE_PROTOCOL_VERSION,
        "instance_id": "instance-a",
        "lease_epoch": "lease-a",
        "offer_id": "offer-a",
        "invocation_id": "call_valid",
        "tool_id": OPEN_BROWSER_TOOL_ID,
        "spec_version": OPEN_BROWSER_SPEC_VERSION,
        "schema_version": OPEN_BROWSER_SCHEMA_VERSION,
        "schema_hash": OPEN_BROWSER_SCHEMA_HASH,
        "arguments": {"url": "https://example.com"},
    });
    assert!(validate_satellite_invocation(&valid, "instance-a", "lease-a", &offer_ids).is_some());
    assert!(validate_satellite_invocation(&valid, "instance-b", "lease-a", &offer_ids).is_none());
    let mut wrong_schema = valid;
    wrong_schema["schema_hash"] = serde_json::Value::String("sha256:wrong".to_string());
    assert!(
        validate_satellite_invocation(&wrong_schema, "instance-a", "lease-a", &offer_ids).is_none()
    );
}

#[test]
fn satellite_invocation_validates_all_local_tool_contracts() {
    let cases = [
        (
            DESKTOP_CONTEXT_SNAPSHOT_TOOL_ID,
            DESKTOP_CONTEXT_SNAPSHOT_SPEC_VERSION,
            DESKTOP_CONTEXT_SNAPSHOT_SCHEMA_VERSION,
            DESKTOP_CONTEXT_SNAPSHOT_SCHEMA_HASH,
            serde_json::json!({}),
        ),
        (
            SYSTEM_MEDIA_SNAPSHOT_TOOL_ID,
            SYSTEM_MEDIA_SNAPSHOT_SPEC_VERSION,
            SYSTEM_MEDIA_SNAPSHOT_SCHEMA_VERSION,
            SYSTEM_MEDIA_SNAPSHOT_SCHEMA_HASH,
            serde_json::json!({}),
        ),
        (
            SYSTEM_MEDIA_CONTROL_TOOL_ID,
            SYSTEM_MEDIA_CONTROL_SPEC_VERSION,
            SYSTEM_MEDIA_CONTROL_SCHEMA_VERSION,
            SYSTEM_MEDIA_CONTROL_SCHEMA_HASH,
            serde_json::json!({"action": "pause"}),
        ),
        (
            SYSTEM_PROCESS_SNAPSHOT_TOOL_ID,
            SYSTEM_PROCESS_SNAPSHOT_SPEC_VERSION,
            SYSTEM_PROCESS_SNAPSHOT_SCHEMA_VERSION,
            SYSTEM_PROCESS_SNAPSHOT_SCHEMA_HASH,
            serde_json::json!({}),
        ),
        (
            SYSTEM_PROCESS_TERMINATE_TOOL_ID,
            SYSTEM_PROCESS_TERMINATE_SPEC_VERSION,
            SYSTEM_PROCESS_TERMINATE_SCHEMA_VERSION,
            SYSTEM_PROCESS_TERMINATE_SCHEMA_HASH,
            serde_json::json!({"pid": 1234}),
        ),
        (
            SYSTEM_VOLUME_TOOL_ID,
            SYSTEM_VOLUME_SPEC_VERSION,
            SYSTEM_VOLUME_SCHEMA_VERSION,
            SYSTEM_VOLUME_SCHEMA_HASH,
            serde_json::json!({"action": "set", "value": 30}),
        ),
    ];
    for (tool_id, spec_version, schema_version, schema_hash, arguments) in cases {
        let offer_id = format!("offer-{tool_id}");
        let offer_ids = HashMap::from([(tool_id.to_string(), offer_id.clone())]);
        let value = serde_json::json!({
            "type": "invoke",
            "protocol_version": SATELLITE_PROTOCOL_VERSION,
            "instance_id": "instance-a",
            "lease_epoch": "lease-a",
            "offer_id": offer_id,
            "invocation_id": format!("call_{tool_id}"),
            "tool_id": tool_id,
            "spec_version": spec_version,
            "schema_version": schema_version,
            "schema_hash": schema_hash,
            "arguments": arguments,
        });
        let validated = validate_satellite_invocation(&value, "instance-a", "lease-a", &offer_ids)
            .expect("canonical local invocation should validate");
        assert_eq!(validated.1, tool_id);
    }

    let offer_ids = HashMap::from([(
        SYSTEM_MEDIA_CONTROL_TOOL_ID.to_string(),
        "offer-media".to_string(),
    )]);
    let invalid_action = serde_json::json!({
        "type": "invoke",
        "protocol_version": SATELLITE_PROTOCOL_VERSION,
        "instance_id": "instance-a",
        "lease_epoch": "lease-a",
        "offer_id": "offer-media",
        "invocation_id": "call_invalid_action",
        "tool_id": SYSTEM_MEDIA_CONTROL_TOOL_ID,
        "spec_version": SYSTEM_MEDIA_CONTROL_SPEC_VERSION,
        "schema_version": SYSTEM_MEDIA_CONTROL_SCHEMA_VERSION,
        "schema_hash": SYSTEM_MEDIA_CONTROL_SCHEMA_HASH,
        "arguments": {"action": "volume_up"},
    });
    assert!(
        validate_satellite_invocation(&invalid_action, "instance-a", "lease-a", &offer_ids,)
            .is_none()
    );

    let unknown = serde_json::json!({
        "type": "invoke",
        "protocol_version": SATELLITE_PROTOCOL_VERSION,
        "instance_id": "instance-a",
        "lease_epoch": "lease-a",
        "offer_id": "offer-unknown",
        "invocation_id": "call_unknown",
        "tool_id": "run_shell",
        "spec_version": "1.0.0",
        "schema_version": 1,
        "schema_hash": "sha256:unknown",
        "arguments": {},
    });
    assert!(validate_satellite_invocation(
        &unknown,
        "instance-a",
        "lease-a",
        &HashMap::from([("run_shell".to_string(), "offer-unknown".to_string())]),
    )
    .is_none());
}

#[test]
fn computer_use_permission_mode_is_trusted_transport_metadata_only() {
    let offers = HashMap::from([("computer_use".to_string(), "offer-pc".to_string())]);
    let mut frame = serde_json::json!({"type":"invoke","protocol_version":SATELLITE_PROTOCOL_VERSION,
        "instance_id":"instance-a","lease_epoch":"lease-a","offer_id":"offer-pc","invocation_id":"policy-call",
        "tool_id":"computer_use","spec_version":computer_use::protocol::SPEC_VERSION,
        "schema_version":computer_use::protocol::SCHEMA_VERSION,"schema_hash":computer_use::protocol::SCHEMA_HASH,
        "control_scope":"a".repeat(64),"arguments":{"action":"press_key","key":"Enter",
            "control_session_id":"session","device_epoch":"lease-a","observation_id":"observation"},
        "control_dispatch":{"phase":"execute","permission_mode":"trusted_auto_allow","approval_binding":"b".repeat(64)}});
    let decoded = validate_satellite_invocation(&frame, "instance-a", "lease-a", &offers).unwrap();
    assert_eq!(decoded.2["_permission_mode"], "trusted_auto_allow");
    for mode in [
        serde_json::json!(true),
        serde_json::json!("disabled"),
        serde_json::json!("allow-everything"),
    ] {
        let mut invalid = frame.clone();
        invalid["control_dispatch"]["permission_mode"] = mode;
        assert!(
            validate_satellite_invocation(&invalid, "instance-a", "lease-a", &offers).is_none()
        );
    }
    for field in ["permission_mode", "_permission_mode"] {
        let mut forged = frame.clone();
        forged["arguments"][field] = serde_json::json!("trusted_auto_allow");
        assert!(validate_satellite_invocation(&forged, "instance-a", "lease-a", &offers).is_none());
    }
    frame["control_dispatch"]
        .as_object_mut()
        .unwrap()
        .remove("permission_mode");
    assert!(
        validate_satellite_invocation(&frame, "instance-a", "lease-a", &offers)
            .unwrap()
            .2
            .get("_permission_mode")
            .is_none()
    );
}
use crate::computer_use;
