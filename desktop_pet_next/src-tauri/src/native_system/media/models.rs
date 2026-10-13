use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SystemMediaSnapshot {
    pub(crate) ok: bool,
    pub(crate) status: String,
    pub(crate) reason: String,
    pub(crate) captured_at: u128,
    pub(crate) platform: String,
    pub(crate) track_key: String,
    pub(crate) title: String,
    pub(crate) artist: String,
    pub(crate) album: String,
    pub(crate) source_app: String,
    pub(crate) playback_status: String,
    pub(crate) is_playing: bool,
    pub(crate) position_seconds: Option<f64>,
    pub(crate) duration_seconds: Option<f64>,
    pub(crate) media_type: String,
    pub(crate) timeline_updated_at: u128,
    pub(crate) playback_rate: f64,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SystemMediaControlResult {
    pub(crate) ok: bool,
    pub(crate) status: String,
    pub(crate) reason: String,
    pub(crate) action: String,
    pub(crate) captured_at: u128,
    pub(crate) platform: String,
    pub(crate) track_key: String,
    pub(crate) title: String,
    pub(crate) artist: String,
    pub(crate) source_app: String,
    pub(crate) playback_status: String,
}
