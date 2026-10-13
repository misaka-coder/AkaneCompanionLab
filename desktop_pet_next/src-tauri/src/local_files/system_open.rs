use crate::character_packs::profile::require_text;
use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
};

use crate::local_files::urls::normalize_public_external_url;
use crate::runtime::paths::akane_data_root;

#[tauri::command]
pub(crate) fn open_managed_skills_folder() -> Result<(), String> {
    let skills_dir = akane_data_root()?.join("skills");
    fs::create_dir_all(&skills_dir).map_err(|error| error.to_string())?;
    open_path_in_file_manager(&skills_dir)
}

#[tauri::command]
pub(crate) fn open_local_file(path: String) -> Result<(), String> {
    let path = canonical_existing_path(&path)?;
    open_path_with_system(&path)
}

#[tauri::command]
pub(crate) fn show_item_in_folder(path: String) -> Result<(), String> {
    let path = canonical_existing_path(&path)?;
    reveal_path_in_file_manager(&path)
}

#[tauri::command]
pub(crate) fn open_external_url(url: String) -> Result<(), String> {
    let url = normalize_public_external_url(&url)?;
    open_url_with_system(&url)
}

pub(crate) fn canonical_existing_path(raw_path: &str) -> Result<PathBuf, String> {
    require_text(raw_path, "文件路径")?;
    let path = PathBuf::from(raw_path.trim());
    let path = path.canonicalize().map_err(|error| error.to_string())?;
    if !path.exists() {
        return Err("文件不存在。".to_string());
    }
    Ok(path)
}

pub(crate) fn open_path_in_file_manager(path: &Path) -> Result<(), String> {
    launch_default(path.as_os_str())
}

pub(crate) fn open_path_with_system(path: &Path) -> Result<(), String> {
    launch_default(path.as_os_str())
}

pub(crate) fn open_url_with_system(url: &str) -> Result<(), String> {
    #[cfg(windows)]
    {
        use windows::{
            core::{w, PCWSTR},
            Win32::{
                Foundation::RPC_E_CHANGED_MODE,
                System::Com::{
                    CoInitializeEx, CoUninitialize, COINIT_APARTMENTTHREADED,
                    COINIT_DISABLE_OLE1DDE,
                },
                UI::{Shell::ShellExecuteW, WindowsAndMessaging::SW_SHOWNORMAL},
            },
        };

        let target: Vec<u16> = url.encode_utf16().chain(Some(0)).collect();
        let apartment =
            unsafe { CoInitializeEx(None, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE) };
        if apartment.is_err() && apartment != RPC_E_CHANGED_MODE {
            return Err(format!("无法初始化系统浏览器入口：{apartment:?}"));
        }
        // Resolve the HTTP(S) association directly; explorer treats some URLs as folders.
        let result = unsafe {
            ShellExecuteW(
                None,
                w!("open"),
                PCWSTR(target.as_ptr()),
                None,
                None,
                SW_SHOWNORMAL,
            )
        };
        if apartment.is_ok() {
            unsafe { CoUninitialize() };
        }
        shell_execute_result(result.0 as isize)
    }
    #[cfg(not(windows))]
    launch_default(std::ffi::OsStr::new(url))
}

#[cfg(windows)]
pub(super) fn shell_execute_result(code: isize) -> Result<(), String> {
    if code > 32 {
        Ok(())
    } else {
        Err(format!("系统浏览器未能打开网址（错误代码 {code}）。"))
    }
}

pub(crate) fn reveal_path_in_file_manager(path: &Path) -> Result<(), String> {
    if path.is_dir() {
        return open_path_in_file_manager(path);
    }

    #[cfg(windows)]
    let mut command = {
        let mut command = Command::new("explorer");
        command.arg(format!("/select,{}", path.to_string_lossy()));
        command
    };

    #[cfg(target_os = "macos")]
    let mut command = {
        let mut command = Command::new("open");
        command.arg("-R").arg(path);
        command
    };

    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = {
        let mut command = Command::new("xdg-open");
        command.arg(path.parent().unwrap_or_else(|| Path::new(".")));
        command
    };

    command.spawn().map_err(|error| error.to_string())?;
    Ok(())
}

fn launch_default(target: &std::ffi::OsStr) -> Result<(), String> {
    #[cfg(windows)]
    let executable = "explorer";
    #[cfg(target_os = "macos")]
    let executable = "open";
    #[cfg(all(unix, not(target_os = "macos")))]
    let executable = "xdg-open";
    Command::new(executable)
        .arg(target)
        .spawn()
        .map_err(|error| error.to_string())?;
    Ok(())
}
