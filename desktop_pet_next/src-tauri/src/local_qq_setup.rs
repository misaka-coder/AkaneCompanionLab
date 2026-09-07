//! Desktop-only onboarding for an existing, explicitly trusted Windows NapCat Shell.
//! Credentials and installation paths never enter the public result.
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    fs,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Mutex, OnceLock},
    time::Duration,
};
use tauri::AppHandle;
use tauri_plugin_dialog::DialogExt;

static OPERATION: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());
static CHILD: OnceLock<Mutex<Option<Child>>> = OnceLock::new();
const MAX_CONFIG: u64 = 512 * 1024;

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SetupRequest {
    action: String,
    backend_url: String,
    bot_id: String,
}

struct Binding {
    root: PathBuf,
    backend: reqwest::Url,
    qq: String,
    api: reqwest::Url,
    api_token: String,
    webhook_token: String,
}

fn failure(reason: &str) -> Value {
    json!({"ok": false, "status": "unavailable", "reason": reason})
}

fn loopback_url(raw: &str) -> Result<reqwest::Url, &'static str> {
    let url = reqwest::Url::parse(raw).map_err(|_| "local_qq_invalid_url")?;
    if url.scheme() != "http"
        || !matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "[::1]"))
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || url.path() != "/"
    {
        return Err("local_qq_requires_loopback");
    }
    Ok(url)
}

fn binding(app: &AppHandle, request: &SetupRequest) -> Result<Binding, &'static str> {
    let state = super::load_pet_state(app.clone()).map_err(|_| "local_qq_binding_unavailable")?;
    let instance = super::runtime_instance_id().map_err(|_| "local_qq_binding_unavailable")?;
    let launch_bot = super::runtime_bound_bot_id().map_err(|_| "local_qq_binding_unavailable")?;
    let current = super::current_bound_backend_url(&state);
    let backend = loopback_url(&current)?;
    let requested = loopback_url(&request.backend_url)?;
    let launched = loopback_url(&super::runtime_backend_url())?;
    if state.host_id != instance
        || state.bound_bot_id != request.bot_id
        || request.bot_id != launch_bot
        || backend != requested
        || backend != launched
    {
        return Err("local_qq_binding_changed");
    }
    let qq = std::env::var("QQ_BOT_QQ").unwrap_or_default();
    if !(5..=20).contains(&qq.len()) || !qq.bytes().all(|c| c.is_ascii_digit()) {
        return Err("local_qq_launch_profile_missing");
    }
    let api = loopback_url(&std::env::var("QQ_ONEBOT_HTTP_URL").unwrap_or_default())?;
    let api_token = std::env::var("QQ_ONEBOT_ACCESS_TOKEN").unwrap_or_default();
    let webhook_token = std::env::var("QQ_WEBHOOK_SECRET").unwrap_or_default();
    if api_token.is_empty()
        || webhook_token.is_empty()
        || api.port_or_known_default() == backend.port_or_known_default()
    {
        return Err("local_qq_launch_profile_missing");
    }
    Ok(Binding {
        root: super::akane_data_root().map_err(|_| "local_qq_binding_unavailable")?,
        backend,
        qq,
        api,
        api_token,
        webhook_token,
    })
}

fn read_json(path: &Path) -> Result<Value, &'static str> {
    if fs::metadata(path)
        .map_err(|_| "napcat_config_missing")?
        .len()
        > MAX_CONFIG
    {
        return Err("napcat_config_invalid");
    }
    serde_json::from_slice(&fs::read(path).map_err(|_| "napcat_config_unreadable")?)
        .map_err(|_| "napcat_config_invalid")
}

fn child_path(root: &Path, relative: &str) -> Result<PathBuf, &'static str> {
    let path = root
        .join(relative)
        .canonicalize()
        .map_err(|_| "napcat_installation_incomplete")?;
    if !path.starts_with(root) {
        return Err("napcat_path_escape");
    }
    Ok(path)
}

