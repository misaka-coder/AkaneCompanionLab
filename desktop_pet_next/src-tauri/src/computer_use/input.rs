//! Bounded SendInput. No clipboard mutation, command evaluation or implicit Enter.
use std::sync::atomic::{AtomicBool, Ordering};
use ::windows::Win32::{Foundation::POINT, UI::{Input::KeyboardAndMouse::*, WindowsAndMessaging::*}};
use super::{monitor, windows::{self, Identity}};

fn permitted(id: &Identity, generation: u64, stopped: &AtomicBool) -> bool {
    !stopped.load(Ordering::SeqCst) && !crate::control_lease::stopped() && monitor::input_generation() == generation && windows::foreground(id)
        && windows::identity(windows::handle(id.hwnd)).as_ref() == Some(id)
}
fn keyboard(key: VIRTUAL_KEY, flags: KEYBD_EVENT_FLAGS, scan: u16) -> INPUT {
    INPUT { r#type: INPUT_KEYBOARD, Anonymous: INPUT_0 { ki: KEYBDINPUT { wVk: key, wScan: scan,
        dwFlags: flags, time: 0, dwExtraInfo: monitor::INPUT_TAG } } }
}
fn mouse(flags: MOUSE_EVENT_FLAGS, x: i32, y: i32, data: u32) -> INPUT {
    INPUT { r#type: INPUT_MOUSE, Anonymous: INPUT_0 { mi: MOUSEINPUT { dx: x, dy: y, mouseData: data,
        dwFlags: flags, time: 0, dwExtraInfo: monitor::INPUT_TAG } } }
}
fn send(events: &[INPUT]) -> bool { unsafe { SendInput(events, std::mem::size_of::<INPUT>() as i32) as usize == events.len() } }

/// Err means possible partial injection: callers must report unknown, never retry.
pub fn text(id: &Identity, text: &str, generation: u64, stopped: &AtomicBool) -> Result<(), &'static str> {
    for unit in text.encode_utf16() {
        if !permitted(id, generation, stopped) { return Err("input_interrupted"); }
        if !send(&[keyboard(VIRTUAL_KEY(0), KEYEVENTF_UNICODE, unit), keyboard(VIRTUAL_KEY(0), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, unit)]) {
            return Err("input_rejected_or_partial");
        }
    }
    Ok(())
}
pub fn key_codes(key: &str) -> Option<Vec<VIRTUAL_KEY>> {
    let parts: Vec<_> = key.split('+').collect();
    if parts.is_empty() || parts.len() > 3 { return None; }
    let mut result = Vec::new();
    for (index, part) in parts.iter().enumerate() {
        let modifier = index + 1 < parts.len();
        let code = match part.to_ascii_lowercase().as_str() {
            "ctrl" | "control" if modifier => VK_CONTROL,
            "shift" if modifier => VK_SHIFT, "alt" if modifier => VK_MENU,
            "enter" if !modifier => VK_RETURN, "tab" if !modifier => VK_TAB, "escape" | "esc" if !modifier => VK_ESCAPE,
            "backspace" if !modifier => VK_BACK, "delete" if !modifier => VK_DELETE,
            "left" if !modifier => VK_LEFT, "right" if !modifier => VK_RIGHT, "up" if !modifier => VK_UP, "down" if !modifier => VK_DOWN,
            "home" if !modifier => VK_HOME, "end" if !modifier => VK_END,
            "pageup" if !modifier => VK_PRIOR, "pagedown" if !modifier => VK_NEXT,
            "space" if !modifier => VK_SPACE,
            _ if !modifier && part.to_ascii_uppercase().starts_with('F') && part[1..].parse::<u16>().is_ok_and(|n|(1..=12).contains(&n)) =>
                VIRTUAL_KEY(VK_F1.0 + part[1..].parse::<u16>().unwrap() - 1),
            _ if !modifier && part.len() == 1 && part.as_bytes()[0].is_ascii_alphanumeric() => VIRTUAL_KEY(part.as_bytes()[0].to_ascii_uppercase() as u16),
            _ => return None,
        };
        if result.contains(&code) { return None; } result.push(code);
    }
    Some(result)
}
pub fn press(id: &Identity, key: &str, generation: u64, stopped: &AtomicBool) -> Result<(), &'static str> {
    let codes = key_codes(key).ok_or("key_unsupported")?;
    let mut down = Vec::new(); let mut ok = true;
    for code in &codes {
        if !permitted(id, generation, stopped) { ok = false; break; }
        down.push(*code);
        if !send(&[keyboard(*code, KEYBD_EVENT_FLAGS(0), 0)]) { ok = false; break; }
    }
    // Always release just the keys this action attempted, including on stop.
    for code in down.into_iter().rev() { ok &= send(&[keyboard(code, KEYEVENTF_KEYUP, 0)]); }
    if ok { Ok(()) } else { Err("input_rejected_or_interrupted") }
}
pub fn point(id: &Identity, x: i32, y: i32, wheel: Option<i32>, generation: u64, stopped: &AtomicBool) -> Result<(), &'static str> {
    point_action(id,x,y,wheel,"left",1,generation,stopped)
}

