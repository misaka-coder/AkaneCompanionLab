use crate::{computer_use, personal_browser};
use futures_util::{SinkExt, StreamExt};
use std::time::Duration;
use tauri::{AppHandle, Emitter};

use crate::backend::binding::verify_backend_instance_url;
use crate::desktop_connection::execution::execute_satellite_invocation_once;
use crate::desktop_connection::handshake::{connect_satellite, SatelliteConnection};
use crate::desktop_connection::messages::{satellite_execution_message, satellite_message_json};
use crate::desktop_connection::plugin_events::{
    queue_plugin_agent_event_frame, validate_satellite_agent_event_frame,
    PLUGIN_AGENT_EVENT_FRAME_EVENT,
};
use crate::desktop_connection::validation::validate_satellite_invocation;
use crate::runtime::config::{
    env_flag_enabled, is_device_only_mode, runtime_backend_url, runtime_bound_bot_id,
    runtime_instance_id, EXTERNAL_DEVICE_EXECUTOR_ENV, SATELLITE_TOKEN_ENV,
};
use crate::runtime::launch::write_device_runtime_state;

pub(crate) fn start_desktop_satellite(app_handle: AppHandle) {
    let token = std::env::var(SATELLITE_TOKEN_ENV)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());
    let Some(token) = token else {
        return;
    };
    let Ok(instance_id) = runtime_instance_id() else {
        return;
    };
    let Ok(bot_id) = runtime_bound_bot_id() else {
        return;
    };
    let backend_url = runtime_backend_url();
    tauri::async_runtime::spawn(async move {
        let mut retry_seconds = 2_u64;
        loop {
            let binding = verify_backend_instance_url(&backend_url).await;
            if binding.ok {
                let _ = run_desktop_satellite_session_with_app(
                    &backend_url,
                    &instance_id,
                    &bot_id,
                    &token,
                    Some(&app_handle),
                )
                .await;
                retry_seconds = 2;
            } else {
                retry_seconds = (retry_seconds.saturating_mul(2)).min(30);
            }
            tokio::time::sleep(Duration::from_secs(retry_seconds)).await;
        }
    });
}

#[cfg(test)]
pub(crate) async fn run_desktop_satellite_session(
    backend_url: &str,
    instance_id: &str,
    token: &str,
) -> Result<(), &'static str> {
    run_desktop_satellite_session_with_app(backend_url, instance_id, instance_id, token, None).await
}

