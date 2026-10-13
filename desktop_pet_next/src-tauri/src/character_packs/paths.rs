//! Character-pack locations, identifiers and contained resource paths.
use super::models::CharacterPackJson;
use crate::runtime::paths::akane_data_root;
use std::{
    fs,
    path::{Path, PathBuf},
};

/// Read the `asset_root` from a pack's character.json. Falls back to "assets".
pub(crate) fn read_pack_asset_root(pack_dir: &Path) -> Result<String, String> {
    let character_path = pack_dir.join("character.json");
    if !character_path.is_file() {
        return Err(format!("{} 缺少 character.json。", pack_dir.display()));
    }
    let raw = fs::read_to_string(&character_path).map_err(|error| error.to_string())?;
    let character: CharacterPackJson =
        serde_json::from_str(&raw).map_err(|error| format!("character.json 无效：{error}"))?;
    Ok(character.asset_root().to_string())
}

pub(crate) fn pack_characters_dir(pack_dir: &Path) -> Result<(String, PathBuf), String> {
    let asset_root = read_pack_asset_root(pack_dir)?;
    let asset_root_dir = safe_child_path(pack_dir, &asset_root)?;
    let characters_dir = safe_child_path(&asset_root_dir, "characters")?;
    Ok((asset_root, characters_dir))
}

pub(crate) fn sanitize_asset_id(value: &str) -> String {
    // Match JS sanitizeAssetId: replace path-traversal chars and whitespace with _,
    // strip leading/trailing dots/underscores, but preserve Unicode (Chinese/Japanese).
    let clean: String = value
        .trim()
        .chars()
        .map(|ch| match ch {
            '\\' | '/' | ':' | '*' | '?' | '"' | '<' | '>' | '|' => '_',
            ch if ch.is_whitespace() => '_',
            _ => ch,
        })
        .collect();
    clean.trim_matches(|ch| ch == '_' || ch == '.').to_string()
}

pub(crate) fn sanitize_context_library_folder(value: &str) -> String {
    let clean: String = value
        .trim()
        .chars()
        .map(|ch| match ch {
            '/' | '\\' | '\0' | ':' | '*' | '?' | '"' | '<' | '>' | '|' => '_',
            ch if ch.is_whitespace() => '_',
            _ => ch,
        })
        .collect();
    clean.trim_matches(|ch| ch == '_' || ch == '.').to_string()
}

pub(crate) fn creator_kit_characters_dir() -> Result<PathBuf, String> {
    let characters_dir = akane_data_root()?.join("characters");
    fs::create_dir_all(&characters_dir).map_err(|error| error.to_string())?;
    Ok(characters_dir)
}

pub(crate) fn safe_child_path(base: &Path, relative: &str) -> Result<PathBuf, String> {
    let normalized = normalize_zip_path(relative)?;
    if normalized.is_empty() {
        return Err(format!("角色包内包含不安全路径：{relative}"));
    }
    let target = base.join(normalized.replace('/', std::path::MAIN_SEPARATOR_STR));
    let resolved_base = resolve_existing_ancestor(base)?;
    let resolved_target = resolve_existing_ancestor(&target)?;
    if resolved_target == resolved_base || !resolved_target.starts_with(&resolved_base) {
        return Err(format!("角色包内包含不安全路径：{relative}"));
    }
    Ok(target)
}

// Resolve existing ancestors too: missing descendants must not bypass a
// junction, or compare a raw Windows path against a verbatim canonical path.
fn resolve_existing_ancestor(path: &Path) -> Result<PathBuf, String> {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .map_err(|error| error.to_string())?
            .join(path)
    };
    let mut ancestor = absolute.as_path();
    let mut missing = Vec::new();
    let mut resolved = loop {
        match ancestor.canonicalize() {
            Ok(path) => break path,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                if fs::symlink_metadata(ancestor).is_ok() {
                    return Err(error.to_string());
                }
                let name = ancestor.file_name().ok_or_else(|| error.to_string())?;
                missing.push(name.to_os_string());
                ancestor = ancestor.parent().ok_or_else(|| error.to_string())?;
            }
            Err(error) => return Err(error.to_string()),
        }
    };
    for part in missing.iter().rev() {
        resolved.push(part);
    }
    Ok(resolved)
}

pub(crate) fn assert_safe_remove_target(base: &Path, target: &Path) -> Result<(), String> {
    let base = base.canonicalize().map_err(|error| error.to_string())?;
    let target = target.canonicalize().map_err(|error| error.to_string())?;
    if target == base || !target.starts_with(&base) {
        return Err("拒绝覆盖不安全的目标目录。".to_string());
    }
    Ok(())
}

pub(crate) fn sanitize_pack_id(value: &str) -> String {
    let clean = value
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
        .trim_matches(|ch| ch == '_' || ch == '.')
        .to_string();
    if clean == "." || clean == ".." {
        String::new()
    } else {
        clean
    }
}

pub(crate) fn normalize_zip_path(value: &str) -> Result<String, String> {
    let raw = value.replace('\\', "/");
    if raw.starts_with('/') || raw.contains(':') || raw.chars().any(char::is_control) {
        return Err(format!("角色包内包含不安全路径：{value}"));
    }
    let mut parts = Vec::new();
    for part in raw.split('/') {
        if part.is_empty() || part == "." {
            continue;
        }
        if part == ".." {
            return Err(format!("角色包内包含不安全路径：{value}"));
        }
        parts.push(part);
    }
    Ok(parts.join("/"))
}
