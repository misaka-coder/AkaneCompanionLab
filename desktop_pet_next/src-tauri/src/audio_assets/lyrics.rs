use crate::runtime::files::read_file_bounded;
use std::{fs, path::PathBuf};

use crate::audio_assets::limits::MAX_LYRIC_FILE_BYTES;

pub(crate) fn read_lyric_asset(
    audio_path: &PathBuf,
    explicit_path: Option<&str>,
) -> Option<(String, String)> {
    let path = explicit_path
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .or_else(|| find_adjacent_lyric_path(audio_path))?;
    if !path.is_file() {
        return None;
    }
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase();
    if extension != "lrc" {
        return None;
    }
    let metadata = fs::metadata(&path).ok()?;
    if metadata.len() == 0 || metadata.len() > MAX_LYRIC_FILE_BYTES {
        return None;
    }
    let bytes = read_file_bounded(&path, MAX_LYRIC_FILE_BYTES).ok()?;
    let text = String::from_utf8_lossy(&bytes)
        .trim_start_matches('\u{feff}')
        .to_string();
    if text.trim().is_empty() {
        return None;
    }
    let file_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("lyrics.lrc")
        .to_string();
    Some((file_name, text))
}

pub(crate) fn find_adjacent_lyric_path(audio_path: &PathBuf) -> Option<PathBuf> {
    let parent = audio_path.parent()?;
    let stem = audio_path.file_stem()?.to_str()?;
    for entry in fs::read_dir(parent).ok()? {
        let Ok(entry) = entry else {
            continue;
        };
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let path_stem = path
            .file_stem()
            .and_then(|value| value.to_str())
            .unwrap_or("");
        let extension = path
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or("");
        if path_stem.eq_ignore_ascii_case(stem) && extension.eq_ignore_ascii_case("lrc") {
            return Some(path);
        }
    }
    None
}