fn button_flags(button:&str)->Result<(MOUSE_EVENT_FLAGS,MOUSE_EVENT_FLAGS),&'static str>{
    match button {"left"=>Ok((MOUSEEVENTF_LEFTDOWN,MOUSEEVENTF_LEFTUP)),
        "right"=>Ok((MOUSEEVENTF_RIGHTDOWN,MOUSEEVENTF_RIGHTUP)),
        "middle"=>Ok((MOUSEEVENTF_MIDDLEDOWN,MOUSEEVENTF_MIDDLEUP)),_=>Err("mouse_button_unsupported")}
}

struct HeldButton(Option<MOUSE_EVENT_FLAGS>);
impl HeldButton {
    fn release(&mut self)->bool {match self.0 {Some(flag)=>{if send(&[mouse(flag,0,0,0)]){self.0=None;true}else{false}},None=>true}}
}
impl Drop for HeldButton {fn drop(&mut self){let _=self.release();}}

pub fn point_available(id:&Identity,x:i32,y:i32)->Result<(),&'static str>{
    let _dpi=windows::DpiGuard::new();
    unsafe {
        if GetAncestor(WindowFromPoint(POINT{x,y}),GA_ROOT)!=windows::handle(id.hwnd){return Err("target_occluded");}
    }
    Ok(())
}

fn move_to(id:&Identity,x:i32,y:i32,generation:u64,stopped:&AtomicBool)->Result<(),&'static str>{
    let _dpi = windows::DpiGuard::new();
    if !permitted(id, generation, stopped) { return Err("input_interrupted"); }
    unsafe {
        point_available(id,x,y)?;
        let left = GetSystemMetrics(SM_XVIRTUALSCREEN); let top = GetSystemMetrics(SM_YVIRTUALSCREEN);
        let width = GetSystemMetrics(SM_CXVIRTUALSCREEN); let height = GetSystemMetrics(SM_CYVIRTUALSCREEN);
        if width < 2 || height < 2 { return Err("display_geometry_unavailable"); }
        let nx = ((x-left) as i64 * 65535 / (width-1) as i64) as i32;
        let ny = ((y-top) as i64 * 65535 / (height-1) as i64) as i32;
        if !send(&[mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny, 0)]) { return Err("input_rejected_or_partial"); }
        // Input enqueue success does not prove that the pointer reached the
        // requested physical pixel. Never click at an unverified old position.
        let mut arrived=false;
        for _ in 0..10 {
            let mut actual=POINT::default();
            if GetPhysicalCursorPos(&mut actual).is_ok() && (actual.x-x).abs()<=1 && (actual.y-y).abs()<=1 {arrived=true;break;}
            if !permitted(id,generation,stopped){return Err("input_interrupted");}
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        if !arrived {return Err("pointer_target_mismatch");}
        if !permitted(id, generation, stopped) { return Err("input_interrupted"); }
        if GetAncestor(WindowFromPoint(POINT{x,y}),GA_ROOT)!=windows::handle(id.hwnd){return Err("target_occluded");}
    }
    Ok(())
}

pub fn point_action(id:&Identity,x:i32,y:i32,wheel:Option<i32>,button:&str,count:u64,generation:u64,stopped:&AtomicBool)->Result<(),&'static str>{
    let (down_flag,up_flag)=button_flags(button)?;
    if !(1..=2).contains(&count){return Err("click_count_unsupported");}
    move_to(id,x,y,generation,stopped)?;
    if let Some(delta) = wheel {
            if !send(&[mouse(MOUSEEVENTF_WHEEL, 0, 0, delta as u32)]) { return Err("input_rejected_or_partial"); }
    } else {
        for index in 0..count {
            if index>0 {std::thread::sleep(std::time::Duration::from_millis(35));move_to(id,x,y,generation,stopped)?;}
            if !permitted(id,generation,stopped){return Err("input_interrupted");}
            let down = send(&[mouse(down_flag, 0, 0, 0)]);
            let up = send(&[mouse(up_flag, 0, 0, 0)]);
            if !down || !up { return Err("input_rejected_or_partial"); }
        }
    }
    Ok(())
}

/// Same-window drag. Every interpolation point checks scope, geometry and takeover.
/// Button-up is attempted on every exit, even if SendInput only partly succeeded.
pub fn drag(id:&Identity,from:(i32,i32),to:(i32,i32),duration:u64,generation:u64,stopped:&AtomicBool)->Result<(),&'static str>{
    if !(100..=2000).contains(&duration){return Err("drag_duration_unsupported");}
    let geometry=windows::geometry(id)?;
    move_to(id,from.0,from.1,generation,stopped)?;
    let mut held=HeldButton(Some(MOUSEEVENTF_LEFTUP));
    if !send(&[mouse(MOUSEEVENTF_LEFTDOWN,0,0,0)]){return Err("input_rejected_or_partial");}
    let segments=(duration/20).max(5);
    for index in 1..=segments {
        std::thread::sleep(std::time::Duration::from_millis(duration/segments));
        if windows::geometry(id).as_ref()!=Ok(&geometry){return Err("window_changed_during_drag");}
        let x=from.0+(i64::from(to.0-from.0)*index as i64/segments as i64) as i32;
        let y=from.1+(i64::from(to.1-from.1)*index as i64/segments as i64) as i32;
        move_to(id,x,y,generation,stopped)?;
    }
    if !held.release(){return Err("input_release_unconfirmed");}
    Ok(())
}
