use crate::native_system::media::actions::normalize_system_media_control_action;
use crate::native_system::media::models::{SystemMediaControlResult, SystemMediaSnapshot};
use crate::runtime::time::current_time_millis;

pub(crate) fn system_media_unavailable(
    reason: &str,
    _detail: impl ToString,
) -> SystemMediaSnapshot {
    SystemMediaSnapshot {
        ok: false,
        status: "unavailable".to_string(),
        reason: reason.to_string(),
        captured_at: current_time_millis(),
        platform: std::env::consts::OS.to_string(),
        track_key: String::new(),
        title: String::new(),
        artist: String::new(),
        album: String::new(),
        source_app: String::new(),
        playback_status: "unknown".to_string(),
        is_playing: false,
        position_seconds: None,
        duration_seconds: None,
        media_type: "unknown".to_string(),
        timeline_updated_at: 0,
        playback_rate: 1.0,
    }
}

pub(crate) fn system_media_control_unavailable(
    action: &str,
    reason: &str,
    _detail: impl ToString,
) -> SystemMediaControlResult {
    SystemMediaControlResult {
        ok: false,
        status: "unavailable".to_string(),
        reason: reason.to_string(),
        action: normalize_system_media_control_action(action),
        captured_at: current_time_millis(),
        platform: std::env::consts::OS.to_string(),
        track_key: String::new(),
        title: String::new(),
        artist: String::new(),
        source_app: String::new(),
        playback_status: "unknown".to_string(),
    }
}

pub(crate) fn system_media_control_from_snapshot(
    action: &str,
    ok: bool,
    status: &str,
    reason: &str,
    snapshot: Option<SystemMediaSnapshot>,
) -> SystemMediaControlResult {
    let media = snapshot.unwrap_or_else(|| system_media_unavailable(reason, ""));
    SystemMediaControlResult {
        ok,
        status: status.to_string(),
        reason: reason.to_string(),
        action: action.to_string(),
        captured_at: current_time_millis(),
        platform: media.platform,
        track_key: media.track_key,
        title: media.title,
        artist: media.artist,
        source_app: media.source_app,
        playback_status: media.playback_status,
    }
}

pub(crate) fn system_media_control_early_outcome(
    action: &str,
    before: Option<&SystemMediaSnapshot>,
) -> Option<SystemMediaControlResult> {
    if action == "play"
        && before
            .map(|snapshot| snapshot.playback_status == "playing")
            .unwrap_or(false)
    {
        return Some(system_media_control_from_snapshot(
            action,
            true,
            "already_playing",
            "",
            before.cloned(),
        ));
    }
    if action == "pause"
        && before
            .map(|snapshot| snapshot.playback_status == "paused")
            .unwrap_or(false)
    {
        return Some(system_media_control_from_snapshot(
            action,
            true,
            "already_paused",
            "",
            before.cloned(),
        ));
    }
    let known_no_session = before
        .map(|snapshot| !snapshot.ok && snapshot.reason == "no_active_session")
        .unwrap_or(false);
    if known_no_session {
        if action == "stop" {
            return Some(system_media_control_from_snapshot(
                action,
                true,
                "already_stopped",
                "",
                before.cloned(),
            ));
        }
        return Some(system_media_control_unavailable(
            action,
            "no_active_session",
            "",
        ));
    }
    None
}

/// Whether the post-send snapshot proves the requested action took effect.
pub(crate) fn system_media_target_confirmed(
    action: &str,
    before: Option<&SystemMediaSnapshot>,
    after: Option<&SystemMediaSnapshot>,
) -> bool {
    let Some(after) = after else {
        return false;
    };
    if !after.ok {
        // A stop action may legitimately end the active media session, but an
        // unreadable snapshot is not proof of that: only an explicit
        // no_active_session result counts.  Read failures and permission
        // errors must stay unconfirmed.
        return action == "stop" && after.reason == "no_active_session";
    }
    match action {
        "play" => after.playback_status == "playing",
        "pause" => after.playback_status == "paused" || after.playback_status == "stopped",
        "stop" => matches!(after.playback_status.as_str(), "stopped" | "closed"),
        "next" | "previous" => match before {
            Some(previous) if previous.ok && !previous.track_key.is_empty() => {
                !after.track_key.is_empty() && after.track_key != previous.track_key
            }
            _ => false,
        },
        _ => false,
    }
}

/// Post-send mapping: sending a key is not the same as confirming its effect.
pub(crate) fn system_media_control_post_send_outcome(
    action: &str,
    before: Option<&SystemMediaSnapshot>,
    after: Option<&SystemMediaSnapshot>,
) -> SystemMediaControlResult {
    let final_snapshot = after.cloned().or_else(|| before.cloned());
    if system_media_target_confirmed(action, before, after) {
        system_media_control_from_snapshot(action, true, "executed", "", final_snapshot)
    } else {
        system_media_control_from_snapshot(
            action,
            false,
            "execution_unknown",
            "media_state_not_confirmed",
            final_snapshot,
        )
    }
}