fn validate_root(path: &Path) -> Result<PathBuf, &'static str> {
    let root = path.canonicalize().map_err(|_| "napcat_not_found")?;
    for file in [
        "launcher-user.bat",
        "NapCatWinBootMain.exe",
        "NapCatWinBootHook.dll",
        "napcat.mjs",
        "config/webui.json",
    ] {
        if !child_path(&root, file)?.is_file() {
            return Err("napcat_installation_incomplete");
        }
    }
    Ok(root)
}

fn selection_path(binding: &Binding) -> PathBuf {
    binding.root.join("state").join("desktop-qq-setup.json")
}

fn discover(binding: &Binding) -> Result<PathBuf, &'static str> {
    let saved = selection_path(binding);
    if saved.exists() {
        let data = read_json(&saved)?;
        let path = data["directory"]
            .as_str()
            .ok_or("napcat_selection_invalid")?;
        return validate_root(Path::new(path));
    }
    if let Some(path) = std::env::var_os("AKANE_NAPCAT_ROOT").filter(|v| !v.is_empty()) {
        return validate_root(Path::new(&path));
    }
    let parent = binding.root.join("napcat");
    if let Ok(root) = validate_root(&parent) {
        return Ok(root);
    }
    let mut found = Vec::new();
    if let Ok(entries) = fs::read_dir(parent) {
        for entry in entries.take(100).flatten() {
            if let Ok(root) = validate_root(&entry.path()) {
                found.push(root);
            }
        }
    }
    match found.len() {
        1 => Ok(found.remove(0)),
        0 => Err("napcat_not_found"),
        _ => Err("napcat_multiple_installations"),
    }
}

fn atomic_json(path: &Path, value: &Value) -> Result<(), &'static str> {
    let parent = path.parent().ok_or("napcat_config_write_failed")?;
    fs::create_dir_all(parent).map_err(|_| "napcat_config_write_failed")?;
    let temporary = path.with_extension(format!("{}.tmp", std::process::id()));
    let bytes = serde_json::to_vec_pretty(value).map_err(|_| "napcat_config_write_failed")?;
    use std::io::Write;
    // Do not remove somebody else's pre-existing temporary file on create_new failure.
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(|_| "napcat_config_write_failed")?;
    let outcome = (|| {
        file.write_all(&bytes)?;
        file.sync_all()?;
        drop(file);
        fs::rename(&temporary, path)
    })();
    if outcome.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    outcome.map_err(|_| "napcat_config_write_failed")
}

fn expected_network(binding: &Binding) -> (Value, Value) {
    let mut webhook = binding.backend.clone();
    // Existing local launcher uses the default Bot alias; binding is pinned to that launch Bot.
    webhook.set_path("/api/qq/napcat/event");
    (
        json!({"name":"akane-local-qq-api", "enable":true, "port":binding.api.port_or_known_default(),
        "host":"127.0.0.1", "enableCors":false, "enableWebsocket":false, "messagePostFormat":"array", "token":binding.api_token, "debug":false}),
        json!({"name":"akane-local-qq-webhook", "enable":true, "url":webhook.as_str(), "messagePostFormat":"array",
        "reportSelfMessage":false, "token":binding.webhook_token, "debug":false}),
    )
}

fn configured(root: &Path, binding: &Binding) -> bool {
    let Ok(path) = child_path(root, &format!("config/onebot11_{}.json", binding.qq)) else {
        return false;
    };
    let Ok(data) = read_json(&path) else {
        return false;
    };
    let (server, client) = expected_network(binding);
    [("httpServers", server), ("httpClients", client)]
        .iter()
        .all(|(field, expected)| {
            data["network"][field].as_array().is_some_and(|items| {
                items.iter().any(|item| {
                    expected
                        .as_object()
                        .unwrap()
                        .iter()
                        .all(|(key, value)| item.get(key) == Some(value))
                })
            })
        })
}

