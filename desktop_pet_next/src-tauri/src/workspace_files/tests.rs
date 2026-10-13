use crate::pet_state::models::PetState;
use crate::workspace_files::routes::{is_safe_artifact_handle, workspace_base_url};

#[test]
fn workspace_urls_follow_verified_backend_and_selected_bot() {
    let mut state = PetState::default();
    state.bound_bot_id = "bot-b".to_string();
    let base = "http://127.0.0.1:12001";
    let url = workspace_base_url(&state, base, base, "bot-b").unwrap();
    assert_eq!(url.as_str(), "http://127.0.0.1:12001/api/bots/bot-b");
    assert!(workspace_base_url(&state, base, base, "bot-a").is_err());
    assert!(workspace_base_url(&state, base, "http://127.0.0.1:9999", "bot-b").is_err());
    state.bound_bot_id = "../other".to_string();
    assert!(workspace_base_url(&state, base, base, "../other").is_err());
}

#[test]
fn workspace_artifact_handles_reject_path_or_query_injection() {
    assert!(is_safe_artifact_handle("file_001"));
    assert!(is_safe_artifact_handle("gen-001.preview"));
    assert!(!is_safe_artifact_handle("../../secret"));
    assert!(!is_safe_artifact_handle("file_001?token=secret"));
    assert!(!is_safe_artifact_handle(""));
}
