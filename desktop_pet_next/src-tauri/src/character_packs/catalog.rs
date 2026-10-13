//! Registry and outfit/image discovery.
use super::models::*;
use super::paths::*;
use crate::runtime::config::DEFAULT_CHARACTER_PACK_ID;
use std::fs;
use std::path::Path;

#[tauri::command]
pub(crate) fn list_character_packs() -> Result<Vec<CharacterPackRegistryItem>, String> {
    let characters_dir = creator_kit_characters_dir()?;
    if !characters_dir.is_dir() {
        return Ok(Vec::new());
    }

    let mut packs = Vec::new();
    for entry in fs::read_dir(&characters_dir).map_err(|error| error.to_string())? {
        let Ok(entry) = entry else {
            continue;
        };
        let pack_dir = entry.path();
        if !pack_dir.is_dir() {
            continue;
        }

        let Some(raw_id) = pack_dir.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        let pack_id = sanitize_pack_id(raw_id);
        if pack_id.is_empty() {
            continue;
        }

        let character_path = pack_dir.join("character.json");
        if !character_path.is_file() {
            continue;
        }
        let Ok(raw_profile) = fs::read_to_string(&character_path) else {
            continue;
        };
        let Ok(profile) = serde_json::from_str::<serde_json::Value>(&raw_profile) else {
            continue;
        };
        let Ok(character) = serde_json::from_value::<CharacterPackJson>(profile.clone()) else {
            continue;
        };

        packs.push(registry_item(
            pack_id,
            &pack_dir,
            profile,
            character.asset_root(),
        ));
    }

    packs.sort_by(|a, b| {
        if a.id == DEFAULT_CHARACTER_PACK_ID {
            return std::cmp::Ordering::Less;
        }
        if b.id == DEFAULT_CHARACTER_PACK_ID {
            return std::cmp::Ordering::Greater;
        }
        let a_name = a
            .profile
            .pointer("/identity/name")
            .and_then(|value| value.as_str())
            .unwrap_or(a.id.as_str());
        let b_name = b
            .profile
            .pointer("/identity/name")
            .and_then(|value| value.as_str())
            .unwrap_or(b.id.as_str());
        a_name.cmp(b_name)
    });

    Ok(packs)
}

#[tauri::command]
pub(crate) fn list_pack_assets(pack_id: String) -> Result<Vec<CharacterPackOutfitAsset>, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    if !pack_dir.is_dir() {
        return Err(format!("角色包 {pack_id} 不存在。"));
    }
    let asset_root = read_pack_asset_root(&pack_dir)?;
    Ok(list_character_pack_outfits(&pack_dir, &asset_root))
}

pub(crate) fn has_any_emotion_image(characters_dir: &Path) -> bool {
    let Ok(outfits) = fs::read_dir(characters_dir) else {
        return false;
    };
    for outfit in outfits.flatten() {
        let path = outfit.path();
        if path.is_dir() && has_emotion_image_file(&path) {
            return true;
        }
    }
    false
}

pub(crate) fn list_character_pack_outfits(
    pack_dir: &Path,
    asset_root: &str,
) -> Vec<CharacterPackOutfitAsset> {
    let Ok(asset_root_dir) = safe_child_path(pack_dir, asset_root) else {
        return Vec::new();
    };
    let characters_dir = asset_root_dir.join("characters");
    let Ok(outfits) = fs::read_dir(characters_dir) else {
        return Vec::new();
    };

    let mut result = Vec::new();
    for outfit in outfits.flatten() {
        let outfit_dir = outfit.path();
        if !outfit_dir.is_dir() {
            continue;
        }
        let Some(outfit_id) = outfit_dir.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        let emotions = list_character_pack_emotions(&outfit_dir);
        result.push(CharacterPackOutfitAsset {
            id: outfit_id.to_string(),
            name: outfit_id.to_string(),
            emotions,
        });
    }

    result.sort_by(|a, b| a.id.cmp(&b.id));
    result
}

pub(crate) fn list_character_pack_emotions(outfit_dir: &Path) -> Vec<CharacterPackEmotionAsset> {
    let Ok(files) = fs::read_dir(outfit_dir) else {
        return Vec::new();
    };

    let mut emotions = Vec::new();
    for file in files.flatten() {
        let path = file.path();
        if !path.is_file() || !is_supported_character_image(&path) {
            continue;
        }
        let Some(stem) = path.file_stem().and_then(|value| value.to_str()) else {
            continue;
        };
        let size_bytes = fs::metadata(&path)
            .map(|metadata| metadata.len())
            .unwrap_or(0);
        emotions.push(CharacterPackEmotionAsset {
            id: stem.to_string(),
            name: stem.to_string(),
            path: path.to_string_lossy().to_string(),
            size_bytes,
        });
    }

    emotions.sort_by(|a, b| a.id.cmp(&b.id));
    emotions
}

pub(crate) fn has_emotion_image_file(outfit_dir: &Path) -> bool {
    let Ok(files) = fs::read_dir(outfit_dir) else {
        return false;
    };
    files.flatten().any(|file| {
        let path = file.path();
        path.is_file() && is_supported_character_image(&path)
    })
}

pub(crate) fn find_emotion_image(outfit_dir: &Path, emotion: &str) -> bool {
    let Ok(files) = fs::read_dir(outfit_dir) else {
        return false;
    };
    files.flatten().any(|file| {
        let path = file.path();
        let stem = path
            .file_stem()
            .and_then(|value| value.to_str())
            .unwrap_or("");
        path.is_file() && stem == emotion && is_supported_character_image(&path)
    })
}

pub(crate) fn is_supported_character_image(path: &Path) -> bool {
    matches!(
        path.extension()
            .and_then(|value| value.to_str())
            .unwrap_or("")
            .to_ascii_lowercase()
            .as_str(),
        "png" | "jpg" | "jpeg" | "webp"
    )
}

/// Project the same registry result for listing, edits and new packs.
pub(super) fn registry_item(
    pack_id: String,
    pack_dir: &Path,
    profile: serde_json::Value,
    asset_root: &str,
) -> CharacterPackRegistryItem {
    let outfits = list_character_pack_outfits(pack_dir, asset_root);
    let asset_count = outfits.iter().map(|outfit| outfit.emotions.len()).sum();
    let idle_blink_asset = idle_blink_asset_path(pack_dir, asset_root, &profile);
    CharacterPackRegistryItem {
        id: pack_id,
        source: pack_dir
            .join("character.json")
            .to_string_lossy()
            .to_string(),
        installed_path: pack_dir.to_string_lossy().to_string(),
        asset_count,
        profile,
        outfits,
        idle_blink_asset,
    }
}

/// Optional animation textures use the same containment checks as portraits.
pub(super) fn idle_blink_asset_path(
    pack_dir: &Path,
    asset_root: &str,
    profile: &serde_json::Value,
) -> Option<String> {
    let relative = profile.pointer("/appearance/idle_blink/asset")?.as_str()?;
    let root = safe_child_path(pack_dir, asset_root).ok()?;
    let path = safe_child_path(&root, relative).ok()?;
    if !path.is_file() || !is_supported_character_image(&path) {
        return None;
    }
    Some(path.to_string_lossy().to_string())
}
