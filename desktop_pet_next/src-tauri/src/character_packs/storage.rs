//! Replace files only after their new contents have been written.
use std::{
    fs,
    io::{self, Write},
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};

static NEXT_WRITE: AtomicU64 = AtomicU64::new(0);

pub(crate) fn write_text_atomic(path: &Path, text: &str) -> Result<(), String> {
    write_bytes_atomic(path, text.as_bytes())
}

pub(crate) fn write_bytes_atomic(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("无法定位写入目录：{}", path.display()))?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let file_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| format!("无效文件名：{}", path.display()))?;
    let (tmp_path, mut file) = loop {
        let nonce = NEXT_WRITE.fetch_add(1, Ordering::Relaxed);
        let candidate = parent.join(format!(".{file_name}.tmp-{}-{nonce}", std::process::id()));
        match fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&candidate)
        {
            Ok(file) => break (candidate, file),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error.to_string()),
        }
    };
    let written = file.write_all(bytes).and_then(|_| file.sync_all());
    drop(file);
    let result = written.and_then(|_| fs::rename(&tmp_path, path));
    if result.is_err() {
        let _ = fs::remove_file(&tmp_path);
    }
    result.map_err(|error| error.to_string())
}

pub(crate) fn write_bytes_atomic_with_cleanup(
    path: &Path,
    bytes: &[u8],
    cleanup_paths: &[PathBuf],
) -> Result<(), String> {
    write_bytes_atomic(path, bytes)?;
    for cleanup_path in cleanup_paths {
        if cleanup_path != path && cleanup_path.is_file() {
            fs::remove_file(cleanup_path).map_err(|error| error.to_string())?;
        }
    }
    Ok(())
}
