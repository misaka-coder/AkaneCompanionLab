//! WGC window frames, including occluded windows. No primary-monitor GDI fallback.
use std::{sync::{mpsc::{self, SyncSender}, Arc, OnceLock, atomic::{AtomicBool, Ordering}}, time::{Duration, Instant}};
use windows::{core::Interface, Graphics::{Capture::{Direct3D11CaptureFramePool, GraphicsCaptureItem, GraphicsCaptureSession},
    DirectX::{Direct3D11::IDirect3DDevice, DirectXPixelFormat}},
    Win32::{Foundation::HMODULE, Graphics::{Direct3D::D3D_DRIVER_TYPE_HARDWARE, Direct3D11::*, Dxgi::IDXGIDevice},
        System::WinRT::{RoInitialize, RoUninitialize, RO_INIT_MULTITHREADED,
            Direct3D11::{CreateDirect3D11DeviceFromDXGIDevice, IDirect3DDxgiInterfaceAccess},
            Graphics::Capture::IGraphicsCaptureItemInterop}}};
use super::windows::{handle, Identity};

#[derive(Clone)]
pub struct Frame { pub width: u32, pub height: u32, pub rgba: Vec<u8>, pub png: Vec<u8> }
struct Apartment;
impl Drop for Apartment { fn drop(&mut self) { unsafe { RoUninitialize(); } } }

type CaptureResult = Result<Frame, &'static str>;
struct Request { identity: Identity, cancelled: Arc<AtomicBool>, reply: SyncSender<CaptureResult> }
static WORKER: OnceLock<Result<SyncSender<Request>, &'static str>> = OnceLock::new();

// WGC owns asynchronous native work beyond Close(). A per-call RoUninitialize can
// unload GraphicsCapture.dll while that work still runs (including after returning
// a valid frame). Give capture one process-lifetime apartment, independent of UIA
// and of Tokio's transient blocking threads. All WinRT/D3D objects stay on its owner.
fn worker() -> Result<&'static SyncSender<Request>, &'static str> {
    WORKER.get_or_init(|| {
        let (tx, rx) = mpsc::sync_channel::<Request>(1);
        let (ready_tx, ready_rx) = mpsc::sync_channel(1);
        std::thread::Builder::new().name("akane-wgc-mta".into()).spawn(move || {
            let initialized = unsafe { RoInitialize(RO_INIT_MULTITHREADED) }
                .map_err(|_| "capture_apartment_failed");
            if initialized.is_err() { let _ = ready_tx.send(initialized); return; }
            let _apartment = Apartment;
            if ready_tx.send(Ok(())).is_err() { return; }
            // The static sender intentionally lives as long as the executable.
            // Do not tear this apartment down between frames or on disconnect.
            while let Ok(request) = rx.recv() {
                let result = capture_on_worker(&request.identity, &request.cancelled);
                let _ = request.reply.send(result);
            }
        }).map_err(|_| "capture_worker_unavailable")?;
        ready_rx.recv_timeout(Duration::from_secs(3)).map_err(|_| "capture_worker_unavailable")??;
        Ok(tx)
    }).as_ref().map_err(|reason| *reason)
}

struct Cancellation(Arc<AtomicBool>);
impl Drop for Cancellation { fn drop(&mut self) { self.0.store(true, Ordering::SeqCst); } }

pub fn capture(id: &Identity, stopped: &AtomicBool) -> CaptureResult {
    if stopped.load(Ordering::SeqCst) { return Err("stopped"); }
    let worker = worker()?;
    let cancelled = Arc::new(AtomicBool::new(false));
    let (tx, rx) = mpsc::sync_channel(1);
    worker.try_send(Request { identity: id.clone(), cancelled: cancelled.clone(), reply: tx }).map_err(|e| match e {
        mpsc::TrySendError::Full(_) => "capture_worker_busy",
        mpsc::TrySendError::Disconnected(_) => "capture_worker_unavailable",
    })?;
    wait_for_frame(rx, cancelled, stopped, Instant::now() + Duration::from_secs(4))
}

