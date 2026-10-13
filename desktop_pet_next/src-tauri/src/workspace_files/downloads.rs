use crate::character_packs::storage::write_bytes_atomic_with_cleanup;
use crate::runtime::http::{response_bytes_bounded, BodyReadError};
use sha2::{Digest, Sha256};
use std::fs;
use tauri::AppHandle;

use crate::local_files::exports::export_file_to_desktop_blocking;
use crate::local_files::names::sanitize_file_name;
use crate::local_files::system_open::{open_path_with_system, reveal_path_in_file_manager};
use crate::runtime::config::runtime_instance_id;
use crate::runtime::paths::desktop_runtime_cache_dir;
use crate::workspace_files::limits::MAX_WORKSPACE_FILE_BYTES;
use crate::workspace_files::routes::{is_safe_artifact_handle, verified_workspace_base_url};

#[tauri::command]
pub(crate) async fn open_workspace_item(
    app: AppHandle,
    backend_url: String,
    bot_id: String,
    handle: String,
    item_type: String,
    action: String,
    user_id: String,
    session_id: String,
    real_user_id: String,
    file_name: String,
) -> Result<serde_json::Value, String> {
    let handle = handle.trim().to_string();
    let route_type = match item_type.trim() {
        "generated" | "output" => "generated",
        _ => "attachments",
    };
    let action = action.trim().to_string();
    let file_name = file_name.trim().to_string();
    if !is_safe_artifact_handle(&handle) {
        return Err("invalid_artifact_handle".to_string());
    }
    if !matches!(
        action.as_str(),
        "open" | "reveal" | "save_desktop" | "stage" | "copy_path"
    ) {
        return Err("unknown_workspace_action".to_string());
    }
    let mut content_url = verified_workspace_base_url(&app, &backend_url, &bot_id).await?;
    {
        let mut segments = content_url
            .path_segments_mut()
            .map_err(|_| "backend_url_invalid".to_string())?;
        segments.pop_if_empty();
        segments.extend([
            "desktop-pet",
            "workspace",
            route_type,
            handle.as_str(),
            "content",
        ]);
    }
    content_url
        .query_pairs_mut()
        .append_pair("user_id", &user_id)
        .append_pair("session_id", &session_id)
        .append_pair("real_user_id", &real_user_id);
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(60))
        .build()
        .map_err(|e| format!("reqwest_build: {}", e))?;
    let response = client
        .get(content_url)
        .send()
        .await
        .map_err(|e| format!("content_fetch: {}", e))?;
    if !response.status().is_success() {
        return Err(format!("content_not_found: HTTP {}", response.status()));
    }
    let expected_instance = response
        .headers()
        .get("x-akane-artifact-instance")
        .and_then(|value| value.to_str().ok())
        .unwrap_or("")
        .trim()
        .to_string();
    if expected_instance != runtime_instance_id()? {
        return Err("artifact_instance_mismatch".to_string());
    }
    let expected_size = response
        .headers()
        .get("x-akane-artifact-size")
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.parse::<u64>().ok())
        .ok_or_else(|| "artifact_size_missing".to_string())?;
    if expected_size == 0 || expected_size > MAX_WORKSPACE_FILE_BYTES {
        return Err("artifact_size_rejected".to_string());
    }
    let expected_hash = response
        .headers()
        .get("x-akane-artifact-sha256")
        .and_then(|value| value.to_str().ok())
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase();
    if expected_hash.len() != 64 || !expected_hash.chars().all(|ch| ch.is_ascii_hexdigit()) {
        return Err("artifact_hash_missing".to_string());
    }
    let expected_mime = response
        .headers()
        .get("x-akane-artifact-mime")
        .and_then(|value| value.to_str().ok())
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase();
    let response_mime = response
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("")
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase();
    if expected_mime.is_empty() || expected_mime != response_mime {
        return Err("artifact_mime_mismatch".to_string());
    }
    let actual_name = sanitize_file_name(
        if !file_name.is_empty() {
            file_name
        } else {
            _extract_content_disposition_filename(&response).unwrap_or_else(|| handle.clone())
        }
        .as_str(),
    );
    let bytes = match response_bytes_bounded(response, expected_size as usize).await {
        Ok(bytes) => bytes,
        Err(BodyReadError::TooLarge) => return Err("artifact_size_mismatch".to_string()),
        Err(BodyReadError::Read(error)) => return Err(format!("content_read: {error}")),
    };
    if bytes.len() as u64 != expected_size || bytes.len() as u64 > MAX_WORKSPACE_FILE_BYTES {
        return Err("artifact_size_mismatch".to_string());
    }
    let actual_hash = format!("{:x}", Sha256::digest(&bytes));
    if actual_hash != expected_hash {
        return Err("artifact_hash_mismatch".to_string());
    }
    let cache_dir = desktop_runtime_cache_dir("workspace_items")?;
    let safe_seg = sanitize_file_name(&handle);
    let item_dir = cache_dir.join(&safe_seg);
    fs::create_dir_all(&item_dir).map_err(|e| e.to_string())?;
    let staged_path = item_dir.join(&actual_name);
    write_bytes_atomic_with_cleanup(&staged_path, &bytes, &[])?;
    match action.as_str() {
        "open" => {
            open_path_with_system(&staged_path)?;
            Ok(serde_json::json!({"ok": true, "action": "open"}))
        }
        "reveal" => {
            reveal_path_in_file_manager(&staged_path)?;
            Ok(serde_json::json!({"ok": true, "action": "reveal"}))
        }
        "save_desktop" => {
            let result = tauri::async_runtime::spawn_blocking(move || {
                export_file_to_desktop_blocking(
                    app,
                    staged_path.to_string_lossy().to_string(),
                    actual_name,
                )
            })
            .await
            .map_err(|e| format!("export_task: {}", e))??;
            Ok(serde_json::to_value(result).map_err(|e| e.to_string())?)
        }
        "stage" => Ok(serde_json::json!({
            "ok": true,
            "action": "stage",
            "local_path": staged_path.to_string_lossy().to_string(),
        })),
        "copy_path" => Ok(serde_json::json!({
            "ok": true,
            "action": "copy_path",
            "local_path": staged_path.to_string_lossy().to_string(),
        })),
        _ => Err("unknown_workspace_action".to_string()),
    }
}

pub(crate) fn _extract_content_disposition_filename(
    response: &reqwest::Response,
) -> Option<String> {
    let header = response
        .headers()
        .get("content-disposition")?
        .to_str()
        .ok()?;
    for part in header.split(';') {
        let part = part.trim();
        if let Some(v) = part
            .strip_prefix("filename=\"")
            .and_then(|s| s.strip_suffix('"'))
        {
            if !v.is_empty() {
                return Some(v.to_string());
            }
        }
        if let Some(v) = part.strip_prefix("filename=") {
            let v = v.trim_matches('"').trim();
            if !v.is_empty() {
                return Some(v.to_string());
            }
        }
    }
    None
}
