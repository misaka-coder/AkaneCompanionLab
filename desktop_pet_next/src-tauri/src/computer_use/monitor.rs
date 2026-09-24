//! Physical-input generation and HWND destruction, on a dedicated message thread.
use std::{collections::HashMap, sync::{atomic::{AtomicU64, Ordering}, mpsc, Mutex, OnceLock}, time::{Duration,Instant}};
use windows::Win32::{Foundation::{HWND, LPARAM, LRESULT, WPARAM},
    UI::{Accessibility::{SetWinEventHook, HWINEVENTHOOK}, WindowsAndMessaging::*}};
pub const INPUT_TAG: usize = 0x414b414e;
static INPUT_GENERATION: AtomicU64 = AtomicU64::new(0);
static LAST_INPUT_MS: AtomicU64 = AtomicU64::new(0);
static CLOCK: OnceLock<Instant> = OnceLock::new();
static WINDOW_GENERATIONS: OnceLock<Mutex<HashMap<isize, u64>>> = OnceLock::new();
static STARTED: OnceLock<bool> = OnceLock::new();
pub fn input_generation() -> u64 { INPUT_GENERATION.load(Ordering::SeqCst) }
fn tick() -> u64 { CLOCK.get_or_init(Instant::now).elapsed().as_millis() as u64 + 1 }
fn record_input() {
    LAST_INPUT_MS.store(tick(),Ordering::SeqCst);
    INPUT_GENERATION.fetch_add(1,Ordering::SeqCst);
}
pub fn input_quiet() -> bool {
    let last=LAST_INPUT_MS.load(Ordering::SeqCst);
    (last==0 || tick().saturating_sub(last)>=600) && !(1..=255).any(|key| unsafe {
        windows::Win32::UI::Input::KeyboardAndMouse::GetAsyncKeyState(key) < 0
    })
}
/// A bounded pause for a model-requested observation, never an input retry.
pub fn wait_for_quiet() {
    let deadline=Instant::now()+Duration::from_millis(1200);
    while !input_quiet() && Instant::now()<deadline && !crate::control_lease::stopped() {
        std::thread::sleep(Duration::from_millis(25));
    }
}
#[cfg(test)] pub fn simulate_user_activity() { record_input(); }
pub fn window_generation(hwnd: isize) -> u64 {
    WINDOW_GENERATIONS.get_or_init(|| Mutex::new(HashMap::new())).lock().ok().and_then(|g| g.get(&hwnd).copied()).unwrap_or(0)
}
unsafe extern "system" fn keyboard(code: i32, w: WPARAM, l: LPARAM) -> LRESULT {
    if code >= 0 {
        let event = &*(l.0 as *const KBDLLHOOKSTRUCT);
        if event.dwExtraInfo != INPUT_TAG { record_input(); }
    }
    CallNextHookEx(None, code, w, l)
}
unsafe extern "system" fn mouse(code: i32, w: WPARAM, l: LPARAM) -> LRESULT {
    if code >= 0 {
        let event = &*(l.0 as *const MSLLHOOKSTRUCT);
        if event.dwExtraInfo != INPUT_TAG { record_input(); }
    }
    CallNextHookEx(None, code, w, l)
}
unsafe extern "system" fn destroyed(_: HWINEVENTHOOK, _: u32, hwnd: HWND, object: i32, child: i32, _: u32, _: u32) {
    if object == OBJID_WINDOW.0 && child == 0 {
        if let Ok(mut generations) = WINDOW_GENERATIONS.get_or_init(|| Mutex::new(HashMap::new())).lock() {
            // This only stores native handle generations, never UI contents.
            *generations.entry(hwnd.0 as isize).or_default() += 1;
        }
    }
}
pub fn start() -> bool {
    *STARTED.get_or_init(|| {
        let (tx, rx) = mpsc::sync_channel(1);
        if std::thread::Builder::new().name("akane-desktop-monitor".into()).spawn(move || unsafe {
            let keyboard = SetWindowsHookExW(WH_KEYBOARD_LL, Some(keyboard), None, 0);
            let mouse = SetWindowsHookExW(WH_MOUSE_LL, Some(mouse), None, 0);
            let events = SetWinEventHook(EVENT_OBJECT_DESTROY, EVENT_OBJECT_DESTROY, None, Some(destroyed), 0, 0, WINEVENT_OUTOFCONTEXT);
            let ready = keyboard.is_ok() && mouse.is_ok() && !events.is_invalid();
            let _ = tx.send(ready);
            if !ready { return; }
            let mut message = MSG::default();
            while GetMessageW(&mut message, None, 0, 0).0 > 0 {
                let _ = TranslateMessage(&message); DispatchMessageW(&message);
            }
        }).is_err() { return false; }
        rx.recv_timeout(Duration::from_secs(2)).unwrap_or(false)
    })
}
