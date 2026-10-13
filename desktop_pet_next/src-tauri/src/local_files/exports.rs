use crate::runtime::files::copy_to_staging;
use std::{
    fs,
    path::{Path, PathBuf},
};
use tauri::{AppHandle, Manager};

use crate::local_files::models::ExportedWorkspaceFile;
use crate::local_files::names::{unique_child_file_path, workspace_export_file_name};
use crate::local_files::system_open::canonical_existing_path;
use crate::runtime::paths::desktop_runtime_cache_dir;

#[tauri::command]
pub(crate) async fn export_file_to_desktop(
    app: AppHandle,
    path: String,
    file_name: String,
) -> Result<ExportedWorkspaceFile, String> {
    tauri::async_runtime::spawn_blocking(move || {
        export_file_to_desktop_blocking(app, path, file_name)
    })
    .await
    .map_err(|error| format!("导出文件任务失败：{error}"))?
}

pub(crate) fn export_file_to_desktop_blocking(
    app: AppHandle,
    path: String,
    file_name: String,
) -> Result<ExportedWorkspaceFile, String> {
    export_file_to_desktop_with_directories(path, file_name, || {
        Ok((
            resolve_desktop_export_dir(&app)?,
            desktop_runtime_cache_dir("export_staging")?,
        ))
    })
}

pub(super) fn export_file_to_desktop_with_directories(
    path: String,
    file_name: String,
    directories: impl FnOnce() -> Result<(PathBuf, PathBuf), String>,
) -> Result<ExportedWorkspaceFile, String> {
    let source_path = canonical_existing_path(&path)?;
    if !source_path.is_file() {
        return Err("只能导出文件。".to_string());
    }

    let (export_dir, staging_dir) = directories()?;
    fs::create_dir_all(&export_dir).map_err(|error| error.to_string())?;
    let file_name = workspace_export_file_name(&source_path, &file_name);
    let (staged_path, _) = copy_to_staging(&source_path, &staging_dir, "stage", None)?;
    let target_path = publish_unique_staged_export(&staged_path, &export_dir, &file_name)?;

    Ok(ExportedWorkspaceFile {
        ok: true,
        path: target_path.to_string_lossy().to_string(),
        file_name: target_path
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or(file_name.as_str())
            .to_string(),
        size_bytes: None,
    })
}

pub(crate) fn resolve_desktop_export_dir(app: &AppHandle) -> Result<PathBuf, String> {
    if let Ok(path) = app.path().desktop_dir() {
        return Ok(path.join("Akane Outputs"));
    }
    #[cfg(windows)]
    if let Some(home) = std::env::var_os("USERPROFILE") {
        return Ok(PathBuf::from(home).join("Desktop").join("Akane Outputs"));
    }
    if let Some(home) = std::env::var_os("HOME") {
        return Ok(PathBuf::from(home).join("Desktop").join("Akane Outputs"));
    }
    Err("无法定位桌面目录。".to_string())
}

pub(crate) fn publish_staged_export(staged_path: &Path, target_path: &Path) -> Result<(), String> {
    let target_parent = target_path
        .parent()
        .ok_or_else(|| "invalid_export_target".to_string())?;
    fs::create_dir_all(target_parent).map_err(|error| error.to_string())?;
    if target_path.file_name().is_none() {
        return Err("invalid_export_target".to_string());
    }
    // An exclusive reservation prevents another export or process from
    // replacing a file that appeared after name selection.
    let reservation = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(target_path)
        .map_err(|error| {
            if error.kind() == std::io::ErrorKind::AlreadyExists {
                "export_target_exists".to_string()
            } else {
                error.to_string()
            }
        })?;
    drop(reservation);
    let mut partial = None;
    let publish_result = (|| {
        let (path, _) = copy_to_staging(staged_path, target_parent, "partial", None)?;
        partial = Some(path.clone());
        fs::rename(&path, target_path).map_err(|error| error.to_string())
    })();
    if let Some(path) = partial {
        let _ = fs::remove_file(path);
    }
    if publish_result.is_err() {
        let _ = fs::remove_file(target_path);
    }
    let _ = fs::remove_file(staged_path);
    publish_result
}

pub(crate) fn publish_unique_staged_export(
    staged_path: &Path,
    directory: &Path,
    file_name: &str,
) -> Result<PathBuf, String> {
    let result = (|| {
        for _ in 0..1000 {
            let target = unique_child_file_path(directory, file_name);
            match publish_staged_export(staged_path, &target) {
                Ok(()) => return Ok(target),
                Err(reason) if reason == "export_target_exists" => continue,
                Err(reason) => return Err(reason),
            }
        }
        Err("export_name_unavailable".to_string())
    })();
    if result.is_err() {
        let _ = fs::remove_file(staged_path);
    }
    result
}
