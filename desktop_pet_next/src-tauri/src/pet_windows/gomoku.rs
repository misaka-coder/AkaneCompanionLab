use super::docked::open_docked_window;
use tauri::AppHandle;

#[tauri::command]
pub(crate) async fn open_gomoku_window(app: AppHandle) -> Result<(), String> {
    open_docked_window(
        &app,
        "gomoku",
        "五子棋 · 同伴对弈",
        (396.0, 520.0),
        (360.0, 480.0),
        true,
    )
}
