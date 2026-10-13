use crate::scene_window;
use std::time::Duration;

use crate::runtime::config::{is_safe_instance_id, LOCAL_DEFAULT_INSTANCE_ID};

pub(crate) const DEFAULT_ADMIN_REQUEST_TIMEOUT_SECONDS: u64 = 45;

pub(crate) const PLUGIN_INSTALL_ADMIN_REQUEST_TIMEOUT_SECONDS: u64 = 12 * 60;

pub(crate) fn default_backend_admin_method() -> String {
    "POST".to_string()
}

pub(crate) fn parse_backend_admin_method(raw: &str) -> Result<reqwest::Method, &'static str> {
    match raw.trim().to_ascii_uppercase().as_str() {
        "" | "POST" => Ok(reqwest::Method::POST),
        "GET" => Ok(reqwest::Method::GET),
        "DELETE" => Ok(reqwest::Method::DELETE),
        "PUT" => Ok(reqwest::Method::PUT),
        _ => Err("admin_method_not_allowed"),
    }
}

pub(crate) fn backend_admin_request_timeout(path: &str, method: &reqwest::Method) -> Duration {
    if path.ends_with("/scene/turn") {
        return Duration::from_secs(180);
    }
    let plugin_path = path
        .split_once("/admin/plugins/")
        .map(|(_, suffix)| suffix.trim_matches('/'))
        .unwrap_or_default();
    let is_installation = method == reqwest::Method::POST
        && (matches!(plugin_path, "stages" | "stages/source")
            || (plugin_path.starts_with("stages/") && plugin_path.ends_with("/install")));
    Duration::from_secs(if is_installation {
        PLUGIN_INSTALL_ADMIN_REQUEST_TIMEOUT_SECONDS
    } else {
        DEFAULT_ADMIN_REQUEST_TIMEOUT_SECONDS
    })
}

pub(crate) fn resolve_admin_token(
    instance_id: &str,
    configured_token: Option<String>,
) -> Result<Option<String>, &'static str> {
    if instance_id != LOCAL_DEFAULT_INSTANCE_ID && configured_token.is_none() {
        return Err("missing_admin_token");
    }
    Ok(configured_token)
}

pub(crate) fn same_backend_origin(left: &reqwest::Url, right: &reqwest::Url) -> bool {
    left.scheme() == right.scheme()
        && left.host_str() == right.host_str()
        && left.port_or_known_default() == right.port_or_known_default()
        && left.username().is_empty()
        && left.password().is_none()
}

pub(crate) fn is_allowed_admin_path(path: &str) -> bool {
    if let Some((_bot_id, bot_path)) = scoped_admin_bot_path(path) {
        if bot_path == "desktop-pet/care/shop" || scene_window::is_scene_admin_path(bot_path) {
            return true;
        }
        if bot_path == "sessions/ensure" {
            return true;
        }
        return [
            "control-center/",
            "capabilities/",
            "qq/",
            "admin/",
            "plugins/",
            "memcore/",
            "desktop-pet/project-workspaces/",
        ]
        .iter()
        .any(|prefix| bot_path.starts_with(prefix));
    }
    if path == "/desktop-pet/care/shop"
        || scene_window::is_scene_admin_path(path.trim_start_matches('/'))
    {
        return true;
    }
    if path == "/sessions/ensure" {
        return true;
    }
    [
        "/control-center/",
        "/capabilities/",
        "/api/capabilities/",
        "/api/qq/",
        "/admin/",
        "/plugins/",
        "/memcore/",
        "/desktop-pet/project-workspaces/",
    ]
    .iter()
    .any(|prefix| path.starts_with(prefix))
}

pub(crate) fn scoped_admin_bot_id(path: &str) -> Option<&str> {
    scoped_admin_bot_path(path).map(|(bot_id, _bot_path)| bot_id)
}

pub(crate) fn scoped_admin_bot_path(path: &str) -> Option<(&str, &str)> {
    let remainder = path.strip_prefix("/api/bots/")?;
    let (bot_id, bot_path) = remainder.split_once('/')?;
    if is_safe_instance_id(bot_id) {
        Some((bot_id, bot_path))
    } else {
        None
    }
}
