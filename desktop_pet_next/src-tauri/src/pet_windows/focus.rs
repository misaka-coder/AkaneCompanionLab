use tauri::{AppHandle, Manager};

pub(super) fn focus_existing_window(app: &AppHandle, label: &str) -> Result<bool, String> {
    let Some(window) = app.get_webview_window(label) else {
        return Ok(false);
    };
    window.show().map_err(|error| error.to_string())?;
    window.unminimize().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())?;
    Ok(true)
}
