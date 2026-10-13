use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ExportedWorkspaceFile {
    pub(crate) ok: bool,
    pub(crate) path: String,
    pub(crate) file_name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) size_bytes: Option<u64>,
}
