use crate::native_system::media::models::SystemMediaSnapshot;
use crate::native_system::media::outcomes::{
    system_media_control_early_outcome, system_media_control_post_send_outcome,
    system_media_target_confirmed,
};
use crate::native_system::media::timeline::estimate_media_position;
#[cfg(windows)]
use crate::native_system::media::windows::system_media_session_candidates_windows;

fn media_snapshot(
    ok: bool,
    status: &str,
    reason: &str,
    playback_status: &str,
    track_key: &str,
) -> SystemMediaSnapshot {
    SystemMediaSnapshot {
        ok,
        status: status.to_string(),
        reason: reason.to_string(),
        captured_at: 1,
        platform: "windows".to_string(),
        track_key: track_key.to_string(),
        title: String::new(),
        artist: String::new(),
        album: String::new(),
        source_app: String::new(),
        playback_status: playback_status.to_string(),
        is_playing: playback_status == "playing",
        position_seconds: None,
        duration_seconds: None,
        media_type: "unknown".to_string(),
        timeline_updated_at: 0,
        playback_rate: 1.0,
    }
}

#[cfg(windows)]
#[test]
#[ignore = "reads the interactive user's live media sessions"]
fn live_media_snapshot_probe() {
    let started = std::time::Instant::now();
    let candidates = system_media_session_candidates_windows().expect("SMTC enumeration");
    let snapshots: Vec<_> = candidates.iter().map(|(_, snapshot)| snapshot).collect();
    println!(
        "MEDIA_PROBE {}",
        serde_json::json!({"elapsed_ms": started.elapsed().as_secs_f64() * 1000.0, "sessions": snapshots})
    );
}

#[test]
fn media_position_handles_progress_rate_seek_pause_and_missing_time() {
    assert_eq!(
        estimate_media_position(Some(10.0), Some(100.0), true, 2.0, 1000, 4000),
        Some(16.0)
    );
    assert_eq!(
        estimate_media_position(Some(60.0), Some(100.0), true, 1.0, 4000, 4000),
        Some(60.0)
    );
    assert_eq!(
        estimate_media_position(Some(5.0), Some(100.0), true, 1.0, 5000, 5000),
        Some(5.0)
    );
    assert_eq!(
        estimate_media_position(Some(5.0), Some(100.0), false, 1.0, 1000, 5000),
        Some(5.0)
    );
    assert_eq!(
        estimate_media_position(Some(99.0), Some(100.0), true, 1.0, 1000, 5000),
        Some(100.0)
    );
    assert_eq!(
        estimate_media_position(None, None, true, 1.0, 1000, 5000),
        None
    );
    assert_eq!(
        estimate_media_position(Some(10.0), None, true, 1.0, 0, 5000),
        Some(10.0)
    );
}

#[test]
fn media_target_confirmation_requires_observed_state_change() {
    let paused = media_snapshot(true, "ready", "", "paused", "track-a");
    let playing = media_snapshot(true, "ready", "", "playing", "track-a");
    let playing_b = media_snapshot(true, "ready", "", "playing", "track-b");

    // paused -> play -> playing is confirmed.
    assert!(system_media_target_confirmed(
        "play",
        Some(&paused),
        Some(&playing)
    ));
    // paused -> play -> still paused is NOT confirmed.
    assert!(!system_media_target_confirmed(
        "play",
        Some(&paused),
        Some(&paused)
    ));
    // next with a changed track is confirmed.
    assert!(system_media_target_confirmed(
        "next",
        Some(&playing),
        Some(&playing_b)
    ));
    // next with an unchanged track is NOT confirmed.
    assert!(!system_media_target_confirmed(
        "next",
        Some(&playing),
        Some(&playing)
    ));
    // pause -> paused is confirmed (paused or stopped both count).
    assert!(system_media_target_confirmed(
        "pause",
        Some(&playing),
        Some(&paused)
    ));
    // next cannot be confirmed when the before-track is unknown.
    let no_track = media_snapshot(true, "ready", "", "playing", "");
    assert!(!system_media_target_confirmed(
        "next",
        Some(&no_track),
        Some(&playing_b)
    ));
    // stop that ends the session with an explicit no_active_session result
    // is confirmed.
    let closed = media_snapshot(false, "unavailable", "no_active_session", "unknown", "");
    assert!(system_media_target_confirmed(
        "stop",
        Some(&playing),
        Some(&closed)
    ));
    // an unreadable snapshot is NOT proof that stop ended the session.
    let read_failed = media_snapshot(false, "unavailable", "read_failed", "unknown", "");
    assert!(!system_media_target_confirmed(
        "stop",
        Some(&playing),
        Some(&read_failed)
    ));
    // stop while still playing is NOT confirmed.
    assert!(!system_media_target_confirmed(
        "stop",
        Some(&playing),
        Some(&playing)
    ));
    // a missing post-send snapshot never confirms.
    assert!(!system_media_target_confirmed("play", Some(&paused), None));
}

