use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SystemVolumeResult {
    pub(crate) ok: bool,
    pub(crate) status: String,
    pub(crate) reason: String,
    pub(crate) action: String,
    pub(crate) volume: Option<u32>,
    pub(crate) muted: Option<bool>,
    pub(crate) platform: String,
}

pub(crate) fn system_volume_unavailable(action: &str, reason: &str) -> SystemVolumeResult {
    SystemVolumeResult {
        ok: false,
        status: "unavailable".to_string(),
        reason: reason.to_string(),
        action: action.to_string(),
        volume: None,
        muted: None,
        platform: std::env::consts::OS.to_string(),
    }
}

#[cfg(not(windows))]
pub(crate) fn read_system_volume() -> Result<(f32, bool), String> {
    Err("unsupported_platform".to_string())
}

#[cfg(windows)]
pub(crate) fn read_system_volume() -> Result<(f32, bool), String> {
    let volume = default_audio_endpoint_volume()?;
    let scalar: f32 =
        unsafe { volume.GetMasterVolumeLevelScalar() }.map_err(|error| error.to_string())?;
    let muted: windows::core::BOOL =
        unsafe { volume.GetMute() }.map_err(|error| error.to_string())?;
    Ok((scalar.clamp(0.0, 1.0), muted.as_bool()))
}

#[cfg(not(windows))]
pub(crate) fn set_system_volume(_level: f32) -> Result<(), String> {
    Err("unsupported_platform".to_string())
}

#[cfg(windows)]
pub(crate) fn set_system_volume(level: f32) -> Result<(), String> {
    let volume = default_audio_endpoint_volume()?;
    unsafe {
        volume.SetMasterVolumeLevelScalar(level.clamp(0.0, 1.0), &windows::core::GUID::zeroed())
    }
    .map_err(|error| error.to_string())
}

pub(crate) fn read_system_volume_result(action: &str) -> SystemVolumeResult {
    match read_system_volume() {
        Ok((scalar, muted)) => SystemVolumeResult {
            ok: true,
            status: "ok".to_string(),
            reason: String::new(),
            action: action.to_string(),
            volume: Some((scalar * 100.0).round() as u32),
            muted: Some(muted),
            platform: std::env::consts::OS.to_string(),
        },
        Err(reason) => system_volume_unavailable(
            action,
            if reason == "unsupported_platform" {
                "unsupported_platform"
            } else {
                "volume_read_failed"
            },
        ),
    }
}

pub(crate) fn set_system_volume_result(action: &str, value: u32) -> SystemVolumeResult {
    let level = (value.clamp(0, 100) as f32) / 100.0;
    match set_system_volume(level) {
        Ok(()) => match read_system_volume() {
            Ok((scalar, muted)) => SystemVolumeResult {
                ok: true,
                status: "ok".to_string(),
                reason: String::new(),
                action: action.to_string(),
                volume: Some((scalar * 100.0).round() as u32),
                muted: Some(muted),
                platform: std::env::consts::OS.to_string(),
            },
            Err(_) => system_volume_unavailable(action, "volume_read_failed"),
        },
        Err(reason) => system_volume_unavailable(
            action,
            if reason == "unsupported_platform" {
                "unsupported_platform"
            } else {
                "volume_set_failed"
            },
        ),
    }
}

#[cfg(windows)]
fn default_audio_endpoint_volume(
) -> Result<windows::Win32::Media::Audio::Endpoints::IAudioEndpointVolume, String> {
    let _ = unsafe {
        windows::Win32::System::Com::CoInitializeEx(
            None,
            windows::Win32::System::Com::COINIT_APARTMENTTHREADED,
        )
    };
    let clsid = windows::core::GUID::from_u128(0xbcde0395e52f467c8d3dc4579291692e);
    let enumerator: windows::Win32::Media::Audio::IMMDeviceEnumerator = unsafe {
        windows::Win32::System::Com::CoCreateInstance(
            &clsid,
            None,
            windows::Win32::System::Com::CLSCTX_ALL,
        )
    }
    .map_err(|error| error.to_string())?;
    let device: windows::Win32::Media::Audio::IMMDevice = unsafe {
        enumerator.GetDefaultAudioEndpoint(
            windows::Win32::Media::Audio::eRender,
            windows::Win32::Media::Audio::eConsole,
        )
    }
    .map_err(|error| error.to_string())?;
    let volume: windows::Win32::Media::Audio::Endpoints::IAudioEndpointVolume = unsafe {
        device.Activate::<windows::Win32::Media::Audio::Endpoints::IAudioEndpointVolume>(
            windows::Win32::System::Com::CLSCTX_ALL,
            None,
        )
    }
    .map_err(|error| error.to_string())?;
    Ok(volume)
}
