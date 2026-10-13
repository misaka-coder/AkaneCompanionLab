use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopContextSnapshot {
    pub(crate) ok: bool,
    pub(crate) enabled: bool,
    pub(crate) captured_at: u128,
    pub(crate) platform: String,
    pub(crate) foreground: ForegroundWindowInfo,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ForegroundWindowInfo {
    pub(crate) title: String,
    pub(crate) process_name: String,
    pub(crate) pid: Option<u32>,
    pub(crate) source: String,
}
