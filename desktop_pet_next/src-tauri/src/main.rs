#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    collections::HashMap,
    fs,
    path::{Path, PathBuf},
    process::Command,
    sync::{Mutex, OnceLock},
};

use serde::{Deserialize, Serialize};
use tauri::{
    AppHandle, LogicalSize, Manager, PhysicalPosition, PhysicalSize, Position, Size, WebviewUrl,
    WebviewWindowBuilder, Window,
};
#[cfg(windows)]
use windows::core::{BOOL, PWSTR};
#[cfg(windows)]
use windows::Win32::{
    Foundation::{CloseHandle, HWND, LPARAM, LRESULT, RECT, WPARAM},
    System::Threading::{
        OpenProcess, QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION,
    },
    UI::{
        Shell::{DefSubclassProc, RemoveWindowSubclass, SetWindowSubclass},
        WindowsAndMessaging::{
            EnumChildWindows, GetForegroundWindow, GetWindowRect, GetWindowTextLengthW,
            GetWindowTextW, GetWindowThreadProcessId, HTCLIENT, HTTRANSPARENT, WM_NCDESTROY,
            WM_NCHITTEST,
        },
    },
};

const STATE_FILE: &str = "pet_state.json";
const BASE_WIDTH: f64 = 340.0;
const BASE_HEIGHT: f64 = 560.0;
const DEFAULT_BACKEND_URL: &str = "http://127.0.0.1:9999";
const DEFAULT_PROFILE_USER_ID: &str = "master";
const DEFAULT_CHARACTER_PACK_ID: &str = "akane_sample";
const DEFAULT_OUTFIT: &str = "猫娘";
const DEFAULT_EMOTION: &str = "正常";
const MAX_AUDIO_FILE_BYTES: u64 = 300 * 1024 * 1024;
const MAX_LYRIC_FILE_BYTES: u64 = 512 * 1024;
const MAX_CHARACTER_PACK_ZIP_BYTES: usize = 300 * 1024 * 1024;
const SUPPORTED_AUDIO_EXTENSIONS: &[&str] = &[
    "mp3", "wav", "flac", "ogg", "oga", "m4a", "aac", "opus", "webm",
];

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
#[serde(rename_all = "camelCase")]
struct CharacterRuntimeState {
    version: u32,
    character_pack_id: String,
    session_id: String,
    outfit: String,
    current_emotion: String,
    x: Option<i32>,
    y: Option<i32>,
    width: Option<u32>,
    height: Option<u32>,
    scale: f64,
    opacity: f64,
    updated_at: u64,
}

