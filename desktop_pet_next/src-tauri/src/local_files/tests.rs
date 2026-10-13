use super::{
    exports::{export_file_to_desktop_with_directories, publish_staged_export},
    urls::normalize_public_external_url,
};
use crate::runtime::test_support::Sandbox;
use std::fs;

#[cfg(windows)]
#[test]
fn browser_association_errors_are_not_reported_as_success() {
    for code in [0, 2, 3, 5, 8, 26, 27, 28, 29, 30, 31, 32] {
        assert!(super::system_open::shell_execute_result(code).is_err());
    }
    assert!(super::system_open::shell_execute_result(33).is_ok());
}

#[test]
fn normalized_local_urls_are_rejected_before_opening() {
    for url in [
        "http://127.1/",
        "http://2130706433/",
        "http://localhost./",
        "http://[::ffff:127.0.0.1]/",
        "http://0x7f000001/",
    ] {
        assert!(
            normalize_public_external_url(url).is_err(),
            "accepted local address: {url}"
        );
    }
    assert_eq!(
        normalize_public_external_url("https://example.com/path?q=hello#anchor").unwrap(),
        "https://example.com/path?q=hello#anchor"
    );
    let music_url =
        "https://music.163.com/#/search/m/?s=%E6%90%81%E6%B5%85+%E5%91%A8%E6%9D%B0%E4%BC%A6&type=1";
    assert_eq!(normalize_public_external_url(music_url).unwrap(), music_url);
}

#[test]
fn concurrent_exports_keep_every_file_and_leave_no_temporary_files() {
    use std::{
        sync::{Arc, Barrier},
        thread,
    };
    let sandbox = Sandbox::new();
    let source = sandbox.0.join("report.txt");
    let directory = sandbox.0.join("desktop");
    let staging = sandbox.0.join("cache");
    fs::write(&source, b"content").unwrap();
    let barrier = Arc::new(Barrier::new(16));
    let workers: Vec<_> = (0..16)
        .map(|_| {
            let source = source.to_string_lossy().to_string();
            let directory = directory.clone();
            let staging = staging.clone();
            let barrier = barrier.clone();
            thread::spawn(move || {
                barrier.wait();
                export_file_to_desktop_with_directories(source, "report.txt".to_string(), || {
                    Ok((directory, staging))
                })
            })
        })
        .collect();
    let mut outputs = std::collections::HashSet::new();
    for worker in workers {
        let result = worker.join().unwrap().unwrap();
        assert_eq!(fs::read(&result.path).unwrap(), b"content");
        assert!(outputs.insert(result.path));
    }
    assert_eq!(fs::read_dir(directory).unwrap().count(), 16);
    assert_eq!(fs::read_dir(staging).unwrap().count(), 0);
}

#[test]
fn publication_failure_cleans_only_its_reservation() {
    let sandbox = Sandbox::new();
    let staged = sandbox.0.join("missing.txt");
    let target = sandbox.0.join("failed.txt");
    let other = sandbox.0.join("other.txt");
    fs::write(&other, b"other").unwrap();
    assert!(publish_staged_export(&staged, &target).is_err());
    assert!(!target.exists());
    assert_eq!(fs::read(other).unwrap(), b"other");
    assert_eq!(fs::read_dir(&sandbox.0).unwrap().count(), 1);
}

#[test]
fn publication_never_replaces_an_existing_file() {
    let sandbox = Sandbox::new();
    let staged = sandbox.0.join("staged.txt");
    let target = sandbox.0.join("existing.txt");
    fs::write(&staged, b"new").unwrap();
    fs::write(&target, b"original").unwrap();
    assert!(publish_staged_export(&staged, &target).is_err());
    assert_eq!(fs::read(target).unwrap(), b"original");
    assert_eq!(fs::read(staged).unwrap(), b"new");
}
