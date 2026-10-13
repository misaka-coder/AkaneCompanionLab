use std::sync::{Mutex, OnceLock};
use windows::{
    core::BOOL,
    Win32::{
        Foundation::{HWND, LPARAM, LRESULT, RECT, WPARAM},
        UI::{
            Shell::{DefSubclassProc, RemoveWindowSubclass, SetWindowSubclass},
            WindowsAndMessaging::{
                EnumChildWindows, GetWindowRect, HTCLIENT, HTTRANSPARENT, WM_NCDESTROY,
                WM_NCHITTEST,
            },
        },
    },
};

use crate::pet_windows::hit_regions::HitRegion;

#[cfg(windows)]
#[derive(Debug, Default)]
pub(crate) struct NativeHitTestStore {
    pub(crate) enabled: bool,
    pub(crate) regions: Vec<HitRegion>,
    pub(crate) root_hwnd: isize,
}

#[cfg(windows)]
pub(crate) static NATIVE_HIT_TEST_STORE: OnceLock<Mutex<NativeHitTestStore>> = OnceLock::new();

#[cfg(windows)]
pub(crate) const HIT_TEST_SUBCLASS_ID: usize = 0xA11A_0003;

#[cfg(windows)]
pub(crate) fn native_hit_test_store() -> &'static Mutex<NativeHitTestStore> {
    NATIVE_HIT_TEST_STORE.get_or_init(|| Mutex::new(NativeHitTestStore::default()))
}

#[cfg(windows)]
pub(crate) fn install_native_hit_test(hwnd: HWND) -> Result<(), String> {
    if let Ok(mut store) = native_hit_test_store().lock() {
        store.root_hwnd = hwnd.0 as isize;
    }

    if subclass_hit_test_window(hwnd) {
        refresh_native_child_hit_test_hooks(hwnd);
        Ok(())
    } else {
        Err("SetWindowSubclass failed".to_string())
    }
}

#[cfg(windows)]
pub(crate) fn set_native_hit_test_enabled(enabled: bool) {
    if let Ok(mut store) = native_hit_test_store().lock() {
        store.enabled = enabled;
    }
}

#[cfg(windows)]
pub(crate) fn set_native_hit_regions(regions: Vec<HitRegion>) {
    let root_hwnd = if let Ok(mut store) = native_hit_test_store().lock() {
        store.regions = regions;
        store.root_hwnd
    } else {
        0
    };

    if root_hwnd != 0 {
        refresh_native_child_hit_test_hooks(HWND(root_hwnd as _));
    }
}

#[cfg(windows)]
pub(crate) fn subclass_hit_test_window(hwnd: HWND) -> bool {
    unsafe { SetWindowSubclass(hwnd, Some(native_hit_test_proc), HIT_TEST_SUBCLASS_ID, 0) }
        .as_bool()
}

#[cfg(windows)]
pub(crate) fn refresh_native_child_hit_test_hooks(root_hwnd: HWND) {
    unsafe {
        let _ = EnumChildWindows(Some(root_hwnd), Some(enum_child_hit_test_proc), LPARAM(0));
    }
}

#[cfg(windows)]
pub(crate) unsafe extern "system" fn enum_child_hit_test_proc(hwnd: HWND, _lparam: LPARAM) -> BOOL {
    let _ = subclass_hit_test_window(hwnd);
    BOOL(1)
}

#[cfg(windows)]
pub(crate) unsafe extern "system" fn native_hit_test_proc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    _subclass_id: usize,
    _ref_data: usize,
) -> LRESULT {
    match msg {
        WM_NCHITTEST => {
            if let Some(result) = native_hit_test(hwnd, lparam) {
                return result;
            }
        }
        WM_NCDESTROY => {
            let _ = RemoveWindowSubclass(hwnd, Some(native_hit_test_proc), HIT_TEST_SUBCLASS_ID);
        }
        _ => {}
    }

    DefSubclassProc(hwnd, msg, wparam, lparam)
}

#[cfg(windows)]
pub(crate) fn native_hit_test(hwnd: HWND, lparam: LPARAM) -> Option<LRESULT> {
    let store = native_hit_test_store().lock().ok()?;
    if !store.enabled || store.regions.is_empty() {
        return None;
    }

    let root_hwnd = if store.root_hwnd == 0 {
        hwnd
    } else {
        HWND(store.root_hwnd as _)
    };

    let mut rect = RECT::default();
    if unsafe { GetWindowRect(root_hwnd, &mut rect) }.is_err() {
        return None;
    }

    let x = get_x_lparam(lparam).saturating_sub(rect.left);
    let y = get_y_lparam(lparam).saturating_sub(rect.top);
    let interactive = store
        .regions
        .iter()
        .any(|region| region.contains_point(x, y));

    if interactive {
        Some(LRESULT(HTCLIENT as isize))
    } else {
        Some(LRESULT(HTTRANSPARENT as isize))
    }
}

#[cfg(windows)]
pub(crate) fn get_x_lparam(lparam: LPARAM) -> i32 {
    (lparam.0 as u32 & 0xffff) as i16 as i32
}

#[cfg(windows)]
pub(crate) fn get_y_lparam(lparam: LPARAM) -> i32 {
    ((lparam.0 as u32 >> 16) & 0xffff) as i16 as i32
}
