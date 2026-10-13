pub(crate) mod browser;
pub(crate) mod contracts;
pub(crate) mod execution;
pub(crate) mod handshake;
pub(crate) mod ledger;
pub(crate) mod messages;
pub(crate) mod plugin_events;
pub(crate) mod session;
pub(crate) mod validation;

#[cfg(test)]
mod browser_tests;
#[cfg(test)]
mod contract_tests;
pub(crate) mod device_runtime;
#[cfg(test)]
mod execution_tests;
#[cfg(test)]
mod handshake_tests;
#[cfg(test)]
mod plugin_event_tests;
#[cfg(test)]
mod validation_tests;
#[cfg(test)]
mod wire_tests;
