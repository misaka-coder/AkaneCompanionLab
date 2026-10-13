//! Decode bounded portable archives using the existing ZIP library.
use super::{models::ZipEntry, paths::normalize_zip_path};
use crate::character_packs::constants::MAX_CHARACTER_PACK_ZIP_BYTES;
use std::{
    collections::HashSet,
    io::{Cursor, Read},
};

pub(super) fn read_character_archive(bytes: &[u8]) -> Result<Vec<ZipEntry>, String> {
    read_character_archive_with_limit(bytes, MAX_CHARACTER_PACK_ZIP_BYTES)
}

pub(super) fn read_character_archive_with_limit(
    bytes: &[u8],
    expanded_limit: usize,
) -> Result<Vec<ZipEntry>, String> {
    if bytes.len() < 22 {
        return Err("zip 文件不完整。".into());
    }
    if bytes.len() > MAX_CHARACTER_PACK_ZIP_BYTES {
        return Err("角色包 zip 暂时请控制在 300MB 以内。".into());
    }
    let mut archive = zip::ZipArchive::new(Cursor::new(bytes))
        .map_err(|error| format!("zip 目录无效：{error}"))?;
    if archive.len() > u16::MAX as usize {
        return Err("角色包 zip 的条目数量过多。".into());
    }
    let mut entries = Vec::with_capacity(archive.len());
    let mut seen = HashSet::new();
    let mut expanded = 0usize;
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index).map_err(|error| match error {
            zip::result::ZipError::UnsupportedArchive(reason)
                if reason == zip::result::ZipError::PASSWORD_REQUIRED =>
            {
                "暂不支持加密 zip。".to_string()
            }
            _ => format!("读取 zip 条目失败：{error}"),
        })?;
        if !matches!(
            entry.compression(),
            zip::CompressionMethod::Stored | zip::CompressionMethod::Deflated
        ) {
            return Err("角色包 zip 需要使用 Stored 或 Deflate 压缩。".into());
        }
        if entry.unix_mode().map(|mode| mode & 0o170000) == Some(0o120000) {
            return Err("角色包 zip 不能包含符号链接。".into());
        }
        let raw_name = std::str::from_utf8(entry.name_raw())
            .map_err(|_| "zip 文件名需要是 UTF-8。".to_string())?;
        let normalized = normalize_zip_path(raw_name)?;
        if normalized.is_empty() {
            return Err("zip 条目名称为空。".into());
        }
        if !seen.insert(normalized.to_lowercase()) {
            return Err(format!("zip 包含重复路径：{normalized}"));
        }
        let directory = entry.is_dir();
        let declared = entry.size();
        let remaining = expanded_limit.saturating_sub(expanded);
        if declared > remaining as u64 {
            return Err("角色包解压后的内容过大。".into());
        }
        let mut data = Vec::new();
        (&mut entry)
            .take(remaining as u64 + 1)
            .read_to_end(&mut data)
            .map_err(|error| format!("zip 条目内容无效：{error}"))?;
        if data.len() > remaining || data.len() as u64 != declared {
            return Err("zip 条目大小不匹配或内容过大。".into());
        }
        expanded += data.len();
        let name = if directory {
            format!("{normalized}/")
        } else {
            normalized
        };
        entries.push(ZipEntry { name, data });
    }
    Ok(entries)
}

pub(super) fn detect_character_pack_root(entries: &[ZipEntry]) -> Result<String, String> {
    let matches: Vec<_> = entries
        .iter()
        .filter(|entry| entry.name.rsplit('/').next() == Some("character.json"))
        .map(|entry| {
            entry
                .name
                .strip_suffix("character.json")
                .unwrap()
                .trim_end_matches('/')
                .to_string()
        })
        .collect();
    match matches.len() {
        0 => Err("zip 中没有 character.json。".into()),
        1 => Ok(matches[0].clone()),
        _ => Err("zip 中包含多个 character.json，请一次导入一个角色包。".into()),
    }
}
