use tauri::Window;

use crate::pet_windows::hit_regions::HitRegion;
#[cfg(windows)]
use crate::pet_windows::hit_test::{set_native_hit_regions, set_native_hit_test_enabled};

#[tauri::command]
pub(crate) fn set_always_on_top(window: Window, enabled: bool) -> Result<(), String> {
    window
        .set_always_on_top(enabled)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub(crate) fn set_taskbar_visible(window: Window, visible: bool) -> Result<(), String> {
    window
        .set_skip_taskbar(!visible)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub(crate) fn set_click_through(window: Window, enabled: bool) -> Result<(), String> {
    // Phase A uses whole-window pass-through as a native probe. Future Windows
    // hit-test work can replace this boundary with per-pixel WM_NCHITTEST logic.
    window
        .set_ignore_cursor_events(enabled)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub(crate) fn set_hit_test_enabled(enabled: bool) -> Result<(), String> {
    #[cfg(windows)]
    set_native_hit_test_enabled(enabled);
    #[cfg(not(windows))]
    let _ = enabled;

    Ok(())
}

#[tauri::command]
pub(crate) fn update_hit_regions(regions: Vec<HitRegion>) -> Result<(), String> {
    #[cfg(windows)]
    set_native_hit_regions(regions);
    #[cfg(not(windows))]
    let _ = regions;

    Ok(())
}
