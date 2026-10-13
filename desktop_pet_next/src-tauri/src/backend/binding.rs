use std::{
    sync::{Mutex, OnceLock},
    time::Duration,
};

use crate::backend::models::BackendInstanceVerification;
use crate::pet_state::models::PetState;
use crate::runtime::config::{
    normalize_backend_url, runtime_backend_url, runtime_instance_id, BACKEND_URL_ENV,
};

pub(crate) static VERIFIED_BACKEND_URL: OnceLock<Mutex<String>> = OnceLock::new();

pub(crate) fn verified_backend_store() -> &'static Mutex<String> {
    VERIFIED_BACKEND_URL.get_or_init(|| Mutex::new(String::new()))
}

pub(crate) fn set_verified_backend_url(value: &str) {
    if let Ok(mut current) = verified_backend_store().lock() {
        *current = normalize_backend_url(value);
    }
}

pub(crate) fn current_bound_backend_url(state: &PetState) -> String {
    if let Ok(current) = verified_backend_store().lock() {
        if !current.trim().is_empty() {
            return current.clone();
        }
    }
    if std::env::var_os(BACKEND_URL_ENV).is_some() {
        runtime_backend_url()
    } else {
        normalize_backend_url(&state.backend_url)
    }
}

#[tauri::command]
pub(crate) async fn verify_backend_instance(backend_url: String) -> BackendInstanceVerification {
    let result = verify_backend_instance_url(&backend_url).await;
    if result.ok {
        set_verified_backend_url(&backend_url);
    }
    result
}

pub(crate) async fn verify_backend_instance_url(backend_url: &str) -> BackendInstanceVerification {
    let instance_id = match runtime_instance_id() {
        Ok(value) => value,
        Err(reason) => return backend_verification_failure("invalid_binding", &reason, "", ""),
    };
    let backend = match reqwest::Url::parse(&normalize_backend_url(backend_url)) {
        Ok(value)
            if matches!(value.scheme(), "http" | "https")
                && value.username().is_empty()
                && value.password().is_none() =>
        {
            value
        }
        _ => {
            return backend_verification_failure(
                "invalid_backend_url",
                "invalid_backend_url",
                &instance_id,
                "",
            )
        }
    };
    let health_url = match backend.join("/health") {
        Ok(value) => value,
        Err(_) => {
            return backend_verification_failure(
                "invalid_backend_url",
                "invalid_backend_url",
                &instance_id,
                "",
            )
        }
    };
    let client = match reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(4))
        .timeout(Duration::from_secs(6))
        .build()
    {
        Ok(value) => value,
        Err(_) => {
            return backend_verification_failure(
                "unavailable",
                "http_client_unavailable",
                &instance_id,
                "",
            )
        }
    };
    let response = match client
        .get(health_url)
        .header(reqwest::header::ACCEPT, "application/json")
        .send()
        .await
    {
        Ok(value) => value,
        Err(_) => {
            return backend_verification_failure(
                "unavailable",
                "health_request_failed",
                &instance_id,
                "",
            )
        }
    };
    if !response.status().is_success() {
        return backend_verification_failure("unavailable", "health_http_error", &instance_id, "");
    }
    let payload: serde_json::Value = match response.json().await {
        Ok(value) => value,
        Err(_) => {
            return backend_verification_failure(
                "unavailable",
                "health_invalid_json",
                &instance_id,
                "",
            )
        }
    };
    let (valid, actual_instance_id) = health_payload_matches_instance(&payload, &instance_id);
    if !valid {
        return backend_verification_failure(
            "rejected",
            "instance_id_mismatch",
            &instance_id,
            &actual_instance_id,
        );
    }
    BackendInstanceVerification {
        ok: true,
        status: "verified".to_string(),
        reason: String::new(),
        instance_id,
        actual_instance_id,
    }
}

pub(crate) fn health_payload_matches_instance(
    payload: &serde_json::Value,
    expected: &str,
) -> (bool, String) {
    let actual_instance_id = payload
        .get("instance_id")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    let valid = payload.get("status").and_then(serde_json::Value::as_str) == Some("ok")
        && payload
            .get("root_binding")
            .and_then(serde_json::Value::as_str)
            == Some("valid")
        && actual_instance_id == expected;
    (valid, actual_instance_id)
}

pub(crate) fn backend_verification_failure(
    status: &str,
    reason: &str,
    instance_id: &str,
    actual_instance_id: &str,
) -> BackendInstanceVerification {
    BackendInstanceVerification {
        ok: false,
        status: status.to_string(),
        reason: reason.to_string(),
        instance_id: instance_id.to_string(),
        actual_instance_id: actual_instance_id.to_string(),
    }
}
