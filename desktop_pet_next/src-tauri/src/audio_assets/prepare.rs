use crate::runtime::files::copy_to_staging;
use std::{fs, path::PathBuf};
use tauri::AppHandle;

use crate::audio_assets::limits::{MAX_AUDIO_FILE_BYTES, SUPPORTED_AUDIO_EXTENSIONS};
use crate::audio_assets::lyrics::read_lyric_asset;
use crate::audio_assets::models::PreparedAudioAsset;
use crate::runtime::paths::desktop_runtime_cache_dir;

#[tauri::command]
pub(crate) async fn prepare_audio_asset(
    _app: AppHandle,
    path: String,
    lyric_path: Option<String>,
) -> Result<PreparedAudioAsset, String> {
    tauri::async_runtime::spawn_blocking(move || prepare_audio_asset_blocking(path, lyric_path))
        .await
        .map_err(|error| format!("音频准备任务失败：{error}"))?
}

pub(crate) fn prepare_audio_asset_blocking(
    path: String,
    lyric_path: Option<String>,
) -> Result<PreparedAudioAsset, String> {
    prepare_audio_asset_with_directory(path, lyric_path, || {
        desktop_runtime_cache_dir("attachments/audio")
    })
}

pub(super) fn prepare_audio_asset_with_directory(
    path: String,
    lyric_path: Option<String>,
    directory: impl FnOnce() -> Result<PathBuf, String>,
) -> Result<PreparedAudioAsset, String> {
    let source_path = PathBuf::from(path.trim());
    if !source_path.is_file() {
        return Err("拖入的不是可播放文件。".to_string());
    }

    let extension = source_path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase();
    if !SUPPORTED_AUDIO_EXTENSIONS.contains(&extension.as_str()) {
        return Err("暂时只支持 mp3 / wav / flac / ogg / m4a / aac / opus / webm。".to_string());
    }

    let metadata = fs::metadata(&source_path).map_err(|error| error.to_string())?;
    if metadata.len() == 0 {
        return Err("这个音频文件是空的。".to_string());
    }
    if metadata.len() > MAX_AUDIO_FILE_BYTES {
        return Err("音频文件有点太大了，先控制在 300MB 以内吧。".to_string());
    }

    let cache_dir = directory()?;
    let (cached_path, size_bytes) = copy_to_staging(
        &source_path,
        &cache_dir,
        &extension,
        Some(MAX_AUDIO_FILE_BYTES),
    )?;
    if size_bytes == 0 {
        let _ = fs::remove_file(&cached_path);
        return Err("这个音频文件是空的。".to_string());
    }

    let file_name = source_path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("audio")
        .to_string();
    let display_name = source_path
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or(file_name.as_str())
        .to_string();
    let lyric = read_lyric_asset(&source_path, lyric_path.as_deref());

    Ok(PreparedAudioAsset {
        original_path: source_path.to_string_lossy().to_string(),
        cached_path: cached_path.to_string_lossy().to_string(),
        file_name,
        display_name,
        extension,
        size_bytes,
        lyric_file_name: lyric.as_ref().map(|item| item.0.clone()),
        lyric_text: lyric.map(|item| item.1),
    })
}
