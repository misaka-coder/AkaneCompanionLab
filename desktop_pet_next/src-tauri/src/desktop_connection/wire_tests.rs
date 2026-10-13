use crate::desktop_connection::contracts::{
    DESKTOP_CONTEXT_SNAPSHOT_SCHEMA_HASH, DESKTOP_CONTEXT_SNAPSHOT_SCHEMA_VERSION,
    DESKTOP_CONTEXT_SNAPSHOT_SPEC_VERSION, DESKTOP_CONTEXT_SNAPSHOT_TOOL_ID, OPEN_BROWSER_TOOL_ID,
    SATELLITE_OFFER_TOOL_IDS, SATELLITE_PROTOCOL_VERSION, SYSTEM_MEDIA_CONTROL_TOOL_ID,
    SYSTEM_MEDIA_SNAPSHOT_TOOL_ID, SYSTEM_PROCESS_SNAPSHOT_TOOL_ID,
    SYSTEM_PROCESS_TERMINATE_TOOL_ID, SYSTEM_VOLUME_TOOL_ID,
};
use crate::desktop_connection::messages::satellite_message_json;
use crate::desktop_connection::session::run_desktop_satellite_session;
use futures_util::{SinkExt, StreamExt};

#[tokio::test]
async fn satellite_wire_session_executes_real_context_snapshot() {
    use tokio_tungstenite::tungstenite::Message;

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("test satellite listener should bind");
    let address = listener
        .local_addr()
        .expect("listener should have an address");
    let server = tokio::spawn(async move {
        let (stream, _) = listener
            .accept()
            .await
            .expect("satellite client should connect");
        let mut socket = tokio_tungstenite::accept_async(stream)
            .await
            .expect("satellite websocket should accept");
        socket
            .send(Message::Text(
                serde_json::json!({
                    "type": "hello",
                    "protocol_version": SATELLITE_PROTOCOL_VERSION,
                    "instance_id": "instance-wire-test",
                })
                .to_string()
                .into(),
            ))
            .await
            .expect("hello should send");

        let registration = satellite_message_json(
            socket
                .next()
                .await
                .expect("registration should arrive")
                .expect("registration frame should be valid"),
        )
        .expect("registration should be JSON");
        let offers = registration
            .get("offers")
            .and_then(serde_json::Value::as_array)
            .expect("registration should contain offers");
        assert_eq!(
            registration
                .get("bot_id")
                .and_then(serde_json::Value::as_str),
            Some("instance-wire-test")
        );
        assert_eq!(offers.len(), SATELLITE_OFFER_TOOL_IDS.len());
        for tool_id in SATELLITE_OFFER_TOOL_IDS {
            assert!(offers.iter().any(|offer| {
                offer.get("tool_id").and_then(serde_json::Value::as_str) == Some(tool_id)
            }));
        }

        socket
            .send(Message::Text(
                serde_json::json!({
                    "type": "registered",
                    "protocol_version": SATELLITE_PROTOCOL_VERSION,
                    "instance_id": "instance-wire-test",
                    "bot_id": "instance-wire-test",
                    "lease_epoch": "lease-wire-test",
                    "offer_ids": {
                        OPEN_BROWSER_TOOL_ID: "offer-wire-test",
                        DESKTOP_CONTEXT_SNAPSHOT_TOOL_ID: "offer-wire-test",
                        SYSTEM_MEDIA_SNAPSHOT_TOOL_ID: "offer-wire-test",
                        SYSTEM_MEDIA_CONTROL_TOOL_ID: "offer-wire-test",
                        SYSTEM_PROCESS_SNAPSHOT_TOOL_ID: "offer-wire-test",
                        SYSTEM_PROCESS_TERMINATE_TOOL_ID: "offer-wire-test",
                        SYSTEM_VOLUME_TOOL_ID: "offer-wire-test",
                    },
                })
                .to_string()
                .into(),
            ))
            .await
            .expect("registration result should send");
        socket
            .send(Message::Text(
                serde_json::json!({
                    "type": "invoke",
                    "protocol_version": SATELLITE_PROTOCOL_VERSION,
                    "instance_id": "instance-wire-test",
                    "lease_epoch": "lease-wire-test",
                    "offer_id": "offer-wire-test",
                    "invocation_id": "call_wire_context",
                    "tool_id": DESKTOP_CONTEXT_SNAPSHOT_TOOL_ID,
                    "spec_version": DESKTOP_CONTEXT_SNAPSHOT_SPEC_VERSION,
                    "schema_version": DESKTOP_CONTEXT_SNAPSHOT_SCHEMA_VERSION,
                    "schema_hash": DESKTOP_CONTEXT_SNAPSHOT_SCHEMA_HASH,
                    "arguments": {},
                })
                .to_string()
                .into(),
            ))
            .await
            .expect("invoke should send");

        for expected_type in ["accepted", "running"] {
            let message = satellite_message_json(
                socket
                    .next()
                    .await
                    .expect("execution acknowledgement should arrive")
                    .expect("execution acknowledgement should be valid"),
            )
            .expect("execution acknowledgement should be JSON");
            assert_eq!(
                message.get("type").and_then(serde_json::Value::as_str),
                Some(expected_type)
            );
            assert_eq!(
                message.get("tool_id").and_then(serde_json::Value::as_str),
                Some(DESKTOP_CONTEXT_SNAPSHOT_TOOL_ID)
            );
        }
        let result = satellite_message_json(
            socket
                .next()
                .await
                .expect("terminal result should arrive")
                .expect("terminal result should be valid"),
        )
        .expect("terminal result should be JSON");
        assert_eq!(
            result.get("type").and_then(serde_json::Value::as_str),
            Some("result")
        );
        assert_eq!(
            result.get("status").and_then(serde_json::Value::as_str),
            Some(if result["data"]["ok"] == true {
                "succeeded"
            } else {
                "failed"
            })
        );
        assert!(result
            .get("data")
            .and_then(|data| data.get("foreground"))
            .is_some());
        socket
            .close(None)
            .await
            .expect("test websocket should close");
    });

    let backend_url = format!("http://{address}");
    let client_result =
        run_desktop_satellite_session(&backend_url, "instance-wire-test", "test-token").await;
    server.await.expect("test satellite server should finish");
    assert!(matches!(
        client_result,
        Err("satellite_closed") | Err("satellite_receive_failed")
    ));
}
