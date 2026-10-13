//! Create and edit character identity, persona and dialogue.
use super::catalog::registry_item;
use super::models::*;
use super::paths::*;
use super::profile::require_optional_object;
use super::storage::write_text_atomic;
use crate::character_packs::constants::CHARACTER_PACK_TEMPLATE_JSON;
use std::{fs, path::PathBuf};

#[tauri::command]
pub(crate) fn save_character_pack(
    request: SaveCharacterPackRequest,
) -> Result<CharacterPackRegistryItem, String> {
    save_character_pack_with_directory(request, creator_kit_characters_dir)
}

pub(super) fn save_character_pack_with_directory(
    request: SaveCharacterPackRequest,
    directory: impl FnOnce() -> Result<PathBuf, String>,
) -> Result<CharacterPackRegistryItem, String> {
    let pack_id = sanitize_pack_id(&request.pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }

    let characters_dir = directory()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    if !pack_dir.is_dir() {
        return Err(format!("角色包 {pack_id} 不存在。"));
    }

    let character_path = pack_dir.join("character.json");
    if !character_path.is_file() {
        return Err(format!("{pack_id} 缺少 character.json。"));
    }

    /* read existing character.json */
    let raw = fs::read_to_string(&character_path).map_err(|error| error.to_string())?;
    let mut profile: serde_json::Value =
        serde_json::from_str(&raw).map_err(|error| format!("character.json 无效：{error}"))?;
    let previous_template = profile
        .pointer("/identity/name")
        .and_then(|value| value.as_str())
        .zip(
            profile
                .pointer("/identity/user_title")
                .and_then(|value| value.as_str()),
        )
        .map(|(name, title)| persona_md_text(name, title));

    require_optional_object(&request.identity, "identity")?;
    require_optional_object(&request.persona_form, "persona_form")?;
    require_optional_object(&request.dialogue, "dialogue")?;

    let Some(obj) = profile.as_object_mut() else {
        return Err("character.json 根节点必须是对象。".to_string());
    };

    let identity = obj
        .entry("identity".to_string())
        .or_insert_with(|| serde_json::json!({}));
    if !identity.is_object() {
        return Err("identity 必须是对象。".to_string());
    }
    deep_merge_json(identity, &request.identity);

    let persona_form = obj
        .entry("persona_form".to_string())
        .or_insert_with(|| serde_json::json!({}));
    if !persona_form.is_object() {
        return Err("persona_form 必须是对象。".to_string());
    }
    deep_merge_json(persona_form, &request.persona_form);

    if !request.dialogue.is_null() {
        let dialogue = obj
            .entry("dialogue".to_string())
            .or_insert_with(|| serde_json::json!({}));
        if !dialogue.is_object() {
            return Err("dialogue 必须是对象。".to_string());
        }
        deep_merge_json(dialogue, &request.dialogue);
    }

    /* validate merged result still parses as CharacterPackJson */
    let validated: CharacterPackJson = serde_json::from_value(profile.clone())
        .map_err(|error| format!("合并后的角色数据无效：{error}"))?;

    /* atomic write: temp file then rename to avoid corruption on crash */
    let updated = serde_json::to_string_pretty(&profile)
        .map_err(|error| format!("序列化角色数据失败：{error}"))?;
    write_text_atomic(&character_path, &updated)?;

    /* Refresh generated notes only while their complete content is unchanged. */
    let persona_path = pack_dir.join("persona.md");
    let should_write = match fs::read_to_string(&persona_path) {
        Ok(existing) => previous_template
            .as_deref()
            .map(|template| existing.replace("\r\n", "\n") == template)
            .unwrap_or(false),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => true,
        Err(error) => return Err(format!("读取 persona.md 失败：{error}")),
    };
    if should_write {
        write_text_atomic(
            &persona_path,
            &persona_md_text(&validated.identity.name, &validated.identity.user_title),
        )?;
    }

    /* return updated registry item – reuse validated struct */
    Ok(registry_item(
        pack_id,
        &pack_dir,
        profile,
        validated.asset_root(),
    ))
}

#[tauri::command]
pub(crate) fn create_character_pack(
    request: CreateCharacterPackRequest,
) -> Result<CharacterPackRegistryItem, String> {
    create_character_pack_with_directory(request, creator_kit_characters_dir)
}

