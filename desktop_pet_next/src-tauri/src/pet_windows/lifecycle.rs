use std::{fs, path::PathBuf, process::Command};
use tauri::{AppHandle, Manager, Window};
#[cfg(windows)]
use windows::{
    core::PCWSTR,
    Win32::{
        Foundation::{CloseHandle, HANDLE, WAIT_OBJECT_0},
        System::Threading::{CreateEventW, WaitForSingleObject, INFINITE},
    },
};

use crate::runtime::config::{
    env_flag_enabled, is_loopback_backend_host, runtime_backend_url, runtime_instance_id,
};
use crate::runtime::paths::akane_data_root;

pub(crate) const DESKTOP_ACTIVATION_EVENT_PREFIX: &str = "Local\\Akane.Desktop.Activate.";

#[tauri::command]
pub(crate) fn close_window(window: Window) -> Result<(), String> {
    window.close().map_err(|error| error.to_string())
}

#[tauri::command]
pub(crate) fn close_pet_app(app: AppHandle) -> Result<(), String> {
    // Keep the main WebView alive: the single-instance activation event must
    // still have a window to show after the user closes the desktop view.
    for window in app.webview_windows().values() {
        window.hide().map_err(|error| error.to_string())?;
    }
    Ok(())
}

#[tauri::command]
pub(crate) fn quit_akane() -> Result<(), String> {
    let launcher = PathBuf::from(
        std::env::var_os("AKANE_MANAGED_LAUNCHER").ok_or("managed_launcher_unavailable")?,
    );
    if !launcher.is_absolute()
        || !launcher.is_file()
        || launcher.file_name().and_then(|name| name.to_str()) != Some("start_akane_next.ps1")
    {
        return Err("managed_launcher_invalid".into());
    }
    let root = akane_data_root()?;
    let instance = runtime_instance_id()?;
    let backend = reqwest::Url::parse(&runtime_backend_url()).map_err(|_| "invalid_backend_url")?;
    let stop_backend = env_flag_enabled("AKANE_MANAGED_STOP_BACKEND");
    if stop_backend && !is_loopback_backend_host(backend.host_str().unwrap_or("")) {
        return Err("refusing_to_stop_remote_backend".into());
    }
    let logs = root.join("logs");
    fs::create_dir_all(&logs).map_err(|error| error.to_string())?;
    let output =
        fs::File::create(logs.join("managed-quit.log")).map_err(|error| error.to_string())?;
    let mut command = Command::new("pwsh");
    command
        .arg("-NoProfile")
        .arg("-File")
        .arg(launcher)
        .args(["-Action", "Stop", "-InstanceId", &instance, "-DataRoot"])
        .arg(root)
        .arg("-BackendPort")
        .arg(backend.port_or_known_default().unwrap_or(9999).to_string())
        .stdout(output.try_clone().map_err(|error| error.to_string())?)
        .stderr(output);
    if !stop_backend {
        command.arg("-SkipBackend");
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    // The existing launcher verifies process and instance ownership, then
    // stops the view, device and owned backend. This acknowledges handoff.
    command.spawn().map_err(|error| error.to_string())?;
    Ok(())
}

pub(crate) fn activate_main_window(app: &AppHandle) {
    if !crate::runtime::launch::ui_presentation_ready() {
        return;
    }
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

#[cfg(windows)]
pub(crate) fn start_desktop_activation_listener(app: AppHandle) {
    use std::os::windows::ffi::OsStrExt;

    let Ok(instance_id) = runtime_instance_id() else {
        return;
    };
    let event_name = format!("{DESKTOP_ACTIVATION_EVENT_PREFIX}{instance_id}");
    let mut wide_name: Vec<u16> = std::ffi::OsStr::new(&event_name)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    let event = unsafe { CreateEventW(None, false, false, PCWSTR(wide_name.as_mut_ptr())) };
    let Ok(event) = event else {
        eprintln!("Akane activation event could not be created");
        return;
    };
    // Raw Win32 handles contain a pointer-sized value and are not `Send` in
    // the bindings. Move the numeric value into the listener thread, then
    // reconstruct the handle there.
    let event_handle = event.0 as usize;
    std::thread::spawn(move || {
        let event = HANDLE(event_handle as *mut std::ffi::c_void);
        loop {
            let result = unsafe { WaitForSingleObject(event, INFINITE) };
            if result != WAIT_OBJECT_0 {
                break;
            }
            activate_main_window(&app);
        }
        let _ = unsafe { CloseHandle(event) };
    });
}

#[cfg(not(windows))]
pub(crate) fn start_desktop_activation_listener(_app: AppHandle) {}