fn wait_for_frame(rx: mpsc::Receiver<CaptureResult>, cancelled: Arc<AtomicBool>, stopped: &AtomicBool, deadline: Instant) -> CaptureResult {
    let _cancellation = Cancellation(cancelled);
    loop {
        if stopped.load(Ordering::SeqCst) { return Err("stopped"); }
        if Instant::now() >= deadline { return Err("capture_worker_timeout"); }
        match rx.recv_timeout(Duration::from_millis(20)) {
            Ok(result) => return if stopped.load(Ordering::SeqCst) { Err("stopped") } else { result },
            Err(mpsc::RecvTimeoutError::Timeout) => {},
            Err(mpsc::RecvTimeoutError::Disconnected) => return Err("capture_worker_unavailable"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn stop_does_not_wait_for_a_blocked_capture_provider() {
        let (tx, rx) = mpsc::sync_channel(1);
        let cancelled = Arc::new(AtomicBool::new(false));
        let stopped = Arc::new(AtomicBool::new(false));
        let signal = stopped.clone();
        let stopper = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(30)); signal.store(true, Ordering::SeqCst);
        });
        let start = Instant::now();
        assert!(matches!(wait_for_frame(rx, cancelled.clone(), &stopped, start + Duration::from_secs(4)), Err("stopped")));
        assert!(start.elapsed() < Duration::from_secs(1));
        assert!(cancelled.load(Ordering::SeqCst));
        assert!(tx.send(Err("late_native_result")).is_err());
        stopper.join().unwrap();
    }
    #[test]
    fn timeout_cancels_abandoned_capture_and_rejects_late_result() {
        let (tx, rx) = mpsc::sync_channel(1);
        let cancelled = Arc::new(AtomicBool::new(false));
        assert!(matches!(wait_for_frame(rx, cancelled.clone(), &AtomicBool::new(false),
            Instant::now() + Duration::from_millis(30)), Err("capture_worker_timeout")));
        assert!(cancelled.load(Ordering::SeqCst));
        assert!(tx.send(Err("late_native_result")).is_err());
    }
}

struct PoolGuard(Direct3D11CaptureFramePool);
impl Drop for PoolGuard { fn drop(&mut self) { let _ = self.0.Close(); } }
struct SessionGuard(GraphicsCaptureSession);
impl Drop for SessionGuard { fn drop(&mut self) { let _ = self.0.Close(); } }