impl Default for CharacterRuntimeState {
    fn default() -> Self {
        Self {
            version: 1,
            character_pack_id: String::new(),
            session_id: String::new(),
            outfit: String::new(),
            current_emotion: String::new(),
            x: None,
            y: None,
            width: None,
            height: None,
            scale: 1.0,
            opacity: 1.0,
            updated_at: 0,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
#[serde(rename_all = "camelCase")]
struct PetState {
    x: Option<i32>,
    y: Option<i32>,
    width: Option<u32>,
    height: Option<u32>,
    scale: f64,
    opacity: f64,
    skip_taskbar: bool,
    always_on_top: bool,
    click_through: bool,
    backend_url: String,
    profile_user_id: String,
    #[serde(default = "default_character_pack_id")]
    character_pack_id: String,
    characters: HashMap<String, CharacterRuntimeState>,
    session_id: String,
    outfit: String,
    current_emotion: String,
    #[serde(default = "default_restore_latest_on_startup")]
    restore_latest_on_startup: bool,
    #[serde(default)]
    voice_enabled: bool,
    #[serde(default = "default_voice_input_enabled")]
    voice_input_enabled: bool,
    #[serde(default = "default_voice_volume")]
    voice_volume: f64,
    #[serde(default = "default_desktop_context_enabled")]
    desktop_context_enabled: bool,
    #[serde(default)]
    clipboard_context_enabled: bool,
    #[serde(default)]
    screen_vision_enabled: bool,
    #[serde(default = "default_screen_vision_mode")]
    screen_vision_mode: String,
    #[serde(default)]
    proactive_wake_enabled: bool,
    #[serde(default = "default_proactive_wake_interval_sec")]
    proactive_wake_interval_sec: u32,
    #[serde(default = "default_screen_vision_interval_sec")]
    screen_vision_interval_sec: u32,
    #[serde(default = "default_screen_vision_frame_count")]
    screen_vision_frame_count: u32,
    #[serde(default = "default_hit_test_enabled")]
    hit_test_enabled: bool,
    #[serde(default)]
    hitbox_overlay: bool,
}

impl Default for PetState {
    fn default() -> Self {
        Self {
            x: None,
            y: None,
            width: None,
            height: None,
            scale: 1.0,
            opacity: 1.0,
            skip_taskbar: true,
            always_on_top: true,
            click_through: false,
            backend_url: DEFAULT_BACKEND_URL.to_string(),
            profile_user_id: DEFAULT_PROFILE_USER_ID.to_string(),
            character_pack_id: DEFAULT_CHARACTER_PACK_ID.to_string(),
            characters: HashMap::new(),
            session_id: String::new(),
            outfit: DEFAULT_OUTFIT.to_string(),
            current_emotion: DEFAULT_EMOTION.to_string(),
            restore_latest_on_startup: true,
            voice_enabled: false,
            voice_input_enabled: true,
            voice_volume: 0.85,
            desktop_context_enabled: true,
            clipboard_context_enabled: false,
            screen_vision_enabled: false,
            screen_vision_mode: default_screen_vision_mode(),
            proactive_wake_enabled: false,
            proactive_wake_interval_sec: default_proactive_wake_interval_sec(),
            screen_vision_interval_sec: default_screen_vision_interval_sec(),
            screen_vision_frame_count: default_screen_vision_frame_count(),
            hit_test_enabled: true,
            hitbox_overlay: false,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct WindowGeometry {
    x: i32,
    y: i32,
    width: u32,
    height: u32,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct DesktopContextSnapshot {
    ok: bool,
    enabled: bool,
    captured_at: u128,
    platform: String,
    foreground: ForegroundWindowInfo,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct PreparedAudioAsset {
    original_path: String,
    cached_path: String,
    file_name: String,
    display_name: String,
    extension: String,
    size_bytes: u64,
    lyric_file_name: Option<String>,
    lyric_text: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct CharacterPackInstallResult {
    pack_id: String,
    character_id: String,
    character_name: String,
    installed_path: String,
    file_count: usize,
    requires_restart: bool,
    warnings: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ExportedWorkspaceFile {
    ok: bool,
    path: String,
    file_name: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct CharacterPackRegistryItem {
    id: String,
    source: String,
    installed_path: String,
    asset_count: usize,
    profile: serde_json::Value,
    outfits: Vec<CharacterPackOutfitAsset>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct CharacterPackOutfitAsset {
    id: String,
    name: String,
    emotions: Vec<CharacterPackEmotionAsset>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct CharacterPackEmotionAsset {
    id: String,
    name: String,
    path: String,
    size_bytes: u64,
}

#[derive(Debug, Clone)]
struct ZipEntry {
    name: String,
    data: Vec<u8>,
}

#[derive(Debug, Clone, Deserialize)]
struct CharacterPackJson {
    schema_version: String,
    identity: CharacterPackIdentity,
    #[serde(default)]
    persona_form: serde_json::Value,
    appearance: CharacterPackAppearance,
    dialogue: CharacterPackDialogue,
    #[serde(default)]
    emotion_aliases: serde_json::Value,
    #[serde(default)]
    layout: serde_json::Value,
    #[serde(default)]
    voice: serde_json::Value,
    #[serde(default)]
    assets: CharacterPackAssets,
}

#[derive(Debug, Clone, Deserialize)]
struct CharacterPackIdentity {
    id: String,
    name: String,
    app_name: String,
    #[serde(default)]
    self_reference: String,
    user_title: String,
    #[serde(default)]
    relationship: String,
}

#[derive(Debug, Clone, Deserialize)]
struct CharacterPackAppearance {
    default_outfit: String,
    default_emotion: String,
    #[serde(default)]
    required_emotions: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
struct CharacterPackDialogue {
    local_click_lines: Vec<CharacterPackClickLine>,
}

#[derive(Debug, Clone, Deserialize)]
struct CharacterPackClickLine {
    text: String,
    emotion: String,
}

#[derive(Debug, Clone, Default, Deserialize)]
struct CharacterPackAssets {
    asset_root: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ForegroundWindowInfo {
    title: String,
    process_name: String,
    pid: Option<u32>,
    source: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct HitRegion {
    #[serde(default, rename = "kind")]
    _kind: String,
    rect: HitRect,
    #[serde(default)]
    polygon: Vec<HitPoint>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct HitRect {
    x: i32,
    y: i32,
    width: i32,
    height: i32,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct HitPoint {
    x: i32,
    y: i32,
}

impl HitRegion {
    fn contains_point(&self, x: i32, y: i32) -> bool {
        if self.rect.width <= 0 || self.rect.height <= 0 {
            return false;
        }

        let right = self.rect.x.saturating_add(self.rect.width);
        let bottom = self.rect.y.saturating_add(self.rect.height);
        if x < self.rect.x || x > right || y < self.rect.y || y > bottom {
            return false;
        }

        self.polygon.len() < 3 || point_in_polygon(&self.polygon, x, y)
    }
}

#[tauri::command]
fn load_pet_state(app: AppHandle) -> Result<PetState, String> {
    let path = state_path(&app)?;
    if !path.exists() {
        return Ok(PetState::default());
    }

    let raw = fs::read_to_string(&path).map_err(|error| error.to_string())?;
    let mut state: PetState = serde_json::from_str(&raw).map_err(|error| error.to_string())?;
    normalize_pet_state(&mut state);
    Ok(state)
}

#[tauri::command]
fn save_pet_state(app: AppHandle, state: PetState) -> Result<(), String> {
    let path = state_path(&app)?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }

    let mut normalized = state;
    normalize_pet_state(&mut normalized);

    let raw = serde_json::to_string_pretty(&normalized).map_err(|error| error.to_string())?;
    fs::write(path, raw).map_err(|error| error.to_string())
}

#[tauri::command]
fn get_desktop_context_snapshot() -> DesktopContextSnapshot {
    DesktopContextSnapshot {
        ok: true,
        enabled: true,
        captured_at: current_time_millis(),
        platform: std::env::consts::OS.to_string(),
        foreground: collect_foreground_window(),
    }
}

#[tauri::command]
fn prepare_audio_asset(
    app: AppHandle,
    path: String,
    lyric_path: Option<String>,
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

    let cache_dir = app
        .path()
        .app_cache_dir()
        .map_err(|error| error.to_string())?
        .join("audio");
    fs::create_dir_all(&cache_dir).map_err(|error| error.to_string())?;

    let cached_file_name = format!("track_{}.{}", current_time_millis(), extension);
    let cached_path = cache_dir.join(cached_file_name);
    fs::copy(&source_path, &cached_path).map_err(|error| error.to_string())?;

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
        size_bytes: metadata.len(),
        lyric_file_name: lyric.as_ref().map(|item| item.0.clone()),
        lyric_text: lyric.map(|item| item.1),
    })
}

#[tauri::command]
fn install_character_pack_zip_file(
    app: AppHandle,
    path: String,
    overwrite: bool,
) -> Result<CharacterPackInstallResult, String> {
    let zip_path = PathBuf::from(path.trim());
    if !zip_path.is_file() {
        return Err("请选择一个角色包 zip 文件。".to_string());
    }
    if !zip_path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .eq_ignore_ascii_case("zip")
    {
        return Err("角色包文件需要是 .zip。".to_string());
    }
    let metadata = fs::metadata(&zip_path).map_err(|error| error.to_string())?;
    if metadata.len() == 0 {
        return Err("这个 zip 文件是空的。".to_string());
    }
    if metadata.len() as usize > MAX_CHARACTER_PACK_ZIP_BYTES {
        return Err("角色包 zip 暂时请控制在 300MB 以内。".to_string());
    }

    let bytes = fs::read(&zip_path).map_err(|error| error.to_string())?;
    install_character_pack_zip(app, bytes, overwrite)
}

#[tauri::command]
fn install_character_pack_zip_bytes(
    app: AppHandle,
    file_name: String,
    bytes: Vec<u8>,
    overwrite: bool,
) -> Result<CharacterPackInstallResult, String> {
    if !file_name.trim().to_ascii_lowercase().ends_with(".zip") {
        return Err("角色包文件需要是 .zip。".to_string());
    }
    if bytes.is_empty() {
        return Err("这个 zip 文件是空的。".to_string());
    }
    if bytes.len() > MAX_CHARACTER_PACK_ZIP_BYTES {
        return Err("角色包 zip 暂时请控制在 300MB 以内。".to_string());
    }

    install_character_pack_zip(app, bytes, overwrite)
}

#[tauri::command]
fn open_character_packs_folder() -> Result<(), String> {
    let characters_dir = creator_kit_characters_dir()?;
    fs::create_dir_all(&characters_dir).map_err(|error| error.to_string())?;
    open_path_in_file_manager(&characters_dir)
}

#[tauri::command]
fn open_local_file(path: String) -> Result<(), String> {
    let path = canonical_existing_path(&path)?;
    open_path_with_system(&path)
}

#[tauri::command]
fn show_item_in_folder(path: String) -> Result<(), String> {
    let path = canonical_existing_path(&path)?;
    reveal_path_in_file_manager(&path)
}

#[tauri::command]
fn export_file_to_desktop(
    app: AppHandle,
    path: String,
    file_name: String,
) -> Result<ExportedWorkspaceFile, String> {
    let source_path = canonical_existing_path(&path)?;
    if !source_path.is_file() {
        return Err("只能导出文件。".to_string());
    }

    let export_dir = resolve_desktop_export_dir(&app)?;
    fs::create_dir_all(&export_dir).map_err(|error| error.to_string())?;
    let file_name = workspace_export_file_name(&source_path, &file_name);
    let target_path = unique_child_file_path(&export_dir, &file_name);
    fs::copy(&source_path, &target_path).map_err(|error| error.to_string())?;

    Ok(ExportedWorkspaceFile {
        ok: true,
        path: target_path.to_string_lossy().to_string(),
        file_name: target_path
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or(file_name.as_str())
            .to_string(),
    })
}

#[tauri::command]
fn list_character_packs() -> Result<Vec<CharacterPackRegistryItem>, String> {
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

        let asset_root = character
            .assets
            .asset_root
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .unwrap_or("assets");
        let outfits = list_character_pack_outfits(&pack_dir, asset_root);
        let asset_count = outfits.iter().map(|outfit| outfit.emotions.len()).sum();
        packs.push(CharacterPackRegistryItem {
            id: pack_id,
            source: character_path.to_string_lossy().to_string(),
            installed_path: pack_dir.to_string_lossy().to_string(),
            asset_count,
            profile,
            outfits,
        });
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

fn read_lyric_asset(audio_path: &PathBuf, explicit_path: Option<&str>) -> Option<(String, String)> {
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
    let bytes = fs::read(&path).ok()?;
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

fn install_character_pack_zip(
    app: AppHandle,
    bytes: Vec<u8>,
    overwrite: bool,
) -> Result<CharacterPackInstallResult, String> {
    let entries = read_stored_zip_entries(&bytes)?;
    let root = detect_character_pack_root(&entries)?;
    let pack_id = sanitize_pack_id(
        Path::new(&root)
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or(root.as_str()),
    );
    if pack_id.is_empty() {
        return Err("角色包 zip 缺少有效的包名。".to_string());
    }

    let temp_root = app
        .path()
        .app_cache_dir()
        .map_err(|error| error.to_string())?
        .join(format!("character_pack_import_{}", current_time_millis()));
    let temp_pack_dir = temp_root.join(&pack_id);
    let characters_dir = creator_kit_characters_dir()?;
    let destination = safe_child_path(&characters_dir, &pack_id)?;

    if destination.exists() && !overwrite {
        return Err(format!("角色包 {pack_id} 已存在。勾选覆盖同名后再导入。"));
    }

    let install_result = (|| {
        extract_character_pack_entries(&entries, &root, &temp_pack_dir)?;
        let validation = validate_imported_character_pack(&temp_pack_dir)?;

        fs::create_dir_all(&characters_dir).map_err(|error| error.to_string())?;
        if destination.exists() {
            assert_safe_remove_target(&characters_dir, &destination)?;
            fs::remove_dir_all(&destination).map_err(|error| error.to_string())?;
        }
        fs::rename(&temp_pack_dir, &destination).map_err(|error| error.to_string())?;

        Ok(CharacterPackInstallResult {
            pack_id,
            character_id: validation.character_id,
            character_name: validation.character_name,
            installed_path: destination.to_string_lossy().to_string(),
            file_count: entries
                .iter()
                .filter(|entry| entry.name.starts_with(&format!("{root}/")))
                .filter(|entry| !entry.name.ends_with('/'))
                .count(),
            requires_restart: false,
            warnings: validation.warnings,
        })
    })();

    let _ = fs::remove_dir_all(&temp_root);
    install_result
}

#[derive(Debug)]
struct CharacterPackValidation {
    character_id: String,
    character_name: String,
    warnings: Vec<String>,
}

fn read_stored_zip_entries(bytes: &[u8]) -> Result<Vec<ZipEntry>, String> {
    if bytes.len() < 22 {
        return Err("zip 文件不完整。".to_string());
    }

    let eocd = find_zip_eocd(bytes)?;
    let entry_count = read_u16(bytes, eocd + 10)? as usize;
    let central_dir_offset = read_u32(bytes, eocd + 16)? as usize;
    let mut offset = central_dir_offset;
    let mut entries = Vec::new();

    for _ in 0..entry_count {
        if read_u32(bytes, offset)? != 0x0201_4b50 {
            return Err("zip 中央目录无效。".to_string());
        }

        let flags = read_u16(bytes, offset + 8)?;
        if flags & 0x0001 != 0 {
            return Err("暂不支持加密 zip。".to_string());
        }
        let method = read_u16(bytes, offset + 10)?;
        if method != 0 {
            return Err("暂只支持 Creator Kit 导出的角色包 zip。".to_string());
        }

        let compressed_size = read_u32(bytes, offset + 20)? as usize;
        let uncompressed_size = read_u32(bytes, offset + 24)? as usize;
        let name_len = read_u16(bytes, offset + 28)? as usize;
        let extra_len = read_u16(bytes, offset + 30)? as usize;
        let comment_len = read_u16(bytes, offset + 32)? as usize;
        let local_header_offset = read_u32(bytes, offset + 42)? as usize;
        let name_start = offset + 46;
        let name_end = name_start.saturating_add(name_len);
        let name = normalize_zip_path(
            std::str::from_utf8(
                bytes
                    .get(name_start..name_end)
                    .ok_or_else(|| "zip 文件名范围无效。".to_string())?,
            )
            .map_err(|_| "zip 文件名需要是 UTF-8。".to_string())?,
        )?;

        if read_u32(bytes, local_header_offset)? != 0x0403_4b50 {
            return Err("zip 本地文件头无效。".to_string());
        }
        let local_name_len = read_u16(bytes, local_header_offset + 26)? as usize;
        let local_extra_len = read_u16(bytes, local_header_offset + 28)? as usize;
        let data_offset = local_header_offset
            .saturating_add(30)
            .saturating_add(local_name_len)
            .saturating_add(local_extra_len);
        let data_end = data_offset.saturating_add(compressed_size);
        let data = bytes
            .get(data_offset..data_end)
            .ok_or_else(|| "zip 文件内容范围无效。".to_string())?
            .to_vec();
        if data.len() != uncompressed_size {
            return Err(format!("zip 条目大小不匹配：{name}"));
        }

        entries.push(ZipEntry { name, data });
        offset = offset
            .saturating_add(46)
            .saturating_add(name_len)
            .saturating_add(extra_len)
            .saturating_add(comment_len);
    }

    Ok(entries)
}

fn find_zip_eocd(bytes: &[u8]) -> Result<usize, String> {
    let min = bytes.len().saturating_sub(0xffff + 22);
    let max = bytes.len().saturating_sub(22);
    for offset in (min..=max).rev() {
        if read_u32(bytes, offset)? == 0x0605_4b50 {
            return Ok(offset);
        }
    }
    Err("找不到 zip 结束目录。".to_string())
}

fn detect_character_pack_root(entries: &[ZipEntry]) -> Result<String, String> {
    let matches: Vec<String> = entries
        .iter()
        .filter(|entry| entry.name.ends_with("character.json"))
        .map(|entry| {
            entry
                .name
                .trim_end_matches("character.json")
                .trim_end_matches('/')
                .to_string()
        })
        .collect();

    match matches.len() {
        0 => Err("zip 中没有 character.json。".to_string()),
        1 => Ok(matches[0].clone()),
        _ => Err("zip 中包含多个 character.json，请一次导入一个角色包。".to_string()),
    }
}

fn extract_character_pack_entries(
    entries: &[ZipEntry],
    root: &str,
    target_dir: &Path,
) -> Result<(), String> {
    let prefix = if root.is_empty() {
        String::new()
    } else {
        format!("{root}/")
    };
    for entry in entries {
        if entry.name.ends_with('/') {
            continue;
        }
        if !prefix.is_empty() && !entry.name.starts_with(&prefix) {
            continue;
        }
        let relative = if prefix.is_empty() {
            entry.name.as_str()
        } else {
            entry.name.trim_start_matches(&prefix)
        };
        if relative.is_empty() {
            continue;
        }
        let target = safe_child_path(target_dir, relative)?;
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        fs::write(target, &entry.data).map_err(|error| error.to_string())?;
    }
    Ok(())
}

fn validate_imported_character_pack(pack_dir: &Path) -> Result<CharacterPackValidation, String> {
    let character_path = pack_dir.join("character.json");
    let raw = fs::read_to_string(&character_path).map_err(|error| error.to_string())?;
    let character: CharacterPackJson =
        serde_json::from_str(&raw).map_err(|error| format!("character.json 无效：{error}"))?;
    let mut warnings = Vec::new();

    require_text(&character.schema_version, "schema_version")?;
    if character.schema_version != "akane.character.v0.1"
        && character.schema_version != "akane.character.v0.2"
    {
        return Err("schema_version 需要是 akane.character.v0.1 或 akane.character.v0.2。".to_string());
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
    let characters_dir = pack_dir.join(asset_root).join("characters");
    if characters_dir.is_dir() {
        let default_outfit_dir = characters_dir.join(&character.appearance.default_outfit);
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

fn has_any_emotion_image(characters_dir: &Path) -> bool {
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

fn list_character_pack_outfits(pack_dir: &Path, asset_root: &str) -> Vec<CharacterPackOutfitAsset> {
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
        if emotions.is_empty() {
            continue;
        }
        result.push(CharacterPackOutfitAsset {
            id: outfit_id.to_string(),
            name: outfit_id.to_string(),
            emotions,
        });
    }

    result.sort_by(|a, b| a.id.cmp(&b.id));
    result
}

fn list_character_pack_emotions(outfit_dir: &Path) -> Vec<CharacterPackEmotionAsset> {
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

fn has_emotion_image_file(outfit_dir: &Path) -> bool {
    let Ok(files) = fs::read_dir(outfit_dir) else {
        return false;
    };
    files.flatten().any(|file| {
        let path = file.path();
        path.is_file() && is_supported_character_image(&path)
    })
}

fn find_emotion_image(outfit_dir: &Path, emotion: &str) -> bool {
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

fn is_supported_character_image(path: &Path) -> bool {
    matches!(
        path.extension()
            .and_then(|value| value.to_str())
            .unwrap_or("")
            .to_ascii_lowercase()
            .as_str(),
        "png" | "jpg" | "jpeg" | "webp"
    )
}

fn creator_kit_characters_dir() -> Result<PathBuf, String> {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let desktop_pet_next = manifest_dir
        .parent()
        .ok_or_else(|| "无法定位 desktop_pet_next。".to_string())?;
    let repo_root = desktop_pet_next
        .parent()
        .ok_or_else(|| "无法定位项目根目录。".to_string())?;
    let characters_dir = repo_root.join("desktop_pet_creator_kit").join("characters");
    if !characters_dir
        .parent()
        .is_some_and(|parent| parent.exists())
    {
        return Err("没有找到 desktop_pet_creator_kit。".to_string());
    }
    Ok(characters_dir)
}

fn open_path_in_file_manager(path: &Path) -> Result<(), String> {
    #[cfg(windows)]
    let mut command = {
        let mut command = Command::new("explorer");
        command.arg(path);
        command
    };

    #[cfg(target_os = "macos")]
    let mut command = {
        let mut command = Command::new("open");
        command.arg(path);
        command
    };

    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = {
        let mut command = Command::new("xdg-open");
        command.arg(path);
        command
    };

    command.spawn().map_err(|error| error.to_string())?;
    Ok(())
}

fn canonical_existing_path(raw_path: &str) -> Result<PathBuf, String> {
    require_text(raw_path, "文件路径")?;
    let path = PathBuf::from(raw_path.trim());
    let path = path.canonicalize().map_err(|error| error.to_string())?;
    if !path.exists() {
        return Err("文件不存在。".to_string());
    }
    Ok(path)
}

fn open_path_with_system(path: &Path) -> Result<(), String> {
    #[cfg(windows)]
    let mut command = {
        let mut command = Command::new("explorer");
        command.arg(path);
        command
    };

    #[cfg(target_os = "macos")]
    let mut command = {
        let mut command = Command::new("open");
        command.arg(path);
        command
    };

    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = {
        let mut command = Command::new("xdg-open");
        command.arg(path);
        command
    };

    command.spawn().map_err(|error| error.to_string())?;
    Ok(())
}

fn reveal_path_in_file_manager(path: &Path) -> Result<(), String> {
    if path.is_dir() {
        return open_path_in_file_manager(path);
    }

    #[cfg(windows)]
    let mut command = {
        let mut command = Command::new("explorer");
        command.arg(format!("/select,{}", path.to_string_lossy()));
        command
    };

    #[cfg(target_os = "macos")]
    let mut command = {
        let mut command = Command::new("open");
        command.arg("-R").arg(path);
        command
    };

    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = {
        let mut command = Command::new("xdg-open");
        command.arg(path.parent().unwrap_or_else(|| Path::new(".")));
        command
    };

    command.spawn().map_err(|error| error.to_string())?;
    Ok(())
}

fn resolve_desktop_export_dir(app: &AppHandle) -> Result<PathBuf, String> {
    if let Ok(path) = app.path().desktop_dir() {
        return Ok(path.join("Akane Outputs"));
    }
    #[cfg(windows)]
    if let Some(home) = std::env::var_os("USERPROFILE") {
        return Ok(PathBuf::from(home).join("Desktop").join("Akane Outputs"));
    }
    if let Some(home) = std::env::var_os("HOME") {
        return Ok(PathBuf::from(home).join("Desktop").join("Akane Outputs"));
    }
    Err("无法定位桌面目录。".to_string())
}

fn workspace_export_file_name(source_path: &Path, suggested_name: &str) -> String {
    let fallback = source_path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("akane-output");
    let mut file_name = sanitize_file_name(if suggested_name.trim().is_empty() {
        fallback
    } else {
        suggested_name.trim()
    });
    if Path::new(&file_name).extension().is_none() {
        if let Some(ext) = source_path.extension().and_then(|value| value.to_str()) {
            if !ext.trim().is_empty() {
                file_name.push('.');
                file_name.push_str(ext.trim());
            }
        }
    }
    file_name
}

fn sanitize_file_name(value: &str) -> String {
    let mut name: String = value
        .chars()
        .map(|ch| {
            if ch.is_control() || matches!(ch, '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*') {
                '_'
            } else {
                ch
            }
        })
        .collect();
    name = name.trim().trim_matches('.').trim().to_string();
    if name.is_empty() {
        return "akane-output".to_string();
    }
    if name.chars().count() > 160 {
        name = name.chars().take(160).collect();
        name = name.trim().trim_matches('.').trim().to_string();
    }
    if name.is_empty() {
        "akane-output".to_string()
    } else {
        name
    }
}

fn unique_child_file_path(directory: &Path, file_name: &str) -> PathBuf {
    let first = directory.join(file_name);
    if !first.exists() {
        return first;
    }

    let path = Path::new(file_name);
    let stem = path
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("akane-output");
    let extension = path.extension().and_then(|value| value.to_str()).unwrap_or("");
    for index in 2..1000 {
        let candidate_name = if extension.is_empty() {
            format!("{stem} ({index})")
        } else {
            format!("{stem} ({index}).{extension}")
        };
        let candidate = directory.join(candidate_name);
        if !candidate.exists() {
            return candidate;
        }
    }
    directory.join(format!("{}_{}", current_time_millis(), file_name))
}

fn require_text(value: &str, label: &str) -> Result<(), String> {
    if value.trim().is_empty() {
        Err(format!("{label} 不能为空。"))
    } else {
        Ok(())
    }
}

fn require_optional_object(value: &serde_json::Value, label: &str) -> Result<(), String> {
    if value.is_null() || value.is_object() {
        Ok(())
    } else {
        Err(format!("{label} 必须是对象。"))
    }
}

fn safe_child_path(base: &Path, relative: &str) -> Result<PathBuf, String> {
    let normalized = normalize_zip_path(relative)?;
    let target = base.join(normalized.replace('/', std::path::MAIN_SEPARATOR_STR));
    let base = base
        .canonicalize()
        .or_else(|_| Ok::<PathBuf, std::io::Error>(base.to_path_buf()))
        .map_err(|error| error.to_string())?;
    let parent = target
        .parent()
        .unwrap_or(base.as_path())
        .canonicalize()
        .unwrap_or_else(|_| target.parent().unwrap_or(base.as_path()).to_path_buf());
    if !parent.starts_with(&base) && parent != base {
        return Err(format!("角色包内包含不安全路径：{relative}"));
    }
    Ok(target)
}

fn assert_safe_remove_target(base: &Path, target: &Path) -> Result<(), String> {
    let base = base.canonicalize().map_err(|error| error.to_string())?;
    let target = target.canonicalize().map_err(|error| error.to_string())?;
    if target == base || !target.starts_with(&base) {
        return Err("拒绝覆盖不安全的目标目录。".to_string());
    }
    Ok(())
}

fn sanitize_pack_id(value: &str) -> String {
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

fn normalize_zip_path(value: &str) -> Result<String, String> {
    let raw = value.replace('\\', "/");
    if raw.starts_with('/') || raw.contains(':') {
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

fn read_u16(bytes: &[u8], offset: usize) -> Result<u16, String> {
    let raw = bytes
        .get(offset..offset.saturating_add(2))
        .ok_or_else(|| "zip 文件结构不完整。".to_string())?;
    Ok(u16::from_le_bytes([raw[0], raw[1]]))
}

fn read_u32(bytes: &[u8], offset: usize) -> Result<u32, String> {
    let raw = bytes
        .get(offset..offset.saturating_add(4))
        .ok_or_else(|| "zip 文件结构不完整。".to_string())?;
    Ok(u32::from_le_bytes([raw[0], raw[1], raw[2], raw[3]]))
}

fn find_adjacent_lyric_path(audio_path: &PathBuf) -> Option<PathBuf> {
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

#[tauri::command]
fn apply_window_state(window: Window, state: PetState) -> Result<WindowGeometry, String> {
    window
        .set_always_on_top(state.always_on_top)
        .map_err(|error| error.to_string())?;
    window
        .set_skip_taskbar(state.skip_taskbar)
        .map_err(|error| error.to_string())?;
    window
        .set_ignore_cursor_events(false)
        .map_err(|error| error.to_string())?;

    let has_position = state.x.is_some() && state.y.is_some();
    if let (Some(x), Some(y)) = (state.x, state.y) {
        window
            .set_position(Position::Physical(PhysicalPosition::new(x, y)))
            .map_err(|error| error.to_string())?;
    }

    if let (Some(width), Some(height)) = (state.width, state.height) {
        window
            .set_size(Size::Physical(PhysicalSize::new(width, height)))
            .map_err(|error| error.to_string())?;
    } else {
        set_scaled_size(&window, state.scale)?;
    }

    if !has_position {
        place_window_bottom_right(&window)?;
    }

    get_window_geometry(window)
}

#[tauri::command]
fn set_visual_scale(window: Window, scale: f64) -> Result<WindowGeometry, String> {
    let old_position = window.outer_position().ok();
    let old_size = window.outer_size().ok();
    set_scaled_size(&window, scale)?;
    if let (Some(position), Some(size)) = (old_position, old_size) {
        if let Ok(new_size) = window.outer_size() {
            let dx = size.width as i32 - new_size.width as i32;
            let dy = size.height as i32 - new_size.height as i32;
            window
                .set_position(Position::Physical(PhysicalPosition::new(
                    position.x.saturating_add(dx),
                    position.y.saturating_add(dy),
                )))
                .map_err(|error| error.to_string())?;
        }
    }
    get_window_geometry(window)
}

#[tauri::command]
fn set_always_on_top(window: Window, enabled: bool) -> Result<(), String> {
    window
        .set_always_on_top(enabled)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn set_taskbar_visible(window: Window, visible: bool) -> Result<(), String> {
    window
        .set_skip_taskbar(!visible)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn set_click_through(window: Window, enabled: bool) -> Result<(), String> {
    // Phase A uses whole-window pass-through as a native probe. Future Windows
    // hit-test work can replace this boundary with per-pixel WM_NCHITTEST logic.
    window
        .set_ignore_cursor_events(enabled)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn set_hit_test_enabled(enabled: bool) -> Result<(), String> {
    #[cfg(windows)]
    set_native_hit_test_enabled(enabled);
    #[cfg(not(windows))]
    let _ = enabled;

    Ok(())
}

#[tauri::command]
fn update_hit_regions(regions: Vec<HitRegion>) -> Result<(), String> {
    #[cfg(windows)]
    set_native_hit_regions(regions);
    #[cfg(not(windows))]
    let _ = regions;

    Ok(())
}

#[tauri::command]
fn reset_window_geometry(window: Window) -> Result<WindowGeometry, String> {
    set_scaled_size(&window, 1.0)?;
    place_window_bottom_right(&window)?;
    get_window_geometry(window)
}

#[tauri::command]
fn move_window_by(window: Window, dx: i32, dy: i32) -> Result<WindowGeometry, String> {
    let position = window.outer_position().map_err(|error| error.to_string())?;
    window
        .set_position(Position::Physical(PhysicalPosition::new(
            position.x.saturating_add(dx),
            position.y.saturating_add(dy),
        )))
        .map_err(|error| error.to_string())?;
    get_window_geometry(window)
}

#[tauri::command]
fn close_window(window: Window) -> Result<(), String> {
    window.close().map_err(|error| error.to_string())
}

#[tauri::command]
fn close_pet_app(app: AppHandle) -> Result<(), String> {
    for label in ["settings", "workspace", "workshop", "main"] {
        if let Some(window) = app.get_webview_window(label) {
            let _ = window.close();
        }
    }
    Ok(())
}

fn settings_window_url() -> &'static str {
    match std::env::var("AKANE_CONTROL_CENTER_LAB") {
        Ok(value) if value == "1" || value.eq_ignore_ascii_case("true") => "control-center-lab.html",
        _ => "settings.html",
    }
}

#[tauri::command]
async fn open_settings_window(app: AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("settings") {
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
        return Ok(());
    }

    let builder =
        WebviewWindowBuilder::new(&app, "settings", WebviewUrl::App(settings_window_url().into()))
            .title("Akane Next 设置")
            .inner_size(1080.0, 720.0)
            .min_inner_size(760.0, 560.0)
            .resizable(true)
            .decorations(false)
            .always_on_top(false)
            .skip_taskbar(false)
            .center()
            .visible(true);

    let window = builder.build().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

#[tauri::command]
async fn open_workspace_window(app: AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("workspace") {
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
        return Ok(());
    }

    let builder =
        WebviewWindowBuilder::new(&app, "workspace", WebviewUrl::App("workspace.html".into()))
            .title("Akane Next 手边物品")
            .inner_size(760.0, 620.0)
            .min_inner_size(520.0, 420.0)
            .resizable(true)
            .decorations(true)
            .always_on_top(false)
            .skip_taskbar(false)
            .center()
            .visible(true);

    let window = builder.build().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

#[tauri::command]
async fn open_workshop_window(app: AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("workshop") {
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
        return Ok(());
    }

    let builder =
        WebviewWindowBuilder::new(&app, "workshop", WebviewUrl::App("workshop.html".into()))
            .title("Akane Next 角色工坊")
            .inner_size(860.0, 620.0)
            .min_inner_size(640.0, 460.0)
            .resizable(true)
            .decorations(true)
            .always_on_top(false)
            .skip_taskbar(false)
            .center()
            .visible(true);

    let window = builder.build().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

#[tauri::command]
fn get_window_geometry(window: Window) -> Result<WindowGeometry, String> {
    let position = window.outer_position().map_err(|error| error.to_string())?;
    let size = window.outer_size().map_err(|error| error.to_string())?;

    Ok(WindowGeometry {
        x: position.x,
        y: position.y,
        width: size.width,
        height: size.height,
    })
}

fn set_scaled_size(window: &Window, scale: f64) -> Result<(), String> {
    let scale = clamp(scale, 0.75, 1.45);
    let width = (BASE_WIDTH * scale).round();
    let height = (BASE_HEIGHT * scale).round();

    window
        .set_size(Size::Logical(LogicalSize::new(width, height)))
        .map_err(|error| error.to_string())
}

fn place_window_bottom_right(window: &Window) -> Result<(), String> {
    let size = window.outer_size().map_err(|error| error.to_string())?;
    let monitor = window
        .current_monitor()
        .map_err(|error| error.to_string())?
        .or(window
            .primary_monitor()
            .map_err(|error| error.to_string())?);
    let Some(monitor) = monitor else {
        return Ok(());
    };

    let area = monitor.work_area();
    let x = area
        .position
        .x
        .saturating_add(area.size.width as i32)
        .saturating_sub(size.width as i32)
        .saturating_sub(20);
    let y = area
        .position
        .y
        .saturating_add(area.size.height as i32)
        .saturating_sub(size.height as i32)
        .saturating_sub(30);

    window
        .set_position(Position::Physical(PhysicalPosition::new(x, y)))
        .map_err(|error| error.to_string())
}

fn state_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_config_dir()
        .map(|dir| dir.join(STATE_FILE))
        .map_err(|error| error.to_string())
}

fn clamp(value: f64, min: f64, max: f64) -> f64 {
    value.max(min).min(max)
}

fn current_time_millis() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0)
}

fn normalize_pet_state(state: &mut PetState) {
    state.scale = clamp(state.scale, 0.75, 1.45);
    state.opacity = clamp(state.opacity, 0.55, 1.0);
    state.click_through = false;
    state.backend_url = normalize_backend_url(&state.backend_url);
    state.profile_user_id = DEFAULT_PROFILE_USER_ID.to_string();
    if state.character_pack_id.trim().is_empty() {
        state.character_pack_id = DEFAULT_CHARACTER_PACK_ID.to_string();
    }
    if state.outfit.trim().is_empty() {
        state.outfit = DEFAULT_OUTFIT.to_string();
    }
    if state.current_emotion.trim().is_empty() {
        state.current_emotion = DEFAULT_EMOTION.to_string();
    }
    state.voice_volume = clamp(state.voice_volume, 0.0, 1.0);
    state.screen_vision_mode = normalize_screen_vision_mode(&state.screen_vision_mode);
    state.proactive_wake_interval_sec = state.proactive_wake_interval_sec.clamp(15, 600);
    state.screen_vision_interval_sec = state.screen_vision_interval_sec.clamp(15, 600);
    state.screen_vision_frame_count = state.screen_vision_frame_count.clamp(1, 5);
    for runtime in state.characters.values_mut() {
        normalize_character_runtime_state(runtime);
    }
}

fn normalize_character_runtime_state(runtime: &mut CharacterRuntimeState) {
    if runtime.version == 0 {
        runtime.version = 1;
    }
    runtime.character_pack_id = runtime.character_pack_id.trim().to_string();
    runtime.session_id = runtime.session_id.trim().to_string();
    runtime.outfit = runtime.outfit.trim().to_string();
    runtime.current_emotion = runtime.current_emotion.trim().to_string();
    runtime.scale = clamp(runtime.scale, 0.75, 1.45);
    runtime.opacity = clamp(runtime.opacity, 0.55, 1.0);
}

fn default_hit_test_enabled() -> bool {
    true
}

fn default_voice_volume() -> f64 {
    0.85
}

fn default_restore_latest_on_startup() -> bool {
    true
}

fn default_voice_input_enabled() -> bool {
    true
}

fn default_desktop_context_enabled() -> bool {
    true
}

fn default_character_pack_id() -> String {
    DEFAULT_CHARACTER_PACK_ID.to_string()
}

fn default_screen_vision_mode() -> String {
    "summary".to_string()
}

fn normalize_screen_vision_mode(value: &str) -> String {
    match value.trim().to_ascii_lowercase().as_str() {
        "direct" => "direct".to_string(),
        _ => "summary".to_string(),
    }
}

fn default_proactive_wake_interval_sec() -> u32 {
    30
}

fn default_screen_vision_interval_sec() -> u32 {
    25
}

fn default_screen_vision_frame_count() -> u32 {
    4
}

#[cfg(windows)]
fn collect_foreground_window() -> ForegroundWindowInfo {
    let hwnd = unsafe { GetForegroundWindow() };
    if hwnd.0.is_null() {
        return empty_foreground_window("none");
    }

    let mut pid = 0u32;
    unsafe {
        GetWindowThreadProcessId(hwnd, Some(&mut pid));
    }

    if pid == std::process::id() {
        return ForegroundWindowInfo {
            title: String::new(),
            process_name: String::new(),
            pid: Some(pid),
            source: "self".to_string(),
        };
    }

    ForegroundWindowInfo {
        title: read_window_title(hwnd),
        process_name: read_process_name(pid),
        pid: if pid == 0 { None } else { Some(pid) },
        source: "foreground".to_string(),
    }
}

#[cfg(not(windows))]
fn collect_foreground_window() -> ForegroundWindowInfo {
    empty_foreground_window("unsupported_platform")
}

fn empty_foreground_window(source: &str) -> ForegroundWindowInfo {
    ForegroundWindowInfo {
        title: String::new(),
        process_name: String::new(),
        pid: None,
        source: source.to_string(),
    }
}

#[cfg(windows)]
fn read_window_title(hwnd: HWND) -> String {
    let len = unsafe { GetWindowTextLengthW(hwnd) };
    if len <= 0 {
        return String::new();
    }

    let mut buffer = vec![0u16; len as usize + 1];
    let copied = unsafe { GetWindowTextW(hwnd, &mut buffer) };
    if copied <= 0 {
        return String::new();
    }

    String::from_utf16_lossy(&buffer[..copied as usize])
        .trim()
        .chars()
        .take(240)
        .collect()
}

#[cfg(windows)]
fn read_process_name(pid: u32) -> String {
    if pid == 0 {
        return String::new();
    }

    let handle = match unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) } {
        Ok(handle) => handle,
        Err(_) => return String::new(),
    };

    let mut buffer = vec![0u16; 32768];
    let mut size = buffer.len() as u32;
    let result = unsafe {
        QueryFullProcessImageNameW(
            handle,
            Default::default(),
            PWSTR(buffer.as_mut_ptr()),
            &mut size,
        )
    };
    let _ = unsafe { CloseHandle(handle) };

    if result.is_err() || size == 0 {
        return String::new();
    }

    let image_path = String::from_utf16_lossy(&buffer[..size as usize]);
    std::path::Path::new(&image_path)
        .file_name()
        .map(|name| name.to_string_lossy().to_string())
        .unwrap_or(image_path)
        .chars()
        .take(100)
        .collect()
}

fn normalize_backend_url(value: &str) -> String {
    let trimmed = value.trim().trim_end_matches('/').to_string();
    if trimmed.is_empty() {
        DEFAULT_BACKEND_URL.to_string()
    } else {
        trimmed
    }
}

fn point_in_polygon(points: &[HitPoint], x: i32, y: i32) -> bool {
    if points.len() < 3 {
        return false;
    }

    let px = x as f64;
    let py = y as f64;
    let mut inside = false;
    let mut previous = points.len() - 1;

    for current in 0..points.len() {
        let a = &points[current];
        let b = &points[previous];
        let ax = a.x as f64;
        let ay = a.y as f64;
        let bx = b.x as f64;
        let by = b.y as f64;

        if (ay > py) != (by > py) && px < (bx - ax) * (py - ay) / (by - ay) + ax {
            inside = !inside;
        }

        previous = current;
    }

    inside
}

#[cfg(windows)]
#[derive(Debug, Default)]
struct NativeHitTestStore {
    enabled: bool,
    regions: Vec<HitRegion>,
    root_hwnd: isize,
}

#[cfg(windows)]
static NATIVE_HIT_TEST_STORE: OnceLock<Mutex<NativeHitTestStore>> = OnceLock::new();
#[cfg(windows)]
const HIT_TEST_SUBCLASS_ID: usize = 0xA11A_0003;

#[cfg(windows)]
fn native_hit_test_store() -> &'static Mutex<NativeHitTestStore> {
    NATIVE_HIT_TEST_STORE.get_or_init(|| Mutex::new(NativeHitTestStore::default()))
}

#[cfg(windows)]
fn install_native_hit_test(hwnd: HWND) -> Result<(), String> {
    if let Ok(mut store) = native_hit_test_store().lock() {
        store.root_hwnd = hwnd.0 as isize;
    }

    if subclass_hit_test_window(hwnd) {
        refresh_native_child_hit_test_hooks(hwnd);
        Ok(())
    } else {
        Err("SetWindowSubclass failed".to_string())
    }
}

#[cfg(windows)]
fn set_native_hit_test_enabled(enabled: bool) {
    if let Ok(mut store) = native_hit_test_store().lock() {
        store.enabled = enabled;
    }
}

#[cfg(windows)]
fn set_native_hit_regions(regions: Vec<HitRegion>) {
    let root_hwnd = if let Ok(mut store) = native_hit_test_store().lock() {
        store.regions = regions;
        store.root_hwnd
    } else {
        0
    };

    if root_hwnd != 0 {
        refresh_native_child_hit_test_hooks(HWND(root_hwnd as _));
    }
}

#[cfg(windows)]
fn subclass_hit_test_window(hwnd: HWND) -> bool {
    unsafe { SetWindowSubclass(hwnd, Some(native_hit_test_proc), HIT_TEST_SUBCLASS_ID, 0) }
        .as_bool()
}

#[cfg(windows)]
fn refresh_native_child_hit_test_hooks(root_hwnd: HWND) {
    unsafe {
        let _ = EnumChildWindows(Some(root_hwnd), Some(enum_child_hit_test_proc), LPARAM(0));
    }
}

#[cfg(windows)]
unsafe extern "system" fn enum_child_hit_test_proc(hwnd: HWND, _lparam: LPARAM) -> BOOL {
    let _ = subclass_hit_test_window(hwnd);
    BOOL(1)
}

#[cfg(windows)]
unsafe extern "system" fn native_hit_test_proc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    _subclass_id: usize,
    _ref_data: usize,
) -> LRESULT {
    match msg {
        WM_NCHITTEST => {
            if let Some(result) = native_hit_test(hwnd, lparam) {
                return result;
            }
        }
        WM_NCDESTROY => {
            let _ = RemoveWindowSubclass(hwnd, Some(native_hit_test_proc), HIT_TEST_SUBCLASS_ID);
        }
        _ => {}
    }

    DefSubclassProc(hwnd, msg, wparam, lparam)
}

#[cfg(windows)]
fn native_hit_test(hwnd: HWND, lparam: LPARAM) -> Option<LRESULT> {
    let store = native_hit_test_store().lock().ok()?;
    if !store.enabled || store.regions.is_empty() {
        return None;
    }

    let root_hwnd = if store.root_hwnd == 0 {
        hwnd
    } else {
        HWND(store.root_hwnd as _)
    };

    let mut rect = RECT::default();
    if unsafe { GetWindowRect(root_hwnd, &mut rect) }.is_err() {
        return None;
    }

    let x = get_x_lparam(lparam).saturating_sub(rect.left);
    let y = get_y_lparam(lparam).saturating_sub(rect.top);
    let interactive = store
        .regions
        .iter()
        .any(|region| region.contains_point(x, y));

    if interactive {
        Some(LRESULT(HTCLIENT as isize))
    } else {
        Some(LRESULT(HTTRANSPARENT as isize))
    }
}

#[cfg(windows)]
fn get_x_lparam(lparam: LPARAM) -> i32 {
    (lparam.0 as u32 & 0xffff) as i16 as i32
}

#[cfg(windows)]
fn get_y_lparam(lparam: LPARAM) -> i32 {
    ((lparam.0 as u32 >> 16) & 0xffff) as i16 as i32
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            #[cfg(windows)]
            {
                if let Some(window) = app.get_webview_window("main") {
                    match window.hwnd() {
                        Ok(hwnd) => {
                            if let Err(error) = install_native_hit_test(hwnd) {
                                eprintln!("Akane native hit-test hook unavailable: {error}");
                            }
                        }
                        Err(error) => {
                            eprintln!("Akane native window handle unavailable: {error}");
                        }
                    }
                }
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            load_pet_state,
            save_pet_state,
            get_desktop_context_snapshot,
            prepare_audio_asset,
            list_character_packs,
            install_character_pack_zip_file,
            install_character_pack_zip_bytes,
            open_character_packs_folder,
            open_local_file,
            show_item_in_folder,
            export_file_to_desktop,
            apply_window_state,
            set_visual_scale,
            set_always_on_top,
            set_taskbar_visible,
            set_click_through,
            set_hit_test_enabled,
            update_hit_regions,
            reset_window_geometry,
            move_window_by,
            close_window,
            close_pet_app,
            open_settings_window,
            open_workspace_window,
            open_workshop_window,
            get_window_geometry
        ])
        .plugin(tauri_plugin_http::init())
        .run(tauri::generate_context!())
        .expect("error while running Akane Desktop Pet Next");
}
