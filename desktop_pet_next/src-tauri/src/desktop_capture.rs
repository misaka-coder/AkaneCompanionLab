//! Capture on the bound Windows device; no shell, files, or server-side fallback.
#[cfg(not(windows))]
pub fn capture() -> Result<serde_json::Value, &'static str> {
    Err("unsupported_platform")
}

#[cfg(windows)]
pub fn capture() -> Result<serde_json::Value, &'static str> {
    use base64::Engine;
    use windows::Win32::UI::HiDpi::{SetThreadDpiAwarenessContext,
        DPI_AWARENESS_CONTEXT, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2};
    // The device-only process does not pass through Tauri's DPI setup. Scope
    // awareness to this capture thread and restore it on every return path.
    struct DpiGuard(DPI_AWARENESS_CONTEXT);
    impl Drop for DpiGuard {
        fn drop(&mut self) { unsafe { SetThreadDpiAwarenessContext(self.0); } }
    }
    let previous = unsafe { SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) };
    if previous.0.is_null() { return Err("screen_dpi_context_unavailable"); }
    let _dpi = DpiGuard(previous);
    use windows::Win32::Graphics::Gdi::*;
    use windows::Win32::UI::WindowsAndMessaging::{GetSystemMetrics, SM_CXSCREEN, SM_CYSCREEN};
    let width = unsafe { GetSystemMetrics(SM_CXSCREEN) };
    let height = unsafe { GetSystemMetrics(SM_CYSCREEN) };
    if width <= 0 || height <= 0 || i64::from(width) * i64::from(height) > 16_777_216 {
        return Err("screen_dimensions_unavailable");
    }
    let mut pixels = vec![0_u8; width as usize * height as usize * 4];
    unsafe {
        let screen = GetDC(None);
        if screen.0.is_null() { return Err("screen_capture_unavailable"); }
        let memory = CreateCompatibleDC(Some(screen));
        let bitmap = CreateCompatibleBitmap(screen, width, height);
        if memory.0.is_null() || bitmap.0.is_null() {
            if !bitmap.0.is_null() { let _ = DeleteObject(HGDIOBJ(bitmap.0)); }
            if !memory.0.is_null() { let _ = DeleteDC(memory); }
            ReleaseDC(None, screen);
            return Err("screen_capture_unavailable");
        }
        let previous = SelectObject(memory, HGDIOBJ(bitmap.0));
        let copied = BitBlt(memory, 0, 0, width, height, Some(screen), 0, 0, SRCCOPY | CAPTUREBLT);
        SelectObject(memory, previous);
        let mut info = BITMAPINFO::default();
        info.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        info.bmiHeader.biWidth = width;
        info.bmiHeader.biHeight = -height;
        info.bmiHeader.biPlanes = 1;
        info.bmiHeader.biBitCount = 32;
        info.bmiHeader.biCompression = BI_RGB.0;
        let lines = if copied.is_ok() {
            GetDIBits(screen, bitmap, 0, height as u32, Some(pixels.as_mut_ptr().cast()), &mut info, DIB_RGB_COLORS)
        } else { 0 };
        let _ = DeleteObject(HGDIOBJ(bitmap.0));
        let _ = DeleteDC(memory);
        ReleaseDC(None, screen);
        if lines != height { return Err("screen_capture_failed"); }
    }
    for pixel in pixels.chunks_exact_mut(4) {
        pixel.swap(0, 2);
        pixel[3] = 255;
    }
    let mut bytes = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut bytes, width as u32, height as u32);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        let mut writer = encoder.write_header().map_err(|_| "screen_encoding_failed")?;
        writer.write_image_data(&pixels).map_err(|_| "screen_encoding_failed")?;
    }
    if bytes.len() > 8 * 1024 * 1024 { return Err("screen_image_too_large"); }
    Ok(serde_json::json!({"ok": true, "width": width, "height": height,
        "capturedAt": super::current_time_millis(), "mimeType": "image/png",
        "imageBase64": base64::engine::general_purpose::STANDARD.encode(bytes)}))
}

#[cfg(all(test, windows))]
mod tests {
    #[test]
    #[ignore = "requires an interactive Windows desktop; captures screen in memory"]
    fn capture_uses_physical_pixels_and_restores_thread_context() {
        use windows::Win32::UI::HiDpi::*;
        use windows::Win32::UI::WindowsAndMessaging::*;
        use base64::Engine;
        unsafe {
            let original = SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
            let physical = (GetSystemMetrics(SM_CXSCREEN), GetSystemMetrics(SM_CYSCREEN));
            SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_UNAWARE);
            let captured = super::capture();
            let restored = AreDpiAwarenessContextsEqual(GetThreadDpiAwarenessContext(), DPI_AWARENESS_CONTEXT_UNAWARE).as_bool();
            SetThreadDpiAwarenessContext(original);
            let captured = captured.expect("real desktop capture");
            assert!(restored);
            assert_eq!(captured["width"], physical.0);
            assert_eq!(captured["height"], physical.1);
            let bytes = base64::engine::general_purpose::STANDARD.decode(captured["imageBase64"].as_str().unwrap()).unwrap();
            let png = png::Decoder::new(std::io::Cursor::new(bytes)).read_info().unwrap();
            assert_eq!((png.info().width, png.info().height), (physical.0 as u32, physical.1 as u32));
            println!("physical capture verified: {}x{}", physical.0, physical.1);
        }
    }
}
