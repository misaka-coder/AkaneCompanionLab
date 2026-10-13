//! Blocking image I/O with unchanged asynchronous Tauri wrappers.
use super::{
    catalog::list_character_pack_outfits,
    models::{CharacterPackOutfitAsset, UploadPortraitResult},
    paths::*,
    portrait_formats::*,
    storage::write_bytes_atomic_with_cleanup,
};
use crate::character_packs::constants::MAX_PORTRAIT_IMAGE_BYTES;
use std::{fs, path::PathBuf};
use tauri::AppHandle;

#[tauri::command]
pub(crate) async fn upload_portrait_image(
    _app: AppHandle,
    pack_id: String,
    outfit: String,
    emotion: String,
    image_bytes: Vec<u8>,
) -> Result<UploadPortraitResult, String> {
    tauri::async_runtime::spawn_blocking(move || {
        upload_portrait_image_blocking(pack_id, outfit, emotion, image_bytes)
    })
    .await
    .map_err(|error| format!("立绘导入任务失败：{error}"))?
}

pub(crate) fn upload_portrait_image_blocking(
    pack_id: String,
    outfit: String,
    emotion: String,
    image_bytes: Vec<u8>,
) -> Result<UploadPortraitResult, String> {
    upload_portrait_image_with_directory(
        pack_id,
        outfit,
        emotion,
        image_bytes,
        creator_kit_characters_dir,
    )
}

pub(super) fn upload_portrait_image_with_directory(
    pack_id: String,
    outfit: String,
    emotion: String,
    image_bytes: Vec<u8>,
    directory: impl FnOnce() -> Result<PathBuf, String>,
) -> Result<UploadPortraitResult, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }

    let outfit = sanitize_asset_id(&outfit);
    if outfit.is_empty() {
        return Err("服装 ID 不能为空。".to_string());
    }

    let emotion = sanitize_asset_id(&emotion);
    if emotion.is_empty() {
        return Err("表情 ID 不能为空。".to_string());
    }

    if image_bytes.is_empty() {
        return Err("图片数据为空。".to_string());
    }
    if image_bytes.len() > MAX_PORTRAIT_IMAGE_BYTES {
        return Err("图片大小不能超过 20 MB。".to_string());
    }

    /* detect format from magic bytes, reject unsupported types */
    let extension = detect_image_extension(&image_bytes)?;

    let characters_dir = directory()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    if !pack_dir.is_dir() {
        return Err(format!("角色包 {pack_id} 不存在。"));
    }

    let (_asset_root, characters_dir) = pack_characters_dir(&pack_dir)?;
    let outfit_dir = safe_child_path(&characters_dir, &outfit)?;

    /* create outfit directory if needed */
    fs::create_dir_all(&outfit_dir).map_err(|error| error.to_string())?;

    let cleanup_paths = existing_portrait_image_paths(&outfit_dir, &emotion)?;
    let file_name = format!("{emotion}.{extension}");
    let target_path = safe_child_path(&outfit_dir, &file_name)?;
    write_bytes_atomic_with_cleanup(&target_path, &image_bytes, &cleanup_paths)?;

    let size_bytes = image_bytes.len() as u64;

    Ok(UploadPortraitResult {
        ok: true,
        outfit,
        emotion,
        path: target_path.to_string_lossy().to_string(),
        size_bytes,
    })
}

#[tauri::command]
pub(crate) async fn import_generated_portrait_image(
    pack_id: String,
    outfit: String,
    emotion: String,
    image_bytes: Vec<u8>,
    extension: Option<String>,
    mime_type: Option<String>,
    overwrite: bool,
) -> Result<Vec<CharacterPackOutfitAsset>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        import_generated_portrait_image_blocking(
            pack_id,
            outfit,
            emotion,
            image_bytes,
            extension,
            mime_type,
            overwrite,
        )
    })
    .await
    .map_err(|error| format!("生成立绘写入任务失败：{error}"))?
}

