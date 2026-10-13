use crate::character_packs::{
    models::{CharacterPackActivationResult, CharacterPackJson},
    paths::{creator_kit_characters_dir, safe_child_path, sanitize_pack_id},
    storage::write_text_atomic,
};
use std::{fs, path::PathBuf};
use tauri::{AppHandle, Manager};

use crate::backend::binding::{set_verified_backend_url, verify_backend_instance_url};
use crate::pet_state::models::{ClientLaunchBinding, PetState};
use crate::pet_state::normalize::{default_pet_state, normalize_pet_state};
use crate::runtime::config::{
    normalize_backend_url, runtime_backend_url, runtime_bound_bot_id, runtime_instance_id,
    BACKEND_URL_ENV, LOCAL_DEFAULT_INSTANCE_ID,
};
use crate::runtime::paths::akane_data_root;

pub(crate) const STATE_FILE: &str = "pet_state.json";

#[tauri::command]
pub(crate) fn load_pet_state(app: AppHandle) -> Result<PetState, String> {
    let path = state_path(&app)?;
    if !path.exists() {
        return default_pet_state();
    }

    let raw = fs::read_to_string(&path).map_err(|error| error.to_string())?;
    let mut state: PetState = serde_json::from_str(&raw).map_err(|error| error.to_string())?;
    normalize_pet_state(&mut state)?;
    Ok(state)
}

#[tauri::command]
pub(crate) fn get_client_launch_binding() -> Result<ClientLaunchBinding, String> {
    let host_id = runtime_instance_id()?;
    Ok(ClientLaunchBinding {
        instance_id: host_id.clone(),
        host_id,
        bound_bot_id: runtime_bound_bot_id()?,
        backend_url: runtime_backend_url(),
        has_backend_override: std::env::var_os(BACKEND_URL_ENV).is_some(),
    })
}

#[tauri::command]
pub(crate) async fn save_pet_state(app: AppHandle, state: PetState) -> Result<(), String> {
    let path = state_path(&app)?;

    let mut normalized = state;
    normalize_pet_state(&mut normalized)?;

    if path.is_file() {
        let previous_backend_url = fs::read_to_string(&path)
            .ok()
            .and_then(|raw| serde_json::from_str::<PetState>(&raw).ok())
            .map(|state| normalize_backend_url(&state.backend_url))
            .unwrap_or_default();
        if !previous_backend_url.is_empty() && previous_backend_url != normalized.backend_url {
            let binding = verify_backend_instance_url(&normalized.backend_url).await;
            if !binding.ok {
                return Err("backend_instance_verification_failed".to_string());
            }
            set_verified_backend_url(&normalized.backend_url);
        }
    }

    let raw = serde_json::to_string_pretty(&normalized).map_err(|error| error.to_string())?;
    write_text_atomic(&path, &raw)
}

pub(crate) fn state_path(app: &AppHandle) -> Result<PathBuf, String> {
    let path = akane_data_root()?.join("state").join(STATE_FILE);
    if !path.exists() && runtime_instance_id()? == LOCAL_DEFAULT_INSTANCE_ID {
        let legacy_path = app
            .path()
            .app_config_dir()
            .map(|dir| dir.join(STATE_FILE))
            .map_err(|error| error.to_string())?;
        if legacy_path.is_file() {
            let parent = path
                .parent()
                .ok_or_else(|| "无法定位桌宠状态目录。".to_string())?;
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
            fs::copy(legacy_path, &path).map_err(|error| error.to_string())?;
        }
    }
    Ok(path)
}

#[tauri::command]
pub(crate) fn activate_character_pack(
    app: AppHandle,
    pack_id: String,
) -> Result<CharacterPackActivationResult, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }

    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    let character_path = pack_dir.join("character.json");
    if !character_path.is_file() {
        return Err(format!("角色包 {pack_id} 不存在或缺少 character.json。"));
    }
    let raw_profile = fs::read_to_string(&character_path).map_err(|error| error.to_string())?;
    let character = serde_json::from_str::<CharacterPackJson>(&raw_profile)
        .map_err(|error| format!("角色包 {pack_id} 的 character.json 无效：{error}"))?;
    app.get_webview_window("main")
        .ok_or_else(|| "桌宠主窗口不存在，无法切换角色。".to_string())?;

    let path = state_path(&app)?;
    let original = fs::read_to_string(&path).ok();
    let mut state = match original.as_deref() {
        Some(raw) => serde_json::from_str::<PetState>(raw).map_err(|error| error.to_string())?,
        None => default_pet_state()?,
    };
    normalize_pet_state(&mut state)?;
    state.character_pack_id = pack_id.clone();
    let raw_state = serde_json::to_string_pretty(&state).map_err(|error| error.to_string())?;
    write_text_atomic(&path, &raw_state)?;

    Ok(CharacterPackActivationResult {
        pack_id,
        character_name: character.identity.name,
    })
}
