use crate::character_packs::paths::creator_kit_characters_dir;
use crate::desktop_connection::session::start_desktop_satellite;
use crate::device_control;
#[cfg(windows)]
use crate::pet_windows::hit_test::install_native_hit_test;
use crate::pet_windows::lifecycle::start_desktop_activation_listener;
use crate::pet_windows::secondary::open_settings_window;
use crate::runtime::config::env_flag_enabled;
use crate::runtime::paths::desktop_runtime_cache_dir;
use tauri::Manager;

pub(crate) fn setup(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let characters_dir = creator_kit_characters_dir()?;
    app.asset_protocol_scope()
        .allow_directory(&characters_dir, true)
        .map_err(|error| error.to_string())?;

    // Audio preparation writes into the instance-owned cache, not
    // Tauri's former application cache. Only this managed subtree is
    // readable through asset URLs; dropped source directories stay private.
    let audio_cache_dir = desktop_runtime_cache_dir("attachments/audio")?;
    app.asset_protocol_scope()
        .allow_directory(&audio_cache_dir, true)
        .map_err(|error| error.to_string())?;

    start_desktop_activation_listener(app.handle().clone());
    device_control::start_foreground_server();
    start_desktop_satellite(app.handle().clone());

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
}
