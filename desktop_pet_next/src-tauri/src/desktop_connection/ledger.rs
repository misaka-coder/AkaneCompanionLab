use std::{
    collections::{HashMap, VecDeque},
    sync::{Mutex, OnceLock},
};

pub(crate) const SATELLITE_LEDGER_LIMIT: usize = 512;

pub(crate) static SATELLITE_INVOCATION_LEDGER: OnceLock<Mutex<SatelliteInvocationLedger>> =
    OnceLock::new();

#[derive(Debug, Clone)]
pub(crate) struct SatelliteTerminalResult {
    pub(crate) tool_id: String,
    pub(crate) status: String,
    pub(crate) reason: String,
    pub(crate) data: serde_json::Value,
}

#[derive(Debug, Default)]
pub(crate) struct SatelliteInvocationLedger {
    pub(crate) entries: HashMap<String, SatelliteTerminalResult>,
    pub(crate) order: VecDeque<String>,
}

impl SatelliteInvocationLedger {
    pub(super) fn get(&self, invocation_id: &str) -> Option<SatelliteTerminalResult> {
        self.entries.get(invocation_id).cloned()
    }

    pub(super) fn insert(&mut self, invocation_id: String, result: SatelliteTerminalResult) {
        if !self.entries.contains_key(&invocation_id) {
            self.order.push_back(invocation_id.clone());
        }
        self.entries.insert(invocation_id, result);
        while self.order.len() > SATELLITE_LEDGER_LIMIT {
            if let Some(expired) = self.order.pop_front() {
                self.entries.remove(&expired);
            }
        }
    }
}

pub(crate) fn satellite_terminal_result(
    tool_id: &str,
    status: &str,
    reason: &str,
    data: serde_json::Value,
) -> SatelliteTerminalResult {
    SatelliteTerminalResult {
        tool_id: tool_id.to_string(),
        status: status.to_string(),
        reason: reason.to_string(),
        data,
    }
}
