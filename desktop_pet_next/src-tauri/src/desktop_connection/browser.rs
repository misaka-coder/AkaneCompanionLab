use std::sync::Mutex;

use crate::desktop_connection::contracts::OPEN_BROWSER_TOOL_ID;
use crate::desktop_connection::ledger::{
    satellite_terminal_result, SatelliteInvocationLedger, SatelliteTerminalResult,
    SATELLITE_INVOCATION_LEDGER,
};
use crate::local_files::system_open::open_url_with_system;
use crate::local_files::urls::normalize_public_external_url;

// Reserve browser execution until its terminal result is stored. The shared
// ledger stays available to other tools while the OS opener runs.
static BROWSER_EXECUTION: Mutex<()> = Mutex::new(());

pub(crate) fn execute_satellite_browser_once(
    invocation_id: &str,
    raw_url: &str,
) -> SatelliteTerminalResult {
    execute_satellite_browser_once_with(invocation_id, raw_url, open_url_with_system)
}

pub(crate) fn execute_satellite_browser_once_with<F>(
    invocation_id: &str,
    raw_url: &str,
    opener: F,
) -> SatelliteTerminalResult
where
    F: FnOnce(&str) -> Result<(), String>,
{
    let Ok(_execution) = BROWSER_EXECUTION.lock() else {
        return satellite_terminal_result(
            OPEN_BROWSER_TOOL_ID,
            "failed",
            "browser_execution_state_unavailable",
            serde_json::Value::Null,
        );
    };
    let ledger = SATELLITE_INVOCATION_LEDGER
        .get_or_init(|| Mutex::new(SatelliteInvocationLedger::default()));
    {
        let Ok(current) = ledger.lock() else {
            return satellite_terminal_result(
                OPEN_BROWSER_TOOL_ID,
                "failed",
                "browser_execution_state_unavailable",
                serde_json::Value::Null,
            );
        };
        if let Some(existing) = current.get(invocation_id) {
            if existing.tool_id == OPEN_BROWSER_TOOL_ID {
                return existing;
            }
            return satellite_terminal_result(
                OPEN_BROWSER_TOOL_ID,
                "rejected",
                "invocation_id_tool_conflict",
                serde_json::Value::Null,
            );
        }
    }
    let result = match normalize_public_external_url(raw_url) {
        Ok(url) => match opener(&url) {
            Ok(()) => SatelliteTerminalResult {
                tool_id: OPEN_BROWSER_TOOL_ID.to_string(),
                status: "succeeded".to_string(),
                reason: String::new(),
                data: serde_json::Value::Object(serde_json::Map::new()),
            },
            Err(_) => SatelliteTerminalResult {
                tool_id: OPEN_BROWSER_TOOL_ID.to_string(),
                status: "failed".to_string(),
                reason: "os_open_failed".to_string(),
                data: serde_json::Value::Null,
            },
        },
        Err(_) => SatelliteTerminalResult {
            tool_id: OPEN_BROWSER_TOOL_ID.to_string(),
            status: "rejected".to_string(),
            reason: "unsafe_url".to_string(),
            data: serde_json::Value::Null,
        },
    };
    if let Ok(mut current) = ledger.lock() {
        current.insert(invocation_id.to_string(), result.clone());
    } else {
        return satellite_terminal_result(
            OPEN_BROWSER_TOOL_ID,
            "failed",
            "browser_execution_state_unavailable",
            serde_json::Value::Null,
        );
    }
    result
}