fn prepare(root: &Path, binding: &Binding) -> Result<(), &'static str> {
    if configured(root, binding) {
        return Ok(());
    }
    let path = child_path(root, &format!("config/onebot11_{}.json", binding.qq))
        .map_err(|_| "napcat_account_config_missing")?;
    let mut data = read_json(&path)?;
    let network = data
        .get_mut("network")
        .and_then(Value::as_object_mut)
        .ok_or("napcat_config_invalid")?;
    let (server, client) = expected_network(binding);
    for (field, replacement) in [("httpServers", server), ("httpClients", client)] {
        let items = network
            .get_mut(field)
            .and_then(Value::as_array_mut)
            .ok_or("napcat_config_invalid")?;
        items.retain(|item| item["name"] != replacement["name"]);
        items.push(replacement);
    }
    // One retained pre-wizard backup, never overwrite an existing recovery copy.
    let backup = path.with_extension("json.before-akane-setup");
    if !backup.exists() {
        use std::io::Write;
        let bytes = fs::read(&path).map_err(|_| "napcat_config_unreadable")?;
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&backup)
            .map_err(|_| "napcat_backup_failed")?;
        if file
            .write_all(&bytes)
            .and_then(|_| file.sync_all())
            .is_err()
        {
            drop(file);
            // This file was created above by this operation, not a pre-existing user backup.
            let _ = fs::remove_file(&backup);
            return Err("napcat_backup_failed");
        }
    } else if read_json(&backup)
        .ok()
        .and_then(|data| data.get("network").cloned())
        .is_none()
    {
        return Err("napcat_backup_failed");
    }
    atomic_json(&path, &data)
}

async fn webui(root: &Path) -> Result<Option<reqwest::Url>, &'static str> {
    let config = read_json(&child_path(root, "config/webui.json")?)?;
    let port = config["port"]
        .as_u64()
        .filter(|p| *p > 0 && *p <= 65535)
        .ok_or("napcat_webui_disabled")?;
    let client = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_millis(700))
        .build()
        .map_err(|_| "napcat_webui_unavailable")?;
    // Only the configured port is trusted; an occupied port is surfaced, not guessed as another instance.
    let url = reqwest::Url::parse(&format!("http://127.0.0.1:{port}/webui/")).unwrap();
    match client.get(url.clone()).send().await {
        Err(_) => Ok(None),
        Ok(mut response) => {
            if !response.status().is_success()
                || response
                    .content_length()
                    .is_some_and(|size| size > MAX_CONFIG)
            {
                return Err("napcat_webui_port_conflict");
            }
            let mut bytes = Vec::new();
            while let Some(chunk) = response
                .chunk()
                .await
                .map_err(|_| "napcat_webui_unavailable")?
            {
                if bytes.len() + chunk.len() > MAX_CONFIG as usize {
                    return Err("napcat_webui_port_conflict");
                }
                bytes.extend_from_slice(&chunk);
            }
            if !String::from_utf8_lossy(&bytes).contains("<title>NapCat WebUI</title>") {
                return Err("napcat_webui_port_conflict");
            }
            Ok(Some(url))
        }
    }
}

fn child_alive() -> bool {
    let Ok(mut slot) = CHILD.get_or_init(|| Mutex::new(None)).lock() else {
        return true;
    };
    if let Some(child) = slot.as_mut() {
        match child.try_wait() {
            Ok(Some(_)) => *slot = None,
            _ => return true,
        }
    }
    false
}

fn spawn(root: &Path, qq: &str) -> Result<(), &'static str> {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // Fixed command and numeric account only. Selected paths are cwd, never interpolated into a shell command.
        let cmd = std::env::var_os("SystemRoot")
            .map(PathBuf::from)
            .ok_or("napcat_start_failed")?
            .join("System32/cmd.exe");
        let child = Command::new(cmd)
            .args(["/d", "/c", "launcher-user.bat", "-q", qq])
            .current_dir(root)
            .creation_flags(0x08000000)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|_| "napcat_start_failed")?;
        *CHILD
            .get_or_init(|| Mutex::new(None))
            .lock()
            .map_err(|_| "napcat_start_failed")? = Some(child);
        Ok(())
    }
    #[cfg(not(windows))]
    {
        let _ = (root, qq);
        Err("local_qq_windows_only")
    }
}

