use std::net::IpAddr;

pub(crate) const DATA_ROOT_ENV: &str = "AKANE_DATA_ROOT";

pub(crate) const INSTANCE_ID_ENV: &str = "AKANE_INSTANCE_ID";

pub(crate) const BOUND_BOT_ID_ENV: &str = "AKANE_BOUND_BOT_ID";

pub(crate) const ADMIN_TOKEN_ENV: &str = "AKANE_ADMIN_TOKEN";

pub(crate) const SATELLITE_TOKEN_ENV: &str = "AKANE_DESKTOP_SATELLITE_TOKEN";

pub(crate) const BACKEND_URL_ENV: &str = "AKANE_BACKEND_URL";

pub(crate) const EXTERNAL_DEVICE_EXECUTOR_ENV: &str = "AKANE_DESKTOP_EXECUTOR_EXTERNAL";

pub(crate) const APP_DIRECTORY_NAME: &str = "Akane";

pub(crate) const LOCAL_DEFAULT_INSTANCE_ID: &str = "local-default";

pub(crate) const DEFAULT_BACKEND_URL: &str = "http://127.0.0.1:9999";

pub(crate) const DEFAULT_PROFILE_USER_ID: &str = "master";

pub(crate) const DEFAULT_CHARACTER_PACK_ID: &str = "akane_v1";

pub(crate) const DEFAULT_OUTFIT: &str = "default";

pub(crate) const DEFAULT_EMOTION: &str = "normal";

pub(crate) fn runtime_instance_id() -> Result<String, String> {
    let value = std::env::var(INSTANCE_ID_ENV).unwrap_or_default();
    let instance_id = if value.trim().is_empty() {
        LOCAL_DEFAULT_INSTANCE_ID.to_string()
    } else {
        value.trim().to_string()
    };
    if !is_safe_instance_id(&instance_id) {
        return Err("invalid_instance_id".to_string());
    }
    Ok(instance_id)
}

pub(crate) fn runtime_bound_bot_id() -> Result<String, String> {
    let value = std::env::var(BOUND_BOT_ID_ENV).unwrap_or_default();
    let bot_id = if value.trim().is_empty() {
        runtime_instance_id()?
    } else {
        value.trim().to_string()
    };
    if !is_safe_instance_id(&bot_id) {
        return Err("invalid_bound_bot_id".to_string());
    }
    Ok(bot_id)
}

pub(crate) fn runtime_backend_url() -> String {
    std::env::var(BACKEND_URL_ENV)
        .ok()
        .map(|value| normalize_backend_url(&value))
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| DEFAULT_BACKEND_URL.to_string())
}

pub(crate) fn normalize_backend_url(value: &str) -> String {
    let trimmed = value.trim().trim_end_matches('/').to_string();
    if trimmed.is_empty() {
        DEFAULT_BACKEND_URL.to_string()
    } else {
        trimmed
    }
}

pub(crate) fn is_safe_instance_id(value: &str) -> bool {
    let mut chars = value.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    if !first.is_ascii_alphanumeric() || value.len() > 64 {
        return false;
    }
    chars.all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | '.' | '-'))
}

pub(crate) fn is_device_only_mode() -> bool {
    std::env::args().any(|arg| arg == "--device-only")
}

pub(crate) fn env_flag_enabled(name: &str) -> bool {
    match std::env::var(name) {
        Ok(value) => value == "1" || value.eq_ignore_ascii_case("true"),
        Err(_) => false,
    }
}

pub(crate) fn is_loopback_backend_host(host: &str) -> bool {
    let value = host.trim().trim_matches(['[', ']']).to_ascii_lowercase();
    value == "localhost"
        || value
            .parse::<IpAddr>()
            .map(|ip| ip.is_loopback())
            .unwrap_or(false)
}
