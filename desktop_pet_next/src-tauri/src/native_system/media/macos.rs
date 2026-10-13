use std::process::Command;

use crate::native_system::media::models::SystemMediaControlResult;
use crate::native_system::media::outcomes::{
    system_media_control_from_snapshot, system_media_control_unavailable,
};

#[cfg(target_os = "macos")]
pub(crate) fn control_system_media_macos(action: &str) -> Result<SystemMediaControlResult, String> {
    let script = match action {
        "play" => r#"tell application "Music" to play"#,
        "pause" => r#"tell application "Music" to pause"#,
        "next" => r#"tell application "Music" to next track"#,
        "previous" => r#"tell application "Music" to previous track"#,
        "stop" => r#"tell application "Music" to stop"#,
        _ => {
            return Ok(system_media_control_unavailable(
                action,
                "invalid_action",
                "",
            ))
        }
    };
    let output = Command::new("osascript")
        .arg("-e")
        .arg(script)
        .output()
        .map_err(|error| error.to_string())?;
    let ok = output.status.success();
    Ok(system_media_control_from_snapshot(
        action,
        ok,
        if ok { "executed" } else { "failed" },
        if ok { "" } else { "apple_music_unavailable" },
        None,
    ))
}
