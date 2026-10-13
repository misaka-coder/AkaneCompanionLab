use tauri::AppHandle;
use tauri_plugin_dialog::DialogExt;

use crate::backend::binding::current_bound_backend_url;
use crate::pet_state::persistence::load_pet_state;
use crate::runtime::config::is_loopback_backend_host;

#[tauri::command]
pub(crate) fn pick_local_plugin_path(app: AppHandle, kind: String) -> serde_json::Value {
    let Some(kind) = normalize_plugin_picker_kind(&kind) else {
        return serde_json::json!({"ok": false, "status": "rejected", "reason": "plugin_picker_kind_invalid"});
    };
    let state = match load_pet_state(app.clone()) {
        Ok(value) => value,
        Err(_) => {
            return serde_json::json!({"ok": false, "status": "unavailable", "reason": "client_state_unavailable"})
        }
    };
    let backend = match reqwest::Url::parse(&current_bound_backend_url(&state)) {
        Ok(value) => value,
        Err(_) => {
            return serde_json::json!({"ok": false, "status": "rejected", "reason": "invalid_backend_url"})
        }
    };
    if !is_loopback_backend_host(backend.host_str().unwrap_or("")) {
        return serde_json::json!({"ok": false, "status": "remote", "reason": "plugin_picker_requires_local_backend"});
    }
    let selected = if kind == "source" {
        app.dialog().file().blocking_pick_folder()
    } else {
        app.dialog()
            .file()
            .add_filter("Python wheel", &["whl"])
            .blocking_pick_file()
    };
    let Some(selected) = selected else {
        return serde_json::json!({"ok": false, "status": "cancelled", "reason": "picker_cancelled"});
    };
    let path = match selected.into_path() {
        Ok(value) => value,
        Err(_) => {
            return serde_json::json!({"ok": false, "status": "rejected", "reason": "selected_plugin_path_unavailable"});
        }
    };
    let valid = if kind == "source" {
        path.is_dir()
    } else {
        path.is_file()
            && path
                .extension()
                .and_then(|value| value.to_str())
                .map(|value| value.eq_ignore_ascii_case("whl"))
                .unwrap_or(false)
    };
    if !valid {
        return serde_json::json!({"ok": false, "status": "rejected", "reason": "selected_plugin_path_invalid"});
    }
    serde_json::json!({
        "ok": true,
        "status": "selected",
        "kind": kind,
        "path": path.to_string_lossy(),
    })
}

pub(crate) fn normalize_plugin_picker_kind(raw: &str) -> Option<&'static str> {
    match raw.trim().to_ascii_lowercase().as_str() {
        "source" => Some("source"),
        "wheel" => Some("wheel"),
        _ => None,
    }
}
