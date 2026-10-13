use windows::{
    core::Interface,
    Media::Control::{
        GlobalSystemMediaTransportControlsSession, GlobalSystemMediaTransportControlsSessionManager,
    },
};

use crate::native_system::media::models::{SystemMediaControlResult, SystemMediaSnapshot};
use crate::native_system::media::outcomes::{
    system_media_control_early_outcome, system_media_control_from_snapshot,
    system_media_control_post_send_outcome, system_media_control_unavailable,
    system_media_target_confirmed, system_media_unavailable,
};
use crate::native_system::media::timeline::estimate_media_position;
#[cfg(windows)]
use crate::native_system::media::timeline::{
    build_system_media_track_key, playback_status_label, time_span_seconds,
};
use crate::runtime::time::current_time_millis;

#[cfg(windows)]
pub(crate) fn read_current_system_media_windows() -> Result<SystemMediaSnapshot, String> {
    match select_system_media_session_windows() {
        Ok((_session, snapshot)) => Ok(snapshot),
        Err("no_active_session") => Ok(system_media_unavailable("no_active_session", "")),
        Err(error) => Err(error.to_string()),
    }
}

#[cfg(windows)]
pub(crate) fn system_media_session_candidates_windows() -> Result<
    Vec<(
        GlobalSystemMediaTransportControlsSession,
        SystemMediaSnapshot,
    )>,
    String,
> {
    let manager = GlobalSystemMediaTransportControlsSessionManager::RequestAsync()
        .map_err(|error| error.to_string())?
        .get()
        .map_err(|error| error.to_string())?;
    let current_session = manager.GetCurrentSession().ok();
    let mut candidates = Vec::new();
    if let Some(current_session) = current_session
        .as_ref()
        .filter(|session| !session.as_raw().is_null())
    {
        if let Ok(snapshot) = read_system_media_session(&current_session) {
            candidates.push((current_session.clone(), snapshot));
        }
    }

    let sessions = manager.GetSessions().map_err(|error| error.to_string())?;
    let size = sessions.Size().map_err(|error| error.to_string())?;
    for index in 0..size {
        let session = sessions.GetAt(index).map_err(|error| error.to_string())?;
        if session.as_raw().is_null()
            || current_session
                .as_ref()
                .is_some_and(|current| current == &session)
        {
            continue;
        }
        let snapshot = match read_system_media_session(&session) {
            Ok(value) => value,
            Err(_) => continue,
        };
        if !candidates.iter().any(|(_, current)| {
            current.track_key == snapshot.track_key
                && current.playback_status == snapshot.playback_status
        }) {
            candidates.push((session, snapshot));
        }
    }
    Ok(candidates)
}

#[cfg(windows)]
pub(crate) fn select_system_media_session_windows() -> Result<
    (
        GlobalSystemMediaTransportControlsSession,
        SystemMediaSnapshot,
    ),
    &'static str,
> {
    let candidates = system_media_session_candidates_windows()
        .map_err(|_| "media_session_enumeration_failed")?;
    if candidates.is_empty() {
        return Err("no_active_session");
    }

    candidates
        .iter()
        .find(|(_, snapshot)| snapshot.ok && snapshot.playback_status == "playing")
        .or_else(|| candidates.iter().find(|(_, snapshot)| snapshot.ok))
        .map(|(session, snapshot)| (session.clone(), snapshot.clone()))
        .ok_or("no_active_session")
}

#[cfg(windows)]
pub(crate) fn control_system_media_windows(
    action: &str,
) -> Result<SystemMediaControlResult, String> {
    let (session, before) = match select_system_media_session_windows() {
        Ok(value) => value,
        Err("no_active_session") => {
            return Ok(system_media_control_unavailable(
                action,
                "no_active_session",
                "",
            ))
        }
        Err(reason) => return Ok(system_media_control_unavailable(action, reason, "")),
    };
    if let Some(early) = system_media_control_early_outcome(action, Some(&before)) {
        return Ok(early);
    }

    let accepted = invoke_system_media_session(&session, action)?;
    if !accepted {
        return Ok(system_media_control_from_snapshot(
            action,
            false,
            "failed",
            "session_rejected",
            Some(before),
        ));
    };

    let after = poll_system_media_control_confirmation(action, &session, Some(&before));
    Ok(system_media_control_post_send_outcome(
        action,
        Some(&before),
        after.as_ref(),
    ))
}

#[cfg(windows)]
pub(crate) fn invoke_system_media_session(
    session: &GlobalSystemMediaTransportControlsSession,
    action: &str,
) -> Result<bool, String> {
    let accepted = match action {
        "play" => session
            .TryPlayAsync()
            .map_err(|error| error.to_string())?
            .get()
            .map_err(|error| error.to_string())?,
        "pause" => session
            .TryPauseAsync()
            .map_err(|error| error.to_string())?
            .get()
            .map_err(|error| error.to_string())?,
        "stop" => session
            .TryStopAsync()
            .map_err(|error| error.to_string())?
            .get()
            .map_err(|error| error.to_string())?,
        "next" => session
            .TrySkipNextAsync()
            .map_err(|error| error.to_string())?
            .get()
            .map_err(|error| error.to_string())?,
        "previous" => session
            .TrySkipPreviousAsync()
            .map_err(|error| error.to_string())?
            .get()
            .map_err(|error| error.to_string())?,
        _ => return Err("invalid_action".to_string()),
    };
    Ok(accepted)
}

