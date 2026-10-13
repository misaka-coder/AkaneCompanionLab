use crate::desktop_connection::execution::desktop_context_terminal_result;
use crate::native_system::context::{
    desktop_context_snapshot_from_foreground, empty_foreground_window,
};
use crate::native_system::context_models::ForegroundWindowInfo;

#[test]
fn perception_context_never_reports_missing_observations_as_success() {
    for (source, expected_reason) in [
        ("self", "foreground_is_desktop_pet"),
        ("none", "foreground_unavailable"),
        ("foreground", "foreground_unavailable"),
        ("unsupported_platform", "unsupported_platform"),
    ] {
        let snapshot =
            desktop_context_snapshot_from_foreground(empty_foreground_window(source), 123);
        assert!(!snapshot.ok);
        let result = desktop_context_terminal_result(snapshot);
        assert_eq!(result.status, "failed");
        assert_eq!(result.reason, expected_reason);
        assert_eq!(result.data["capturedAt"], 123);
        assert_eq!(result.data["foreground"]["source"], source);
    }
    let snapshot = desktop_context_snapshot_from_foreground(
        ForegroundWindowInfo {
            title: "Synthetic current window".to_string(),
            process_name: "editor.exe".to_string(),
            pid: Some(123),
            source: "foreground".to_string(),
        },
        456,
    );
    let result = desktop_context_terminal_result(snapshot);
    assert_eq!(result.status, "succeeded");
    assert!(result.reason.is_empty());
    assert_eq!(result.data["capturedAt"], 456);
    assert_eq!(
        result.data["foreground"]["title"],
        "Synthetic current window"
    );
}