fn snapshot(root: &Path, binding: &Binding, ready: bool) -> Value {
    json!({"ok":true, "status":"available", "installed":true, "webuiReady":ready,
        "starting": !ready && child_alive(), "configured":configured(root, binding), "botQq":binding.qq,
        "backendPort":binding.backend.port_or_known_default(), "onebotPort":binding.api.port_or_known_default()})
}

#[tauri::command]
pub async fn local_qq_setup(app: AppHandle, window: tauri::Window, request: SetupRequest) -> Value {
    if window.label() != "settings" {
        return failure("local_qq_settings_only");
    }
    let Ok(_guard) = OPERATION.try_lock() else {
        return failure("local_qq_busy");
    };
    if !cfg!(windows) {
        return failure("local_qq_windows_only");
    }
    let binding = match binding(&app, &request) {
        Ok(v) => v,
        Err(e) => return failure(e),
    };
    if !matches!(
        request.action.as_str(),
        "detect" | "select" | "start" | "openLogin" | "openFolder"
    ) {
        return failure("local_qq_unknown_action");
    }
    if request.action == "select" {
        if child_alive() {
            return failure("napcat_selection_running");
        }
        let picker_app = app.clone();
        let selected = tauri::async_runtime::spawn_blocking(move || {
            picker_app.dialog().file().blocking_pick_folder()
        })
        .await;
        let selected = match selected {
            Ok(Some(value)) => value,
            Ok(None) => return json!({"ok":false,"status":"cancelled","reason":"picker_cancelled"}),
            Err(_) => return failure("napcat_selection_failed"),
        };
        // Re-check binding after the user has spent time in the native picker.
        if let Err(e) = self::binding(&app, &request) {
            return failure(e);
        }
        let root = match selected
            .into_path()
            .ok()
            .and_then(|p| validate_root(&p).ok())
        {
            Some(root) => root,
            None => return failure("napcat_installation_incomplete"),
        };
        if let Err(e) = atomic_json(&selection_path(&binding), &json!({"directory":root})) {
            return failure(e);
        }
    }
    let root = match discover(&binding) {
        Ok(v) => v,
        Err(e) => return failure(e),
    };
    // Recovery must still work when the WebUI config is broken or its port is occupied.
    if request.action == "openFolder" {
        if super::open_path_in_file_manager(&root).is_err() {
            return failure("napcat_open_failed");
        }
        let mut result = snapshot(&root, &binding, false);
        result["operation"] = json!("openFolder");
        return result;
    }
    let mut login_url = match webui(&root).await {
        Ok(v) => v,
        Err(e) => {
            let mut result = snapshot(&root, &binding, false);
            result["ok"] = json!(false);
            result["reason"] = json!(e);
            return result;
        }
    };
    if let Err(e) = self::binding(&app, &request) {
        return failure(e);
    }
    match request.action.as_str() {
        "start" => {
            if login_url.is_some() {
                if !configured(&root, &binding) {
                    return failure("napcat_restart_required");
                }
            } else if !child_alive() {
                let port = binding.api.port_or_known_default().unwrap_or(80);
                if std::net::TcpStream::connect_timeout(
                    &std::net::SocketAddr::from(([127, 0, 0, 1], port)),
                    Duration::from_millis(200),
                )
                .is_ok()
                {
                    return failure("napcat_onebot_port_in_use");
                }
                match prepare(&root, &binding) {
                    // A first login generates its account-specific OneBot file. Do not invent that schema.
                    Ok(()) | Err("napcat_account_config_missing") => {}
                    Err(e) => return failure(e),
                }
                if let Err(e) = spawn(&root, &binding.qq) {
                    return failure(e);
                }
                // Starting a process is not login success. Readiness may arrive after this bounded observation.
                for _ in 0..8 {
                    tokio::time::sleep(Duration::from_millis(500)).await;
                    match webui(&root).await {
                        Ok(Some(url)) => {
                            login_url = Some(url);
                            break;
                        }
                        Err(e) => return failure(e),
                        _ => {}
                    }
                }
            }
        }
        "openLogin" => {
            let Some(mut url) = login_url.clone() else {
                return failure("napcat_not_started");
            };
            let data = match read_json(&child_path(&root, "config/webui.json").unwrap_or_default())
            {
                Ok(v) => v,
                Err(e) => return failure(e),
            };
            let token = data["token"].as_str().unwrap_or_default();
            if token.is_empty() {
                return failure("napcat_webui_token_missing");
            }
            // The private login URL goes directly to the OS, never through UI state, logs or model history.
            url.query_pairs_mut().append_pair("token", token);
            if super::open_url_with_system(url.as_str()).is_err() {
                return failure("napcat_open_failed");
            }
        }
        _ => {}
    }
    let mut result = snapshot(&root, &binding, login_url.is_some());
    result["operation"] = json!(request.action);
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    static SEQUENCE: AtomicUsize = AtomicUsize::new(0);

    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "akane-qq-setup-test-{}-{}",
                std::process::id(),
                SEQUENCE.fetch_add(1, Ordering::SeqCst)
            ));
            fs::create_dir(&path).unwrap();
            Self(path.canonicalize().unwrap())
        }
        fn install(&self) -> PathBuf {
            let root = self.0.join("napcat").join("fixture-shell");
            fs::create_dir_all(root.join("config")).unwrap();
            for name in [
                "launcher-user.bat",
                "NapCatWinBootMain.exe",
                "NapCatWinBootHook.dll",
                "napcat.mjs",
            ] {
                fs::write(root.join(name), b"test fixture, never executed").unwrap();
            }
            fs::write(
                root.join("config/webui.json"),
                br#"{"port":6099,"token":"fixture-webui-secret"}"#,
            )
            .unwrap();
            fs::write(root.join("config/onebot11_123456789.json"), br#"{"other":"preserve","network":{"httpServers":[{"name":"another-app","token":"another-app-secret"}],"httpClients":[],"websocketServers":[{"name":"untouched"}]}}"#).unwrap();
            root.canonicalize().unwrap()
        }
        fn binding(&self) -> Binding {
            Binding {
                root: self.0.clone(),
                backend: loopback_url("http://127.0.0.1:12001").unwrap(),
                qq: "123456789".into(),
                api: loopback_url("http://127.0.0.1:3003").unwrap(),
                api_token: "fixture-api-secret".into(),
                webhook_token: "fixture-webhook-secret".into(),
            }
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            // Exact per-test directory, created above; never a user-selected path.
            if self
                .0
                .starts_with(std::env::temp_dir().canonicalize().unwrap())
            {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
    }

    #[test]
    fn rejects_remote_credential_query_and_nonroot_endpoints() {
        for raw in [
            "https://remote.invalid",
            "http://192.168.1.2:3003",
            "file:///tmp/a",
            "http://secret@127.0.0.1",
            "http://127.0.0.1?token=x",
            "http://127.0.0.1/a",
        ] {
            assert!(loopback_url(raw).is_err(), "{raw}");
        }
        for raw in [
            "http://localhost:12001",
            "http://127.0.0.1:12001",
            "http://[::1]:12001",
        ] {
            assert!(loopback_url(raw).is_ok());
        }
    }

    #[test]
    fn validates_complete_shell_and_rejects_child_escape() {
        let fixture = Fixture::new();
        let root = fixture.install();
        assert_eq!(validate_root(&root).unwrap(), root);
        fs::write(root.parent().unwrap().join("outside"), b"fixture").unwrap();
        assert_eq!(
            child_path(&root, "../outside").unwrap_err(),
            "napcat_path_escape"
        );
        fs::remove_file(root.join("napcat.mjs")).unwrap();
        assert_eq!(
            validate_root(&root).unwrap_err(),
            "napcat_installation_incomplete"
        );
    }

    #[test]
    fn prepare_preserves_other_apps_and_is_idempotent_with_backup() {
        let fixture = Fixture::new();
        let root = fixture.install();
        let binding = fixture.binding();
        let path = root.join("config/onebot11_123456789.json");
        let original = fs::read(&path).unwrap();
        assert!(!configured(&root, &binding));
        prepare(&root, &binding).unwrap();
        assert!(configured(&root, &binding));
        let result = read_json(&path).unwrap();
        assert_eq!(result["other"], "preserve");
        assert_eq!(
            result["network"]["httpServers"][0]["token"],
            "another-app-secret"
        );
        assert_eq!(
            result["network"]["websocketServers"][0]["name"],
            "untouched"
        );
        let backup = path.with_extension("json.before-akane-setup");
        assert_eq!(fs::read(&backup).unwrap(), original);
        let once = fs::read(&path).unwrap();
        prepare(&root, &binding).unwrap();
        assert_eq!(fs::read(path).unwrap(), once);
        assert_eq!(fs::read(backup).unwrap(), original);
    }

    #[test]
    fn malformed_config_is_not_overwritten() {
        let fixture = Fixture::new();
        let root = fixture.install();
        let path = root.join("config/onebot11_123456789.json");
        fs::write(&path, b"invalid-json").unwrap();
        assert_eq!(
            prepare(&root, &fixture.binding()).unwrap_err(),
            "napcat_config_invalid"
        );
        assert_eq!(fs::read(path).unwrap(), b"invalid-json");
    }

    #[test]
    fn cannot_remove_an_existing_temporary_file() {
        let fixture = Fixture::new();
        let path = fixture.0.join("selection.json");
        let temporary = path.with_extension(format!("{}.tmp", std::process::id()));
        fs::write(&temporary, b"not-ours").unwrap();
        assert!(atomic_json(&path, &json!({})).is_err());
        assert_eq!(fs::read(temporary).unwrap(), b"not-ours");
    }

    #[test]
    fn public_snapshot_has_no_credentials_paths_or_login_url() {
        let fixture = Fixture::new();
        let root = fixture.install();
        let payload = snapshot(&root, &fixture.binding(), false).to_string();
        for secret in [
            "fixture-api-secret",
            "fixture-webhook-secret",
            "fixture-webui-secret",
            "directory",
            "token",
            "http://",
        ] {
            assert!(!payload.contains(secret), "unexpected public field");
        }
        assert!(!payload.contains(&fixture.0.to_string_lossy().to_string()));
        assert!(payload.contains("123456789"));
        assert!(payload.contains("\"webuiReady\":false"));
    }

    #[test]
    fn webui_identity_and_redirect_are_checked_without_authentication() {
        use std::io::{Read, Write};
        for (status, body, expected) in [
            ("200 OK", "<title>NapCat WebUI</title>", true),
            ("200 OK", "another application", false),
            ("302 Found", "redirect", false),
        ] {
            let fixture = Fixture::new();
            let root = fixture.install();
            let server = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
            let port = server.local_addr().unwrap().port();
            atomic_json(
                &root.join("config/webui.json"),
                &json!({"port":port,"token":"never-send-to-probe"}),
            )
            .unwrap();
            let worker = std::thread::spawn(move || {
                let (mut stream, _) = server.accept().unwrap();
                stream
                    .set_read_timeout(Some(Duration::from_secs(3)))
                    .unwrap();
                let mut buffer = [0; 4096];
                let size = stream.read(&mut buffer).unwrap();
                let request = String::from_utf8_lossy(&buffer[..size]);
                assert!(!request.contains("never-send-to-probe"));
                assert!(request.starts_with("GET /webui/"));
                write!(
                    stream,
                    "HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                )
                .unwrap();
            });
            let result = tauri::async_runtime::block_on(webui(&root));
            assert_eq!(result.is_ok_and(|v| v.is_some()), expected);
            worker.join().unwrap();
        }
    }

    #[test]
    fn wizard_request_cannot_supply_arbitrary_path_or_secret() {
        assert!(serde_json::from_value::<SetupRequest>(json!({"action":"start","backendUrl":"http://127.0.0.1:12001","botId":"test","directory":"C:/untrusted"})).is_err());
    }
}
