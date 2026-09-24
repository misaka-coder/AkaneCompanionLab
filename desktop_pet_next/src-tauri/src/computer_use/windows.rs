//! Win32 discovery. Raw handles and process identities never leave this module's caller.
use serde_json::{json, Value};
use windows::core::BOOL;
use windows::Win32::{
    Foundation::{CloseHandle, FILETIME, HWND, LPARAM, POINT, RECT},
    Graphics::{Dwm::{DwmGetWindowAttribute, DWMWA_EXTENDED_FRAME_BOUNDS}, Gdi::ClientToScreen},
    System::Threading::{GetProcessTimes, OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION},
    UI::{HiDpi::{GetDpiForWindow, SetThreadDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2},
        WindowsAndMessaging::*},
};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Identity { pub hwnd: isize, pub pid: u32, pub started: u64, pub generation: u64 }
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Geometry { pub bounds: [i32; 4], pub client: [i32; 4], pub dpi: u32 }

pub struct DpiGuard(windows::Win32::UI::HiDpi::DPI_AWARENESS_CONTEXT);
impl DpiGuard {
    pub fn new() -> Self { unsafe { Self(SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)) } }
}
impl Drop for DpiGuard { fn drop(&mut self) { unsafe { SetThreadDpiAwarenessContext(self.0); } } }
pub fn handle(value: isize) -> HWND { HWND(value as *mut _) }

pub fn identity(hwnd: HWND) -> Option<Identity> {
    unsafe {
        if !IsWindow(Some(hwnd)).as_bool() { return None; }
        let mut pid = 0;
        GetWindowThreadProcessId(hwnd, Some(&mut pid));
        let process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid).ok()?;
        let mut creation = FILETIME::default(); let mut exit = FILETIME::default();
        let mut kernel = FILETIME::default(); let mut user = FILETIME::default();
        let result = GetProcessTimes(process, &mut creation, &mut exit, &mut kernel, &mut user);
        let _ = CloseHandle(process);
        result.ok()?;
        Some(Identity { hwnd: hwnd.0 as isize, pid, generation: super::monitor::window_generation(hwnd.0 as isize),
            started: ((creation.dwHighDateTime as u64) << 32) | creation.dwLowDateTime as u64 })
    }
}
pub fn title(hwnd: HWND) -> String {
    unsafe { let mut text = vec![0u16; 513]; let len = GetWindowTextW(hwnd, &mut text);
        String::from_utf16_lossy(&text[..len.max(0) as usize]) }
}
pub fn enumerate() -> Vec<(Identity, String)> {
    unsafe extern "system" fn visit(hwnd: HWND, value: LPARAM) -> BOOL {
        if IsWindowVisible(hwnd).as_bool() && GetWindowTextLengthW(hwnd) > 0 {
            if let Some(id) = identity(hwnd) {
                let out = &mut *(value.0 as *mut Vec<(Identity, String)>);
                if out.len() < 512 { out.push((id, title(hwnd))); }
            }
        }
        BOOL(1)
    }
    let mut out = Vec::new();
    unsafe { let _ = EnumWindows(Some(visit), LPARAM(&mut out as *mut _ as isize)); }
    out
}
pub fn geometry(id: &Identity) -> Result<Geometry, &'static str> {
    let _dpi = DpiGuard::new(); let hwnd = handle(id.hwnd);
    if identity(hwnd).as_ref() != Some(id) { return Err("window_identity_expired"); }
    unsafe {
        if IsIconic(hwnd).as_bool() { return Err("window_minimized"); }
        if !IsWindowVisible(hwnd).as_bool() { return Err("window_not_visible"); }
        let mut bounds = RECT::default();
        if DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, &mut bounds as *mut _ as *mut _,
                                 std::mem::size_of::<RECT>() as u32).is_err() {
            GetWindowRect(hwnd, &mut bounds).map_err(|_| "window_geometry_unavailable")?;
        }
        let mut client = RECT::default();
        GetClientRect(hwnd, &mut client).map_err(|_| "client_geometry_unavailable")?;
        let mut point = POINT::default();
        if !ClientToScreen(hwnd, &mut point).as_bool() { return Err("client_geometry_unavailable"); }
        Ok(Geometry { bounds: [bounds.left, bounds.top, bounds.right, bounds.bottom],
            client: [point.x, point.y, point.x + client.right, point.y + client.bottom], dpi: GetDpiForWindow(hwnd) })
    }
}
pub fn foreground(id: &Identity) -> bool { unsafe { GetForegroundWindow() == handle(id.hwnd) } }
pub fn minimized(id:&Identity)->bool {unsafe{IsIconic(handle(id.hwnd)).as_bool()}}
pub fn summary(id:&Identity)->Value {
    let mut out=json!({"title":title(handle(id.hwnd)),"application":process_name(id),
        "minimized":minimized(id),"visible":unsafe{IsWindowVisible(handle(id.hwnd)).as_bool()},"foreground_matches":foreground(id)});
    if let Ok(geometry)=geometry(id){out["window_bounds"]=bounds_json(geometry.bounds);}
    out
}
pub fn at_point(x:i32,y:i32)->Option<Identity>{
    let _dpi=DpiGuard::new();unsafe{identity(GetAncestor(WindowFromPoint(POINT{x,y}),GA_ROOT))}
}

