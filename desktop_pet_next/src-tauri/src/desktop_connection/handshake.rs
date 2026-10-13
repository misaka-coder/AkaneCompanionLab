use futures_util::{SinkExt, StreamExt};
use std::{collections::HashMap, time::Duration};
use tokio_tungstenite::tungstenite::{client::IntoClientRequest, http::HeaderValue, Message};
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream};

use crate::desktop_connection::contracts::{
    registration_frame, SATELLITE_OFFER_TOOL_IDS, SATELLITE_PROTOCOL_VERSION,
};
use crate::desktop_connection::messages::satellite_message_json;
use crate::runtime::config::{is_loopback_backend_host, normalize_backend_url};

pub(super) struct SatelliteConnection {
    pub(super) socket: WebSocketStream<MaybeTlsStream<tokio::net::TcpStream>>,
    pub(super) lease_epoch: String,
    pub(super) offer_ids: HashMap<String, String>,
}

pub(crate) fn satellite_websocket_url(backend_url: &str) -> Result<reqwest::Url, &'static str> {
    let mut backend = reqwest::Url::parse(&normalize_backend_url(backend_url))
        .map_err(|_| "invalid_backend_url")?;
    if !backend.username().is_empty() || backend.password().is_some() {
        return Err("invalid_backend_url");
    }
    let host = backend.host_str().unwrap_or("");
    match backend.scheme() {
        "https" => backend
            .set_scheme("wss")
            .map_err(|_| "invalid_backend_url")?,
        "http" if is_loopback_backend_host(host) => backend
            .set_scheme("ws")
            .map_err(|_| "invalid_backend_url")?,
        _ => return Err("secure_satellite_transport_required"),
    }
    backend
        .join("/capabilities/satellite/ws")
        .map_err(|_| "invalid_backend_url")
}

pub(super) async fn connect_satellite(
    backend_url: &str,
    instance_id: &str,
    bot_id: &str,
    token: &str,
    desktop_ui: bool,
    presentation_only: bool,
) -> Result<SatelliteConnection, &'static str> {
    let mut websocket_url = satellite_websocket_url(backend_url)?;
    if presentation_only {
        websocket_url.set_path("/capabilities/satellite/presentation/ws");
    }
    let mut request = websocket_url
        .as_str()
        .into_client_request()
        .map_err(|_| "satellite_request_invalid")?;
    let authorization =
        HeaderValue::from_str(&format!("Bearer {token}")).map_err(|_| "satellite_token_invalid")?;
    request.headers_mut().insert("authorization", authorization);
    let (mut socket, _) = tokio_tungstenite::connect_async(request)
        .await
        .map_err(|_| "satellite_connect_failed")?;

    let hello_message = tokio::time::timeout(Duration::from_secs(8), socket.next())
        .await
        .map_err(|_| "satellite_hello_timeout")?
        .ok_or("satellite_closed")?
        .map_err(|_| "satellite_receive_failed")?;
    let hello = satellite_message_json(hello_message).ok_or("satellite_hello_invalid")?;
    if hello.get("type").and_then(serde_json::Value::as_str) != Some("hello")
        || hello
            .get("protocol_version")
            .and_then(serde_json::Value::as_u64)
            != Some(SATELLITE_PROTOCOL_VERSION)
        || hello.get("instance_id").and_then(serde_json::Value::as_str) != Some(instance_id)
    {
        return Err("satellite_hello_mismatch");
    }

    let registration = registration_frame(instance_id, bot_id, desktop_ui, presentation_only)?;
    socket
        .send(Message::Text(registration.to_string().into()))
        .await
        .map_err(|_| "satellite_register_send_failed")?;

    let registered_message = tokio::time::timeout(Duration::from_secs(8), socket.next())
        .await
        .map_err(|_| "satellite_register_timeout")?
        .ok_or("satellite_closed")?
        .map_err(|_| "satellite_receive_failed")?;
    let registered =
        satellite_message_json(registered_message).ok_or("satellite_register_invalid")?;
    let lease_epoch = registered
        .get("lease_epoch")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    let raw_offer_ids = registered
        .get("offer_ids")
        .and_then(serde_json::Value::as_object)
        .ok_or("satellite_register_mismatch")?;
    let mut offer_ids = HashMap::new();
    for tool_id in SATELLITE_OFFER_TOOL_IDS {
        let offer_id = raw_offer_ids
            .get(tool_id)
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .trim();
        if !offer_id.is_empty() {
            offer_ids.insert(tool_id.to_string(), offer_id.to_string());
        }
    }
    if registered.get("type").and_then(serde_json::Value::as_str) != Some("registered")
        || registered
            .get("protocol_version")
            .and_then(serde_json::Value::as_u64)
            != Some(SATELLITE_PROTOCOL_VERSION)
        || registered
            .get("instance_id")
            .and_then(serde_json::Value::as_str)
            != Some(instance_id)
        || registered.get("bot_id").and_then(serde_json::Value::as_str) != Some(bot_id)
        || lease_epoch.is_empty()
        || (!presentation_only && offer_ids.is_empty())
        || (presentation_only && !offer_ids.is_empty())
    {
        return Err("satellite_register_mismatch");
    }
    Ok(SatelliteConnection {
        socket,
        lease_epoch,
        offer_ids,
    })
}
