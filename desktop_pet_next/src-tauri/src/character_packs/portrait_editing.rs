//! Outfit and emotion asset directory edits.
use super::portrait_formats::existing_portrait_image_paths;
use super::{catalog::list_character_pack_outfits, models::CharacterPackOutfitAsset, paths::*};
use std::{fs, path::PathBuf};

#[tauri::command]
pub(crate) fn create_portrait_outfit(
    pack_id: String,
    outfit: String,
) -> Result<Vec<CharacterPackOutfitAsset>, String> {
    create_portrait_outfit_with_directory(pack_id, outfit, creator_kit_characters_dir)
}

pub(super) fn create_portrait_outfit_with_directory(
    pack_id: String,
    outfit: String,
    directory: impl FnOnce() -> Result<PathBuf, String>,
) -> Result<Vec<CharacterPackOutfitAsset>, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }

    let outfit = sanitize_asset_id(&outfit);
    if outfit.is_empty() {
        return Err("服装名称不能为空。".to_string());
    }

    let characters_dir = directory()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    if !pack_dir.is_dir() {
        return Err(format!("角色包 {pack_id} 不存在。"));
    }

    let (asset_root, characters_dir) = pack_characters_dir(&pack_dir)?;
    fs::create_dir_all(&characters_dir).map_err(|error| error.to_string())?;
    let outfit_dir = safe_child_path(&characters_dir, &outfit)?;
    if outfit_dir.exists() {
        return Err(format!("服装 {outfit} 已存在。"));
    }
    fs::create_dir(&outfit_dir).map_err(|error| error.to_string())?;

    Ok(list_character_pack_outfits(&pack_dir, &asset_root))
}

#[tauri::command]
pub(crate) fn delete_portrait_image(
    pack_id: String,
    outfit: String,
    emotion: String,
) -> Result<Vec<CharacterPackOutfitAsset>, String> {
    delete_portrait_image_with_directory(pack_id, outfit, emotion, creator_kit_characters_dir)
}

pub(super) fn delete_portrait_image_with_directory(
    pack_id: String,
    outfit: String,
    emotion: String,
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

    let characters_dir = directory()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    let (asset_root, characters_dir) = pack_characters_dir(&pack_dir)?;
    let outfit_dir = safe_child_path(&characters_dir, &outfit)?;

    let mut deleted = false;
    for path in existing_portrait_image_paths(&outfit_dir, &emotion)? {
        if path.is_file() {
            fs::remove_file(&path).map_err(|error| error.to_string())?;
            deleted = true;
        }
    }
    if !deleted {
        return Err(format!("表情 {emotion} 在服装 {outfit} 中不存在。"));
    }

    Ok(list_character_pack_outfits(&pack_dir, &asset_root))
}

#[tauri::command]
pub(crate) fn rename_portrait_emotion(
    pack_id: String,
    outfit: String,
    old_emotion: String,
    new_emotion: String,
) -> Result<Vec<CharacterPackOutfitAsset>, String> {
    rename_portrait_emotion_with_directory(
        pack_id,
        outfit,
        old_emotion,
        new_emotion,
        creator_kit_characters_dir,
    )
}

pub(super) fn rename_portrait_emotion_with_directory(
    pack_id: String,
    outfit: String,
    old_emotion: String,
    new_emotion: String,
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
    let old_emotion = sanitize_asset_id(&old_emotion);
    if old_emotion.is_empty() {
        return Err("原表情 ID 不能为空。".to_string());
    }
    let new_emotion = sanitize_asset_id(&new_emotion);
    if new_emotion.is_empty() {
        return Err("新表情 ID 不能为空。".to_string());
    }
    if old_emotion == new_emotion {
        return Err("新旧表情 ID 相同。".to_string());
    }

    let characters_dir = directory()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    let (asset_root, characters_dir) = pack_characters_dir(&pack_dir)?;
    let outfit_dir = safe_child_path(&characters_dir, &outfit)?;

    let new_paths = existing_portrait_image_paths(&outfit_dir, &new_emotion)?;
    if new_paths.iter().any(|path| path.exists()) {
        return Err(format!(
            "表情 {old_emotion} 不存在或 {new_emotion} 已占用。"
        ));
    }
    let old_paths = existing_portrait_image_paths(&outfit_dir, &old_emotion)?;
    let mut renamed = false;
    for (old_path, new_path) in old_paths.iter().zip(&new_paths) {
        if old_path.is_file() && !new_path.exists() {
            fs::rename(&old_path, &new_path).map_err(|error| error.to_string())?;
            renamed = true;
        }
    }
    if !renamed {
        return Err(format!(
            "表情 {old_emotion} 不存在或 {new_emotion} 已占用。"
        ));
    }

    Ok(list_character_pack_outfits(&pack_dir, &asset_root))
}

#[tauri::command]
pub(crate) fn rename_portrait_outfit(
    pack_id: String,
    old_outfit: String,
    new_outfit: String,
) -> Result<Vec<CharacterPackOutfitAsset>, String> {
    rename_portrait_outfit_with_directory(
        pack_id,
        old_outfit,
        new_outfit,
        creator_kit_characters_dir,
    )
}

pub(super) fn rename_portrait_outfit_with_directory(
    pack_id: String,
    old_outfit: String,
    new_outfit: String,
    directory: impl FnOnce() -> Result<PathBuf, String>,
) -> Result<Vec<CharacterPackOutfitAsset>, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    let old_outfit = sanitize_asset_id(&old_outfit);
    if old_outfit.is_empty() {
        return Err("原服装 ID 不能为空。".to_string());
    }
    let new_outfit = sanitize_asset_id(&new_outfit);
    if new_outfit.is_empty() {
        return Err("新服装 ID 不能为空。".to_string());
    }
    if old_outfit == new_outfit {
        return Err("新旧服装 ID 相同。".to_string());
    }

    let characters_dir = directory()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    let (asset_root, chars_dir) = pack_characters_dir(&pack_dir)?;
    let old_dir = safe_child_path(&chars_dir, &old_outfit)?;
    let new_dir = safe_child_path(&chars_dir, &new_outfit)?;

    if !old_dir.is_dir() {
        return Err(format!("服装 {old_outfit} 不存在。"));
    }
    if new_dir.exists() {
        return Err(format!("服装 {new_outfit} 已存在。"));
    }

    fs::rename(&old_dir, &new_dir).map_err(|error| error.to_string())?;
    Ok(list_character_pack_outfits(&pack_dir, &asset_root))
}