/// Mutate a previously discovered window; the caller owns permission and scope.
/// Every asynchronous change is verified before returning success.
pub fn manage(id:&Identity,action:&str,x:i32,y:i32,generation:u64)->Result<(),&'static str>{
    if matches!(action,"activate"|"restore"){return activate(id,generation);}
    let _dpi=DpiGuard::new();
    let check=||{
        if crate::control_lease::stopped()||super::monitor::input_generation()!=generation{return Err("window_action_interrupted");}
        if identity(handle(id.hwnd)).as_ref()!=Some(id){return Err("window_identity_expired");}Ok(())
    };
    check()?;
    unsafe{
        match action {
            "minimize"=>{let _=ShowWindowAsync(handle(id.hwnd),SW_MINIMIZE);},
            "move"=>{
                if minimized(id){return Err("window_minimized");}
                SetWindowPos(handle(id.hwnd),None,x,y,0,0,SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE).map_err(|_|"window_move_failed")?;
            },_=>return Err("window_action_unsupported"),
        }
        for _ in 0..30 {
            check()?;
            if action=="minimize"&&minimized(id){return Ok(());}
            if action=="move" {
                let mut rect=RECT::default();
                if GetWindowRect(handle(id.hwnd),&mut rect).is_ok()&&rect.left==x&&rect.top==y{return Ok(());}
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
    }
    Err("window_action_not_verified")
}
/// Follow the OS owner chain, never a title/process-name guess.
pub fn foreground_owned_by(root:&Identity)->Option<Identity>{
    unsafe {
        let foreground=GetForegroundWindow();let candidate=identity(foreground)?;
        if owned_by(root,&candidate){Some(candidate)}else{None}
    }
}
pub fn owned_by(root:&Identity,candidate:&Identity)->bool {
    if candidate.pid!=root.pid||candidate.started!=root.started||identity(handle(root.hwnd)).as_ref()!=Some(root){return false;}
    unsafe {
        let mut current=handle(candidate.hwnd);
        for _ in 0..8 {
            if current==handle(root.hwnd){return true;}
            let Ok(owner)=GetWindow(current,GW_OWNER)else{return false;};current=owner;
            if current.is_invalid(){return false;}
        }
        false
    }
}
pub fn process_name(id: &Identity) -> String {
    unsafe {
        let Ok(process) = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, id.pid) else { return String::new(); };
        let mut text = vec![0u16; 32768]; let mut length = text.len() as u32;
        let result = QueryFullProcessImageNameW(process, PROCESS_NAME_WIN32, windows::core::PWSTR(text.as_mut_ptr()), &mut length);
        let _ = CloseHandle(process);
        if result.is_err() { return String::new(); }
        String::from_utf16_lossy(&text[..length as usize]).rsplit(['\\','/']).next().unwrap_or("").to_lowercase()
    }
}
pub fn activate(id: &Identity,generation:u64) -> Result<(), &'static str> {
    if identity(handle(id.hwnd)).as_ref() != Some(id) { return Err("window_identity_expired"); }
    let permitted=||!crate::control_lease::stopped()&&super::monitor::input_generation()==generation;
    unsafe {
        if !permitted(){return Err("activation_interrupted");}
        if IsIconic(handle(id.hwnd)).as_bool() {
            if !ShowWindowAsync(handle(id.hwnd),SW_RESTORE).as_bool(){return Err("window_restore_denied");}
            for _ in 0..20 {
                if !permitted(){return Err("activation_interrupted");}
                if !IsIconic(handle(id.hwnd)).as_bool(){break;}
                std::thread::sleep(std::time::Duration::from_millis(10));
            }
            if IsIconic(handle(id.hwnd)).as_bool(){return Err("window_restore_timeout");}
        }
        if !permitted(){return Err("activation_interrupted");}
        let mut requested=SetForegroundWindow(handle(id.hwnd)).as_bool();
        if !requested && permitted() && crate::device_control::request_foreground_handoff() {
            if !permitted(){return Err("activation_interrupted");}
            if identity(handle(id.hwnd)).as_ref()!=Some(id){return Err("window_identity_expired");}
            requested=SetForegroundWindow(handle(id.hwnd)).as_bool();
        }
        for _ in 0..20 {
            if !permitted(){return Err("activation_interrupted");}
            if foreground(id){return Ok(());}
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        if !requested{return Err("activation_denied");}
    }
    if foreground(id) { Ok(()) } else { Err("foreground_mismatch") }
}
pub fn bounds_json(rect: [i32; 4]) -> Value {
    json!({"x":rect[0], "y":rect[1], "width":rect[2]-rect[0], "height":rect[3]-rect[1]})
}
/// A caret is evidence of a text insertion point, not of an editable/password
/// field or a conversation recipient. Callers must preserve that distinction.
pub fn caret_evidence(id:&Identity)->Value {
    let _dpi=DpiGuard::new();
    unsafe {
        let thread=GetWindowThreadProcessId(handle(id.hwnd),None);
        let mut info=GUITHREADINFO{cbSize:std::mem::size_of::<GUITHREADINFO>() as u32,..Default::default()};
        if GetGUIThreadInfo(thread,&mut info).is_err()||info.hwndCaret.is_invalid()||info.hwndFocus.is_invalid()
            ||GetAncestor(info.hwndFocus,GA_ROOT)!=handle(id.hwnd)||GetAncestor(info.hwndCaret,GA_ROOT)!=handle(id.hwnd)
            ||info.rcCaret.bottom<=info.rcCaret.top {return json!({"source":"unavailable"});}
        let mut point=POINT{x:info.rcCaret.left,y:info.rcCaret.top};
        if !ClientToScreen(info.hwndCaret,&mut point).as_bool(){return json!({"source":"unavailable"});}
        use sha2::{Digest,Sha256};
        let key=format!("{:x}",Sha256::digest(format!("{}:{}:{}:{}",id.pid,id.started,info.hwndFocus.0 as isize,info.hwndCaret.0 as isize).as_bytes()));
        json!({"source":"win32_caret","identity":key,"foreground_matches":foreground(id),
            "bounds":bounds_json([point.x,point.y,point.x+(info.rcCaret.right-info.rcCaret.left).max(1),point.y+info.rcCaret.bottom-info.rcCaret.top]),
            "editable":"unknown","password":"unknown"})
    }
}