/// Bounded confirmation poll on the same SMTC session that received the
/// command. Never blocks long and never fails the pet flow: it returns the
/// last readable snapshot.
#[cfg(windows)]
pub(crate) fn poll_system_media_control_confirmation(
    action: &str,
    session: &GlobalSystemMediaTransportControlsSession,
    before: Option<&SystemMediaSnapshot>,
) -> Option<SystemMediaSnapshot> {
    let mut last: Option<SystemMediaSnapshot> = None;
    for _ in 0..SYSTEM_MEDIA_POLL_ATTEMPTS {
        match read_system_media_session(session) {
            Ok(snapshot) => {
                let confirmed = system_media_target_confirmed(action, before, Some(&snapshot));
                last = Some(snapshot);
                if confirmed {
                    break;
                }
            }
            Err(_) => {}
        }
        std::thread::sleep(std::time::Duration::from_millis(
            SYSTEM_MEDIA_POLL_DELAY_MILLIS,
        ));
    }
    last
}

#[cfg(windows)]
pub(crate) fn read_system_media_session(
    session: &GlobalSystemMediaTransportControlsSession,
) -> Result<SystemMediaSnapshot, String> {
    let media = session
        .TryGetMediaPropertiesAsync()
        .map_err(|error| error.to_string())?
        .get()
        .map_err(|error| error.to_string())?;
    let timeline = session
        .GetTimelineProperties()
        .map_err(|error| error.to_string())?;
    let playback = session
        .GetPlaybackInfo()
        .map_err(|error| error.to_string())?;
    let raw_status = playback
        .PlaybackStatus()
        .map_err(|error| error.to_string())?;

    let title = media
        .Title()
        .map_err(|error| error.to_string())?
        .to_string();
    let artist = media
        .Artist()
        .map_err(|error| error.to_string())?
        .to_string();
    let album = media
        .AlbumTitle()
        .map_err(|error| error.to_string())?
        .to_string();
    let source_app = session
        .SourceAppUserModelId()
        .map_err(|error| error.to_string())?
        .to_string();
    let playback_status = playback_status_label(raw_status.0);
    let position_seconds =
        time_span_seconds(timeline.Position().map_err(|error| error.to_string())?);
    let start_seconds = time_span_seconds(timeline.StartTime().map_err(|error| error.to_string())?);
    let end_seconds = time_span_seconds(timeline.EndTime().map_err(|error| error.to_string())?);
    let duration_seconds = match (start_seconds, end_seconds) {
        (Some(start), Some(end)) if end > start => Some(end - start),
        _ => None,
    };
    let captured_at = current_time_millis();
    let timeline_updated_at = timeline
        .LastUpdatedTime()
        .ok()
        .map(|time| {
            ((time.UniversalTime as i128 - 116_444_736_000_000_000i128).max(0) / 10_000) as u128
        })
        .unwrap_or(0);
    let playback_rate = playback
        .PlaybackRate()
        .and_then(|value| value.Value())
        .ok()
        .filter(|value| value.is_finite() && *value >= 0.0)
        .unwrap_or(1.0);
    let media_type = match media
        .PlaybackType()
        .and_then(|value| value.Value())
        .ok()
        .map(|value| value.0)
    {
        Some(1) => "music",
        Some(2) => "video",
        _ => "unknown",
    }
    .to_string();
    let position_seconds = estimate_media_position(
        position_seconds.map(|position| (position - start_seconds.unwrap_or(0.0)).max(0.0)),
        duration_seconds,
        raw_status.0 == 4,
        playback_rate,
        timeline_updated_at,
        captured_at,
    );
    let track_key = build_system_media_track_key(&source_app, &title, &artist, &album);
    let has_track = !title.trim().is_empty() || !artist.trim().is_empty();

    Ok(SystemMediaSnapshot {
        ok: has_track,
        status: if has_track { "ready" } else { "empty" }.to_string(),
        reason: String::new(),
        captured_at,
        platform: "windows".to_string(),
        track_key,
        title,
        artist,
        album,
        source_app,
        playback_status,
        is_playing: raw_status.0 == 4,
        position_seconds,
        duration_seconds,
        media_type,
        timeline_updated_at,
        playback_rate,
    })
}

pub(crate) const SYSTEM_MEDIA_POLL_ATTEMPTS: usize = 6;

pub(crate) const SYSTEM_MEDIA_POLL_DELAY_MILLIS: u64 = 150;
