#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    collections::HashMap,
    fs,
    io::Write,
    net::IpAddr,
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
use windows::core::{Interface, BOOL, PWSTR};
#[cfg(windows)]
use windows::Media::Control::{
    GlobalSystemMediaTransportControlsSession, GlobalSystemMediaTransportControlsSessionManager,
};
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
const DATA_ROOT_ENV: &str = "AKANE_DATA_ROOT";
const APP_DIRECTORY_NAME: &str = "Akane";
const CHARACTER_PACK_TEMPLATE_JSON: &str =
    include_str!("../../../desktop_pet_creator_kit/templates/character_pack/character.json");
const BASE_WIDTH: f64 = 340.0;
const BASE_HEIGHT: f64 = 560.0;
const DEFAULT_BACKEND_URL: &str = "http://127.0.0.1:9999";
const DEFAULT_PROFILE_USER_ID: &str = "master";
const DEFAULT_CHARACTER_PACK_ID: &str = "akane_v1";
const DEFAULT_OUTFIT: &str = "default";
const DEFAULT_EMOTION: &str = "normal";
const MAX_AUDIO_FILE_BYTES: u64 = 300 * 1024 * 1024;
const MAX_LYRIC_FILE_BYTES: u64 = 512 * 1024;
const MAX_CHARACTER_PACK_ZIP_BYTES: usize = 300 * 1024 * 1024;
const MAX_PORTRAIT_IMAGE_BYTES: usize = 20 * 1024 * 1024;
const PRIVATE_LOCAL_DIRECTORY: &str = "_local";
const SUPPORTED_AUDIO_EXTENSIONS: &[&str] = &[
    "mp3", "wav", "flac", "ogg", "oga", "m4a", "aac", "opus", "webm",
];
const SUPPORTED_PORTRAIT_EXTENSIONS: &[&str] = &["png", "jpg", "jpeg", "webp"];

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
    care: serde_json::Value,
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
            care: serde_json::Value::Null,
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
struct SystemMediaSnapshot {
    ok: bool,
    status: String,
    reason: String,
    captured_at: u128,
    platform: String,
    track_key: String,
    title: String,
    artist: String,
    album: String,
    source_app: String,
    playback_status: String,
    is_playing: bool,
    position_seconds: Option<f64>,
    duration_seconds: Option<f64>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct SystemMediaControlResult {
    ok: bool,
    status: String,
    reason: String,
    action: String,
    captured_at: u128,
    platform: String,
    track_key: String,
    title: String,
    artist: String,
    source_app: String,
    playback_status: String,
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
struct CharacterPackActivationResult {
    pack_id: String,
    character_name: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ExportedWorkspaceFile {
    ok: bool,
    path: String,
    file_name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    size_bytes: Option<u64>,
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
#[serde(rename_all = "camelCase")]
struct SaveCharacterPackRequest {
    pack_id: String,
    identity: serde_json::Value,
    persona_form: serde_json::Value,
    #[serde(default)]
    dialogue: serde_json::Value,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CreateCharacterPackRequest {
    pack_id: String,
    name: String,
    app_name: String,
    user_title: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CreateCharacterContextLibraryRequest {
    pack_id: String,
    folder: String,
    name: String,
    description: String,
    load_when: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SetCharacterVoiceProfileRequest {
    pack_id: String,
    provider: String,
    profile_id: String,
    notes: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct UploadPortraitResult {
    ok: bool,
    outfit: String,
    emotion: String,
    path: String,
    size_bytes: u64,
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

    let mut normalized = state;
    normalize_pet_state(&mut normalized);

    let raw = serde_json::to_string_pretty(&normalized).map_err(|error| error.to_string())?;
    write_text_atomic(&path, &raw)
}

#[tauri::command]
fn activate_character_pack(
    app: AppHandle,
    pack_id: String,
) -> Result<CharacterPackActivationResult, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }

    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    let character_path = pack_dir.join("character.json");
    if !character_path.is_file() {
        return Err(format!("角色包 {pack_id} 不存在或缺少 character.json。"));
    }
    let raw_profile = fs::read_to_string(&character_path).map_err(|error| error.to_string())?;
    let character = serde_json::from_str::<CharacterPackJson>(&raw_profile)
        .map_err(|error| format!("角色包 {pack_id} 的 character.json 无效：{error}"))?;
    app.get_webview_window("main")
        .ok_or_else(|| "桌宠主窗口不存在，无法切换角色。".to_string())?;

    let path = state_path(&app)?;
    let original = fs::read_to_string(&path).ok();
    let mut state = match original.as_deref() {
        Some(raw) => serde_json::from_str::<PetState>(raw).map_err(|error| error.to_string())?,
        None => PetState::default(),
    };
    normalize_pet_state(&mut state);
    state.character_pack_id = pack_id.clone();
    let raw_state = serde_json::to_string_pretty(&state).map_err(|error| error.to_string())?;
    write_text_atomic(&path, &raw_state)?;

    Ok(CharacterPackActivationResult {
        pack_id,
        character_name: character.identity.name,
    })
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
async fn get_current_system_media() -> SystemMediaSnapshot {
    tauri::async_runtime::spawn_blocking(read_current_system_media)
        .await
        .unwrap_or_else(|error| system_media_unavailable("join_failed", error.to_string()))
}

#[tauri::command]
async fn control_system_media(action: String) -> SystemMediaControlResult {
    tauri::async_runtime::spawn_blocking(move || control_system_media_blocking(action))
        .await
        .unwrap_or_else(|error| {
            system_media_control_unavailable("", "join_failed", error.to_string())
        })
}

#[tauri::command]
async fn prepare_audio_asset(
    app: AppHandle,
    path: String,
    lyric_path: Option<String>,
) -> Result<PreparedAudioAsset, String> {
    tauri::async_runtime::spawn_blocking(move || {
        prepare_audio_asset_blocking(app, path, lyric_path)
    })
    .await
    .map_err(|error| format!("音频准备任务失败：{error}"))?
}

fn prepare_audio_asset_blocking(
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
async fn install_character_pack_zip_file(
    app: AppHandle,
    path: String,
    overwrite: bool,
) -> Result<CharacterPackInstallResult, String> {
    tauri::async_runtime::spawn_blocking(move || {
        install_character_pack_zip_file_blocking(app, path, overwrite)
    })
    .await
    .map_err(|error| format!("角色包导入任务失败：{error}"))?
}

fn install_character_pack_zip_file_blocking(
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
async fn install_character_pack_zip_bytes(
    app: AppHandle,
    file_name: String,
    bytes: Vec<u8>,
    overwrite: bool,
) -> Result<CharacterPackInstallResult, String> {
    tauri::async_runtime::spawn_blocking(move || {
        install_character_pack_zip_bytes_blocking(app, file_name, bytes, overwrite)
    })
    .await
    .map_err(|error| format!("角色包导入任务失败：{error}"))?
}

fn install_character_pack_zip_bytes_blocking(
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
fn open_external_url(url: String) -> Result<(), String> {
    let url = normalize_public_external_url(&url)?;
    open_url_with_system(&url)
}

#[tauri::command]
async fn export_file_to_desktop(
    app: AppHandle,
    path: String,
    file_name: String,
) -> Result<ExportedWorkspaceFile, String> {
    tauri::async_runtime::spawn_blocking(move || {
        export_file_to_desktop_blocking(app, path, file_name)
    })
    .await
    .map_err(|error| format!("导出文件任务失败：{error}"))?
}

fn export_file_to_desktop_blocking(
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
        size_bytes: None,
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

#[tauri::command]
fn save_character_pack(
    request: SaveCharacterPackRequest,
) -> Result<CharacterPackRegistryItem, String> {
    let pack_id = sanitize_pack_id(&request.pack_id);
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

    /* read existing character.json */
    let raw = fs::read_to_string(&character_path).map_err(|error| error.to_string())?;
    let mut profile: serde_json::Value =
        serde_json::from_str(&raw).map_err(|error| format!("character.json 无效：{error}"))?;

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

    /* regenerate persona.md only if it still looks like the template */
    let persona_path = pack_dir.join("persona.md");
    let should_write = match fs::read_to_string(&persona_path) {
        Ok(existing) => {
            // If all three template headers are present, it's still a template
            // and safe to regenerate. If any are missing, the user has edited it.
            existing.contains("## Voice")
                && existing.contains("## Relationship Boundary")
                && existing.contains("## World Notes")
        }
        Err(_) => true, // file doesn't exist — create it
    };
    if should_write {
        write_text_atomic(
            &persona_path,
            &persona_md_text(&validated.identity.name, &validated.identity.user_title),
        )?;
    }

    /* return updated registry item – reuse validated struct */
    let asset_root = validated
        .assets
        .asset_root
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or("assets");
    let outfits = list_character_pack_outfits(&pack_dir, asset_root);
    let asset_count = outfits.iter().map(|outfit| outfit.emotions.len()).sum();

    Ok(CharacterPackRegistryItem {
        id: pack_id,
        source: character_path.to_string_lossy().to_string(),
        installed_path: pack_dir.to_string_lossy().to_string(),
        asset_count,
        profile,
        outfits,
    })
}

#[tauri::command]
fn set_character_voice_profile(
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

    let asset_root = validated
        .assets
        .asset_root
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or("assets");
    let outfits = list_character_pack_outfits(&pack_dir, asset_root);
    let asset_count = outfits.iter().map(|outfit| outfit.emotions.len()).sum();

    Ok(CharacterPackRegistryItem {
        id: pack_id,
        source: character_path.to_string_lossy().to_string(),
        installed_path: pack_dir.to_string_lossy().to_string(),
        asset_count,
        profile,
        outfits,
    })
}

#[tauri::command]
fn clear_character_voice_profile(pack_id: String) -> Result<CharacterPackRegistryItem, String> {
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

    let asset_root = validated
        .assets
        .asset_root
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or("assets");
    let outfits = list_character_pack_outfits(&pack_dir, asset_root);
    let asset_count = outfits.iter().map(|outfit| outfit.emotions.len()).sum();

    Ok(CharacterPackRegistryItem {
        id: pack_id,
        source: character_path.to_string_lossy().to_string(),
        installed_path: pack_dir.to_string_lossy().to_string(),
        asset_count,
        profile,
        outfits,
    })
}

#[tauri::command]
fn create_character_pack(
    request: CreateCharacterPackRequest,
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

    let characters_dir = creator_kit_characters_dir()?;
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

    let profile = character_json;
    let outfits = list_character_pack_outfits(&pack_dir, "assets");
    let asset_count = outfits.iter().map(|outfit| outfit.emotions.len()).sum();

    Ok(CharacterPackRegistryItem {
        id: pack_id,
        source: pack_dir
            .join("character.json")
            .to_string_lossy()
            .to_string(),
        installed_path: pack_dir.to_string_lossy().to_string(),
        asset_count,
        profile,
        outfits,
    })
}

#[tauri::command]
fn create_character_context_library(
    request: CreateCharacterContextLibraryRequest,
) -> Result<CharacterPackRegistryItem, String> {
    let pack_id = sanitize_pack_id(&request.pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    let folder = sanitize_context_library_folder(&request.folder);
    if folder.is_empty() {
        return Err("资料库文件夹名不能为空。".to_string());
    }
    if folder.chars().count() > 80 {
        return Err("资料库文件夹名过长，请控制在 80 个字符以内。".to_string());
    }
    if folder.eq_ignore_ascii_case(PRIVATE_LOCAL_DIRECTORY) || folder.eq_ignore_ascii_case("assets")
    {
        return Err("这个文件夹名由角色包保留，请换一个名称。".to_string());
    }
    let name = request.name.trim();
    let description = request.description.trim();
    let load_when = request.load_when.trim();
    if name.is_empty() {
        return Err("资料库名称不能为空。".to_string());
    }
    if description.is_empty() {
        return Err("请说明这个资料库保存什么内容。".to_string());
    }
    if load_when.is_empty() {
        return Err("请说明角色应在什么时候读取这个资料库。".to_string());
    }
    if name.chars().count() > 80
        || description.chars().count() > 400
        || load_when.chars().count() > 300
    {
        return Err("资料库说明过长，请精简后再保存。".to_string());
    }

    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    if !pack_dir.is_dir() {
        return Err(format!("角色包 {pack_id} 不存在。"));
    }
    let character_path = pack_dir.join("character.json");
    let raw = fs::read_to_string(&character_path).map_err(|error| error.to_string())?;
    let mut profile: serde_json::Value =
        serde_json::from_str(&raw).map_err(|error| format!("character.json 无效：{error}"))?;
    let Some(profile_object) = profile.as_object_mut() else {
        return Err("character.json 根节点必须是对象。".to_string());
    };

    let libraries = profile_object
        .entry("context_libraries".to_string())
        .or_insert_with(|| serde_json::json!([]));
    let Some(libraries) = libraries.as_array_mut() else {
        return Err("context_libraries 必须是数组。".to_string());
    };
    if libraries.iter().any(|library| {
        library
            .get("folder")
            .and_then(|value| value.as_str())
            .map(|value| value.eq_ignore_ascii_case(&folder))
            .unwrap_or(false)
    }) {
        return Err(format!("资料库文件夹 {folder} 已经登记过了。"));
    }

    let library_dir = safe_child_path(&pack_dir, &folder)?;
    if library_dir.exists() && !library_dir.is_dir() {
        return Err(format!("{folder} 已存在，但不是文件夹。"));
    }
    let created_directory = !library_dir.exists();
    fs::create_dir_all(&library_dir).map_err(|error| error.to_string())?;

    libraries.push(serde_json::json!({
        "folder": folder,
        "name": name,
        "description": description,
        "load_when": load_when,
        "aliases": {}
    }));

    let validated: CharacterPackJson = match serde_json::from_value(profile.clone()) {
        Ok(value) => value,
        Err(error) => {
            if created_directory {
                let _ = fs::remove_dir(&library_dir);
            }
            return Err(format!("更新后的角色数据无效：{error}"));
        }
    };
    let updated = match serde_json::to_string_pretty(&profile) {
        Ok(value) => value,
        Err(error) => {
            if created_directory {
                let _ = fs::remove_dir(&library_dir);
            }
            return Err(format!("序列化角色数据失败：{error}"));
        }
    };
    if let Err(error) = write_text_atomic(&character_path, &updated) {
        if created_directory {
            let _ = fs::remove_dir(&library_dir);
        }
        return Err(error);
    }

    let asset_root = validated
        .assets
        .asset_root
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or("assets");
    let outfits = list_character_pack_outfits(&pack_dir, asset_root);
    let asset_count = outfits.iter().map(|outfit| outfit.emotions.len()).sum();

    Ok(CharacterPackRegistryItem {
        id: pack_id,
        source: character_path.to_string_lossy().to_string(),
        installed_path: pack_dir.to_string_lossy().to_string(),
        asset_count,
        profile,
        outfits,
    })
}

#[tauri::command]
async fn upload_portrait_image(
    _app: AppHandle,
    pack_id: String,
    outfit: String,
    emotion: String,
    image_bytes: Vec<u8>,
) -> Result<UploadPortraitResult, String> {
    tauri::async_runtime::spawn_blocking(move || {
        upload_portrait_image_blocking(pack_id, outfit, emotion, image_bytes)
    })
    .await
    .map_err(|error| format!("立绘导入任务失败：{error}"))?
}

fn upload_portrait_image_blocking(
    pack_id: String,
    outfit: String,
    emotion: String,
    image_bytes: Vec<u8>,
) -> Result<UploadPortraitResult, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }

    let outfit = sanitize_asset_id(&outfit);
    if outfit.is_empty() {
        return Err("服装 ID 不能为空。".to_string());
    }

    let emotion = sanitize_asset_id(&emotion);
    if emotion.is_empty() {
        return Err("表情 ID 不能为空。".to_string());
    }

    if image_bytes.is_empty() {
        return Err("图片数据为空。".to_string());
    }
    if image_bytes.len() > MAX_PORTRAIT_IMAGE_BYTES {
        return Err("图片大小不能超过 20 MB。".to_string());
    }

    /* detect format from magic bytes, reject unsupported types */
    let extension = detect_image_extension(&image_bytes)?;

    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    if !pack_dir.is_dir() {
        return Err(format!("角色包 {pack_id} 不存在。"));
    }

    let (_asset_root, characters_dir) = pack_characters_dir(&pack_dir)?;
    let outfit_dir = safe_child_path(&characters_dir, &outfit)?;

    /* create outfit directory if needed */
    fs::create_dir_all(&outfit_dir).map_err(|error| error.to_string())?;

    /* remove existing image with the same emotion but different extension */
    for existing in ["png", "jpg", "jpeg", "webp"] {
        let old_path = outfit_dir.join(format!("{emotion}.{existing}"));
        if old_path.is_file() && existing != extension {
            fs::remove_file(&old_path).map_err(|error| error.to_string())?;
        }
    }

    let file_name = format!("{emotion}.{extension}");
    let target_path = outfit_dir.join(&file_name);
    write_bytes_atomic(&target_path, &image_bytes)?;

    let size_bytes = image_bytes.len() as u64;

    Ok(UploadPortraitResult {
        ok: true,
        outfit,
        emotion,
        path: target_path.to_string_lossy().to_string(),
        size_bytes,
    })
}

#[tauri::command]
async fn import_generated_portrait_image(
    pack_id: String,
    outfit: String,
    emotion: String,
    image_bytes: Vec<u8>,
    extension: Option<String>,
    mime_type: Option<String>,
    overwrite: bool,
) -> Result<Vec<CharacterPackOutfitAsset>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        import_generated_portrait_image_blocking(
            pack_id,
            outfit,
            emotion,
            image_bytes,
            extension,
            mime_type,
            overwrite,
        )
    })
    .await
    .map_err(|error| format!("生成立绘写入任务失败：{error}"))?
}

fn import_generated_portrait_image_blocking(
    pack_id: String,
    outfit: String,
    emotion: String,
    image_bytes: Vec<u8>,
    extension: Option<String>,
    mime_type: Option<String>,
    overwrite: bool,
) -> Result<Vec<CharacterPackOutfitAsset>, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }

    let outfit = sanitize_asset_id(&outfit);
    if outfit.is_empty() {
        return Err("服装 ID 不能为空。".to_string());
    }

    let emotion = sanitize_asset_id(&emotion);
    if emotion.is_empty() {
        return Err("表情 ID 不能为空。".to_string());
    }

    if image_bytes.is_empty() {
        return Err("图片数据为空。".to_string());
    }
    if image_bytes.len() > MAX_PORTRAIT_IMAGE_BYTES {
        return Err("图片大小不能超过 20 MB。".to_string());
    }

    let extension = resolve_generated_portrait_extension(&image_bytes, extension, mime_type)?;

    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    if !pack_dir.is_dir() {
        return Err(format!("角色包 {pack_id} 不存在。"));
    }

    let (asset_root, characters_dir) = pack_characters_dir(&pack_dir)?;
    let outfit_dir = safe_child_path(&characters_dir, &outfit)?;
    fs::create_dir_all(&outfit_dir).map_err(|error| error.to_string())?;

    let existing_paths = existing_portrait_image_paths(&outfit_dir, &emotion)?;
    if !overwrite && existing_paths.iter().any(|path| path.is_file()) {
        return Err(format!(
            "表情 {emotion} 已存在，请确认 overwrite 后再覆盖。"
        ));
    }
    let target_path = safe_child_path(&outfit_dir, &format!("{emotion}.{extension}"))?;
    let cleanup_paths = if overwrite {
        existing_paths
    } else {
        Vec::new()
    };
    write_bytes_atomic_with_cleanup(&target_path, &image_bytes, &cleanup_paths)?;

    Ok(list_character_pack_outfits(&pack_dir, &asset_root))
}

#[tauri::command]
fn create_portrait_outfit(
    pack_id: String,
    outfit: String,
) -> Result<Vec<CharacterPackOutfitAsset>, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }

    let outfit = sanitize_asset_id(&outfit);
    if outfit.is_empty() {
        return Err("服装名称不能为空。".to_string());
    }

    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    if !pack_dir.is_dir() {
        return Err(format!("角色包 {pack_id} 不存在。"));
    }

    let (asset_root, characters_dir) = pack_characters_dir(&pack_dir)?;
    fs::create_dir_all(&characters_dir).map_err(|error| error.to_string())?;
    let outfit_dir = safe_child_path(&characters_dir, &outfit)?;
    if outfit_dir.exists() {
        return Err(format!("服装 {outfit} 已存在。"));
    }
    fs::create_dir(&outfit_dir).map_err(|error| error.to_string())?;

    Ok(list_character_pack_outfits(&pack_dir, &asset_root))
}

#[tauri::command]
async fn read_portrait_image(
    pack_id: String,
    outfit: String,
    emotion: String,
) -> Result<tauri::ipc::Response, String> {
    tauri::async_runtime::spawn_blocking(move || {
        read_portrait_image_blocking(pack_id, outfit, emotion)
    })
    .await
    .map_err(|error| format!("立绘读取任务失败：{error}"))?
}

fn read_portrait_image_blocking(
    pack_id: String,
    outfit: String,
    emotion: String,
) -> Result<tauri::ipc::Response, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    let outfit = sanitize_asset_id(&outfit);
    if outfit.is_empty() {
        return Err("服装名称不能为空。".to_string());
    }
    let emotion = sanitize_asset_id(&emotion);
    if emotion.is_empty() {
        return Err("表情名称不能为空。".to_string());
    }

    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    let (_asset_root, characters_dir) = pack_characters_dir(&pack_dir)?;
    let outfit_dir = safe_child_path(&characters_dir, &outfit)?;

    for extension in ["png", "jpg", "jpeg", "webp"] {
        let image_path = outfit_dir.join(format!("{emotion}.{extension}"));
        if image_path.is_file() {
            let bytes = fs::read(&image_path).map_err(|error| error.to_string())?;
            return Ok(tauri::ipc::Response::new(bytes));
        }
    }

    Err(format!("找不到立绘：{outfit} / {emotion}"))
}

/// Detect image file extension from magic bytes.
/// Supported: png, jpg/jpeg, webp.
fn detect_image_extension(bytes: &[u8]) -> Result<String, String> {
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

fn resolve_generated_portrait_extension(
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

fn parse_portrait_extension_hint(value: Option<&str>) -> Result<Option<String>, String> {
    let Some(raw) = value.map(str::trim).filter(|value| !value.is_empty()) else {
        return Ok(None);
    };
    normalize_portrait_extension(raw)
        .map(Some)
        .ok_or_else(|| "不支持的立绘扩展名。仅支持 png、jpg、jpeg、webp。".to_string())
}

fn parse_portrait_mime_hint(value: Option<&str>) -> Result<Option<String>, String> {
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

fn normalize_portrait_extension(value: &str) -> Option<String> {
    let normalized = value.trim().trim_start_matches('.').to_ascii_lowercase();
    match normalized.as_str() {
        "png" => Some("png".to_string()),
        "jpg" | "jpeg" => Some("jpg".to_string()),
        "webp" => Some("webp".to_string()),
        _ => None,
    }
}

fn existing_portrait_image_paths(outfit_dir: &Path, emotion: &str) -> Result<Vec<PathBuf>, String> {
    SUPPORTED_PORTRAIT_EXTENSIONS
        .iter()
        .map(|extension| safe_child_path(outfit_dir, &format!("{emotion}.{extension}")))
        .collect()
}

#[tauri::command]
fn list_pack_assets(pack_id: String) -> Result<Vec<CharacterPackOutfitAsset>, String> {
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

#[tauri::command]
async fn export_character_pack(
    app: AppHandle,
    pack_id: String,
) -> Result<ExportedWorkspaceFile, String> {
    tauri::async_runtime::spawn_blocking(move || export_character_pack_blocking(app, pack_id))
        .await
        .map_err(|error| format!("角色包导出任务失败：{error}"))?
}

fn export_character_pack_blocking(
    app: AppHandle,
    pack_id: String,
) -> Result<ExportedWorkspaceFile, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    if !pack_dir.is_dir() {
        return Err(format!("角色包 {pack_id} 不存在。"));
    }

    let zip_name = format!("{pack_id}.zip");
    let export_dir = resolve_desktop_export_dir(&app)?;
    fs::create_dir_all(&export_dir).map_err(|error| error.to_string())?;
    let target_path = unique_child_file_path(&export_dir, &zip_name);

    let file = fs::File::create(&target_path).map_err(|error| error.to_string())?;
    let mut writer = zip::ZipWriter::new(file);
    let options = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated);

    let pack_prefix = pack_dir
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or(&pack_id)
        .to_string();

    fn walk_zip(
        writer: &mut zip::ZipWriter<fs::File>,
        base: &Path,
        prefix: &str,
        options: zip::write::SimpleFileOptions,
    ) -> Result<(), String> {
        if !base.is_dir() {
            return Ok(());
        }
        for entry in fs::read_dir(base).map_err(|e| e.to_string())? {
            let entry = entry.map_err(|e| e.to_string())?;
            let path = entry.path();
            let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
            if path.is_dir() && name.eq_ignore_ascii_case(PRIVATE_LOCAL_DIRECTORY) {
                continue;
            }
            let relative = format!("{prefix}/{name}");

            if path.is_dir() {
                writer
                    .add_directory(&relative, options)
                    .map_err(|e| e.to_string())?;
                walk_zip(writer, &path, &relative, options)?;
            } else if path.is_file() {
                writer
                    .start_file(&relative, options)
                    .map_err(|e| e.to_string())?;
                let bytes = fs::read(&path).map_err(|e| e.to_string())?;
                writer.write_all(&bytes).map_err(|e| e.to_string())?;
            }
        }
        Ok(())
    }

    writer
        .add_directory(&pack_prefix, options)
        .map_err(|e| e.to_string())?;
    walk_zip(&mut writer, &pack_dir, &pack_prefix, options)?;

    let file = writer.finish().map_err(|e| e.to_string())?;
    let metadata = file.metadata().map_err(|e| e.to_string())?;

    Ok(ExportedWorkspaceFile {
        ok: true,
        path: target_path.to_string_lossy().to_string(),
        file_name: zip_name,
        size_bytes: Some(metadata.len()),
    })
}

#[tauri::command]
fn delete_portrait_image(
    pack_id: String,
    outfit: String,
    emotion: String,
) -> Result<Vec<CharacterPackOutfitAsset>, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    let outfit = sanitize_asset_id(&outfit);
    if outfit.is_empty() {
        return Err("服装 ID 不能为空。".to_string());
    }
    let emotion = sanitize_asset_id(&emotion);
    if emotion.is_empty() {
        return Err("表情 ID 不能为空。".to_string());
    }

    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    let (asset_root, characters_dir) = pack_characters_dir(&pack_dir)?;
    let outfit_dir = safe_child_path(&characters_dir, &outfit)?;

    let mut deleted = false;
    for ext in ["png", "jpg", "jpeg", "webp"] {
        let path = outfit_dir.join(format!("{emotion}.{ext}"));
        if path.is_file() {
            fs::remove_file(&path).map_err(|error| error.to_string())?;
            deleted = true;
        }
    }
    if !deleted {
        return Err(format!("表情 {emotion} 在服装 {outfit} 中不存在。"));
    }

    Ok(list_character_pack_outfits(&pack_dir, &asset_root))
}

#[tauri::command]
fn rename_portrait_emotion(
    pack_id: String,
    outfit: String,
    old_emotion: String,
    new_emotion: String,
) -> Result<Vec<CharacterPackOutfitAsset>, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    let outfit = sanitize_asset_id(&outfit);
    if outfit.is_empty() {
        return Err("服装 ID 不能为空。".to_string());
    }
    let old_emotion = sanitize_asset_id(&old_emotion);
    if old_emotion.is_empty() {
        return Err("原表情 ID 不能为空。".to_string());
    }
    let new_emotion = sanitize_asset_id(&new_emotion);
    if new_emotion.is_empty() {
        return Err("新表情 ID 不能为空。".to_string());
    }
    if old_emotion == new_emotion {
        return Err("新旧表情 ID 相同。".to_string());
    }

    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    let (asset_root, characters_dir) = pack_characters_dir(&pack_dir)?;
    let outfit_dir = safe_child_path(&characters_dir, &outfit)?;

    let mut renamed = false;
    for ext in ["png", "jpg", "jpeg", "webp"] {
        let old_path = outfit_dir.join(format!("{old_emotion}.{ext}"));
        let new_path = outfit_dir.join(format!("{new_emotion}.{ext}"));
        if old_path.is_file() && !new_path.exists() {
            fs::rename(&old_path, &new_path).map_err(|error| error.to_string())?;
            renamed = true;
        }
    }
    if !renamed {
        return Err(format!(
            "表情 {old_emotion} 不存在或 {new_emotion} 已占用。"
        ));
    }

    Ok(list_character_pack_outfits(&pack_dir, &asset_root))
}

#[tauri::command]
fn rename_portrait_outfit(
    pack_id: String,
    old_outfit: String,
    new_outfit: String,
) -> Result<Vec<CharacterPackOutfitAsset>, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    let old_outfit = sanitize_asset_id(&old_outfit);
    if old_outfit.is_empty() {
        return Err("原服装 ID 不能为空。".to_string());
    }
    let new_outfit = sanitize_asset_id(&new_outfit);
    if new_outfit.is_empty() {
        return Err("新服装 ID 不能为空。".to_string());
    }
    if old_outfit == new_outfit {
        return Err("新旧服装 ID 相同。".to_string());
    }

    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    let (asset_root, chars_dir) = pack_characters_dir(&pack_dir)?;
    let old_dir = safe_child_path(&chars_dir, &old_outfit)?;
    let new_dir = safe_child_path(&chars_dir, &new_outfit)?;

    if !old_dir.is_dir() {
        return Err(format!("服装 {old_outfit} 不存在。"));
    }
    if new_dir.exists() {
        return Err(format!("服装 {new_outfit} 已存在。"));
    }

    fs::rename(&old_dir, &new_dir).map_err(|error| error.to_string())?;
    Ok(list_character_pack_outfits(&pack_dir, &asset_root))
}

#[tauri::command]
fn set_default_emotion(pack_id: String, field: String, value: String) -> Result<String, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    if !["default_emotion", "default_outfit", "music_emotion"].contains(&field.as_str()) {
        return Err(format!("不允许修改字段：{field}"));
    }
    let value = sanitize_asset_id(&value);
    if value.is_empty() {
        return Err(format!("{field} 的值不能为空。"));
    }

    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    let character_path = pack_dir.join("character.json");
    if !character_path.is_file() {
        return Err(format!("{pack_id} 缺少 character.json。"));
    }

