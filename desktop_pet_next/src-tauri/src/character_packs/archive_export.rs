//! Export portable public character files through desktop staging.
use super::paths::{creator_kit_characters_dir, safe_child_path, sanitize_pack_id};
use crate::character_packs::constants::MAX_CHARACTER_PACK_ZIP_BYTES;
use crate::character_packs::constants::PRIVATE_LOCAL_DIRECTORY;
use crate::local_files::{
    exports::{publish_unique_staged_export, resolve_desktop_export_dir},
    models::ExportedWorkspaceFile,
};
use crate::runtime::files::unique_staging_path;
use crate::runtime::paths::desktop_runtime_cache_dir;
use std::{
    fs,
    io::{self, Read},
    path::Path,
};
use tauri::AppHandle;

#[tauri::command]
pub(crate) async fn export_character_pack(
    app: AppHandle,
    pack_id: String,
) -> Result<ExportedWorkspaceFile, String> {
    tauri::async_runtime::spawn_blocking(move || export_character_pack_blocking(app, pack_id))
        .await
        .map_err(|error| format!("角色包导出任务失败：{error}"))?
}

pub(crate) fn export_character_pack_blocking(
    app: AppHandle,
    pack_id: String,
) -> Result<ExportedWorkspaceFile, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    if !pack_dir.is_dir() {
        return Err(format!("角色包 {pack_id} 不存在。"));
    }

    let zip_name = format!("{pack_id}.zip");
    let export_dir = resolve_desktop_export_dir(&app)?;
    fs::create_dir_all(&export_dir).map_err(|error| error.to_string())?;
    let staging_dir = desktop_runtime_cache_dir("export_staging")?;
    fs::create_dir_all(&staging_dir).map_err(|error| error.to_string())?;
    let staged_path = unique_staging_path(&staging_dir, "zip");

    let size = write_character_pack_archive(&pack_dir, &staged_path)?;
    let target_path = publish_unique_staged_export(&staged_path, &export_dir, &zip_name)?;

    Ok(ExportedWorkspaceFile {
        ok: true,
        path: target_path.to_string_lossy().to_string(),
        file_name: target_path
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or(&zip_name)
            .to_string(),
        size_bytes: Some(size),
    })
}

pub(super) fn write_character_pack_archive(
    pack_dir: &Path,
    staged_path: &Path,
) -> Result<u64, String> {
    let file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(staged_path)
        .map_err(|error| error.to_string())?;
    let result = (move || {
        let mut writer = zip::ZipWriter::new(file);
        let options = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);

        let mut remaining = MAX_CHARACTER_PACK_ZIP_BYTES as u64;
        let pack_prefix = pack_dir
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("character")
            .to_string();

        fn walk_zip(
            writer: &mut zip::ZipWriter<fs::File>,
            base: &Path,
            prefix: &str,
            options: zip::write::SimpleFileOptions,
            root: &Path,
            remaining: &mut u64,
        ) -> Result<(), String> {
            if !base.is_dir() {
                return Ok(());
            }
            for entry in fs::read_dir(base).map_err(|e| e.to_string())? {
                let entry = entry.map_err(|e| e.to_string())?;
                let path = entry.path();
                let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
                if name.eq_ignore_ascii_case(PRIVATE_LOCAL_DIRECTORY) {
                    continue;
                }
                if entry
                    .file_type()
                    .map_err(|error| error.to_string())?
                    .is_symlink()
                {
                    return Err("角色包导出不能包含目录或文件链接。".to_string());
                }
                let child = path
                    .strip_prefix(root)
                    .map_err(|error| error.to_string())?
                    .to_string_lossy();
                safe_child_path(root, &child)?;
                let relative = format!("{prefix}/{name}");

                if path.is_dir() {
                    writer
                        .add_directory(&relative, options)
                        .map_err(|e| e.to_string())?;
                    walk_zip(writer, &path, &relative, options, root, remaining)?;
                } else if path.is_file() {
                    writer
                        .start_file(&relative, options)
                        .map_err(|e| e.to_string())?;
                    let input = fs::File::open(&path).map_err(|error| error.to_string())?;
                    let copied = io::copy(&mut input.take(remaining.saturating_add(1)), writer)
                        .map_err(|error| error.to_string())?;
                    *remaining = remaining
                        .checked_sub(copied)
                        .ok_or_else(|| "角色包解压后的内容过大。".to_string())?;
                }
            }
            Ok(())
        }

        writer
            .add_directory(&pack_prefix, options)
            .map_err(|e| e.to_string())?;
        walk_zip(
            &mut writer,
            pack_dir,
            &pack_prefix,
            options,
            pack_dir,
            &mut remaining,
        )?;

        let file = writer.finish().map_err(|e| e.to_string())?;
        let size = file.metadata().map_err(|e| e.to_string())?.len();
        drop(file);
        Ok(size)
    })();
    if result.is_err() {
        let _ = fs::remove_file(staged_path);
    }
    result
}
