use tauri::{LogicalSize, PhysicalPosition, PhysicalSize, Position, Size, Window};

use crate::pet_state::models::PetState;
use crate::pet_state::normalize::{clamp, normalize_pet_state};
use crate::pet_windows::models::WindowGeometry;

pub(crate) const BASE_WIDTH: f64 = 340.0;

pub(crate) const BASE_HEIGHT: f64 = 560.0;

#[tauri::command]
pub(crate) fn apply_window_state(
    window: Window,
    mut state: PetState,
) -> Result<WindowGeometry, String> {
    normalize_pet_state(&mut state)?;
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
pub(crate) fn set_visual_scale(window: Window, scale: f64) -> Result<WindowGeometry, String> {
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
pub(crate) fn reset_window_geometry(window: Window) -> Result<WindowGeometry, String> {
    set_scaled_size(&window, 1.0)?;
    place_window_bottom_right(&window)?;
    get_window_geometry(window)
}

#[tauri::command]
pub(crate) fn move_window_by(window: Window, dx: i32, dy: i32) -> Result<WindowGeometry, String> {
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
pub(crate) fn get_window_geometry(window: Window) -> Result<WindowGeometry, String> {
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
pub(crate) fn resize_pet_window(window: Window, width: f64, height: f64) -> Result<(), String> {
    if width < 180.0 || height < 180.0 || width > 1200.0 || height > 1200.0 {
        return Err(format!("窗口尺寸超出范围：{width}x{height}"));
    }
    window
        .set_size(Size::Logical(LogicalSize::new(width, height)))
        .map_err(|error| error.to_string())
}

pub(crate) fn set_scaled_size(window: &Window, scale: f64) -> Result<(), String> {
    let scale = clamp(scale, 0.75, 1.45);
    let width = (BASE_WIDTH * scale).round();
    let height = (BASE_HEIGHT * scale).round();

    window
        .set_size(Size::Logical(LogicalSize::new(width, height)))
        .map_err(|error| error.to_string())
}

pub(crate) fn place_window_bottom_right(window: &Window) -> Result<(), String> {
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

pub(crate) fn saved_window_position_visible(window: &Window, x: i32, y: i32) -> bool {
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
