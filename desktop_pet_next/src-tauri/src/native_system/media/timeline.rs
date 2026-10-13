pub(crate) fn estimate_media_position(
    position: Option<f64>,
    duration: Option<f64>,
    playing: bool,
    rate: f64,
    updated_at: u128,
    now: u128,
) -> Option<f64> {
    let raw = position.filter(|value| value.is_finite() && *value >= 0.0)?;
    let elapsed =
        if playing && updated_at > 0 && now >= updated_at && now - updated_at <= 86_400_000 {
            (now - updated_at) as f64 / 1000.0
        } else {
            0.0
        };
    let estimate = (raw + elapsed * rate).max(0.0);
    Some(
        duration
            .filter(|value| *value > 0.0)
            .map_or(estimate, |end| estimate.min(end)),
    )
}

#[cfg(windows)]
pub(crate) fn playback_status_label(value: i32) -> String {
    match value {
        0 => "closed",
        1 => "opened",
        2 => "changing",
        3 => "stopped",
        4 => "playing",
        5 => "paused",
        _ => "unknown",
    }
    .to_string()
}

#[cfg(windows)]
pub(crate) fn time_span_seconds(value: windows::Foundation::TimeSpan) -> Option<f64> {
    let seconds = value.Duration as f64 / 10_000_000.0;
    if seconds.is_finite() && seconds >= 0.0 {
        Some(seconds)
    } else {
        None
    }
}

#[cfg(windows)]
pub(crate) fn build_system_media_track_key(
    source_app: &str,
    title: &str,
    artist: &str,
    album: &str,
) -> String {
    [source_app, title, artist, album]
        .into_iter()
        .map(|part| part.trim().to_lowercase())
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join("::")
}
