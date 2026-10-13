use super::focus::focus_existing_window;
use tauri::{AppHandle, WebviewUrl, WebviewWindowBuilder};

use crate::runtime::config::env_flag_enabled;

pub(crate) fn settings_window_url_for(open_model_settings: bool) -> &'static str {
    if open_model_settings {
        "control-center-lab.html?page=model"
    } else {
        "control-center-lab.html"
    }
}

pub(crate) fn settings_window_url() -> &'static str {
    if env_flag_enabled("AKANE_OPEN_QQ_SETUP") {
        return "control-center-lab.html?page=system";
    }
    settings_window_url_for(env_flag_enabled("AKANE_OPEN_MODEL_SETTINGS"))
}

#[tauri::command]
pub(crate) async fn open_settings_window(app: AppHandle) -> Result<(), String> {
    if focus_existing_window(&app, "settings")? {
        return Ok(());
    }

    let builder = WebviewWindowBuilder::new(
        &app,
        "settings",
        WebviewUrl::App(settings_window_url().into()),
    )
    .title("Akane Next 设置")
    .inner_size(1080.0, 720.0)
    .min_inner_size(760.0, 560.0)
    .resizable(true)
    .decorations(false)
    .always_on_top(false)
    .skip_taskbar(false)
    .center()
    .visible(true);

    let window = builder.build().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

#[tauri::command]
pub(crate) async fn open_workspace_window(app: AppHandle) -> Result<(), String> {
    if focus_existing_window(&app, "workspace")? {
        return Ok(());
    }

    let builder =
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

    let window = builder.build().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

#[tauri::command]
pub(crate) async fn open_shop_window(app: AppHandle) -> Result<(), String> {
    if focus_existing_window(&app, "shop")? {
        return Ok(());
    }

    let builder = WebviewWindowBuilder::new(&app, "shop", WebviewUrl::App("shop.html".into()))
        .title("Akane · 片刻小卖铺")
        .inner_size(820.0, 690.0)
        .min_inner_size(480.0, 420.0)
        .resizable(true)
        .decorations(true)
        .always_on_top(false)
        .skip_taskbar(false)
        .center()
        .visible(true);

    let window = builder.build().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

#[tauri::command]
pub(crate) async fn open_workshop_window(app: AppHandle) -> Result<(), String> {
    if focus_existing_window(&app, "workshop")? {
        return Ok(());
    }

    let builder =
        WebviewWindowBuilder::new(&app, "workshop", WebviewUrl::App("workshop.html".into()))
            .title("角色工坊")
            .inner_size(860.0, 620.0)
            .min_inner_size(640.0, 460.0)
            .resizable(true)
            .decorations(true)
            .always_on_top(false)
            .skip_taskbar(false)
            .center()
            .visible(true);

    let window = builder.build().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}
