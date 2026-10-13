//! Character voice profile selection and public notes.
use super::catalog::registry_item;
use super::models::*;
use super::paths::*;
use super::storage::write_text_atomic;
use std::fs;

#[tauri::command]
pub(crate) fn set_character_voice_profile(
    request: SetCharacterVoiceProfileRequest,
) -> Result<CharacterPackRegistryItem, String> {
    let pack_id = sanitize_pack_id(&request.pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    let provider = normalize_character_voice_provider(&request.provider)?;
    let profile_id = sanitize_voice_profile_id(&request.profile_id);
    if profile_id.is_empty() {
        return Err("声线 ID 不能为空。".to_string());
    }
    let notes = sanitize_voice_notes(request.notes.as_deref());

    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    if !pack_dir.is_dir() {
        return Err(format!("角色包 {pack_id} 不存在。"));
    }

    let character_path = pack_dir.join("character.json");
    if !character_path.is_file() {
        return Err(format!("{pack_id} 缺少 character.json。"));
    }

    let raw = fs::read_to_string(&character_path).map_err(|error| error.to_string())?;
    let mut profile: serde_json::Value =
        serde_json::from_str(&raw).map_err(|error| format!("character.json 无效：{error}"))?;
    let Some(obj) = profile.as_object_mut() else {
        return Err("character.json 根节点必须是对象。".to_string());
    };

    obj.insert(
        "voice".to_string(),
        serde_json::json!({
            "provider": provider,
            "profile_id": profile_id,
            "notes": notes,
        }),
    );

    let validated: CharacterPackJson = serde_json::from_value(profile.clone())
        .map_err(|error| format!("更新后的角色语音配置无效：{error}"))?;
    let updated =
        serde_json::to_string_pretty(&profile).map_err(|error| format!("序列化失败：{error}"))?;
    write_text_atomic(&character_path, &updated)?;

    Ok(registry_item(
        pack_id,
        &pack_dir,
        profile,
        validated.asset_root(),
    ))
}

#[tauri::command]
pub(crate) fn clear_character_voice_profile(
    pack_id: String,
) -> Result<CharacterPackRegistryItem, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }

    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    if !pack_dir.is_dir() {
        return Err(format!("角色包 {pack_id} 不存在。"));
    }

    let character_path = pack_dir.join("character.json");
    if !character_path.is_file() {
        return Err(format!("{pack_id} 缺少 character.json。"));
    }

    let raw = fs::read_to_string(&character_path).map_err(|error| error.to_string())?;
    let mut profile: serde_json::Value =
        serde_json::from_str(&raw).map_err(|error| format!("character.json 无效：{error}"))?;
    let Some(obj) = profile.as_object_mut() else {
        return Err("character.json 根节点必须是对象。".to_string());
    };

    obj.insert(
        "voice".to_string(),
        serde_json::json!({
            "provider": "",
            "profile_id": "",
            "notes": "",
        }),
    );

    let validated: CharacterPackJson = serde_json::from_value(profile.clone())
        .map_err(|error| format!("更新后的角色语音配置无效：{error}"))?;
    let updated =
        serde_json::to_string_pretty(&profile).map_err(|error| format!("序列化失败：{error}"))?;
    write_text_atomic(&character_path, &updated)?;

    Ok(registry_item(
        pack_id,
        &pack_dir,
        profile,
        validated.asset_root(),
    ))
}

pub(crate) fn sanitize_voice_profile_id(value: &str) -> String {
    let safe = value
        .trim()
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '_' || ch == '-' || ch == '.' {
                ch
            } else {
                '_'
            }
        })
        .collect::<String>()
        .trim_matches(|ch| ch == '_' || ch == '.' || ch == '-')
        .to_string();
    safe.chars().take(120).collect()
}

pub(crate) fn normalize_character_voice_provider(value: &str) -> Result<String, String> {
    let raw = value.trim().to_ascii_lowercase();
    match raw.as_str() {
        "" | "gpt_sovits" | "gpt-sovits" | "gptsovits" | "provider.tts.gpt_sovits.local" => {
            Ok("gpt_sovits".to_string())
        }
        "irodori" | "irodori_tts" | "irodori-tts" | "provider.tts.irodori.local" => {
            Ok("irodori".to_string())
        }
        _ => Err("请选择 GPT-SoVITS 或 Irodori-TTS 声线档案。".to_string()),
    }
}

pub(crate) fn sanitize_voice_notes(value: Option<&str>) -> String {
    let text = value.unwrap_or("").replace(['\r', '\n'], " ");
    let text = text.trim();
    if text.is_empty() {
        return "由控制中心设置。".to_string();
    }
    let lowered = text.to_ascii_lowercase();
    if lowered.contains("api_key")
        || lowered.contains("password")
        || lowered.contains("secret")
        || lowered.contains("token")
        || text.contains("://")
    {
        return "由控制中心设置。".to_string();
    }
    text.chars().take(160).collect()
}
