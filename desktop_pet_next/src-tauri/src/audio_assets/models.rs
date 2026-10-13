use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PreparedAudioAsset {
    pub(crate) original_path: String,
    pub(crate) cached_path: String,
    pub(crate) file_name: String,
    pub(crate) display_name: String,
    pub(crate) extension: String,
    pub(crate) size_bytes: u64,
    pub(crate) lyric_file_name: Option<String>,
    pub(crate) lyric_text: Option<String>,
}
