#[cfg(windows)]
use windows::Win32::{
    Foundation::{CloseHandle, WAIT_OBJECT_0},
    System::{
        Diagnostics::ToolHelp::{
            CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
            TH32CS_SNAPPROCESS,
        },
        Threading::{
            OpenProcess, TerminateProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE,
            PROCESS_TERMINATE,
        },
    },
};

use crate::native_system::process_models::{
    ProcessEntry, SystemProcessSnapshot, SystemProcessTerminateResult,
};
use crate::runtime::time::current_time_millis;

pub(crate) fn system_process_unavailable(reason: &str) -> SystemProcessSnapshot {
    SystemProcessSnapshot {
        ok: false,
        status: "unavailable".to_string(),
        reason: reason.to_string(),
        captured_at: current_time_millis(),
        platform: std::env::consts::OS.to_string(),
        processes: Vec::new(),
        total: 0,
        matched: 0,
        offset: 0,
        has_more: false,
        next_offset: None,
    }
}

#[cfg(not(windows))]
pub(crate) fn read_system_process_snapshot(arguments: &serde_json::Value) -> SystemProcessSnapshot {
    system_process_unavailable("unsupported_platform")
}

#[cfg(windows)]
pub(crate) fn read_system_process_snapshot(arguments: &serde_json::Value) -> SystemProcessSnapshot {
    let snapshot = match unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) } {
        Ok(snapshot) => snapshot,
        Err(_) => return system_process_unavailable("process_enumeration_failed"),
    };
    let mut entries: Vec<ProcessEntry> = Vec::new();
    let mut entry = PROCESSENTRY32W::default();
    entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
    let mut next = unsafe { Process32FirstW(snapshot, &mut entry) };
    if next.is_err() {
        let _ = unsafe { CloseHandle(snapshot) };
        return system_process_unavailable("process_enumeration_failed");
    }
    while next.is_ok() {
        let name = String::from_utf16_lossy(&entry.szExeFile)
            .trim_end_matches('\0')
            .to_string();
        entries.push(ProcessEntry {
            pid: entry.th32ProcessID,
            name: name.chars().take(100).collect(),
        });
        next = unsafe { Process32NextW(snapshot, &mut entry) };
    }
    let _ = unsafe { CloseHandle(snapshot) };
    process_snapshot_page(entries, arguments)
}

pub(crate) fn process_snapshot_page(
    mut entries: Vec<ProcessEntry>,
    arguments: &serde_json::Value,
) -> SystemProcessSnapshot {
    let total = entries.len();
    let query = arguments
        .get("query")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("")
        .trim()
        .to_lowercase();
    entries.retain(|entry| entry.name.to_lowercase().contains(&query));
    entries.sort_by_key(|entry| entry.pid);
    let matched = entries.len();
    let offset = arguments
        .get("offset")
        .and_then(serde_json::Value::as_u64)
        .unwrap_or(0) as usize;
    let limit = arguments
        .get("limit")
        .and_then(serde_json::Value::as_u64)
        .unwrap_or(128)
        .clamp(1, 128) as usize;
    let processes: Vec<_> = entries.into_iter().skip(offset).take(limit).collect();
    let next = offset.saturating_add(processes.len());
    let has_more = next < matched;
    SystemProcessSnapshot {
        ok: true,
        status: "ok".to_string(),
        reason: String::new(),
        captured_at: current_time_millis(),
        platform: std::env::consts::OS.to_string(),
        processes,
        total,
        matched,
        offset,
        has_more,
        next_offset: has_more.then_some(next),
    }
}

pub(crate) fn system_process_terminate_unavailable(
    pid: u32,
    reason: &str,
) -> SystemProcessTerminateResult {
    SystemProcessTerminateResult {
        ok: false,
        status: "unavailable".to_string(),
        reason: reason.to_string(),
        pid,
        platform: std::env::consts::OS.to_string(),
    }
}

#[cfg(not(windows))]
pub(crate) fn terminate_system_process(pid: u32) -> SystemProcessTerminateResult {
    system_process_terminate_unavailable(pid, "unsupported_platform")
}

#[cfg(windows)]
pub(crate) fn terminate_system_process(pid: u32) -> SystemProcessTerminateResult {
    if pid == 0 {
        return system_process_terminate_unavailable(pid, "invalid_pid");
    }
    if pid == std::process::id() {
        return system_process_terminate_unavailable(pid, "protected_process");
    }
    let handle = match unsafe { OpenProcess(PROCESS_TERMINATE | PROCESS_SYNCHRONIZE, false, pid) } {
        Ok(handle) => handle,
        Err(_) => return system_process_terminate_unavailable(pid, "permission_denied"),
    };
    let result = unsafe { TerminateProcess(handle, 1) };
    let terminated =
        result.is_ok() && unsafe { WaitForSingleObject(handle, 5_000) } == WAIT_OBJECT_0;
    let _ = unsafe { CloseHandle(handle) };
    if !terminated {
        return system_process_terminate_unavailable(
            pid,
            if result.is_err() {
                "terminate_failed"
            } else {
                "termination_unconfirmed"
            },
        );
    }
    SystemProcessTerminateResult {
        ok: true,
        status: "ok".to_string(),
        reason: String::new(),
        pid,
        platform: std::env::consts::OS.to_string(),
    }
}
