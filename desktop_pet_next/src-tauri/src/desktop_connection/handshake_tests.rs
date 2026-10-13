use crate::desktop_connection::handshake::satellite_websocket_url;

#[test]
fn satellite_transport_requires_tls_away_from_loopback() {
    assert_eq!(
        satellite_websocket_url("https://akane.example.com")
            .unwrap()
            .as_str(),
        "wss://akane.example.com/capabilities/satellite/ws"
    );
    assert_eq!(
        satellite_websocket_url("http://127.0.0.1:9999")
            .unwrap()
            .as_str(),
        "ws://127.0.0.1:9999/capabilities/satellite/ws"
    );
    assert_eq!(
        satellite_websocket_url("http://10.0.0.4:9999"),
        Err("secure_satellite_transport_required")
    );
}
