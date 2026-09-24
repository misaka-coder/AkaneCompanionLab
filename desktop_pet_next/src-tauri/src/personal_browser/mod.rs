mod mcp;
mod session;
use serde_json::{Value, json};
pub const TOOL_ID: &str = "browser_page_personal";
pub const SPEC_VERSION: &str = "1.0.0";
pub const SCHEMA_VERSION: u64 = 1;
pub const SCHEMA_HASH: &str = "sha256:cee066db252ae095cf766dc26624e680085820b079a3b5cce4bfc35ff5dd1363";
pub fn execute(id: &str, args: &Value) -> Value { session::execute(id, args) }
pub fn valid(args: &serde_json::Map<String,Value>) -> bool {
    let Some(action) = args.get("action").and_then(Value::as_str) else {return false;};
    if !["connect","list_tabs","select_tab","disconnect","handoff","current","snapshot","elements","screenshot","read_text",
        "navigate","scroll","click","fill","press","status"].contains(&action) {return false;}
    if action != "connect" && ["browser_session_id","device_epoch"].iter().any(|k| !args.contains_key(*k)) { return false; }
    if matches!(action,"navigate"|"scroll"|"click"|"fill"|"press") && !args.contains_key("observation_id") { return false; }
    if match action { "select_tab" => !args.contains_key("tab_id"), "navigate" => !args.contains_key("url"),
        "click" => !args.contains_key("ref"), "fill" => !args.contains_key("ref") || !args.contains_key("text"),
        "press" => !args.contains_key("key"), _ => false } { return false; }
    args.iter().all(|(key, v)| match key.as_str() {
        "action" => true,
        "browser_session_id"|"device_epoch"|"tab_id"|"observation_id"|"ref" => v.as_str().is_some_and(|v|!v.is_empty() && v.len()<=160),
        "url" => v.as_str().is_some_and(|v|v.chars().count()<=1600),
        "text" => v.as_str().is_some_and(|v|v.chars().count()<=500 && !v.contains(['\r','\n','\t','\0'])),
        "key" => v.as_str().is_some_and(|v|["Enter","Escape","Tab","ArrowDown","ArrowUp","ArrowLeft","ArrowRight","PageDown","PageUp","Home","End"].contains(&v)),
        "scroll_delta" => v.as_i64().is_some_and(|v|(-2400..=2400).contains(&v)),
        "observation_mode" => matches!(v.as_str(),Some("text"|"hybrid"|"visual")), _=>false,
    })
}
#[tauri::command]
pub async fn configure_personal_browser(enabled: bool) -> Value {
    crate::device_control::request("chrome_configure",json!({"enabled":enabled})).await
}
#[tauri::command]
pub async fn get_personal_browser_status() -> Value { crate::device_control::request("chrome_status",json!({})).await }
pub(crate) fn local_control(action:&str,args:&Value)->Value {
    match action {
        "chrome_status"=>session::status(),
        "chrome_configure"=>args["enabled"].as_bool().map(session::configure).unwrap_or_else(||fail("invalid_control_arguments")),
        _=>fail("unknown_local_control"),
    }
}
pub fn connection_closed(epoch: &str) { session::connection_closed(epoch); }
pub fn fail(reason: &str) -> Value { let mut out=crate::computer_use::protocol::fail(reason);out["backend"]=json!("personal_chrome");out }
