//! Restart only the verified local launcher binding; credentials stay native.
use serde::Deserialize;
use serde_json::{json, Value};
use std::{fs, path::PathBuf, process::Command, time::Duration};
use tauri::AppHandle;

static OPERATION: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct RestartRequest {
    action: String,
    backend_url: String,
    bot_id: String,
}

fn failure(reason: &str) -> Value {
    json!({"ok":false, "status":"failed", "reason":reason})
}

fn restart_observed(data: &Value, previous_pid: u64) -> bool {
    data["ok"] == true
        && data["processId"]
            .as_u64()
            .is_some_and(|pid| pid > 0 && pid != previous_pid)
}

fn launcher() -> Result<PathBuf, &'static str> {
    let path = PathBuf::from(
        std::env::var_os("AKANE_MANAGED_LAUNCHER").ok_or("managed_launcher_unavailable")?,
    );
    if !path.is_absolute()
        || !path.is_file()
        || path.file_name().and_then(|v| v.to_str()) != Some("start_akane_next.ps1")
        || !crate::runtime::config::env_flag_enabled("AKANE_MANAGED_STOP_BACKEND")
    {
        return Err("managed_launcher_unavailable");
    }
    Ok(path)
}

#[tauri::command]
pub(crate) async fn memory_backend_restart(app: AppHandle, request: RestartRequest) -> Value {
    let Ok(_operation) = OPERATION.try_lock() else {
        return failure("backend_restart_busy");
    };
    let path = match launcher() {
        Ok(v) => v,
        Err(e) => return failure(e),
    };
    let instance = match crate::runtime::config::runtime_instance_id() {
        Ok(v) => v,
        Err(_) => return failure("invalid_runtime_binding"),
    };
    let state = match crate::pet_state::persistence::load_pet_state(app) {
        Ok(v) => v,
        Err(_) => return failure("client_state_unavailable"),
    };
    let backend = match reqwest::Url::parse(&crate::runtime::config::runtime_backend_url()) {
        Ok(v) => v,
        Err(_) => return failure("invalid_backend_url"),
    };
    let bound = crate::backend::binding::current_bound_backend_url(&state);
    if state.host_id != instance
        || state.bound_bot_id != request.bot_id
        || crate::runtime::config::runtime_bound_bot_id()
            .ok()
            .as_deref()
            != Some(request.bot_id.as_str())
        || reqwest::Url::parse(&bound).ok().as_ref() != Some(&backend)
        || reqwest::Url::parse(&request.backend_url).ok().as_ref() != Some(&backend)
        || backend.scheme() != "http"
        || backend.path() != "/"
        || !backend.username().is_empty()
        || backend.password().is_some()
        || backend.query().is_some()
        || backend.fragment().is_some()
        || !crate::runtime::config::is_loopback_backend_host(backend.host_str().unwrap_or(""))
    {
        return failure("backend_restart_binding_mismatch");
    }
    if request.action == "detect" {
        return json!({"ok":true, "status":"available"});
    }
    if request.action != "restart" {
        return failure("invalid_restart_action");
    }
    // Establish writable launch resources before shutting down any service.
    let root = match crate::runtime::paths::akane_data_root() {
        Ok(v) => v,
        Err(_) => return failure("client_state_unavailable"),
    };
    let logs = root.join("logs");
    if fs::create_dir_all(&logs).is_err() {
        return failure("backend_restart_log_unavailable");
    }
    let output = match fs::File::create(logs.join("managed-backend-restart.log")) {
        Ok(v) => v,
        Err(_) => return failure("backend_restart_log_unavailable"),
    };
    let mut probe_shell = Command::new("pwsh");
    probe_shell
        .args(["-NoProfile", "-Command", "exit 0"])
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        probe_shell.creation_flags(0x08000000);
    }
    if !probe_shell.status().is_ok_and(|v| v.success()) {
        return failure("managed_launcher_unavailable");
    }
    if !crate::backend::binding::verify_backend_instance_url(backend.as_str())
        .await
        .ok
    {
        return failure("backend_instance_not_verified");
    }
    let token = match crate::backend::policy::resolve_admin_token(
        &instance,
        std::env::var(crate::runtime::config::ADMIN_TOKEN_ENV)
            .ok()
            .filter(|v| !v.trim().is_empty()),
    ) {
        Ok(v) => v,
        Err(e) => return failure(e),
    };
    let client = match reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(55))
        .build()
    {
        Ok(v) => v,
        Err(_) => return failure("http_client_unavailable"),
    };
    let prepare_url = backend
        .join("control-center/backend-restart/prepare")
        .unwrap();
    let mut previous_pid = None;
    for _ in 0..120 {
        let mut call = client.post(prepare_url.clone());
        if let Some(ref value) = token {
            call = call.bearer_auth(value);
        }
        let prepared: Value = match call.send().await {
            Ok(v) => match v.json().await {
                Ok(v) => v,
                Err(_) => return failure("backend_restart_bad_response"),
            },
            Err(_) => return failure("backend_restart_prepare_failed"),
        };
        if prepared["ok"] == true && prepared["status"] == "ready" {
            previous_pid = prepared["processId"].as_u64();
            break;
        }
        if !matches!(prepared["status"].as_str(), Some("busy" | "preparing")) {
            return failure(
                prepared["reason"]
                    .as_str()
                    .unwrap_or("backend_restart_prepare_failed"),
            );
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
    let Some(previous_pid) = previous_pid else {
        return failure("backend_restart_wait_timeout");
    };
    let port = backend.port_or_known_default().unwrap_or(9999).to_string();
    let launched = tokio::task::spawn_blocking(move || -> Result<bool, &'static str> {
        let mut command = Command::new("pwsh");
        command
            .args(["-NoProfile", "-File"])
            .arg(path)
            .args(["-Action", "Restart", "-InstanceId", &instance, "-DataRoot"])
            .arg(root)
            .args(["-BackendPort", &port, "-SkipDesktop", "-NoBuild"])
            .stdin(std::process::Stdio::null())
            .stdout(
                output
                    .try_clone()
                    .map_err(|_| "backend_restart_log_unavailable")?,
            )
            .stderr(output);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = command
            .spawn()
            .map_err(|_| "backend_restart_launch_failed")?;
        for _ in 0..240 {
            if let Some(status) = child
                .try_wait()
                .map_err(|_| "backend_restart_launch_failed")?
            {
                return Ok(status.success());
            }
            std::thread::sleep(Duration::from_millis(500));
        }
        // Stop this helper only, never an unverified backend or desktop process.
        let _ = child.kill();
        let _ = child.wait();
        Err("backend_restart_launch_timeout")
    })
    .await;
    match launched {
        Ok(Ok(true)) => {}
        Ok(Err(reason)) => return failure(reason),
        _ => return failure("backend_restart_launch_failed"),
    }
    let read_url = match backend.join(&format!(
        "api/bots/{}/control-center/memory-settings",
        request.bot_id
    )) {
        Ok(v) => v,
        Err(_) => return failure("invalid_runtime_binding"),
    };
    let probe = match reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(5))
        .build()
    {
        Ok(v) => v,
        Err(_) => return failure("http_client_unavailable"),
    };
    for _ in 0..30 {
        let mut call = probe.get(read_url.clone());
        if let Some(ref value) = token {
            call = call.bearer_auth(value);
        }
        if let Ok(response) = call.send().await {
            if let Ok(data) = response.json::<Value>().await {
                if restart_observed(&data, previous_pid)
                    && crate::backend::binding::verify_backend_instance_url(backend.as_str())
                        .await
                        .ok
                {
                    return json!({"ok":true, "status":"completed", "observed":true});
                }
            }
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
    failure("backend_restart_not_confirmed")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn old_health_and_invalid_pid_cannot_confirm_restart() {
        assert!(!restart_observed(&json!({"ok":true,"processId":10}), 10));
        assert!(!restart_observed(&json!({"ok":true,"processId":0}), 10));
        assert!(!restart_observed(&json!({"ok":false,"processId":11}), 10));
        assert!(!restart_observed(&json!({"ok":true,"processId":"11"}), 10));
        assert!(restart_observed(&json!({"ok":true,"processId":11}), 10));
    }
}
