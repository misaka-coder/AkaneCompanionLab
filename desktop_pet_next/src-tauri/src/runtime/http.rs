#[derive(Debug)]
pub(crate) enum BodyReadError {
    TooLarge,
    Read(reqwest::Error),
}

pub(crate) async fn response_bytes_bounded(
    mut response: reqwest::Response,
    max_bytes: usize,
) -> Result<Vec<u8>, BodyReadError> {
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(BodyReadError::Read)? {
        if chunk.len() > max_bytes.saturating_sub(bytes.len()) {
            return Err(BodyReadError::TooLarge);
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
}
