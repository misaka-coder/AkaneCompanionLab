use crate::pet_state::models::{CharacterRuntimeState, PetState};
use crate::runtime::config::{
    is_safe_instance_id, normalize_backend_url, runtime_backend_url, runtime_bound_bot_id,
    runtime_instance_id, BOUND_BOT_ID_ENV, DEFAULT_CHARACTER_PACK_ID, DEFAULT_EMOTION,
    DEFAULT_OUTFIT, DEFAULT_PROFILE_USER_ID, LOCAL_DEFAULT_INSTANCE_ID,
};

pub(crate) fn default_pet_state() -> Result<PetState, String> {
    let mut state = PetState::default();
    state.instance_id = runtime_instance_id()?;
    state.host_id = state.instance_id.clone();
    state.bound_bot_id = runtime_bound_bot_id()?;
    state.backend_url = runtime_backend_url();
    normalize_pet_state(&mut state)?;
    Ok(state)
}

pub(crate) fn normalize_pet_state(state: &mut PetState) -> Result<(), String> {
    let runtime_instance_id = runtime_instance_id()?;
    let runtime_bound_bot_id = runtime_bound_bot_id()?;
    normalize_pet_binding_ids(
        state,
        &runtime_instance_id,
        &runtime_bound_bot_id,
        std::env::var_os(BOUND_BOT_ID_ENV).is_some(),
    )?;
    state.width = None;
    state.height = None;
    state.scale = clamp(state.scale, 0.75, 1.45);
    state.opacity = clamp(state.opacity, 0.55, 1.0);
    state.click_through = false;
    state.backend_url = normalize_backend_url(&state.backend_url);
    state.profile_user_id = DEFAULT_PROFILE_USER_ID.to_string();
    if state.character_pack_id.trim().is_empty() {
        state.character_pack_id = DEFAULT_CHARACTER_PACK_ID.to_string();
    }
    if state.outfit.trim().is_empty() {
        state.outfit = DEFAULT_OUTFIT.to_string();
    }
    if state.current_emotion.trim().is_empty() {
        state.current_emotion = DEFAULT_EMOTION.to_string();
    }
    state.voice_volume = clamp(state.voice_volume, 0.0, 1.0);
    state.proactive_wake_interval_sec = state.proactive_wake_interval_sec.clamp(15, 600);
    state.screen_vision_frame_count = state.screen_vision_frame_count.clamp(1, 5);
    for runtime in state.characters.values_mut() {
        normalize_character_runtime_state(runtime);
    }
    Ok(())
}

pub(crate) fn normalize_pet_binding_ids(
    state: &mut PetState,
    runtime_host_id: &str,
    default_bound_bot_id: &str,
    enforce_bound_bot_id: bool,
) -> Result<(), String> {
    if state.instance_id.trim().is_empty() && runtime_host_id == LOCAL_DEFAULT_INSTANCE_ID {
        state.instance_id = runtime_host_id.to_string();
    }
    if state.instance_id != runtime_host_id {
        return Err("client_state_instance_mismatch".to_string());
    }
    if state.host_id.trim().is_empty() {
        state.host_id = state.instance_id.clone();
    }
    if state.host_id != runtime_host_id {
        return Err("client_state_host_mismatch".to_string());
    }
    if state.bound_bot_id.trim().is_empty() {
        state.bound_bot_id = default_bound_bot_id.to_string();
    }
    if !is_safe_instance_id(&state.bound_bot_id) {
        return Err("invalid_bound_bot_id".to_string());
    }
    if enforce_bound_bot_id && state.bound_bot_id != default_bound_bot_id {
        return Err("client_state_bound_bot_mismatch".to_string());
    }
    Ok(())
}

pub(crate) fn normalize_character_runtime_state(runtime: &mut CharacterRuntimeState) {
    if runtime.version == 0 {
        runtime.version = 1;
    }
    runtime.width = None;
    runtime.height = None;
    runtime.character_pack_id = runtime.character_pack_id.trim().to_string();
    runtime.session_id = runtime.session_id.trim().to_string();
    runtime.outfit = runtime.outfit.trim().to_string();
    runtime.current_emotion = runtime.current_emotion.trim().to_string();
    runtime.scale = clamp(runtime.scale, 0.75, 1.45);
    runtime.opacity = clamp(runtime.opacity, 0.55, 1.0);
}

pub(crate) fn clamp(value: f64, min: f64, max: f64) -> f64 {
    value.max(min).min(max)
}
