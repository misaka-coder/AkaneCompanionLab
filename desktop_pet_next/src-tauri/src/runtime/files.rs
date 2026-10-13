use std::{
    fs::{self, File, OpenOptions},
    io::{self, Read},
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};

pub(crate) fn unique_staging_path(directory: &Path, extension: &str) -> PathBuf {
    static SEQUENCE: AtomicU64 = AtomicU64::new(0);
    directory.join(format!(
        ".akane-{}-{}-{}.{}",
        std::process::id(),
        super::time::current_time_millis(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed),
        extension
    ))
}

pub(crate) fn copy_to_staging(
    source: &Path,
    directory: &Path,
    extension: &str,
    max_bytes: Option<u64>,
) -> Result<(PathBuf, u64), String> {
    let input = File::open(source).map_err(|error| error.to_string())?;
    fs::create_dir_all(directory).map_err(|error| error.to_string())?;
    let path = unique_staging_path(directory, extension);
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)
        .map_err(|error| error.to_string())?;
    let result = (|| {
        let size = io::copy(
            &mut input.take(
                max_bytes
                    .map(|limit| limit.saturating_add(1))
                    .unwrap_or(u64::MAX),
            ),
            &mut output,
        )
        .map_err(|error| error.to_string())?;
        if max_bytes.is_some_and(|limit| size > limit) {
            return Err("staging_file_too_large".to_string());
        }
        output.sync_all().map_err(|error| error.to_string())?;
        Ok(size)
    })();
    drop(output);
    match result {
        Ok(size) => Ok((path, size)),
        Err(error) => {
            let _ = fs::remove_file(&path);
            Err(error)
        }
    }
}

pub(crate) fn read_file_bounded(source: &Path, max_bytes: u64) -> io::Result<Vec<u8>> {
    let input = File::open(source)?;
    let mut bytes = Vec::new();
    input
        .take(max_bytes.saturating_add(1))
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > max_bytes {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "file_too_large"));
    }
    Ok(bytes)
}
