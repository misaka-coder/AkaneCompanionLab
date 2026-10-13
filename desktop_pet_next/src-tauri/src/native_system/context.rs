#[cfg(windows)]
use windows::{
    core::PWSTR,
    Win32::{
        Foundation::{CloseHandle, HWND},
        System::Threading::{
            OpenProcess, QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION,
        },
        UI::WindowsAndMessaging::{
            GetForegroundWindow, GetWindowTextLengthW, GetWindowTextW, GetWindowThreadProcessId,
        },
    },
};

use crate::native_system::context_models::{DesktopContextSnapshot, ForegroundWindowInfo};
use crate::runtime::time::current_time_millis;

pub(crate) fn get_desktop_context_snapshot() -> DesktopContextSnapshot {
    desktop_context_snapshot_from_foreground(collect_foreground_window(), current_time_millis())
}

pub(crate) fn desktop_context_snapshot_from_foreground(
    foreground: ForegroundWindowInfo,
    captured_at: u128,
) -> DesktopContextSnapshot {
    // An on-demand observation, never a cached external window relabelled as current.
    let ok = foreground.source == "foreground"
        && (!foreground.title.trim().is_empty() || !foreground.process_name.trim().is_empty());
    DesktopContextSnapshot {
        ok,
        enabled: foreground.source != "unsupported_platform",
        captured_at,
        platform: std::env::consts::OS.to_string(),
        foreground,
    }
}

#[cfg(windows)]
pub(crate) fn collect_foreground_window() -> ForegroundWindowInfo {
    let hwnd = unsafe { GetForegroundWindow() };
    if hwnd.0.is_null() {
        return empty_foreground_window("none");
    }

    let mut pid = 0u32;
    unsafe {
        GetWindowThreadProcessId(hwnd, Some(&mut pid));
    }

    if pid == std::process::id() {
        return ForegroundWindowInfo {
            title: String::new(),
            process_name: String::new(),
            pid: Some(pid),
            source: "self".to_string(),
        };
    }

    ForegroundWindowInfo {
        title: read_window_title(hwnd),
        process_name: read_process_name(pid),
        pid: if pid == 0 { None } else { Some(pid) },
        source: "foreground".to_string(),
    }
}

#[cfg(not(windows))]
pub(crate) fn collect_foreground_window() -> ForegroundWindowInfo {
    empty_foreground_window("unsupported_platform")
}

pub(crate) fn empty_foreground_window(source: &str) -> ForegroundWindowInfo {
    ForegroundWindowInfo {
        title: String::new(),
        process_name: String::new(),
        pid: None,
        source: source.to_string(),
    }
}

#[cfg(windows)]
pub(crate) fn read_window_title(hwnd: HWND) -> String {
    let len = unsafe { GetWindowTextLengthW(hwnd) };
    if len <= 0 {
        return String::new();
    }

    let mut buffer = vec![0u16; len as usize + 1];
    let copied = unsafe { GetWindowTextW(hwnd, &mut buffer) };
    if copied <= 0 {
        return String::new();
    }

    String::from_utf16_lossy(&buffer[..copied as usize])
        .trim()
        .chars()
        .take(240)
        .collect()
}

#[cfg(windows)]
pub(crate) fn read_process_name(pid: u32) -> String {
    if pid == 0 {
        return String::new();
    }

    let handle = match unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) } {
        Ok(handle) => handle,
        Err(_) => return String::new(),
    };

    let mut buffer = vec![0u16; 32768];
    let mut size = buffer.len() as u32;
    let result = unsafe {
        QueryFullProcessImageNameW(
            handle,
            Default::default(),
            PWSTR(buffer.as_mut_ptr()),
            &mut size,
        )
    };
    let _ = unsafe { CloseHandle(handle) };

    if result.is_err() || size == 0 {
        return String::new();
    }

    let image_path = String::from_utf16_lossy(&buffer[..size as usize]);
    std::path::Path::new(&image_path)
        .file_name()
        .map(|name| name.to_string_lossy().to_string())
        .unwrap_or(image_path)
        .chars()
        .take(100)
        .collect()
}
