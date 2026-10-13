pub(crate) const MAX_AUDIO_FILE_BYTES: u64 = 300 * 1024 * 1024;

pub(crate) const MAX_LYRIC_FILE_BYTES: u64 = 512 * 1024;

pub(crate) const SUPPORTED_AUDIO_EXTENSIONS: &[&str] = &[
    "mp3", "wav", "flac", "ogg", "oga", "m4a", "aac", "opus", "webm",
];
