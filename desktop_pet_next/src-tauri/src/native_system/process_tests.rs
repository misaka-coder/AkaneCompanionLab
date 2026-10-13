use crate::native_system::process_models::ProcessEntry;
use crate::native_system::processes::{process_snapshot_page, terminate_system_process};

#[test]
fn satellite_process_search_filters_before_pagination() {
    let entries: Vec<_> = (1..=350)
        .map(|pid| ProcessEntry {
            pid,
            name: if pid == 300 {
                "TargetApp.EXE".into()
            } else {
                "other.exe".into()
            },
        })
        .collect();
    let found = process_snapshot_page(entries.clone(), &serde_json::json!({"query": "targetapp"}));
    assert_eq!(found.total, 350);
    assert_eq!(found.matched, 1);
    assert_eq!(found.processes[0].pid, 300);
    assert!(!found.has_more);
    let first = process_snapshot_page(entries.clone(), &serde_json::json!({}));
    assert_eq!(first.processes.len(), 128);
    assert_eq!(first.next_offset, Some(128));
    let last = process_snapshot_page(entries.clone(), &serde_json::json!({"offset": 256}));
    assert_eq!(last.processes.len(), 94);
    assert!(!last.has_more);
    let empty = process_snapshot_page(entries, &serde_json::json!({"query": "missing"}));
    assert_eq!(empty.matched, 0);
    assert!(empty.processes.is_empty());
}

#[cfg(windows)]
#[test]
fn satellite_refuses_to_terminate_its_own_process() {
    let result = terminate_system_process(std::process::id());
    assert!(!result.ok);
    assert_eq!(result.reason, "protected_process");
}