pub(super) fn create_character_pack_with_directory(
    request: CreateCharacterPackRequest,
    directory: impl FnOnce() -> Result<PathBuf, String>,
) -> Result<CharacterPackRegistryItem, String> {
    let pack_id = sanitize_pack_id(&request.pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    let name = request.name.trim().to_string();
    if name.is_empty() {
        return Err("角色名称不能为空。".to_string());
    }
    let app_name = if request.app_name.trim().is_empty() {
        format!("{name} Pet")
    } else {
        request.app_name.trim().to_string()
    };
    let user_title = if request.user_title.trim().is_empty() {
        "主人".to_string()
    } else {
        request.user_title.trim().to_string()
    };
    let default_outfit = "default".to_string();

    let characters_dir = directory()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    if pack_dir.exists() {
        return Err(format!("角色包 {pack_id} 已存在。"));
    }

    /* load the build-time embedded template and fill in user-provided fields */
    let template_raw = CHARACTER_PACK_TEMPLATE_JSON;
    let mut character_json: serde_json::Value =
        serde_json::from_str(template_raw).map_err(|error| format!("模板 JSON 无效：{error}"))?;

    if let Some(obj) = character_json.as_object_mut() {
        if let Some(identity) = obj.get_mut("identity").and_then(|v| v.as_object_mut()) {
            identity.insert("id".to_string(), serde_json::Value::String(pack_id.clone()));
            identity.insert("name".to_string(), serde_json::Value::String(name.clone()));
            identity.insert(
                "app_name".to_string(),
                serde_json::Value::String(app_name.clone()),
            );
            identity.insert(
                "user_title".to_string(),
                serde_json::Value::String(user_title.clone()),
            );
            identity.insert(
                "relationship".to_string(),
                serde_json::Value::String(format!(
                    "住在桌面边上的 {name}，会按自己的性格陪伴和回应 {user_title}。"
                )),
            );
        }
        if let Some(persona) = obj.get_mut("persona_form").and_then(|v| v.as_object_mut()) {
            persona.insert(
                "proactive_style".to_string(),
                serde_json::Value::String(format!(
                    "{user_title}暂时没有说话时，按角色风格轻轻搭一句话。"
                )),
            );
        }
        if let Some(dialogue) = obj.get_mut("dialogue").and_then(|v| v.as_object_mut()) {
            dialogue.insert(
                "input_placeholder".to_string(),
                serde_json::Value::String(format!("和 {name} 说点什么……")),
            );
            dialogue.insert(
                "session_display_title".to_string(),
                serde_json::Value::String(format!("{name} 桌宠对话")),
            );
            dialogue.insert(
                "tts_test_text".to_string(),
                serde_json::Value::String(format!("{name}：语音播放测试。")),
            );
            dialogue.insert("proactive_wake_prompt".to_string(), serde_json::Value::String(
                format!("{user_title}暂时没有说话。你像坐在旁边陪伴一样，轻轻搭一句自然的话。桌面线索只当背景，不要刻意围绕窗口标题发挥。")
            ));
        }
    }

    /* create directory structure */
    let assets_chars_dir = pack_dir
        .join("assets")
        .join("characters")
        .join(&default_outfit);
    fs::create_dir_all(&assets_chars_dir).map_err(|error| error.to_string())?;

    let character_json_str = serde_json::to_string_pretty(&character_json)
        .map_err(|error| format!("序列化 character.json 失败：{error}"))?;
    write_text_atomic(&pack_dir.join("character.json"), &character_json_str)?;

    write_text_atomic(
        &pack_dir.join("persona.md"),
        &persona_md_text(&name, &user_title),
    )?;

    Ok(registry_item(pack_id, &pack_dir, character_json, "assets"))
}

/// Generate the default `persona.md` content for a character pack.
/// Generated notes may be refreshed until the creator edits their content.
pub(crate) fn persona_md_text(name: &str, user_title: &str) -> String {
    format!(
        "# {name} Persona\n\n\
         ## Voice\n\n\
         Write how {name} speaks, what tone they use, and how they address {user_title}.\n\n\
         ## Relationship Boundary\n\n\
         Describe the relationship, allowed topics, and things the character should avoid.\n\n\
         ## World Notes\n\n\
         Add background, preferences, habits, and repeated motifs here.\n\n\
         The desktop-pet backend reads this file for the selected character pack. \
         Keep the notes concise and usable as prompt reference.\n",
    )
}

/// Recursively merge `source` into `target` for matching JSON objects.
/// Arrays and primitives are replaced; objects are merged key-by-key.
pub(crate) fn deep_merge_json(target: &mut serde_json::Value, source: &serde_json::Value) {
    if let (Some(target_map), Some(source_map)) = (target.as_object_mut(), source.as_object()) {
        for (key, source_value) in source_map {
            match target_map.get_mut(key) {
                Some(target_value) if target_value.is_object() && source_value.is_object() => {
                    deep_merge_json(target_value, source_value);
                }
                _ => {
                    target_map.insert(key.clone(), source_value.clone());
                }
            }
        }
    }
}
