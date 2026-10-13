//! Image signatures, metadata hints and existing format paths.
use super::paths::safe_child_path;
use crate::character_packs::constants::SUPPORTED_PORTRAIT_EXTENSIONS;
use std::path::{Path, PathBuf};

/// Detect image file extension from magic bytes.
/// Supported: png, jpg/jpeg, webp.
pub(crate) fn detect_image_extension(bytes: &[u8]) -> Result<String, String> {
    if bytes.len() < 12 {
        return Err("图片数据不完整。".to_string());
    }

    if bytes[0..4] == [0x89, b'P', b'N', b'G'] {
        return Ok("png".to_string());
    }
    if bytes[0..2] == [0xFF, 0xD8] {
        return Ok("jpg".to_string());
    }
    if bytes.len() >= 12
        && bytes[0..4] == [b'R', b'I', b'F', b'F']
        && bytes[8..12] == [b'W', b'E', b'B', b'P']
    {
        return Ok("webp".to_string());
    }
    Err("不支持的图片格式。仅支持 PNG、JPEG、WebP。".to_string())
}

pub(crate) fn resolve_generated_portrait_extension(
    bytes: &[u8],
    extension: Option<String>,
    mime_type: Option<String>,
) -> Result<String, String> {
    let detected = detect_image_extension(bytes)?;
    let extension_hint = parse_portrait_extension_hint(extension.as_deref())?;
    let mime_hint = parse_portrait_mime_hint(mime_type.as_deref())?;

    if let (Some(ext), Some(mime_ext)) = (&extension_hint, &mime_hint) {
        if ext != mime_ext {
            return Err("extension 与 mimeType 指向的图片格式不一致。".to_string());
        }
    }

    if let Some(hint) = extension_hint.or(mime_hint) {
        if hint != detected {
            return Err("图片数据格式与 extension/mimeType 不一致。".to_string());
        }
    }

    Ok(detected)
}

pub(crate) fn parse_portrait_extension_hint(value: Option<&str>) -> Result<Option<String>, String> {
    let Some(raw) = value.map(str::trim).filter(|value| !value.is_empty()) else {
        return Ok(None);
    };
    normalize_portrait_extension(raw)
        .map(Some)
        .ok_or_else(|| "不支持的立绘扩展名。仅支持 png、jpg、jpeg、webp。".to_string())
}

pub(crate) fn parse_portrait_mime_hint(value: Option<&str>) -> Result<Option<String>, String> {
    let Some(raw) = value.map(str::trim).filter(|value| !value.is_empty()) else {
        return Ok(None);
    };
    let media_type = raw
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase();
    let extension = match media_type.as_str() {
        "image/png" => Some("png"),
        "image/jpeg" | "image/jpg" => Some("jpg"),
        "image/webp" => Some("webp"),
        _ => None,
    };
    extension
        .map(|value| Some(value.to_string()))
        .ok_or_else(|| {
            "不支持的立绘 MIME 类型。仅支持 image/png、image/jpeg、image/webp。".to_string()
        })
}

pub(crate) fn normalize_portrait_extension(value: &str) -> Option<String> {
    let normalized = value.trim().trim_start_matches('.').to_ascii_lowercase();
    match normalized.as_str() {
        "png" => Some("png".to_string()),
        "jpg" | "jpeg" => Some("jpg".to_string()),
        "webp" => Some("webp".to_string()),
        _ => None,
    }
}

pub(crate) fn existing_portrait_image_paths(
    outfit_dir: &Path,
    emotion: &str,
) -> Result<Vec<PathBuf>, String> {
    SUPPORTED_PORTRAIT_EXTENSIONS
        .iter()
        .map(|extension| safe_child_path(outfit_dir, &format!("{emotion}.{extension}")))
        .collect()
}
