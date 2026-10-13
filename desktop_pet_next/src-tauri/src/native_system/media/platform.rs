#[cfg(target_os = "macos")]
use crate::native_system::media::macos::control_system_media_macos;
use crate::native_system::media::models::{SystemMediaControlResult, SystemMediaSnapshot};
use crate::native_system::media::outcomes::{
    system_media_control_unavailable, system_media_unavailable,
};
#[cfg(windows)]
use crate::native_system::media::windows::{
    control_system_media_windows, read_current_system_media_windows,
};

#[cfg(target_os = "macos")]
pub(crate) fn control_system_media_platform(action: &str) -> SystemMediaControlResult {
    match control_system_media_macos(action) {
        Ok(result) => result,
        Err(error) => system_media_control_unavailable(action, "control_failed", error),
    }
}

#[cfg(all(not(windows), not(target_os = "macos")))]
pub(crate) fn control_system_media_platform(action: &str) -> SystemMediaControlResult {
    // TODO: Linux MPRIS via D-Bus. Keep a structured unavailable response
    // until a real MPRIS implementation exists.
    system_media_control_unavailable(action, "unsupported_platform", std::env::consts::OS)
}

#[cfg(windows)]
pub(crate) fn control_system_media_platform(action: &str) -> SystemMediaControlResult {
    match control_system_media_windows(action) {
        Ok(result) => result,
        Err(error) => system_media_control_unavailable(action, "control_failed", error),
    }
}

#[cfg(not(windows))]
pub(crate) fn read_current_system_media() -> SystemMediaSnapshot {
    system_media_unavailable("unsupported_platform", std::env::consts::OS)
}

#[cfg(windows)]
pub(crate) fn read_current_system_media() -> SystemMediaSnapshot {
    match read_current_system_media_windows() {
        Ok(snapshot) => snapshot,
        Err(error) => system_media_unavailable("read_failed", error),
    }
}