    let raw = fs::read_to_string(&character_path).map_err(|error| error.to_string())?;
    let mut profile: serde_json::Value =
        serde_json::from_str(&raw).map_err(|error| format!("character.json 无效：{error}"))?;

    if let Some(appearance) = profile
        .get_mut("appearance")
        .and_then(|v| v.as_object_mut())
    {
        appearance.insert(field.clone(), serde_json::Value::String(value.clone()));
    }

    /* validate */
    let _validated: CharacterPackJson = serde_json::from_value(profile.clone())
        .map_err(|error| format!("更新后的角色数据无效：{error}"))?;

    /* atomic write */
    let updated =
        serde_json::to_string_pretty(&profile).map_err(|error| format!("序列化失败：{error}"))?;
    write_text_atomic(&character_path, &updated)?;

    Ok(format!("{field} → {value}"))
}

#[tauri::command]
fn set_default_portrait(
    pack_id: String,
    outfit: String,
    emotion: String,
) -> Result<String, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    let outfit = sanitize_asset_id(&outfit);
    if outfit.is_empty() {
        return Err("服装名称不能为空。".to_string());
    }
    let emotion = sanitize_asset_id(&emotion);
    if emotion.is_empty() {
        return Err("表情名称不能为空。".to_string());
    }

    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    let character_path = pack_dir.join("character.json");
    if !character_path.is_file() {
        return Err(format!("{pack_id} 缺少 character.json。"));
    }

    let (_asset_root, characters_dir) = pack_characters_dir(&pack_dir)?;
    let outfit_dir = safe_child_path(&characters_dir, &outfit)?;
    if !outfit_dir.is_dir() {
        return Err(format!("服装 {outfit} 不存在。"));
    }
    if !find_emotion_image(&outfit_dir, &emotion) {
        return Err(format!("服装 {outfit} 中没有表情 {emotion}。"));
    }

    let raw = fs::read_to_string(&character_path).map_err(|error| error.to_string())?;
    let mut profile: serde_json::Value =
        serde_json::from_str(&raw).map_err(|error| format!("character.json 无效：{error}"))?;
    let appearance = profile
        .get_mut("appearance")
        .and_then(|value| value.as_object_mut())
        .ok_or_else(|| "character.json 缺少 appearance 对象。".to_string())?;
    appearance.insert(
        "default_outfit".to_string(),
        serde_json::Value::String(outfit.clone()),
    );
    appearance.insert(
        "default_emotion".to_string(),
        serde_json::Value::String(emotion.clone()),
    );

    let _validated: CharacterPackJson = serde_json::from_value(profile.clone())
        .map_err(|error| format!("更新后的角色数据无效：{error}"))?;
    let updated =
        serde_json::to_string_pretty(&profile).map_err(|error| format!("序列化失败：{error}"))?;
    write_text_atomic(&character_path, &updated)?;

    Ok(format!("{outfit} / {emotion}"))
}

