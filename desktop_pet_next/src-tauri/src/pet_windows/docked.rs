use super::focus::focus_existing_window;
use super::placement::panel_position;
use crate::pet_windows::models::WindowGeometry;
use tauri::{AppHandle, Manager, PhysicalPosition, Position, WebviewUrl, WebviewWindowBuilder};

/// Shared native shell for small frameless windows next to the pet.
/// Each caller still registers its command, HTML entry and window permissions.
pub(super) fn open_docked_window(
    app: &AppHandle,
    label: &str,
    title: &str,
    size: (f64, f64),
    min_size: (f64, f64),
    shadow: bool,
) -> Result<(), String> {
    if focus_existing_window(app, label)? {
        return Ok(());
    }
    let window =
        WebviewWindowBuilder::new(app, label, WebviewUrl::App(format!("{label}.html").into()))
            .title(title)
            .inner_size(size.0, size.1)
            .min_inner_size(min_size.0, min_size.1)
            .decorations(false)
            .transparent(true)
            .always_on_top(true)
            .skip_taskbar(true)
            .resizable(false)
            .shadow(shadow)
            .center()
            .visible(false)
            .build()
            .map_err(|error| error.to_string())?;

    if let Some(pet) = app.get_webview_window("main") {
        if let (Ok(pos), Ok(pet_size)) = (pet.outer_position(), pet.outer_size()) {
            let window_size = window
                .outer_size()
                .unwrap_or(tauri::PhysicalSize::new(size.0 as u32, size.1 as u32));
            let area = pet
                .current_monitor()
                .ok()
                .flatten()
                .or_else(|| pet.primary_monitor().ok().flatten())
                .map(|monitor| {
                    let work = monitor.work_area();
                    WindowGeometry {
                        x: work.position.x,
                        y: work.position.y,
                        width: work.size.width,
                        height: work.size.height,
                    }
                })
                .unwrap_or(WindowGeometry {
                    x: 0,
                    y: 0,
                    width: 1920,
                    height: 1080,
                });
            let pet_geometry = WindowGeometry {
                x: pos.x,
                y: pos.y,
                width: pet_size.width,
                height: pet_size.height,
            };
            let (x, y) = panel_position(
                &pet_geometry,
                (window_size.width, window_size.height),
                &area,
            );
            let _ = window.set_position(Position::Physical(PhysicalPosition::new(x, y)));
        }
    }
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}
