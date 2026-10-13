use super::prepare::prepare_audio_asset_with_directory;
use crate::runtime::test_support::Sandbox;
use std::{
    fs,
    path::PathBuf,
    sync::{Arc, Barrier},
    thread,
};

#[test]
fn concurrent_audio_preparation_keeps_independent_bytes_and_lyrics() {
    let sandbox = Sandbox::new();
    let cache = sandbox.0.join("audio");
    let barrier = Arc::new(Barrier::new(16));
    let workers: Vec<_> = (0..16)
        .map(|index| {
            let source = sandbox.0.join(format!("track-{index}.wav"));
            fs::write(&source, format!("audio-{index}")).unwrap();
            fs::write(source.with_extension("lrc"), "\u{feff}[00:00.00]歌词").unwrap();
            let cache = cache.clone();
            let barrier = barrier.clone();
            thread::spawn(move || {
                barrier.wait();
                let audio = prepare_audio_asset_with_directory(
                    source.to_string_lossy().to_string(),
                    None,
                    || Ok(cache),
                )
                .unwrap();
                assert_eq!(
                    fs::read(&audio.cached_path).unwrap(),
                    format!("audio-{index}").as_bytes()
                );
                assert_eq!(audio.lyric_text.as_deref(), Some("[00:00.00]歌词"));
                assert_eq!(audio.size_bytes, format!("audio-{index}").len() as u64);
                audio.cached_path
            })
        })
        .collect();
    let paths: std::collections::HashSet<_> = workers
        .into_iter()
        .map(|worker| worker.join().unwrap())
        .collect();
    assert_eq!(paths.len(), 16);
    assert_eq!(fs::read_dir(cache).unwrap().count(), 16);
}

#[test]
fn invalid_audio_returns_before_accessing_the_cache() {
    let sandbox = Sandbox::new();
    let source = sandbox.0.join("empty.wav");
    fs::write(&source, []).unwrap();
    let result = prepare_audio_asset_with_directory(
        source.to_string_lossy().to_string(),
        None,
        || -> Result<PathBuf, String> { panic!("cache should not be accessed") },
    );
    assert!(result.unwrap_err().contains("空"));
}
