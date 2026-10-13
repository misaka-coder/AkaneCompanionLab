//! Disposable file fixtures; never redirect the process's live data root.
use std::{
    fs,
    path::PathBuf,
    sync::atomic::{AtomicU64, Ordering},
};

static NEXT_FIXTURE: AtomicU64 = AtomicU64::new(0);

pub(super) struct Sandbox(pub(super) PathBuf);

impl Sandbox {
    pub(super) fn new() -> Self {
        loop {
            let path = std::env::temp_dir().join(format!(
                "akane-character-test-{}-{}-{}",
                std::process::id(),
                crate::runtime::time::current_time_millis(),
                NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed)
            ));
            match fs::create_dir(&path) {
                Ok(()) => return Self(path),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => panic!("Cannot create isolated fixture: {error}"),
            }
        }
    }
}

impl Drop for Sandbox {
    fn drop(&mut self) {
        // Only the uniquely created fixture directory is retired.
        let _ = fs::remove_dir_all(&self.0);
    }
}
