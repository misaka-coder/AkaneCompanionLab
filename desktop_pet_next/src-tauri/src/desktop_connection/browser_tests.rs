use super::browser::execute_satellite_browser_once_with;
use std::sync::{
    atomic::{AtomicUsize, Ordering},
    Barrier,
};
use std::time::Duration;

#[test]
fn browser_launch_failure_is_returned_and_not_retried_on_duplicate_delivery() {
    let first = execute_satellite_browser_once_with(
        "browser_association_failure_test",
        "https://music.163.com/#/search/m/?s=Song&type=1",
        |_| Err("no browser association".to_string()),
    );
    assert_eq!(first.status, "failed");
    assert_eq!(first.reason, "os_open_failed");
    let replay = execute_satellite_browser_once_with(
        "browser_association_failure_test",
        "https://music.163.com/#/search/m/?s=Song&type=1",
        |_| panic!("duplicate delivery must not launch a second window"),
    );
    assert_eq!(replay.status, "failed");
    assert_eq!(replay.reason, "os_open_failed");
}

#[test]
fn concurrent_browser_delivery_opens_only_once() {
    let start = Barrier::new(8);
    let opened = AtomicUsize::new(0);
    let results = std::thread::scope(|scope| {
        let threads: Vec<_> = (0..8)
            .map(|_| {
                scope.spawn(|| {
                    start.wait();
                    execute_satellite_browser_once_with(
                        "browser_concurrent_delivery_test",
                        "https://example.com/",
                        |_| {
                            opened.fetch_add(1, Ordering::SeqCst);
                            std::thread::sleep(Duration::from_millis(75));
                            Ok(())
                        },
                    )
                })
            })
            .collect();
        threads
            .into_iter()
            .map(|thread| thread.join().unwrap())
            .collect::<Vec<_>>()
    });
    assert_eq!(opened.load(Ordering::SeqCst), 1);
    assert!(results
        .iter()
        .all(|result| result.status == "succeeded" && result.reason.is_empty()));
}

#[test]
fn satellite_duplicate_invocation_opens_only_once() {
    let calls = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let first_calls = calls.clone();
    let first = execute_satellite_browser_once_with(
        "call_rust_idempotency",
        "https://example.com/docs",
        move |_| {
            first_calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            Ok(())
        },
    );
    let second_calls = calls.clone();
    let second = execute_satellite_browser_once_with(
        "call_rust_idempotency",
        "https://example.com/docs",
        move |_| {
            second_calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            Ok(())
        },
    );
    assert_eq!(first.status, "succeeded");
    assert_eq!(second.status, "succeeded");
    assert_eq!(calls.load(std::sync::atomic::Ordering::SeqCst), 1);
}

#[test]
fn satellite_rejects_private_url_before_os_open() {
    let calls = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let opener_calls = calls.clone();
    let result = execute_satellite_browser_once_with(
        "call_rust_private_url",
        "http://127.0.0.1:9999/admin",
        move |_| {
            opener_calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            Ok(())
        },
    );
    assert_eq!(result.status, "rejected");
    assert_eq!(result.reason, "unsafe_url");
    assert_eq!(calls.load(std::sync::atomic::Ordering::SeqCst), 0);
}
