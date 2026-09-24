pub mod protocol;
mod context;
mod workflow;
#[cfg(test)] mod visual_batch_tests;
pub fn user_input_generation() -> u64 {
    #[cfg(windows)] { monitor::input_generation() }
    #[cfg(not(windows))] { 0 }
}
#[cfg(windows)] pub fn input_quiet() -> bool { monitor::input_quiet() }
pub fn start_input_monitor() -> bool {
    #[cfg(windows)] { monitor::start() }
    #[cfg(not(windows))] { false }
}
pub fn input_enabled() -> bool {
    #[cfg(windows)] { session::input_enabled() }
    #[cfg(not(windows))] { false }
}
#[cfg(all(test,windows))]
pub fn visible_chrome_titles_for_readonly_test()->Vec<String>{
    windows::enumerate().into_iter().filter(|(id,_)|windows::process_name(id)=="chrome.exe"&&windows::geometry(id).is_ok())
        .map(|(_,title)|title).collect()
}
#[cfg(windows)] mod windows;
#[cfg(windows)] mod capture;
#[cfg(windows)] mod region;
#[cfg(windows)] mod accessibility;
#[cfg(windows)] mod monitor;
#[cfg(windows)] mod input;
#[cfg(windows)] mod policy;
#[cfg(windows)] mod session;
#[cfg(windows)] mod targets;
#[cfg(all(test, windows))] mod tests;
#[cfg(all(test, windows))] mod live_enhanced;
#[cfg(all(test, windows))] mod live_visual;
#[cfg(all(test, windows))] mod model_eval;
#[cfg(all(test, windows))] mod capture_lifecycle_tests;

pub fn execute(invocation: &str, arguments: &serde_json::Value) -> serde_json::Value {
    #[cfg(windows)] { session::execute(invocation, arguments) }
    #[cfg(not(windows))] { let _ = (invocation, arguments); protocol::fail("unsupported_platform") }
}

/// Local stop must never wait for UIA, frame capture, or the Satellite receive loop.
#[tauri::command]
pub async fn stop_computer_use() -> serde_json::Value {
    crate::device_control::request("stop", serde_json::json!({})).await
}

#[tauri::command]
pub async fn get_computer_use_status() -> serde_json::Value {
    crate::device_control::request("status", serde_json::json!({})).await
}
#[tauri::command]
pub async fn get_computer_use_targets()->serde_json::Value {
    crate::device_control::request("targets", serde_json::json!({})).await
}
#[tauri::command]
pub async fn configure_computer_use_target(target_id:String)->serde_json::Value {
    crate::device_control::request("target", serde_json::json!({"target_id":target_id})).await
}
#[tauri::command]
pub async fn configure_computer_use(input_enabled: bool) -> serde_json::Value {
    crate::device_control::request("configure", serde_json::json!({"enabled":input_enabled})).await
}
#[tauri::command]
pub async fn resume_computer_use() -> serde_json::Value {
    crate::device_control::request("resume", serde_json::json!({})).await
}

/// Only the execution owner calls this; presentation commands use local IPC.
pub(crate) fn local_control(action: &str, args: &serde_json::Value) -> serde_json::Value {
    #[cfg(windows)] { match action {
        "status" => session::local_status(), "stop" => session::stop_local(),
        "resume" => session::resume_local(), "targets" => targets::list(),
        "configure" => args["enabled"].as_bool().map(session::configure).unwrap_or_else(||protocol::fail("invalid_control_arguments")),
        "target" => args["target_id"].as_str().map(targets::configure).unwrap_or_else(||protocol::fail("invalid_control_arguments")),
        _ => protocol::fail("unknown_local_control"),
    } }
    #[cfg(not(windows))] { let _=(action,args);protocol::fail("unsupported_platform") }
}

pub struct ConnectionGuard(pub String);
impl ConnectionGuard {
    pub fn new(epoch:String)->Self {crate::control_lease::begin_connection(&epoch);Self(epoch)}
}
impl Drop for ConnectionGuard {
    fn drop(&mut self) {
        if !crate::control_lease::epoch_valid(&self.0){return;}
        crate::control_lease::stop();
        crate::personal_browser::connection_closed(&self.0);
        #[cfg(windows)] session::connection_closed(&self.0);
    }
}
