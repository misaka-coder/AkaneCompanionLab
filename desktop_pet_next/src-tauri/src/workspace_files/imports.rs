use crate::runtime::files::read_file_bounded;
use std::{fs, path::Path};
use tauri::AppHandle;
use tauri_plugin_dialog::DialogExt;

use crate::local_files::system_open::canonical_existing_path;
use crate::workspace_files::limits::{
    MAX_WORKSPACE_FILE_BYTES, MAX_WORKSPACE_IMPORT_FILES, MAX_WORKSPACE_IMPORT_TOTAL_BYTES,
};
use crate::workspace_files::routes::verified_workspace_base_url;

#[tauri::command]
pub(crate) async fn choose_workspace_files(app: AppHandle) -> Result<Vec<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .file()
            .blocking_pick_files()
            .unwrap_or_default()
            .into_iter()
            .map(|file| {
                file.into_path()
                    .map(|path| path.to_string_lossy().into_owned())
                    .map_err(|_| "selected_file_unavailable".to_string())
            })
            .collect::<Result<Vec<_>, _>>()
    })
    .await
    .map_err(|_| "file_picker_failed".to_string())?
}

#[tauri::command]
pub(crate) async fn import_dropped_files(
    app: AppHandle,
    backend_url: String,
    bot_id: String,
    paths: Vec<String>,
    user_id: String,
    session_id: String,
    real_user_id: String,
    character_pack_id: String,
) -> Result<serde_json::Value, String> {
    if paths.is_empty() {
        return Ok(serde_json::json!({"ok": false, "reason": "no_paths"}));
    }
    if paths.len() > MAX_WORKSPACE_IMPORT_FILES {
        return Ok(serde_json::json!({"ok": false, "reason": "too_many_files"}));
    }
    let mut import_url = verified_workspace_base_url(&app, &backend_url, &bot_id).await?;
    import_url
        .path_segments_mut()
        .map_err(|_| "backend_url_invalid".to_string())?
        .extend(["desktop-pet", "workspace", "import-file"]);
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(120))
        .build()
        .map_err(|e| format!("reqwest_build: {}", e))?;
    let mut imported_items: Vec<serde_json::Value> = Vec::new();
    let mut skipped_items: Vec<serde_json::Value> = Vec::new();
    let mut total_bytes = 0u64;
    for raw_path in &paths {
        let path = canonical_existing_path(raw_path).unwrap_or_default();
        let file_name = Path::new(raw_path)
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("file")
            .to_string();
        if path.as_os_str().is_empty() || !path.is_file() {
            skipped_items.push(serde_json::json!({"file_name": file_name, "reason": "not_found"}));
            continue;
        }
        let metadata = match fs::metadata(&path) {
            Ok(value) => value,
            Err(_) => {
                skipped_items.push(
                    serde_json::json!({"file_name": file_name, "reason": "file_read_failed"}),
                );
                continue;
            }
        };
        if metadata.len() == 0 {
            skipped_items.push(serde_json::json!({"file_name": file_name, "reason": "empty_file"}));
            continue;
        }
        if metadata.len() > MAX_WORKSPACE_FILE_BYTES
            || total_bytes.saturating_add(metadata.len()) > MAX_WORKSPACE_IMPORT_TOTAL_BYTES
        {
            skipped_items
                .push(serde_json::json!({"file_name": file_name, "reason": "file_too_large"}));
            continue;
        }
        let max_bytes = MAX_WORKSPACE_FILE_BYTES
            .min(MAX_WORKSPACE_IMPORT_TOTAL_BYTES.saturating_sub(total_bytes));
        let bytes = match read_file_bounded(&path, max_bytes) {
            Ok(value) => value,
            Err(error) => {
                let reason = if error.kind() == std::io::ErrorKind::InvalidData {
                    "file_too_large"
                } else {
                    "file_read_failed"
                };
                skipped_items.push(serde_json::json!({"file_name": file_name, "reason": reason}));
                continue;
            }
        };
        if bytes.is_empty() {
            skipped_items.push(serde_json::json!({"file_name": file_name, "reason": "empty_file"}));
            continue;
        }
        total_bytes = total_bytes.saturating_add(bytes.len() as u64);
        let part = reqwest::multipart::Part::bytes(bytes).file_name(file_name.clone());
        let form = reqwest::multipart::Form::new()
            .text("user_id", user_id.clone())
            .text("session_id", session_id.clone())
            .text("real_user_id", real_user_id.clone())
            .text("character_pack_id", character_pack_id.clone())
            .part("files", part);
        let response = match client.post(import_url.clone()).multipart(form).send().await {
            Ok(value) => value,
            Err(_) => {
                skipped_items
                    .push(serde_json::json!({"file_name": file_name, "reason": "upload_failed"}));
                continue;
            }
        };
        let status = response.status();
        let body: serde_json::Value = response
            .json()
            .await
            .unwrap_or_else(|_| serde_json::json!({"ok": false, "status": status.as_u16()}));
        if !status.is_success()
            || !body
                .get("ok")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false)
        {
            skipped_items.push(serde_json::json!({
                "file_name": file_name,
                "reason": body.get("reason").and_then(serde_json::Value::as_str).unwrap_or("upload_rejected")
            }));
            continue;
        }
        if let Some(items) = body.get("items").and_then(serde_json::Value::as_array) {
            imported_items.extend(items.iter().cloned());
        }
        if let Some(skipped) = body.get("skipped").and_then(serde_json::Value::as_array) {
            for item in skipped {
                skipped_items.push(serde_json::json!({
                    "file_name": file_name,
                    "reason": item.get("reason").and_then(serde_json::Value::as_str).unwrap_or("import_failed")
                }));
            }
        }
    }
    Ok(serde_json::json!({
        "ok": !imported_items.is_empty(),
        "reason": if imported_items.is_empty() {
            skipped_items.first().and_then(|v| v.get("reason")).and_then(serde_json::Value::as_str).unwrap_or("no_files")
        } else { "" },
        "imported": imported_items.len(),
        "items": imported_items,
        "skipped_count": skipped_items.len(),
        "skipped": skipped_items,
    }))
}
