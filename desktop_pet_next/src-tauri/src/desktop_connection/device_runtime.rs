use crate::backend::binding::verify_backend_instance_url;
use crate::desktop_connection::session::run_desktop_satellite_session_with_app;
use crate::device_control;
use crate::runtime::config::{
    runtime_backend_url, runtime_bound_bot_id, runtime_instance_id, SATELLITE_TOKEN_ENV,
};
use crate::runtime::launch::write_device_runtime_state;
use std::time::Duration;

pub(crate) fn run() {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("device_runtime");
    runtime.block_on(async {
        let _ = write_device_runtime_state("starting", "device_initializing", "");
        let Ok(instance_id) = runtime_instance_id() else {
            return;
        };
        let Ok(bot_id) = runtime_bound_bot_id() else {
            return;
        };
        let Ok(token) = std::env::var(SATELLITE_TOKEN_ENV) else {
            return;
        };
        if token.trim().is_empty() {
            return;
        }
        if let Err(reason) = device_control::start_server() {
            let _ = write_device_runtime_state("offline", "local_control_unavailable", reason);
            return;
        }
        let backend_url = runtime_backend_url();
        loop {
            if verify_backend_instance_url(&backend_url).await.ok {
                let _ = write_device_runtime_state("connecting", "satellite_connecting", "");
                if let Err(reason) = run_desktop_satellite_session_with_app(
                    &backend_url,
                    &instance_id,
                    &bot_id,
                    &token,
                    None,
                )
                .await
                {
                    let _ = write_device_runtime_state("offline", "satellite_disconnected", reason);
                }
            } else {
                let _ = write_device_runtime_state(
                    "offline",
                    "backend_unavailable",
                    "backend_health_failed",
                );
            }
            tokio::time::sleep(Duration::from_secs(3)).await;
        }
    });
}