pub(crate) async fn run_desktop_satellite_session_with_app(
    backend_url: &str,
    instance_id: &str,
    bot_id: &str,
    token: &str,
    app_handle: Option<&AppHandle>,
) -> Result<(), &'static str> {
    use tokio_tungstenite::tungstenite::Message;

    let presentation_only = app_handle.is_some() && env_flag_enabled(EXTERNAL_DEVICE_EXECUTOR_ENV);
    let SatelliteConnection {
        mut socket,
        lease_epoch,
        offer_ids,
    } = connect_satellite(
        backend_url,
        instance_id,
        bot_id,
        token,
        app_handle.is_some(),
        presentation_only,
    )
    .await?;

    if is_device_only_mode() {
        let _ = write_device_runtime_state("online", "registered", "");
    }

    let _computer_connection = if presentation_only {
        None
    } else {
        Some(computer_use::ConnectionGuard::new(lease_epoch.clone()))
    };
    let (computer_results_tx, mut computer_results_rx) =
        tokio::sync::mpsc::channel::<serde_json::Value>(16);

    let mut heartbeat = tokio::time::interval(Duration::from_secs(8));
    heartbeat.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    heartbeat.tick().await;
    loop {
        tokio::select! {
            Some(completed) = computer_results_rx.recv() => {
                socket.send(Message::Text(completed.to_string().into())).await
                    .map_err(|_| "satellite_result_send_failed")?;
            }
            _ = heartbeat.tick() => {
                let payload = serde_json::json!({
                    "type": "heartbeat",
                    "instance_id": instance_id,
                    "lease_epoch": lease_epoch,
                });
                socket.send(Message::Text(payload.to_string().into()))
                    .await
                    .map_err(|_| "satellite_heartbeat_failed")?;
            }
            next = socket.next() => {
                let message = next
                    .ok_or("satellite_closed")?
                    .map_err(|_| "satellite_receive_failed")?;
                match message {
                    Message::Ping(payload) => {
                        socket.send(Message::Pong(payload)).await
                            .map_err(|_| "satellite_pong_failed")?;
                    }
                    Message::Close(_) => return Err("satellite_closed"),
                    other => {
                        let Some(payload) = satellite_message_json(other) else {
                            continue;
                        };
                        if payload.get("type").and_then(serde_json::Value::as_str)
                            == Some("agent_event_frame")
                        {
                            let validated = validate_satellite_agent_event_frame(
                                &payload,
                                instance_id,
                                &lease_epoch,
                            );
                            let delivery_id = validated
                                .as_ref()
                                .map(|(value, _)| value.as_str())
                                .unwrap_or("");
                            let queued = validated
                                .as_ref()
                                .filter(|_| app_handle.is_some())
                                .map(|(delivery_id, frame)| {
                                    queue_plugin_agent_event_frame(delivery_id, frame.clone())
                                })
                                .unwrap_or(false);
                            if queued {
                                if let (Some(handle), Some((delivery_id, frame))) =
                                    (app_handle, validated.as_ref())
                                {
                                    let _ = handle.emit_to(
                                        "main",
                                        PLUGIN_AGENT_EVENT_FRAME_EVENT,
                                        serde_json::json!({
                                            "deliveryId": delivery_id,
                                            "payload": frame,
                                        }),
                                    );
                                }
                            }
                            let result = serde_json::json!({
                                "type": "agent_event_result",
                                "instance_id": instance_id,
                                "lease_epoch": lease_epoch,
                                "delivery_id": delivery_id,
                                "status": if queued { "queued" } else { "failed" },
                                "reason": if queued { "" } else if validated.is_some() { "desktop_view_unavailable" } else { "agent_event_frame_invalid" },
                            });
                            socket.send(Message::Text(result.to_string().into())).await
                                .map_err(|_| "satellite_result_send_failed")?;
                            continue;
                        }
                        if presentation_only || payload.get("type").and_then(serde_json::Value::as_str) != Some("invoke") {
                            continue;
                        }
                        let Some((invocation_id, tool_id, arguments)) = validate_satellite_invocation(
                            &payload,
                            instance_id,
                            &lease_epoch,
                            &offer_ids,
                        ) else {
                            continue;
                        };
                        let Some(offer_id) = offer_ids.get(&tool_id) else {
                            continue;
                        };
                        let accepted = satellite_execution_message(
                            "accepted",
                            instance_id,
                            &lease_epoch,
                            offer_id,
                            &invocation_id,
                            &tool_id,
                            "",
                            "",
                            None,
                        );
                        socket.send(Message::Text(accepted.to_string().into())).await
                            .map_err(|_| "satellite_accept_failed")?;
                        let running = satellite_execution_message(
                            "running",
                            instance_id,
                            &lease_epoch,
                            offer_id,
                            &invocation_id,
                            &tool_id,
                            "",
                            "",
                            None,
                        );
                        socket.send(Message::Text(running.to_string().into())).await
                            .map_err(|_| "satellite_running_failed")?;
                        if matches!(tool_id.as_ref(), computer_use::protocol::TOOL_ID | personal_browser::TOOL_ID) {
                            // Keep receiving stop/status and heartbeats while UIA or WGC is busy.
                            let tx = computer_results_tx.clone();
                            let instance = instance_id.to_string(); let epoch = lease_epoch.clone(); let offer = offer_id.clone();
                            tauri::async_runtime::spawn(async move {
                                let result = execute_satellite_invocation_once(&invocation_id, &tool_id, &arguments).await;
                                let completed = satellite_execution_message("result", &instance, &epoch, &offer,
                                    &invocation_id, &tool_id, &result.status, &result.reason, Some(&result.data));
                                let _ = tx.send(completed).await;
                            });
                            continue;
                        }
                        let result = execute_satellite_invocation_once(
                            &invocation_id,
                            &tool_id,
                            &arguments,
                        ).await;
                        let completed = satellite_execution_message(
                            "result",
                            instance_id,
                            &lease_epoch,
                            offer_id,
                            &invocation_id,
                            &tool_id,
                            &result.status,
                            &result.reason,
                            Some(&result.data),
                        );
                        socket.send(Message::Text(completed.to_string().into())).await
                            .map_err(|_| "satellite_result_send_failed")?;
                    }
                }
            }
        }
    }
}
