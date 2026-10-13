use super::http::{response_bytes_bounded, BodyReadError};
use std::{
    io::{Read, Write},
    net::TcpListener,
    thread,
};

async fn read_fixture(response: &'static [u8], max_bytes: usize) -> Result<Vec<u8>, BodyReadError> {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let server = thread::spawn(move || {
        let (mut socket, _) = listener.accept().unwrap();
        let mut request = [0u8; 4096];
        socket.read(&mut request).unwrap();
        let _ = socket.write_all(response);
    });
    let response = reqwest::Client::new()
        .get(format!("http://{address}/"))
        .send()
        .await
        .unwrap();
    let result = response_bytes_bounded(response, max_bytes).await;
    server.join().unwrap();
    result
}

#[tokio::test]
async fn chunked_bodies_stop_at_the_limit_and_valid_bodies_are_preserved() {
    let response = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n3\r\nabc\r\n3\r\ndef\r\n0\r\n\r\n";
    assert!(matches!(
        read_fixture(response, 5).await,
        Err(BodyReadError::TooLarge)
    ));
    assert_eq!(read_fixture(response, 6).await.unwrap(), b"abcdef");
}

#[tokio::test]
async fn truncated_bodies_keep_the_read_failure() {
    let response = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nabc";
    assert!(matches!(
        read_fixture(response, 10).await,
        Err(BodyReadError::Read(_))
    ));
}