fn capture_on_worker(id: &Identity, stopped: &AtomicBool) -> CaptureResult {
    if stopped.load(Ordering::SeqCst) { return Err("stopped"); }
    super::windows::geometry(id)?;
    if !GraphicsCaptureSession::IsSupported().unwrap_or(false) { return Err("wgc_unavailable"); }
    let interop = windows::core::factory::<GraphicsCaptureItem, IGraphicsCaptureItemInterop>()
        .map_err(|_| "capture_factory_failed")?;
    let item: GraphicsCaptureItem = unsafe { interop.CreateForWindow(handle(id.hwnd)) }
        .map_err(|_| "capture_window_unavailable")?;
    let size = item.Size().map_err(|_| "capture_size_unavailable")?;
    if size.Width <= 0 || size.Height <= 0 || (size.Width as i64 * size.Height as i64) > 16_777_216 {
        return Err("capture_size_unsupported");
    }
    let mut device = None; let mut context = None;
    unsafe { D3D11CreateDevice(None, D3D_DRIVER_TYPE_HARDWARE, HMODULE::default(),
        D3D11_CREATE_DEVICE_BGRA_SUPPORT, None, D3D11_SDK_VERSION, Some(&mut device), None, Some(&mut context)) }
        .map_err(|_| "capture_device_failed")?;
    let device = device.ok_or("capture_device_failed")?;
    let context = context.ok_or("capture_device_failed")?;
    let dxgi: IDXGIDevice = device.cast().map_err(|_| "capture_device_failed")?;
    let runtime: IDirect3DDevice = unsafe { CreateDirect3D11DeviceFromDXGIDevice(&dxgi) }
        .and_then(|v| v.cast()).map_err(|_| "capture_device_failed")?;
    let pool = PoolGuard(Direct3D11CaptureFramePool::CreateFreeThreaded(&runtime, DirectXPixelFormat::B8G8R8A8UIntNormalized, 1, size)
        .map_err(|_| "capture_pool_failed")?);
    let session = SessionGuard(pool.0.CreateCaptureSession(&item).map_err(|_| "capture_session_failed")?);
    let result = (|| {
        // Cursor animation is not a change to the target control. Keeping it in
        // the WGC image makes a stationary busy pointer invalidate click patches.
        session.0.SetIsCursorCaptureEnabled(false).map_err(|_| "capture_cursor_configuration_failed")?;
        session.0.StartCapture().map_err(|_| "capture_start_failed")?;
        let deadline = Instant::now() + Duration::from_millis(1800);
        let frame = loop {
            if stopped.load(std::sync::atomic::Ordering::SeqCst) { return Err("stopped"); }
            if let Ok(frame) = pool.0.TryGetNextFrame() { break frame; }
            if Instant::now() >= deadline { return Err("capture_frame_timeout"); }
            std::thread::sleep(Duration::from_millis(15));
        };
        let result = (|| {
            let current = frame.ContentSize().map_err(|_| "capture_frame_invalid")?;
            if current.Width != size.Width || current.Height != size.Height { return Err("capture_size_changed"); }
            let surface = frame.Surface().map_err(|_| "capture_surface_failed")?;
            let access: IDirect3DDxgiInterfaceAccess = surface.cast().map_err(|_| "capture_surface_failed")?;
            let texture: ID3D11Texture2D = unsafe { access.GetInterface() }.map_err(|_| "capture_texture_failed")?;
            let mut desc = D3D11_TEXTURE2D_DESC::default();
            unsafe { texture.GetDesc(&mut desc); }
            if desc.Width < size.Width as u32 || desc.Height < size.Height as u32 { return Err("capture_size_changed"); }
            desc.Usage = D3D11_USAGE_STAGING; desc.BindFlags = 0;
            desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ.0 as u32; desc.MiscFlags = 0;
            let mut staging = None;
            unsafe { device.CreateTexture2D(&desc, None, Some(&mut staging)) }.map_err(|_| "capture_copy_failed")?;
            let staging = staging.ok_or("capture_copy_failed")?;
            let mut mapped = D3D11_MAPPED_SUBRESOURCE::default();
            unsafe { context.CopyResource(&staging, &texture);
                context.Map(&staging, 0, D3D11_MAP_READ, 0, Some(&mut mapped)) }.map_err(|_| "capture_map_failed")?;
            let width = size.Width as u32; let height = size.Height as u32;
            let mut rgba = vec![0u8; width as usize * height as usize * 4];
            for row in 0..height as usize {
                let bytes = unsafe { std::slice::from_raw_parts((mapped.pData as *const u8).add(row * mapped.RowPitch as usize), width as usize * 4) };
                for (src, dst) in bytes.chunks_exact(4).zip(rgba[row * width as usize * 4..(row + 1) * width as usize * 4].chunks_exact_mut(4)) {
                    dst.copy_from_slice(&[src[2], src[1], src[0], 255]);
                }
            }
            unsafe { context.Unmap(&staging, 0); }
            let mut png = Vec::new();
            { let mut encoder = png::Encoder::new(&mut png, width, height);
              encoder.set_color(png::ColorType::Rgba); encoder.set_depth(png::BitDepth::Eight);
              let mut writer = encoder.write_header().map_err(|_| "capture_encode_failed")?;
              writer.write_image_data(&rgba).map_err(|_| "capture_encode_failed")?; }
            if png.len() > 8 * 1024 * 1024 { return Err("capture_payload_too_large"); }
            Ok(Frame { width, height, rgba, png })
        })();
        let _ = frame.Close();
        result
    })();
    result
}
