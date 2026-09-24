use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

static ACTIVE: AtomicBool = AtomicBool::new(false);
static REVISION: AtomicU64 = AtomicU64::new(0);
static ACK: AtomicU64 = AtomicU64::new(0);

#[tauri::command]
pub fn scene_playback_active() -> bool { ACTIVE.load(Ordering::SeqCst) }

#[tauri::command]
pub fn ack_scene_playback(window: WebviewWindow, revision: u64) -> Result<(), String> {
    if window.label() != "main" { return Err("desktop_window_required".into()); }
    if REVISION.load(Ordering::SeqCst) == revision { ACK.store(revision, Ordering::SeqCst); }
    Ok(())
}

fn release(app: &AppHandle) {
    REVISION.fetch_add(1, Ordering::SeqCst);
    ACTIVE.store(false, Ordering::SeqCst);
    let _ = app.emit_to("main", "scene-playback-release", ());
}

#[tauri::command]
pub fn release_scene_playback(app: AppHandle) -> Result<(), String> {
    release(&app);
    Ok(())
}

#[tauri::command]
pub async fn claim_scene_playback(app: AppHandle, window: WebviewWindow) -> Result<(), String> {
    if window.label() != "scene" { return Err("scene_window_required".into()); }
    let revision = REVISION.fetch_add(1, Ordering::SeqCst) + 1;
    ACTIVE.store(true, Ordering::SeqCst);
    if let Err(error) = app.emit_to("main", "scene-playback-claim", revision) {
        release(&app);
        return Err(error.to_string());
    }
    let start = Instant::now();
    while ACK.load(Ordering::SeqCst) != revision {
        if REVISION.load(Ordering::SeqCst) != revision { return Err("scene_playback_superseded".into()); }
        if start.elapsed() > Duration::from_secs(4) {
            release(&app);
            return Err("桌宠尚未完成声音交接，请稍后重新连接。".into());
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
    Ok(())
}

pub fn is_scene_write_path(path: &str) -> bool {
    matches!(path, "scene/actions" | "scene/turn" | "scene/receipts" | "scene/resources/import" | "scene/cancel" | "scene/shop/items/add")
        || path.starts_with("scene/story/")
}

#[tauri::command]
pub async fn open_scene_window(app: AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("scene") {
        window.show().map_err(|error| error.to_string())?;
        window.unminimize().map_err(|error| error.to_string())?;
        return window.set_focus().map_err(|error| error.to_string());
    }
    let window = WebviewWindowBuilder::new(&app, "scene", WebviewUrl::App("scene.html".into()))
        .title("Akane · 片刻小屋")
        .inner_size(1280.0, 800.0)
        .min_inner_size(800.0, 600.0)
        .resizable(true)
        .decorations(true)
        .always_on_top(false)
        .disable_drag_drop_handler()
        .center()
        .build()
        .map_err(|error| error.to_string())?;
    let owner = app.clone();
    window.on_window_event(move |event| {
        if matches!(event, tauri::WindowEvent::Destroyed | tauri::WindowEvent::CloseRequested { .. }) {
            release(&owner);
        }
    });
    window.set_focus().map_err(|error| error.to_string())
}
