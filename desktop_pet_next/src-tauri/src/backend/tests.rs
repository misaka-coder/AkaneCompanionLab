use crate::backend::binding::health_payload_matches_instance;
use crate::backend::plugin_picker::normalize_plugin_picker_kind;
use crate::backend::policy::{
    backend_admin_request_timeout, is_allowed_admin_path, parse_backend_admin_method,
    resolve_admin_token, same_backend_origin, scoped_admin_bot_id,
    DEFAULT_ADMIN_REQUEST_TIMEOUT_SECONDS, PLUGIN_INSTALL_ADMIN_REQUEST_TIMEOUT_SECONDS,
};
use crate::runtime::config::LOCAL_DEFAULT_INSTANCE_ID;
use std::time::Duration;

#[test]
fn health_binding_rejects_another_instance() {
    let payload = serde_json::json!({
        "status": "ok",
        "instance_id": "instance-b",
        "root_binding": "valid",
    });
    let (matches, actual) = health_payload_matches_instance(&payload, "instance-a");
    assert!(!matches);
    assert_eq!(actual, "instance-b");
}

#[test]
fn health_binding_accepts_exact_instance_only() {
    let payload = serde_json::json!({
        "status": "ok",
        "instance_id": "instance-a",
        "root_binding": "valid",
    });
    assert_eq!(
        health_payload_matches_instance(&payload, "instance-a"),
        (true, "instance-a".to_string())
    );
}

#[test]
fn admin_proxy_rejects_external_or_non_admin_targets() {
    let backend = reqwest::Url::parse("http://127.0.0.1:9999").unwrap();
    let external =
        reqwest::Url::parse("http://127.0.0.1:9998/control-center/model-service").unwrap();
    assert!(!same_backend_origin(&backend, &external));
    assert!(is_allowed_admin_path("/control-center/model-service"));
    assert!(is_allowed_admin_path(
        "/api/bots/personal/control-center/model-service"
    ));
    assert!(is_allowed_admin_path("/api/bots/personal/sessions/ensure"));
    assert!(is_allowed_admin_path("/sessions/ensure"));
    assert!(!is_allowed_admin_path("/api/bots/personal/sessions/reset"));
    assert!(!is_allowed_admin_path("/api/bots/personal/think"));
    for path in ["scene/snapshot", "scene/story/catalog", "scene/actions"] {
        assert!(is_allowed_admin_path(&format!("/{path}")));
        assert!(is_allowed_admin_path(&format!("/api/bots/personal/{path}")));
    }
    assert!(!is_allowed_admin_path("/scene/assets/background.png"));
    assert!(!is_allowed_admin_path("/scene/not-an-admin-route"));
    assert!(is_allowed_admin_path("/api/qq/self-check"));
    assert!(!is_allowed_admin_path("/think"));
    assert_eq!(
        scoped_admin_bot_id("/api/bots/personal/control-center/model-service"),
        Some("personal")
    );
    assert_eq!(
        scoped_admin_bot_id("/api/bots/../control-center/model-service"),
        None
    );
}

#[test]
fn admin_proxy_accepts_only_required_http_methods() {
    assert_eq!(parse_backend_admin_method(""), Ok(reqwest::Method::POST));
    assert_eq!(parse_backend_admin_method("get"), Ok(reqwest::Method::GET));
    assert_eq!(
        parse_backend_admin_method("DELETE"),
        Ok(reqwest::Method::DELETE)
    );
    assert_eq!(parse_backend_admin_method("put"), Ok(reqwest::Method::PUT));
    assert_eq!(
        parse_backend_admin_method("PATCH"),
        Err("admin_method_not_allowed")
    );
}

#[test]
fn plugin_installation_proxy_allows_backend_build_timeout() {
    assert_eq!(
        backend_admin_request_timeout("/admin/plugins/stages/source", &reqwest::Method::POST),
        Duration::from_secs(PLUGIN_INSTALL_ADMIN_REQUEST_TIMEOUT_SECONDS)
    );
    assert_eq!(
        backend_admin_request_timeout(
            "/api/bots/personal/admin/plugins/stages/abc/install",
            &reqwest::Method::POST
        ),
        Duration::from_secs(PLUGIN_INSTALL_ADMIN_REQUEST_TIMEOUT_SECONDS)
    );
    assert_eq!(
        backend_admin_request_timeout("/admin/plugins/status", &reqwest::Method::GET),
        Duration::from_secs(DEFAULT_ADMIN_REQUEST_TIMEOUT_SECONDS)
    );
}

#[test]
fn plugin_picker_accepts_only_source_or_wheel() {
    assert_eq!(normalize_plugin_picker_kind(" source "), Some("source"));
    assert_eq!(normalize_plugin_picker_kind("WHEEL"), Some("wheel"));
    assert_eq!(normalize_plugin_picker_kind("zip"), None);
}

#[test]
fn named_admin_requests_require_and_attach_the_process_token() {
    assert_eq!(
        resolve_admin_token("instance-a", None),
        Err("missing_admin_token")
    );
    assert_eq!(
        resolve_admin_token(LOCAL_DEFAULT_INSTANCE_ID, None),
        Ok(None)
    );

    let token = resolve_admin_token("instance-a", Some("secret-value".to_string()))
        .unwrap()
        .unwrap();
    let request = reqwest::Client::new()
        .post("http://127.0.0.1:9999/control-center/model-service")
        .bearer_auth(token)
        .build()
        .unwrap();
    assert_eq!(
        request
            .headers()
            .get(reqwest::header::AUTHORIZATION)
            .and_then(|value| value.to_str().ok()),
        Some("Bearer secret-value")
    );
}
