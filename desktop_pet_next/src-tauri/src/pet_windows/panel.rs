use super::docked::open_docked_window;
use tauri::AppHandle;

#[tauri::command]
pub(crate) async fn open_panel_window(app: AppHandle) -> Result<(), String> {
    open_docked_window(
        &app,
        "panel",
        "Akane · 面板",
        (400.0, 580.0),
        (360.0, 420.0),
        false,
    )
}
