use crate::desktop_connection::contracts::{
    DESKTOP_CONTEXT_SNAPSHOT_TOOL_ID, SYSTEM_MEDIA_SNAPSHOT_TOOL_ID,
};
use crate::desktop_connection::execution::execute_satellite_invocation_once;
use crate::native_system::media::outcomes::system_media_unavailable;

#[tokio::test]
async fn satellite_context_result_is_serialized_and_idempotent() {
    let first = execute_satellite_invocation_once(
        "call_context_snapshot_rust",
        DESKTOP_CONTEXT_SNAPSHOT_TOOL_ID,
        &serde_json::json!({}),
    )
    .await;
    let second = execute_satellite_invocation_once(
        "call_context_snapshot_rust",
        DESKTOP_CONTEXT_SNAPSHOT_TOOL_ID,
        &serde_json::json!({}),
    )
    .await;
    assert_eq!(
        first.status,
        if first.data["ok"] == true {
            "succeeded"
        } else {
            "failed"
        }
    );
    assert_eq!(first.reason.is_empty(), first.data["ok"] == true);
    assert!(first.data.get("foreground").is_some());
    assert_eq!(first.status, second.status);
    assert_eq!(first.reason, second.reason);
    assert_eq!(first.data, second.data);
}

#[tokio::test]
async fn satellite_system_media_snapshot_calls_the_real_local_reader() {
    let result = execute_satellite_invocation_once(
        "call_system_media_snapshot_rust",
        SYSTEM_MEDIA_SNAPSHOT_TOOL_ID,
        &serde_json::json!({}),
    )
    .await;
    assert_eq!(result.status, "succeeded");
    assert!(result.data.get("status").is_some());
    assert!(result.data.get("platform").is_some());
}

#[test]
fn satellite_media_results_serialize_without_local_paths() {
    let media = system_media_unavailable("no_active_session", "private detail");
    let data = serde_json::to_value(media).expect("media result should serialize");
    assert_eq!(
        data.get("reason").and_then(serde_json::Value::as_str),
        Some("no_active_session")
    );
    let serialized = data.to_string().to_ascii_lowercase();
    assert!(!serialized.contains("private detail"));
    assert!(!serialized.contains("absolute_path"));
}
