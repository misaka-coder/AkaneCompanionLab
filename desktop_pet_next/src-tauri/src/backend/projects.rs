use tauri::AppHandle;
use tauri_plugin_dialog::DialogExt;

use crate::backend::admin::backend_admin_request;
use crate::backend::binding::current_bound_backend_url;
use crate::backend::models::BackendAdminRequest;
use crate::pet_state::persistence::load_pet_state;
use crate::runtime::config::is_loopback_backend_host;

#[tauri::command]
pub(crate) async fn manage_project_workspaces(
    app: AppHandle,
    action: String,
    workspace_id: Option<String>,
    display_name: Option<String>,
    include_archived: Option<bool>,
) -> serde_json::Value {
    let action = action.trim();
    if !matches!(action, "list" | "current" | "create" | "select" | "archive") {
        return serde_json::json!({"ok": false, "status": "rejected", "reason": "unknown_project_workspace_action"});
    }
    let payload = serde_json::json!({
        "action": action,
        "workspace_id": workspace_id.unwrap_or_default(),
        "display_name": display_name.unwrap_or_default(),
        "include_archived": include_archived.unwrap_or(false),
    });
    post_project_workspace_admin(app, payload).await
}

#[tauri::command]
pub(crate) async fn bind_project_directory(app: AppHandle) -> serde_json::Value {
    let selected = app.dialog().file().blocking_pick_folder();
    let Some(selected) = selected else {
        return serde_json::json!({"ok": false, "status": "cancelled", "reason": "picker_cancelled"});
    };
    let directory = match selected.into_path() {
        Ok(value) => value,
        Err(_) => {
            return serde_json::json!({"ok": false, "status": "rejected", "reason": "selected_directory_unavailable"});
        }
    };
    let payload = serde_json::json!({
        "action": "bind",
        "host_directory": directory.to_string_lossy(),
        "display_name": directory.file_name().map(|value| value.to_string_lossy()).unwrap_or_default(),
    });
    post_project_workspace_admin(app, payload).await
}

pub(crate) async fn post_project_workspace_admin(
    app: AppHandle,
    mut payload: serde_json::Value,
) -> serde_json::Value {
    let state = match load_pet_state(app.clone()) {
        Ok(value) => value,
        Err(_) => {
            return serde_json::json!({"ok": false, "status": "unavailable", "reason": "client_state_unavailable"})
        }
    };
    let backend_url = current_bound_backend_url(&state);
    let mut target = match reqwest::Url::parse(&backend_url) {
        Ok(value) => value,
        Err(_) => {
            return serde_json::json!({"ok": false, "status": "rejected", "reason": "invalid_backend_url"})
        }
    };
    let binds_host_directory =
        payload.get("action").and_then(serde_json::Value::as_str) == Some("bind");
    if binds_host_directory && !is_loopback_backend_host(target.host_str().unwrap_or("")) {
        return serde_json::json!({"ok": false, "status": "rejected", "reason": "project_binding_requires_local_backend"});
    }
    target.set_path(&format!(
        "/api/bots/{}/desktop-pet/project-workspaces/action",
        state.bound_bot_id
    ));
    target.set_query(None);
    if let Some(object) = payload.as_object_mut() {
        let session_id = if state.session_id.trim().is_empty() {
            "desktop"
        } else {
            state.session_id.trim()
        };
        object.insert(
            "user_id".to_string(),
            serde_json::Value::String(session_id.to_string()),
        );
        object.insert(
            "session_id".to_string(),
            serde_json::Value::String(session_id.to_string()),
        );
        object.insert(
            "real_user_id".to_string(),
            serde_json::Value::String(state.profile_user_id.clone()),
        );
    }
    let response = backend_admin_request(
        app,
        BackendAdminRequest {
            url: target.to_string(),
            method: "POST".to_string(),
            body: payload.to_string(),
        },
    )
    .await;
    let parsed = serde_json::from_str::<serde_json::Value>(&response.body).ok();
    if !response.ok {
        if let Some(payload) = parsed {
            return payload;
        }
        return serde_json::json!({
            "ok": false,
            "status": response.status,
            "reason": response.reason,
            "http_status": response.http_status,
        });
    }
    parsed.unwrap_or_else(|| {
        serde_json::json!({"ok": false, "status": "failed", "reason": "project_workspace_response_invalid"})
    })
}
