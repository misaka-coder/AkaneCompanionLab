use crate::runtime::http::{response_bytes_bounded, BodyReadError};
use tauri::AppHandle;

use crate::backend::binding::{
    current_bound_backend_url, set_verified_backend_url, verify_backend_instance_url,
};
use crate::backend::models::{BackendAdminRequest, BackendAdminResponse};
use crate::backend::policy::{
    backend_admin_request_timeout, is_allowed_admin_path, parse_backend_admin_method,
    resolve_admin_token, same_backend_origin, scoped_admin_bot_id,
};
use crate::pet_state::persistence::load_pet_state;
use crate::runtime::config::{runtime_instance_id, ADMIN_TOKEN_ENV};

#[tauri::command]
pub(crate) async fn backend_admin_request(
    app: AppHandle,
    request: BackendAdminRequest,
) -> BackendAdminResponse {
    let runtime_instance_id = match runtime_instance_id() {
        Ok(value) => value,
        Err(_) => return admin_failure("invalid_runtime_binding", "invalid_instance_id", 400),
    };
    let state = match load_pet_state(app) {
        Ok(value) => value,
        Err(_) => return admin_failure("state_unavailable", "client_state_unavailable", 503),
    };
    if state.host_id != runtime_instance_id {
        return admin_failure("instance_mismatch", "client_state_instance_mismatch", 409);
    }

    let method = match parse_backend_admin_method(&request.method) {
        Ok(value) => value,
        Err(reason) => return admin_failure("invalid_request", reason, 405),
    };

    let target = match reqwest::Url::parse(request.url.trim()) {
        Ok(value) => value,
        Err(_) => return admin_failure("invalid_request", "invalid_admin_url", 400),
    };
    let bound_backend_url = current_bound_backend_url(&state);
    let backend = match reqwest::Url::parse(&bound_backend_url) {
        Ok(value) => value,
        Err(_) => return admin_failure("invalid_binding", "invalid_backend_url", 409),
    };
    if !same_backend_origin(&target, &backend) || !is_allowed_admin_path(target.path()) {
        return admin_failure("rejected", "admin_target_not_allowed", 403);
    }
    if let Some(target_bot_id) = scoped_admin_bot_id(target.path()) {
        if target_bot_id != state.bound_bot_id {
            return admin_failure("rejected", "admin_target_bot_mismatch", 403);
        }
    }

    let binding = verify_backend_instance_url(&bound_backend_url).await;
    if !binding.ok {
        return admin_failure(&binding.status, &binding.reason, 409);
    }
    set_verified_backend_url(&bound_backend_url);

    let configured_admin_token = std::env::var(ADMIN_TOKEN_ENV)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());
    let admin_token = match resolve_admin_token(&runtime_instance_id, configured_admin_token) {
        Ok(value) => value,
        Err(reason) => return admin_failure("unauthorized", reason, 401),
    };

    let request_timeout = backend_admin_request_timeout(target.path(), &method);
    let client = match reqwest::Client::builder().timeout(request_timeout).build() {
        Ok(value) => value,
        Err(_) => return admin_failure("unavailable", "http_client_unavailable", 503),
    };
    let mut builder = client
        .request(method.clone(), target)
        .header(reqwest::header::ACCEPT, "application/json")
        .header(reqwest::header::CONTENT_TYPE, "application/json");
    if method != reqwest::Method::GET {
        builder = builder.body(request.body);
    }
    if let Some(token) = admin_token {
        builder = builder.bearer_auth(token);
    }

    let response = match builder.send().await {
        Ok(value) => value,
        Err(_) => return admin_failure("request_failed", "admin_request_failed", 502),
    };
    let http_status = response.status().as_u16();
    let content_type = response
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("application/json")
        .to_string();
    let body = match response_bytes_bounded(response, 4 * 1024 * 1024).await {
        Ok(value) => String::from_utf8_lossy(&value).to_string(),
        Err(BodyReadError::TooLarge) => {
            return admin_failure("bad_response", "admin_response_too_large", 502)
        }
        Err(BodyReadError::Read(_)) => {
            return admin_failure("bad_response", "admin_response_unreadable", 502)
        }
    };

    BackendAdminResponse {
        ok: (200..300).contains(&http_status),
        status: if (200..300).contains(&http_status) {
            "completed".to_string()
        } else {
            format!("http-{http_status}")
        },
        reason: String::new(),
        http_status,
        content_type,
        body,
    }
}

pub(crate) fn admin_failure(status: &str, reason: &str, http_status: u16) -> BackendAdminResponse {
    let body = serde_json::json!({
        "ok": false,
        "status": status,
        "reason": reason,
    })
    .to_string();
    BackendAdminResponse {
        ok: false,
        status: status.to_string(),
        reason: reason.to_string(),
        http_status,
        content_type: "application/json".to_string(),
        body,
    }
}
