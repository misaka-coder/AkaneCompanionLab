use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ProcessEntry {
    pub(crate) pid: u32,
    pub(crate) name: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SystemProcessSnapshot {
    pub(crate) ok: bool,
    pub(crate) status: String,
    pub(crate) reason: String,
    pub(crate) captured_at: u128,
    pub(crate) platform: String,
    pub(crate) processes: Vec<ProcessEntry>,
    pub(crate) total: usize,
    pub(crate) matched: usize,
    pub(crate) offset: usize,
    pub(crate) has_more: bool,
    pub(crate) next_offset: Option<usize>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SystemProcessTerminateResult {
    pub(crate) ok: bool,
    pub(crate) status: String,
    pub(crate) reason: String,
    pub(crate) pid: u32,
    pub(crate) platform: String,
}
