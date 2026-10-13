//! Default portraits and per-outfit calibration profiles.
use super::{
    catalog::find_emotion_image, models::CharacterPackJson, paths::*, storage::write_text_atomic,
};
use std::{fs, path::PathBuf};

#[tauri::command]
pub(crate) fn set_default_emotion(
    pack_id: String,
    field: String,
    value: String,
) -> Result<String, String> {
    set_default_emotion_with_directory(pack_id, field, value, creator_kit_characters_dir)
}

pub(super) fn set_default_emotion_with_directory(
    pack_id: String,
    field: String,
    value: String,
    directory: impl FnOnce() -> Result<PathBuf, String>,
) -> Result<String, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    if !["default_emotion", "default_outfit", "music_emotion"].contains(&field.as_str()) {
        return Err(format!("不允许修改字段：{field}"));
    }
    let value = sanitize_asset_id(&value);
    if value.is_empty() {
        return Err(format!("{field} 的值不能为空。"));
    }

    let characters_dir = directory()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    let character_path = pack_dir.join("character.json");
    if !character_path.is_file() {
        return Err(format!("{pack_id} 缺少 character.json。"));
    }

    let raw = fs::read_to_string(&character_path).map_err(|error| error.to_string())?;
    let mut profile: serde_json::Value =
        serde_json::from_str(&raw).map_err(|error| format!("character.json 无效：{error}"))?;

    if let Some(appearance) = profile
        .get_mut("appearance")
        .and_then(|v| v.as_object_mut())
    {
        appearance.insert(field.clone(), serde_json::Value::String(value.clone()));
    }

    /* validate */
    let _validated: CharacterPackJson = serde_json::from_value(profile.clone())
        .map_err(|error| format!("更新后的角色数据无效：{error}"))?;

    /* atomic write */
    let updated =
        serde_json::to_string_pretty(&profile).map_err(|error| format!("序列化失败：{error}"))?;
    write_text_atomic(&character_path, &updated)?;

    Ok(format!("{field} → {value}"))
}

#[tauri::command]
pub(crate) fn set_default_portrait(
    pack_id: String,
    outfit: String,
    emotion: String,
) -> Result<String, String> {
    set_default_portrait_with_directory(pack_id, outfit, emotion, creator_kit_characters_dir)
}

pub(super) fn set_default_portrait_with_directory(
    pack_id: String,
    outfit: String,
    emotion: String,
    directory: impl FnOnce() -> Result<PathBuf, String>,
) -> Result<String, String> {
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
    let character_path = pack_dir.join("character.json");
    if !character_path.is_file() {
        return Err(format!("{pack_id} 缺少 character.json。"));
    }

    let (_asset_root, characters_dir) = pack_characters_dir(&pack_dir)?;
    let outfit_dir = safe_child_path(&characters_dir, &outfit)?;
    if !outfit_dir.is_dir() {
        return Err(format!("服装 {outfit} 不存在。"));
    }
    if !find_emotion_image(&outfit_dir, &emotion) {
        return Err(format!("服装 {outfit} 中没有表情 {emotion}。"));
    }

    let raw = fs::read_to_string(&character_path).map_err(|error| error.to_string())?;
    let mut profile: serde_json::Value =
        serde_json::from_str(&raw).map_err(|error| format!("character.json 无效：{error}"))?;
    let appearance = profile
        .get_mut("appearance")
        .and_then(|value| value.as_object_mut())
        .ok_or_else(|| "character.json 缺少 appearance 对象。".to_string())?;
    appearance.insert(
        "default_outfit".to_string(),
        serde_json::Value::String(outfit.clone()),
    );
    appearance.insert(
        "default_emotion".to_string(),
        serde_json::Value::String(emotion.clone()),
    );

    let _validated: CharacterPackJson = serde_json::from_value(profile.clone())
        .map_err(|error| format!("更新后的角色数据无效：{error}"))?;
    let updated =
        serde_json::to_string_pretty(&profile).map_err(|error| format!("序列化失败：{error}"))?;
    write_text_atomic(&character_path, &updated)?;

    Ok(format!("{outfit} / {emotion}"))
}

#[tauri::command]
pub(crate) fn save_calibration(
    pack_id: String,
    outfit_id: String,
    layout: serde_json::Value,
) -> Result<String, String> {
    save_calibration_with_directory(pack_id, outfit_id, layout, creator_kit_characters_dir)
}

pub(super) fn save_calibration_with_directory(
    pack_id: String,
    outfit_id: String,
    layout: serde_json::Value,
    directory: impl FnOnce() -> Result<PathBuf, String>,
) -> Result<String, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    let outfit_id = sanitize_asset_id(&outfit_id);
    if outfit_id.is_empty() {
        return Err("服装 ID 不能为空。".to_string());
    }
    if !layout.is_object() {
        return Err("校准数据必须是对象。".to_string());
    }

    let characters_dir = directory()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    let character_path = pack_dir.join("character.json");
    if !character_path.is_file() {
        return Err(format!("{pack_id} 缺少 character.json。"));
    }

    let raw = fs::read_to_string(&character_path).map_err(|error| error.to_string())?;
    let mut profile: serde_json::Value =
        serde_json::from_str(&raw).map_err(|error| format!("character.json 无效：{error}"))?;

    let root = profile
        .as_object_mut()
        .ok_or_else(|| "character.json 根节点必须是对象。".to_string())?;
    let pack_layout = root
        .entry("layout")
        .or_insert_with(|| serde_json::json!({}));
    if pack_layout.is_null() {
        *pack_layout = serde_json::json!({});
    }
    let pack_layout = pack_layout
        .as_object_mut()
        .ok_or_else(|| "layout 必须是对象。".to_string())?;
    let outfits = pack_layout
        .entry("outfits")
        .or_insert_with(|| serde_json::json!({}));
    if outfits.is_null() {
        *outfits = serde_json::json!({});
    }
    let outfits = outfits
        .as_object_mut()
        .ok_or_else(|| "layout.outfits 必须是对象。".to_string())?;
    outfits.insert(outfit_id.clone(), layout);

    /* validate */
    let _validated: CharacterPackJson = serde_json::from_value(profile.clone())
        .map_err(|error| format!("更新后的角色数据无效：{error}"))?;

    /* atomic write */
    let updated =
        serde_json::to_string_pretty(&profile).map_err(|error| format!("序列化失败：{error}"))?;
    write_text_atomic(&character_path, &updated)?;

    Ok(format!("{pack_id}/{outfit_id} 校准已保存"))
}