#[tauri::command]
fn save_calibration(
    pack_id: String,
    outfit_id: String,
    layout: serde_json::Value,
) -> Result<String, String> {
    let pack_id = sanitize_pack_id(&pack_id);
    if pack_id.is_empty() {
        return Err("无效的角色包 ID。".to_string());
    }
    let outfit_id = sanitize_asset_id(&outfit_id);
    if outfit_id.is_empty() {
        return Err("服装 ID 不能为空。".to_string());
    }

    let characters_dir = creator_kit_characters_dir()?;
    let pack_dir = safe_child_path(&characters_dir, &pack_id)?;
    let character_path = pack_dir.join("character.json");
    if !character_path.is_file() {
        return Err(format!("{pack_id} 缺少 character.json。"));
    }

    let raw = fs::read_to_string(&character_path).map_err(|error| error.to_string())?;
    let mut profile: serde_json::Value =
        serde_json::from_str(&raw).map_err(|error| format!("character.json 无效：{error}"))?;

    /* ensure layout.outfits exists */
    if profile.get("layout").is_none() {
        profile["layout"] = serde_json::json!({ "outfits": {} });
    }
    if profile["layout"].get("outfits").is_none() {
        profile["layout"]["outfits"] = serde_json::json!({});
    }
    profile["layout"]["outfits"][&outfit_id] = layout;

    /* validate */
    let _validated: CharacterPackJson = serde_json::from_value(profile.clone())
        .map_err(|error| format!("更新后的角色数据无效：{error}"))?;

    /* atomic write */
    let updated =
        serde_json::to_string_pretty(&profile).map_err(|error| format!("序列化失败：{error}"))?;
    write_text_atomic(&character_path, &updated)?;

    Ok(format!("{pack_id}/{outfit_id} 校准已保存"))
}

