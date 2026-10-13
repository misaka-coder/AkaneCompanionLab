//! Register creator-authored character knowledge folders.
use super::catalog::registry_item;
use super::models::*;
use super::paths::*;
use super::storage::write_text_atomic;
use crate::character_packs::constants::PRIVATE_LOCAL_DIRECTORY;
use std::{fs, path::PathBuf};

#[tauri::command]
pub(crate) fn create_character_context_library(
    request: CreateCharacterContextLibraryRequest,
) -> Result<CharacterPackRegistryItem, String> {
    create_character_context_library_with_directory(request, creator_kit_characters_dir)
}

pub(super) fn create_character_context_library_with_directory(
    request: CreateCharacterContextLibraryRequest,
    directory: impl FnOnce() -> Result<PathBuf, String>,
) -> Result<CharacterPackRegistryItem, String> {
    let pack_id = sanitize_pack_id(&request.pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    if request
        .folder
        .trim()
        .eq_ignore_ascii_case(PRIVATE_LOCAL_DIRECTORY)
    {
        return Err("这个文件夹名由角色包保留，请换一个名称。".to_string());
    }
    let folder = sanitize_context_library_folder(&request.folder);
    if folder.is_empty() {
        return Err("资料库文件夹名不能为空。".to_string());
    }
    if folder.chars().count() > 80 {
        return Err("资料库文件夹名过长，请控制在 80 个字符以内。".to_string());
    }
    if folder.eq_ignore_ascii_case(PRIVATE_LOCAL_DIRECTORY) || folder.eq_ignore_ascii_case("assets")
    {
        return Err("这个文件夹名由角色包保留，请换一个名称。".to_string());
    }
    let name = request.name.trim();
    let description = request.description.trim();
    let load_when = request.load_when.trim();
    if name.is_empty() {
        return Err("资料库名称不能为空。".to_string());
    }
    if description.is_empty() {
        return Err("请说明这个资料库保存什么内容。".to_string());
    }
    if load_when.is_empty() {
        return Err("请说明角色应在什么时候读取这个资料库。".to_string());
    }
    if name.chars().count() > 80
        || description.chars().count() > 400
        || load_when.chars().count() > 300
    {
        return Err("资料库说明过长，请精简后再保存。".to_string());
    }

    let characters_dir = directory()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    if !pack_dir.is_dir() {
        return Err(format!("角色包 {pack_id} 不存在。"));
    }
    let character_path = pack_dir.join("character.json");
    let raw = fs::read_to_string(&character_path).map_err(|error| error.to_string())?;
    let mut profile: serde_json::Value =
        serde_json::from_str(&raw).map_err(|error| format!("character.json 无效：{error}"))?;
    let Some(profile_object) = profile.as_object_mut() else {
        return Err("character.json 根节点必须是对象。".to_string());
    };

    let libraries = profile_object
        .entry("context_libraries".to_string())
        .or_insert_with(|| serde_json::json!([]));
    let Some(libraries) = libraries.as_array_mut() else {
        return Err("context_libraries 必须是数组。".to_string());
    };
    if libraries.iter().any(|library| {
        library
            .get("folder")
            .and_then(|value| value.as_str())
            .map(|value| value.eq_ignore_ascii_case(&folder))
            .unwrap_or(false)
    }) {
        return Err(format!("资料库文件夹 {folder} 已经登记过了。"));
    }

    let library_dir = safe_child_path(&pack_dir, &folder)?;
    if library_dir.exists() && !library_dir.is_dir() {
        return Err(format!("{folder} 已存在，但不是文件夹。"));
    }
    let created_directory = !library_dir.exists();
    fs::create_dir_all(&library_dir).map_err(|error| error.to_string())?;

    libraries.push(serde_json::json!({
        "folder": folder,
        "name": name,
        "description": description,
        "load_when": load_when,
        "aliases": {}
    }));

    let validated: CharacterPackJson = match serde_json::from_value(profile.clone()) {
        Ok(value) => value,
        Err(error) => {
            if created_directory {
                let _ = fs::remove_dir(&library_dir);
            }
            return Err(format!("更新后的角色数据无效：{error}"));
        }
    };
    let updated = match serde_json::to_string_pretty(&profile) {
        Ok(value) => value,
        Err(error) => {
            if created_directory {
                let _ = fs::remove_dir(&library_dir);
            }
            return Err(format!("序列化角色数据失败：{error}"));
        }
    };
    if let Err(error) = write_text_atomic(&character_path, &updated) {
        if created_directory {
            let _ = fs::remove_dir(&library_dir);
        }
        return Err(error);
    }

    Ok(registry_item(
        pack_id,
        &pack_dir,
        profile,
        validated.asset_root(),
    ))
}
