use tauri::AppHandle;

use crate::backend::binding::{current_bound_backend_url, verify_backend_instance_url};
use crate::pet_state::models::PetState;
use crate::pet_state::persistence::load_pet_state;
use crate::runtime::config::{is_safe_instance_id, normalize_backend_url};

pub(crate) fn is_safe_artifact_handle(value: &str) -> bool {
    let value = value.trim();
    !value.is_empty()
        && value.len() <= 128
        && value != "."
        && value != ".."
        && value
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | '-' | '.'))
}

pub(crate) fn workspace_base_url(
    state: &PetState,
    bound_backend: &str,
    requested_backend: &str,
    requested_bot: &str,
) -> Result<reqwest::Url, String> {
    if normalize_backend_url(requested_backend) != normalize_backend_url(bound_backend)
        || requested_bot != state.bound_bot_id
        || !is_safe_instance_id(requested_bot)
    {
        return Err("workspace_binding_changed".to_string());
    }
    let mut url =
        reqwest::Url::parse(bound_backend).map_err(|_| "backend_url_invalid".to_string())?;
    let mut segments = url
        .path_segments_mut()
        .map_err(|_| "backend_url_invalid".to_string())?;
    segments.pop_if_empty();
    segments.extend(["api", "bots", requested_bot]);
    drop(segments);
    url.set_query(None);
    url.set_fragment(None);
    Ok(url)
}

pub(crate) async fn verified_workspace_base_url(
    app: &AppHandle,
    requested_backend: &str,
    requested_bot: &str,
) -> Result<reqwest::Url, String> {
    let state = load_pet_state(app.clone())?;
    let backend = current_bound_backend_url(&state);
    let url = workspace_base_url(&state, &backend, requested_backend, requested_bot)?;
    let verification = verify_backend_instance_url(&backend).await;
    if !verification.ok {
        return Err("backend_instance_verification_failed".to_string());
    }
    Ok(url)
}