pub(crate) fn import_generated_portrait_image_blocking(
    pack_id: String,
    outfit: String,
    emotion: String,
    image_bytes: Vec<u8>,
    extension: Option<String>,
    mime_type: Option<String>,
    overwrite: bool,
) -> Result<Vec<CharacterPackOutfitAsset>, String> {
    import_generated_portrait_image_with_directory(
        pack_id,
        outfit,
        emotion,
        image_bytes,
        extension,
        mime_type,
        overwrite,
        creator_kit_characters_dir,
    )
}

pub(super) fn import_generated_portrait_image_with_directory(
    pack_id: String,
    outfit: String,
    emotion: String,
    image_bytes: Vec<u8>,
    extension: Option<String>,
    mime_type: Option<String>,
    overwrite: bool,
    directory: impl FnOnce() -> Result<PathBuf, String>,
) -> Result<Vec<CharacterPackOutfitAsset>, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }

    let outfit = sanitize_asset_id(&outfit);
    if outfit.is_empty() {
        return Err("服装 ID 不能为空。".to_string());
    }

    let emotion = sanitize_asset_id(&emotion);
    if emotion.is_empty() {
        return Err("表情 ID 不能为空。".to_string());
    }

    if image_bytes.is_empty() {
        return Err("图片数据为空。".to_string());
    }
    if image_bytes.len() > MAX_PORTRAIT_IMAGE_BYTES {
        return Err("图片大小不能超过 20 MB。".to_string());
    }

    let extension = resolve_generated_portrait_extension(&image_bytes, extension, mime_type)?;

    let characters_dir = directory()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    if !pack_dir.is_dir() {
        return Err(format!("角色包 {pack_id} 不存在。"));
    }

    let (asset_root, characters_dir) = pack_characters_dir(&pack_dir)?;
    let outfit_dir = safe_child_path(&characters_dir, &outfit)?;
    fs::create_dir_all(&outfit_dir).map_err(|error| error.to_string())?;

    let existing_paths = existing_portrait_image_paths(&outfit_dir, &emotion)?;
    if !overwrite && existing_paths.iter().any(|path| path.is_file()) {
        return Err(format!(
            "表情 {emotion} 已存在，请确认 overwrite 后再覆盖。"
        ));
    }
    let target_path = safe_child_path(&outfit_dir, &format!("{emotion}.{extension}"))?;
    let cleanup_paths = if overwrite {
        existing_paths
    } else {
        Vec::new()
    };
    write_bytes_atomic_with_cleanup(&target_path, &image_bytes, &cleanup_paths)?;

    Ok(list_character_pack_outfits(&pack_dir, &asset_root))
}

#[tauri::command]
pub(crate) async fn read_portrait_image(
    pack_id: String,
    outfit: String,
    emotion: String,
) -> Result<tauri::ipc::Response, String> {
    tauri::async_runtime::spawn_blocking(move || {
        read_portrait_image_blocking(pack_id, outfit, emotion)
    })
    .await
    .map_err(|error| format!("立绘读取任务失败：{error}"))?
}

pub(crate) fn read_portrait_image_blocking(
    pack_id: String,
    outfit: String,
    emotion: String,
) -> Result<tauri::ipc::Response, String> {
    read_portrait_image_with_directory(pack_id, outfit, emotion, creator_kit_characters_dir)
}

pub(super) fn read_portrait_image_with_directory(
    pack_id: String,
    outfit: String,
    emotion: String,
    directory: impl FnOnce() -> Result<PathBuf, String>,
) -> Result<tauri::ipc::Response, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    let outfit = sanitize_asset_id(&outfit);
    if outfit.is_empty() {
        return Err("服装名称不能为空。".to_string());
    }
    let emotion = sanitize_asset_id(&emotion);
    if emotion.is_empty() {
        return Err("表情名称不能为空。".to_string());
    }

    let characters_dir = directory()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    let (_asset_root, characters_dir) = pack_characters_dir(&pack_dir)?;
    let outfit_dir = safe_child_path(&characters_dir, &outfit)?;

    for image_path in existing_portrait_image_paths(&outfit_dir, &emotion)? {
        if image_path.is_file() {
            let bytes = fs::read(&image_path).map_err(|error| error.to_string())?;
            return Ok(tauri::ipc::Response::new(bytes));
        }
    }

    Err(format!("找不到立绘：{outfit} / {emotion}"))
}