/// Read the `asset_root` from a pack's character.json. Falls back to "assets".
fn read_pack_asset_root(pack_dir: &Path) -> Result<String, String> {
    let character_path = pack_dir.join("character.json");
    if !character_path.is_file() {
        return Err(format!("{} 缺少 character.json。", pack_dir.display()));
    }
    let raw = fs::read_to_string(&character_path).map_err(|error| error.to_string())?;
    let character: CharacterPackJson =
        serde_json::from_str(&raw).map_err(|error| format!("character.json 无效：{error}"))?;
    Ok(character
        .assets
        .asset_root
        .as_deref()
        .map(str::trim)
        .filter(|v| !v.is_empty())
        .unwrap_or("assets")
        .to_string())
}

fn pack_characters_dir(pack_dir: &Path) -> Result<(String, PathBuf), String> {
    let asset_root = read_pack_asset_root(pack_dir)?;
    let asset_root_dir = safe_child_path(pack_dir, &asset_root)?;
    let characters_dir = safe_child_path(&asset_root_dir, "characters")?;
    Ok((asset_root, characters_dir))
}

fn sanitize_asset_id(value: &str) -> String {
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

fn sanitize_context_library_folder(value: &str) -> String {
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

/// Generate the default `persona.md` content for a character pack.
/// This is regenerated on every save until a manual-editing feature is added.
fn persona_md_text(name: &str, user_title: &str) -> String {
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
fn deep_merge_json(target: &mut serde_json::Value, source: &serde_json::Value) {
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
    let backup_name = format!(".{pack_id}.backup_{}", current_time_millis());
    let backup = safe_child_path(&characters_dir, &backup_name)?;

    if destination.exists() && !overwrite {
        return Err(format!("角色包 {pack_id} 已存在。勾选覆盖同名后再导入。"));
    }

    let install_result = (|| {
        extract_character_pack_entries(&entries, &root, &temp_pack_dir)?;
        let validation = validate_imported_character_pack(&temp_pack_dir)?;

        fs::create_dir_all(&characters_dir).map_err(|error| error.to_string())?;
        let mut has_backup = false;
        if destination.exists() {
            assert_safe_remove_target(&characters_dir, &destination)?;
            fs::rename(&destination, &backup).map_err(|error| error.to_string())?;
            has_backup = true;
        }
        if let Err(error) = fs::rename(&temp_pack_dir, &destination) {
            if has_backup {
                let _ = fs::rename(&backup, &destination);
            }
            return Err(error.to_string());
        }
        if has_backup {
            let previous_local = safe_child_path(&backup, PRIVATE_LOCAL_DIRECTORY)?;
            if previous_local.exists() {
                let installed_local = safe_child_path(&destination, PRIVATE_LOCAL_DIRECTORY)?;
                if installed_local.exists() {
                    fs::remove_dir_all(&installed_local).map_err(|error| error.to_string())?;
                }
                if let Err(error) = fs::rename(&previous_local, &installed_local) {
                    assert_safe_remove_target(&characters_dir, &destination)?;
                    let _ = fs::remove_dir_all(&destination);
                    let _ = fs::rename(&backup, &destination);
                    return Err(format!("保留角色本机私有数据失败：{error}"));
                }
            }
            let _ = fs::remove_dir_all(&backup);
        }

        Ok(CharacterPackInstallResult {
            pack_id,
            character_id: validation.character_id,
            character_name: validation.character_name,
            installed_path: destination.to_string_lossy().to_string(),
            file_count: entries
                .iter()
                .filter(|entry| entry.name.starts_with(&format!("{root}/")))
                .filter(|entry| !entry.name.ends_with('/'))
                .filter(|entry| {
                    let relative = entry.name.trim_start_matches(&format!("{root}/"));
                    !is_private_local_relative_path(relative)
                })
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
        if is_private_local_relative_path(relative) {
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

fn is_private_local_relative_path(relative: &str) -> bool {
    relative
        .replace('\\', "/")
        .split('/')
        .next()
        .map(|part| part.eq_ignore_ascii_case(PRIVATE_LOCAL_DIRECTORY))
        .unwrap_or(false)
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
    let characters_dir = akane_data_root()?.join("characters");
    fs::create_dir_all(&characters_dir).map_err(|error| error.to_string())?;
    Ok(characters_dir)
}

fn akane_data_root() -> Result<PathBuf, String> {
    if let Some(explicit) = std::env::var_os(DATA_ROOT_ENV).filter(|value| !value.is_empty()) {
        return absolute_path(PathBuf::from(explicit));
    }

    #[cfg(windows)]
    {
        if let Some(base) = std::env::var_os("LOCALAPPDATA").or_else(|| std::env::var_os("APPDATA"))
        {
            return Ok(PathBuf::from(base).join(APP_DIRECTORY_NAME));
        }
        if let Some(home) = std::env::var_os("USERPROFILE") {
            return Ok(PathBuf::from(home)
                .join("AppData")
                .join("Local")
                .join(APP_DIRECTORY_NAME));
        }
    }

    #[cfg(target_os = "macos")]
    {
        if let Some(home) = std::env::var_os("HOME") {
            return Ok(PathBuf::from(home)
                .join("Library")
                .join("Application Support")
                .join(APP_DIRECTORY_NAME));
        }
    }

    #[cfg(all(unix, not(target_os = "macos")))]
    {
        if let Some(base) = std::env::var_os("XDG_DATA_HOME") {
            return Ok(PathBuf::from(base).join(APP_DIRECTORY_NAME));
        }
        if let Some(home) = std::env::var_os("HOME") {
            return Ok(PathBuf::from(home)
                .join(".local")
                .join("share")
                .join(APP_DIRECTORY_NAME));
        }
    }

    Err("无法定位 Akane 用户数据目录。".to_string())
}

fn absolute_path(path: PathBuf) -> Result<PathBuf, String> {
    if path.is_absolute() {
        return Ok(path);
    }
    std::env::current_dir()
        .map(|current| current.join(path))
        .map_err(|error| error.to_string())
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

fn open_url_with_system(url: &str) -> Result<(), String> {
    #[cfg(windows)]
    let mut command = {
        let mut command = Command::new("explorer");
        command.arg(url);
        command
    };

    #[cfg(target_os = "macos")]
    let mut command = {
        let mut command = Command::new("open");
        command.arg(url);
        command
    };

    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = {
        let mut command = Command::new("xdg-open");
        command.arg(url);
        command
    };

    command.spawn().map_err(|error| error.to_string())?;
    Ok(())
}

fn normalize_public_external_url(raw_url: &str) -> Result<String, String> {
    let url = raw_url.trim();
    if url.is_empty() || url.len() > 1600 {
        return Err("网址为空或过长。".to_string());
    }
    if url.chars().any(|ch| ch.is_control() || ch.is_whitespace()) {
        return Err("网址包含不安全字符。".to_string());
    }

    let scheme_end = url
        .find("://")
        .ok_or_else(|| "只支持 http/https 网址。".to_string())?;
    let scheme = url[..scheme_end].to_ascii_lowercase();
    if scheme != "http" && scheme != "https" {
        return Err("只支持 http/https 网址。".to_string());
    }
    let after_scheme = &url[scheme_end + 3..];
    let authority_end = after_scheme
        .find(['/', '?', '#'])
        .unwrap_or(after_scheme.len());
    let authority = &after_scheme[..authority_end];
    if authority.is_empty() || authority.contains('@') {
        return Err("网址主机不安全。".to_string());
    }

    let host = if authority.starts_with('[') {
        let end = authority
            .find(']')
            .ok_or_else(|| "网址主机不安全。".to_string())?;
        authority[1..end].to_string()
    } else {
        authority
            .split(':')
            .next()
            .unwrap_or("")
            .trim_matches('.')
            .to_string()
    };
    if is_private_or_local_host(&host) {
        return Err("不能打开 localhost 或内网地址。".to_string());
    }
    Ok(url.to_string())
}

fn is_private_or_local_host(host: &str) -> bool {
    let lowered = host.trim().trim_matches(['[', ']']).to_ascii_lowercase();
    if lowered.is_empty() || lowered == "localhost" || lowered.ends_with(".local") {
        return true;
    }
    if let Ok(ip) = lowered.parse::<IpAddr>() {
        return ip.is_loopback()
            || ip.is_unspecified()
            || ip.is_multicast()
            || match ip {
                IpAddr::V4(value) => {
                    value.is_private() || value.is_link_local() || value.octets()[0] == 0
                }
                IpAddr::V6(value) => value.is_unique_local() || value.is_unicast_link_local(),
            };
    }
    false
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
            if ch.is_control() || matches!(ch, '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*')
            {
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
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("");
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

fn write_text_atomic(path: &Path, text: &str) -> Result<(), String> {
    write_bytes_atomic(path, text.as_bytes())
}

fn write_bytes_atomic(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("无法定位写入目录：{}", path.display()))?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let file_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| format!("无效文件名：{}", path.display()))?;
    let tmp_path = parent.join(format!(".{file_name}.tmp"));
    fs::write(&tmp_path, bytes).map_err(|error| error.to_string())?;
    fs::rename(&tmp_path, path).map_err(|error| error.to_string())
}

fn write_bytes_atomic_with_cleanup(
    path: &Path,
    bytes: &[u8],
    cleanup_paths: &[PathBuf],
) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("无法定位写入目录：{}", path.display()))?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let file_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| format!("无效文件名：{}", path.display()))?;
    let tmp_path = parent.join(format!(".{file_name}.tmp"));
    fs::write(&tmp_path, bytes).map_err(|error| error.to_string())?;
    if path.is_file() {
        fs::remove_file(path).map_err(|error| error.to_string())?;
    }
    fs::rename(&tmp_path, path).map_err(|error| error.to_string())?;
    for cleanup_path in cleanup_paths {
        if cleanup_path != path && cleanup_path.is_file() {
            fs::remove_file(cleanup_path).map_err(|error| error.to_string())?;
        }
    }
    Ok(())
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

fn sanitize_voice_profile_id(value: &str) -> String {
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

fn normalize_character_voice_provider(value: &str) -> Result<String, String> {
    let raw = value.trim().to_ascii_lowercase();
    match raw.as_str() {
        "" | "gpt_sovits" | "gpt-sovits" | "gptsovits" | "provider.tts.gpt_sovits.local" => {
            Ok("gpt_sovits".to_string())
        }
        _ => Err("当前只支持把角色声线设置为 GPT-SoVITS 声线档案。".to_string()),
    }
}

fn sanitize_voice_notes(value: Option<&str>) -> String {
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
fn apply_window_state(window: Window, mut state: PetState) -> Result<WindowGeometry, String> {
    normalize_pet_state(&mut state);
    window
        .set_always_on_top(state.always_on_top)
        .map_err(|error| error.to_string())?;
    window
        .set_skip_taskbar(state.skip_taskbar)
        .map_err(|error| error.to_string())?;
    window
        .set_ignore_cursor_events(false)
        .map_err(|error| error.to_string())?;

    if let (Some(width), Some(height)) = (state.width, state.height) {
        window
            .set_size(Size::Physical(PhysicalSize::new(width, height)))
            .map_err(|error| error.to_string())?;
    } else {
        set_scaled_size(&window, state.scale)?;
    }

    let restored_position = if let (Some(x), Some(y)) = (state.x, state.y) {
        if saved_window_position_visible(&window, x, y) {
            window
                .set_position(Position::Physical(PhysicalPosition::new(x, y)))
                .map_err(|error| error.to_string())?;
            true
        } else {
            false
        }
    } else {
        false
    };

    if !restored_position {
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
    if env_flag_enabled("AKANE_OPEN_MODEL_SETTINGS") {
        "control-center-lab.html?page=model"
    } else {
        "control-center-lab.html"
    }
}

fn env_flag_enabled(name: &str) -> bool {
    match std::env::var(name) {
        Ok(value) => value == "1" || value.eq_ignore_ascii_case("true"),
        Err(_) => false,
    }
}

#[tauri::command]
async fn open_settings_window(app: AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("settings") {
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
        return Ok(());
    }

    let builder = WebviewWindowBuilder::new(
        &app,
        "settings",
        WebviewUrl::App(settings_window_url().into()),
    )
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
async fn open_panel_window(app: AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("panel") {
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
        return Ok(());
    }

    let panel = WebviewWindowBuilder::new(&app, "panel", WebviewUrl::App("panel.html".into()))
        .inner_size(400.0, 580.0)
        .min_inner_size(360.0, 420.0)
        .decorations(false)
        .transparent(true)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .shadow(false)
        .center()
        .visible(false)
        .build()
        .map_err(|error| error.to_string())?;

    // Position next to the pet window; prefer right side, fall back to left if near screen edge
    if let Some(pet) = app.get_webview_window("main") {
        if let (Ok(pos), Ok(pet_size)) = (pet.outer_position(), pet.outer_size()) {
            let panel_w = panel.outer_size().map(|s| s.width as i32).unwrap_or(412);
            let gap = 6i32;
            let screen_w = pet
                .current_monitor()
                .ok()
                .flatten()
                .map(|m| m.size().width as i32)
                .unwrap_or(1920);
            let x_right = pos.x + pet_size.width as i32 + gap;
            let x = if x_right + panel_w <= screen_w {
                x_right
            } else {
                (pos.x - panel_w - gap).max(0)
            };
            // Keep panel top within screen height
            let screen_h = pet
                .current_monitor()
                .ok()
                .flatten()
                .map(|m| m.size().height as i32)
                .unwrap_or(1080);
            let panel_h = panel.outer_size().map(|s| s.height as i32).unwrap_or(592);
            let y = pos.y.min(screen_h - panel_h).max(0);
            let _ = panel.set_position(Position::Physical(PhysicalPosition { x, y }));
        }
    }

    panel.show().map_err(|error| error.to_string())?;
    panel.set_focus().map_err(|error| error.to_string())
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
async fn open_shop_window(app: AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("shop") {
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
        return Ok(());
    }

    let builder = WebviewWindowBuilder::new(&app, "shop", WebviewUrl::App("shop.html".into()))
        .title("Akane Next 小卖部")
        .inner_size(660.0, 560.0)
        .min_inner_size(480.0, 420.0)
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
            .title("角色工坊")
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

#[tauri::command]
fn resize_pet_window(window: Window, width: f64, height: f64) -> Result<(), String> {
    if width < 180.0 || height < 180.0 || width > 1200.0 || height > 1200.0 {
        return Err(format!("窗口尺寸超出范围：{width}x{height}"));
    }
    window
        .set_size(Size::Logical(LogicalSize::new(width, height)))
        .map_err(|error| error.to_string())
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

fn saved_window_position_visible(window: &Window, x: i32, y: i32) -> bool {
    let Ok(size) = window.outer_size() else {
        return false;
    };
    let width = size.width as i32;
    let height = size.height as i32;
    if width <= 0 || height <= 0 {
        return false;
    }

    let right = x.saturating_add(width);
    let bottom = y.saturating_add(height);
    let min_visible_width = width.min(96).max(1);
    let min_visible_height = height.min(96).max(1);
    let Ok(monitors) = window.available_monitors() else {
        return false;
    };

    monitors.into_iter().any(|monitor| {
        let area = monitor.work_area();
        let area_left = area.position.x;
        let area_top = area.position.y;
        let area_right = area_left.saturating_add(area.size.width as i32);
        let area_bottom = area_top.saturating_add(area.size.height as i32);
        let visible_width = right.min(area_right).saturating_sub(x.max(area_left));
        let visible_height = bottom.min(area_bottom).saturating_sub(y.max(area_top));
        visible_width >= min_visible_width && visible_height >= min_visible_height
    })
}

fn state_path(app: &AppHandle) -> Result<PathBuf, String> {
    let path = akane_data_root()?.join("state").join(STATE_FILE);
    if !path.exists() {
        let legacy_path = app
            .path()
            .app_config_dir()
            .map(|dir| dir.join(STATE_FILE))
            .map_err(|error| error.to_string())?;
        if legacy_path.is_file() {
            let parent = path
                .parent()
                .ok_or_else(|| "无法定位桌宠状态目录。".to_string())?;
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
            fs::copy(legacy_path, &path).map_err(|error| error.to_string())?;
        }
    }
    Ok(path)
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

fn system_media_unavailable(reason: &str, _detail: impl ToString) -> SystemMediaSnapshot {
    SystemMediaSnapshot {
        ok: false,
        status: "unavailable".to_string(),
        reason: reason.to_string(),
        captured_at: current_time_millis(),
        platform: std::env::consts::OS.to_string(),
        track_key: String::new(),
        title: String::new(),
        artist: String::new(),
        album: String::new(),
        source_app: String::new(),
        playback_status: "unknown".to_string(),
        is_playing: false,
        position_seconds: None,
        duration_seconds: None,
    }
}

fn normalize_system_media_control_action(action: &str) -> String {
    match action.trim().to_ascii_lowercase().as_str() {
        "play" | "resume" => "play",
        "pause" => "pause",
        "stop" => "stop",
        "next" | "skip" => "next",
        "previous" | "prev" => "previous",
        _ => "",
    }
    .to_string()
}

fn system_media_control_unavailable(
    action: &str,
    reason: &str,
    _detail: impl ToString,
) -> SystemMediaControlResult {
    SystemMediaControlResult {
        ok: false,
        status: "unavailable".to_string(),
        reason: reason.to_string(),
        action: normalize_system_media_control_action(action),
        captured_at: current_time_millis(),
        platform: std::env::consts::OS.to_string(),
        track_key: String::new(),
        title: String::new(),
        artist: String::new(),
        source_app: String::new(),
        playback_status: "unknown".to_string(),
    }
}

fn system_media_control_from_snapshot(
    action: &str,
    ok: bool,
    status: &str,
    reason: &str,
    snapshot: Option<SystemMediaSnapshot>,
) -> SystemMediaControlResult {
    let media = snapshot.unwrap_or_else(|| system_media_unavailable(reason, ""));
    SystemMediaControlResult {
        ok,
        status: status.to_string(),
        reason: reason.to_string(),
        action: action.to_string(),
        captured_at: current_time_millis(),
        platform: media.platform,
        track_key: media.track_key,
        title: media.title,
        artist: media.artist,
        source_app: media.source_app,
        playback_status: media.playback_status,
    }
}

fn control_system_media_blocking(action: String) -> SystemMediaControlResult {
    let normalized = normalize_system_media_control_action(&action);
    if normalized.is_empty() {
        return system_media_control_unavailable(&action, "invalid_action", "");
    }
    control_system_media_platform(&normalized)
}

#[cfg(target_os = "macos")]
fn control_system_media_platform(action: &str) -> SystemMediaControlResult {
    match control_system_media_macos(action) {
        Ok(result) => result,
        Err(error) => system_media_control_unavailable(action, "control_failed", error),
    }
}

#[cfg(all(not(windows), not(target_os = "macos")))]
fn control_system_media_platform(action: &str) -> SystemMediaControlResult {
    // TODO: Linux MPRIS via D-Bus. Keep a structured unavailable response
    // until a real MPRIS implementation exists.
    system_media_control_unavailable(action, "unsupported_platform", std::env::consts::OS)
}

#[cfg(windows)]
fn control_system_media_platform(action: &str) -> SystemMediaControlResult {
    match control_system_media_windows(action) {
        Ok(result) => result,
        Err(error) => system_media_control_unavailable(action, "control_failed", error),
    }
}

#[cfg(not(windows))]
fn read_current_system_media() -> SystemMediaSnapshot {
    system_media_unavailable("unsupported_platform", std::env::consts::OS)
}

#[cfg(windows)]
fn read_current_system_media() -> SystemMediaSnapshot {
    match read_current_system_media_windows() {
        Ok(snapshot) => snapshot,
        Err(error) => system_media_unavailable("read_failed", error),
    }
}

#[cfg(windows)]
fn read_current_system_media_windows() -> Result<SystemMediaSnapshot, String> {
    let manager = GlobalSystemMediaTransportControlsSessionManager::RequestAsync()
        .map_err(|error| error.to_string())?
        .get()
        .map_err(|error| error.to_string())?;
    let current_session = manager
        .GetCurrentSession()
        .map_err(|error| error.to_string())?;
    let current_snapshot = if current_session.as_raw().is_null() {
        None
    } else {
        read_system_media_session(&current_session).ok()
    };

    if let Some(snapshot) = current_snapshot.as_ref() {
        if snapshot.ok && snapshot.playback_status == "playing" {
            return Ok(snapshot.clone());
        }
    }

    let sessions = manager.GetSessions().map_err(|error| error.to_string())?;
    let size = sessions.Size().map_err(|error| error.to_string())?;
    let mut fallback_snapshot: Option<SystemMediaSnapshot> = None;
    for index in 0..size {
        let session = sessions.GetAt(index).map_err(|error| error.to_string())?;
        if session.as_raw().is_null() {
            continue;
        }
        let snapshot = match read_system_media_session(&session) {
            Ok(value) => value,
            Err(_) => continue,
        };
        if snapshot.ok && snapshot.playback_status == "playing" {
            return Ok(snapshot);
        }
        if fallback_snapshot.is_none() && snapshot.ok {
            fallback_snapshot = Some(snapshot);
        }
    }

    if let Some(snapshot) = current_snapshot {
        return Ok(snapshot);
    }
    if let Some(snapshot) = fallback_snapshot {
        return Ok(snapshot);
    }
    Ok(system_media_unavailable("no_active_session", ""))
}

#[cfg(target_os = "macos")]
fn control_system_media_macos(action: &str) -> Result<SystemMediaControlResult, String> {
    let script = match action {
        "play" => r#"tell application "Music" to play"#,
        "pause" => r#"tell application "Music" to pause"#,
        "next" => r#"tell application "Music" to next track"#,
        "previous" => r#"tell application "Music" to previous track"#,
        "stop" => r#"tell application "Music" to stop"#,
        _ => {
            return Ok(system_media_control_unavailable(
                action,
                "invalid_action",
                "",
            ))
        }
    };
    let output = Command::new("osascript")
        .arg("-e")
        .arg(script)
        .output()
        .map_err(|error| error.to_string())?;
    let ok = output.status.success();
    Ok(system_media_control_from_snapshot(
        action,
        ok,
        if ok { "executed" } else { "not-executed" },
        if ok { "" } else { "apple_music_unavailable" },
        None,
    ))
}

#[cfg(windows)]
fn control_system_media_windows(action: &str) -> Result<SystemMediaControlResult, String> {
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYBD_EVENT_FLAGS, KEYEVENTF_KEYUP,
        VIRTUAL_KEY, VK_MEDIA_NEXT_TRACK, VK_MEDIA_PLAY_PAUSE, VK_MEDIA_PREV_TRACK, VK_MEDIA_STOP,
    };

    let before = read_current_system_media_windows().ok();
    if action == "play"
        && before
            .as_ref()
            .map(|snapshot| snapshot.playback_status == "playing")
            .unwrap_or(false)
    {
        return Ok(system_media_control_from_snapshot(
            action,
            true,
            "already-playing",
            "",
            before,
        ));
    }
    if action == "pause"
        && before
            .as_ref()
            .map(|snapshot| snapshot.playback_status == "paused")
            .unwrap_or(false)
    {
        return Ok(system_media_control_from_snapshot(
            action,
            true,
            "already-paused",
            "",
            before,
        ));
    }

    // Windows exposes one media key for play/pause. Guard above keeps
    // explicit play/pause actions idempotent when the current state is known.
    let vk: VIRTUAL_KEY = match action {
        "play" | "pause" => VK_MEDIA_PLAY_PAUSE,
        "next" => VK_MEDIA_NEXT_TRACK,
        "previous" => VK_MEDIA_PREV_TRACK,
        "stop" => VK_MEDIA_STOP,
        _ => {
            return Ok(system_media_control_unavailable(
                action,
                "invalid_action",
                "",
            ))
        }
    };

    let executed = unsafe {
        let inputs = [
            INPUT {
                r#type: INPUT_KEYBOARD,
                Anonymous: INPUT_0 {
                    ki: KEYBDINPUT {
                        wVk: vk,
                        wScan: 0,
                        dwFlags: KEYBD_EVENT_FLAGS(0),
                        time: 0,
                        dwExtraInfo: 0,
                    },
                },
            },
            INPUT {
                r#type: INPUT_KEYBOARD,
                Anonymous: INPUT_0 {
                    ki: KEYBDINPUT {
                        wVk: vk,
                        wScan: 0,
                        dwFlags: KEYEVENTF_KEYUP,
                        time: 0,
                        dwExtraInfo: 0,
                    },
                },
            },
        ];
        SendInput(&inputs, std::mem::size_of::<INPUT>() as i32) as usize == inputs.len()
    };
    let after = read_current_system_media_windows().ok().or(before);

    Ok(system_media_control_from_snapshot(
        action,
        executed,
        if executed { "executed" } else { "not-executed" },
        if executed { "" } else { "send_input_failed" },
        after,
    ))
}

#[cfg(windows)]
fn read_system_media_session(
    session: &GlobalSystemMediaTransportControlsSession,
) -> Result<SystemMediaSnapshot, String> {
    let media = session
        .TryGetMediaPropertiesAsync()
        .map_err(|error| error.to_string())?
        .get()
        .map_err(|error| error.to_string())?;
    let timeline = session
        .GetTimelineProperties()
        .map_err(|error| error.to_string())?;
    let playback = session
        .GetPlaybackInfo()
        .map_err(|error| error.to_string())?;
    let raw_status = playback
        .PlaybackStatus()
        .map_err(|error| error.to_string())?;

    let title = media
        .Title()
        .map_err(|error| error.to_string())?
        .to_string();
    let artist = media
        .Artist()
        .map_err(|error| error.to_string())?
        .to_string();
    let album = media
        .AlbumTitle()
        .map_err(|error| error.to_string())?
        .to_string();
    let source_app = session
        .SourceAppUserModelId()
        .map_err(|error| error.to_string())?
        .to_string();
    let playback_status = playback_status_label(raw_status.0);
    let position_seconds =
        time_span_seconds(timeline.Position().map_err(|error| error.to_string())?);
    let start_seconds = time_span_seconds(timeline.StartTime().map_err(|error| error.to_string())?);
    let end_seconds = time_span_seconds(timeline.EndTime().map_err(|error| error.to_string())?);
    let duration_seconds = match (start_seconds, end_seconds) {
        (Some(start), Some(end)) if end > start => Some(end - start),
        _ => None,
    };
    let track_key = build_system_media_track_key(&source_app, &title, &artist, &album);
    let has_track = !title.trim().is_empty() || !artist.trim().is_empty();

    Ok(SystemMediaSnapshot {
        ok: has_track,
        status: if has_track { "ready" } else { "empty" }.to_string(),
        reason: String::new(),
        captured_at: current_time_millis(),
        platform: "windows".to_string(),
        track_key,
        title,
        artist,
        album,
        source_app,
        playback_status,
        is_playing: raw_status.0 == 4,
        position_seconds,
        duration_seconds,
    })
}

#[cfg(windows)]
fn playback_status_label(value: i32) -> String {
    match value {
        0 => "closed",
        1 => "opened",
        2 => "changing",
        3 => "stopped",
        4 => "playing",
        5 => "paused",
        _ => "unknown",
    }
    .to_string()
}

#[cfg(windows)]
fn time_span_seconds(value: windows::Foundation::TimeSpan) -> Option<f64> {
    let seconds = value.Duration as f64 / 10_000_000.0;
    if seconds.is_finite() && seconds >= 0.0 {
        Some(seconds)
    } else {
        None
    }
}

#[cfg(windows)]
fn build_system_media_track_key(
    source_app: &str,
    title: &str,
    artist: &str,
    album: &str,
) -> String {
    [source_app, title, artist, album]
        .into_iter()
        .map(|part| part.trim().to_lowercase())
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join("::")
}

fn normalize_pet_state(state: &mut PetState) {
    state.width = None;
    state.height = None;
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
    runtime.width = None;
    runtime.height = None;
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
            let characters_dir = creator_kit_characters_dir()?;
            app.asset_protocol_scope()
                .allow_directory(&characters_dir, true)
                .map_err(|error| error.to_string())?;

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

            if env_flag_enabled("AKANE_OPEN_SETTINGS_ON_START") {
                let app_handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    if let Err(error) = open_settings_window(app_handle).await {
                        eprintln!("Akane settings window could not open on startup: {error}");
                    }
                });
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            load_pet_state,
            save_pet_state,
            activate_character_pack,
            get_desktop_context_snapshot,
            get_current_system_media,
            control_system_media,
            prepare_audio_asset,
            list_character_packs,
            install_character_pack_zip_file,
            install_character_pack_zip_bytes,
            open_character_packs_folder,
            open_local_file,
            show_item_in_folder,
            open_external_url,
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
            open_panel_window,
            open_workspace_window,
            open_shop_window,
            open_workshop_window,
            get_window_geometry,
            save_character_pack,
            set_character_voice_profile,
            clear_character_voice_profile,
            create_character_pack,
            create_character_context_library,
            create_portrait_outfit,
            upload_portrait_image,
            import_generated_portrait_image,
            read_portrait_image,
            list_pack_assets,
            delete_portrait_image,
            rename_portrait_emotion,
            rename_portrait_outfit,
            set_default_emotion,
            set_default_portrait,
            save_calibration,
            resize_pet_window,
            export_character_pack
        ])
        .plugin(tauri_plugin_http::init())
        .run(tauri::generate_context!())
        .expect("error while running Akane Desktop Pet Next");
}
