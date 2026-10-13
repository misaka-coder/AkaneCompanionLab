use crate::pet_state::models::PetState;
use crate::pet_state::normalize::normalize_pet_binding_ids;

#[test]
fn perception_state_drops_retired_preferences_and_preserves_direct_settings() {
    let mut wire = serde_json::to_value(PetState::default()).unwrap();
    for (key, value) in [
        ("desktopContextEnabled", serde_json::json!(true)),
        ("clipboardContextEnabled", serde_json::json!(true)),
        ("screenVisionMode", serde_json::json!("summary")),
        ("screenVisionIntervalSec", serde_json::json!(25)),
        ("screenVisionSampleIntervalSec", serde_json::json!(0.5)),
        ("screenVisionWindowSec", serde_json::json!(120)),
        ("screenVisionMaxEdge", serde_json::json!(1920)),
        ("screenVisionPacking", serde_json::json!("frames")),
        ("screenVisionSheetCount", serde_json::json!(3)),
    ] {
        wire[key] = value;
    }
    let state: PetState = serde_json::from_value(wire).unwrap();
    let saved = serde_json::to_value(state).unwrap();
    for key in [
        "desktopContextEnabled",
        "clipboardContextEnabled",
        "screenVisionMode",
        "screenVisionIntervalSec",
    ] {
        assert!(saved.get(key).is_none(), "retired field survived: {key}");
    }
    assert_eq!(saved["screenVisionSampleIntervalSec"], 0.5);
    assert_eq!(saved["screenVisionWindowSec"], 120.0);
    assert_eq!(saved["screenVisionMaxEdge"], 1920);
    assert_eq!(saved["screenVisionPacking"], "frames");
    assert_eq!(saved["screenVisionSheetCount"], 3);
}

#[test]
fn legacy_pet_state_migrates_to_host_and_bound_bot_ids() {
    let mut state = PetState::default();
    state.instance_id = "personal".to_string();

    normalize_pet_binding_ids(&mut state, "personal", "personal", false).unwrap();

    assert_eq!(state.instance_id, "personal");
    assert_eq!(state.host_id, "personal");
    assert_eq!(state.bound_bot_id, "personal");
}

#[test]
fn persisted_bound_bot_is_safe_and_environment_override_is_fail_closed() {
    let mut state = PetState::default();
    state.instance_id = "personal".to_string();
    state.host_id = "personal".to_string();
    state.bound_bot_id = "finance".to_string();

    normalize_pet_binding_ids(&mut state, "personal", "personal", false).unwrap();
    assert_eq!(state.bound_bot_id, "finance");
    assert_eq!(
        normalize_pet_binding_ids(&mut state, "personal", "personal", true),
        Err("client_state_bound_bot_mismatch".to_string())
    );

    state.bound_bot_id = "../finance".to_string();
    assert_eq!(
        normalize_pet_binding_ids(&mut state, "personal", "personal", false),
        Err("invalid_bound_bot_id".to_string())
    );
}
