#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    fs,
    path::PathBuf,
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
const DEFAULT_OUTFIT: &str = "猫娘";
const DEFAULT_EMOTION: &str = "正常";

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
            session_id: String::new(),
            outfit: DEFAULT_OUTFIT.to_string(),
            current_emotion: DEFAULT_EMOTION.to_string(),
            restore_latest_on_startup: true,
            voice_enabled: false,
            voice_input_enabled: true,
            voice_volume: 0.85,
            desktop_context_enabled: true,
            clipboard_context_enabled: false,
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
    for label in ["settings", "workspace", "main"] {
        if let Some(window) = app.get_webview_window(label) {
            let _ = window.close();
        }
    }
    Ok(())
}

#[tauri::command]
async fn open_settings_window(app: AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("settings") {
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
        return Ok(());
    }

    let mut builder =
        WebviewWindowBuilder::new(&app, "settings", WebviewUrl::App("settings.html".into()))
            .title("Akane Next 设置")
            .inner_size(440.0, 660.0)
            .min_inner_size(380.0, 520.0)
            .resizable(true)
            .decorations(true)
            .always_on_top(true)
            .skip_taskbar(false)
            .center()
            .visible(true);

    #[cfg(windows)]
    if let Some(main_window) = app.get_webview_window("main") {
        builder = builder
            .owner(&main_window)
            .map_err(|error| error.to_string())?;
    }

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

    let mut builder =
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

    #[cfg(windows)]
    if let Some(main_window) = app.get_webview_window("main") {
        builder = builder
            .owner(&main_window)
            .map_err(|error| error.to_string())?;
    }

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
    if state.outfit.trim().is_empty() {
        state.outfit = DEFAULT_OUTFIT.to_string();
    }
    if state.current_emotion.trim().is_empty() {
        state.current_emotion = DEFAULT_EMOTION.to_string();
    }
    state.voice_volume = clamp(state.voice_volume, 0.0, 1.0);
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
            get_window_geometry
        ])
        .plugin(tauri_plugin_http::init())
        .run(tauri::generate_context!())
        .expect("error while running Akane Desktop Pet Next");
}