#[test]
fn media_control_early_outcome_keeps_idempotent_success_and_no_session() {
    let playing = media_snapshot(true, "ready", "", "playing", "track-a");
    let paused = media_snapshot(true, "ready", "", "paused", "track-a");
    let no_session = media_snapshot(false, "unavailable", "no_active_session", "unknown", "");

    let already_play = system_media_control_early_outcome("play", Some(&playing)).unwrap();
    assert!(already_play.ok);
    assert_eq!(already_play.status, "already_playing");

    let already_pause = system_media_control_early_outcome("pause", Some(&paused)).unwrap();
    assert!(already_pause.ok);
    assert_eq!(already_pause.status, "already_paused");

    let no_session_play = system_media_control_early_outcome("play", Some(&no_session)).unwrap();
    assert!(!no_session_play.ok);
    assert_eq!(no_session_play.status, "unavailable");
    assert_eq!(no_session_play.reason, "no_active_session");

    let stopped_no_session = system_media_control_early_outcome("stop", Some(&no_session)).unwrap();
    assert!(stopped_no_session.ok);
    assert_eq!(stopped_no_session.status, "already_stopped");

    // A paused player that is asked to play must not short-circuit.
    assert!(system_media_control_early_outcome("play", Some(&paused)).is_none());
}

#[test]
fn media_control_post_send_distinguishes_executed_from_unknown() {
    let paused = media_snapshot(true, "ready", "", "paused", "track-a");
    let playing = media_snapshot(true, "ready", "", "playing", "track-a");
    let paused_b = media_snapshot(true, "ready", "", "paused", "track-b");
    let playing_b = media_snapshot(true, "ready", "", "playing", "track-b");

    let confirmed = system_media_control_post_send_outcome("play", Some(&paused), Some(&playing));
    assert!(confirmed.ok);
    assert_eq!(confirmed.status, "executed");
    assert_eq!(confirmed.playback_status, "playing");

    let unconfirmed =
        system_media_control_post_send_outcome("play", Some(&paused), Some(&paused_b));
    assert!(!unconfirmed.ok);
    assert_eq!(unconfirmed.status, "execution_unknown");
    assert_eq!(unconfirmed.reason, "media_state_not_confirmed");
    assert_eq!(unconfirmed.playback_status, "paused");

    let next_unknown =
        system_media_control_post_send_outcome("next", Some(&playing), Some(&playing));
    assert!(!next_unknown.ok);
    assert_eq!(next_unknown.status, "execution_unknown");

    let next_confirmed =
        system_media_control_post_send_outcome("next", Some(&playing), Some(&playing_b));
    assert!(next_confirmed.ok);
    assert_eq!(next_confirmed.status, "executed");

    // Snapshot read failures leave the previous state visible, unconfirmed.
    let read_failed = system_media_control_post_send_outcome("play", Some(&paused), None);
    assert!(!read_failed.ok);
    assert_eq!(read_failed.status, "execution_unknown");
    assert_eq!(read_failed.playback_status, "paused");
}
