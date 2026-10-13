//! Validate imported configuration before replacing an installed pack.
use super::catalog::{find_emotion_image, has_any_emotion_image};
use super::models::{CharacterPackJson, CharacterPackValidation};
use super::paths::safe_child_path;
use super::profile::{require_optional_object, require_text};
use std::{fs, path::Path};

pub(crate) fn validate_imported_character_pack(
    pack_dir: &Path,
) -> Result<CharacterPackValidation, String> {
    let character_path = pack_dir.join("character.json");
    let raw = fs::read_to_string(&character_path).map_err(|error| error.to_string())?;
    let character: CharacterPackJson =
        serde_json::from_str(&raw).map_err(|error| format!("character.json 无效：{error}"))?;
    let mut warnings = Vec::new();

    require_text(&character.schema_version, "schema_version")?;
    if character.schema_version != "akane.character.v0.1"
        && character.schema_version != "akane.character.v0.2"
    {
        return Err(
            "schema_version 需要是 akane.character.v0.1 或 akane.character.v0.2。".to_string(),
        );
    }
    require_text(&character.identity.id, "identity.id")?;
    require_text(&character.identity.name, "identity.name")?;
    require_text(&character.identity.app_name, "identity.app_name")?;
    require_text(&character.identity.user_title, "identity.user_title")?;
    let _self_reference = character.identity.self_reference.trim();
    let _relationship = character.identity.relationship.trim();
    require_optional_object(&character.persona_form, "persona_form")?;
    require_optional_object(&character.emotion_aliases, "emotion_aliases")?;
    require_optional_object(&character.layout, "layout")?;
    require_optional_object(&character.voice, "voice")?;
    require_text(
        &character.appearance.default_outfit,
        "appearance.default_outfit",
    )?;
    require_text(
        &character.appearance.default_emotion,
        "appearance.default_emotion",
    )?;
    if character.dialogue.local_click_lines.is_empty() {
        return Err("dialogue.local_click_lines 至少需要一条台词。".to_string());
    }
    for (index, line) in character.dialogue.local_click_lines.iter().enumerate() {
        require_text(
            &line.text,
            &format!("dialogue.local_click_lines[{index}].text"),
        )?;
        require_text(
            &line.emotion,
            &format!("dialogue.local_click_lines[{index}].emotion"),
        )?;
    }

    let asset_root = character
        .assets
        .asset_root
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or("assets");
    let asset_root_dir = safe_child_path(pack_dir, asset_root)?;
    let characters_dir = safe_child_path(&asset_root_dir, "characters")?;
    if characters_dir.is_dir() {
        let default_outfit_dir =
            safe_child_path(&characters_dir, &character.appearance.default_outfit)?;
        let default_emotion_found = default_outfit_dir.is_dir()
            && find_emotion_image(&default_outfit_dir, &character.appearance.default_emotion);
        if !default_emotion_found && has_any_emotion_image(&characters_dir) {
            return Err("默认服装或默认表情缺少对应图片。".to_string());
        }
    } else {
        warnings.push("未找到 assets/characters，当前运行会使用内置立绘兜底。".to_string());
    }

    for required in &character.appearance.required_emotions {
        if required.trim().is_empty() {
            return Err("appearance.required_emotions 不能包含空值。".to_string());
        }
    }

    Ok(CharacterPackValidation {
        character_id: character.identity.id,
        character_name: character.identity.name,
        warnings,
    })
}
