//! Opt-in acceptance against a disposable native fixture, never personal apps.
use super::{session,protocol};
use serde_json::{json,Value};
use std::sync::{atomic::{AtomicUsize,AtomicIsize,Ordering},mpsc};
use std::time::Duration;
use ::windows::{core::w,Win32::{Foundation::{HWND,LPARAM,LRESULT,WPARAM,POINT},Graphics::Gdi::{GetSysColorBrush,COLOR_WINDOW},
    System::{LibraryLoader::GetModuleHandleW,Threading::GetCurrentThreadId},
    UI::{WindowsAndMessaging::*,Input::KeyboardAndMouse::{GetAsyncKeyState,VK_LBUTTON}}}};

static RIGHT:AtomicUsize=AtomicUsize::new(0);
static DOUBLE:AtomicUsize=AtomicUsize::new(0);
static DRAG_MOVES:AtomicUsize=AtomicUsize::new(0);
static POPUP:AtomicIsize=AtomicIsize::new(0);
unsafe extern "system" fn procedure(hwnd:HWND,message:u32,w:WPARAM,l:LPARAM)->LRESULT{
    if message==WM_CLOSE {if POPUP.load(Ordering::SeqCst)==hwnd.0 as isize{POPUP.store(0,Ordering::SeqCst);}let _=DestroyWindow(hwnd);return LRESULT(0);}
    match message {
        WM_RBUTTONDOWN=>{RIGHT.fetch_add(1,Ordering::SeqCst);},
        WM_LBUTTONDBLCLK=>{DOUBLE.fetch_add(1,Ordering::SeqCst);},
        WM_MOUSEMOVE if w.0&1!=0=>{DRAG_MOVES.fetch_add(1,Ordering::SeqCst);},_=>{}
    }
    if message==WM_COMMAND&&w.0&0xffff==103 {
        let instance=GetModuleHandleW(None).unwrap();
        let popup=CreateWindowExW(WINDOW_EX_STYLE(0),w!("AkaneEnhancedFixture"),w!("Akane Flow Dialog"),WS_OVERLAPPEDWINDOW|WS_VISIBLE,
            240,200,480,240,Some(hwnd),None,Some(instance.into()),None).unwrap();
        let edit=CreateWindowExW(WS_EX_CLIENTEDGE,w!("EDIT"),w!(""),WS_CHILD|WS_VISIBLE|WS_TABSTOP,20,20,400,40,Some(popup),None,Some(instance.into()),None).unwrap();
        POPUP.store(popup.0 as isize,Ordering::SeqCst);
        let _=SetForegroundWindow(popup);let _=::windows::Win32::UI::Input::KeyboardAndMouse::SetFocus(Some(edit));
    }
    DefWindowProcW(hwnd,message,w,l)
}

#[test]
#[ignore="needs an explicit hands-free interval; creates only its own disposable native controls"]
fn live_enhanced_workflow_and_mouse(){
    assert_eq!(std::env::var("AKANE_CU_ENHANCED_TEST").as_deref(),Ok("1"));
    let (tx,rx)=mpsc::sync_channel(1);
    let worker=std::thread::spawn(move||unsafe{
        let instance=GetModuleHandleW(None).unwrap();
        let class=WNDCLASSW{style:CS_DBLCLKS,lpfnWndProc:Some(procedure),hInstance:instance.into(),hbrBackground:GetSysColorBrush(COLOR_WINDOW),lpszClassName:w!("AkaneEnhancedFixture"),..Default::default()};
        assert_ne!(RegisterClassW(&class),0);
        let hwnd=CreateWindowExW(WS_EX_TOPMOST,w!("AkaneEnhancedFixture"),w!("Akane Enhanced Acceptance"),WS_OVERLAPPEDWINDOW|WS_VISIBLE,
            180,140,660,400,None,None,Some(instance.into()),None).unwrap();
        let edit=CreateWindowExW(WS_EX_CLIENTEDGE,w!("EDIT"),w!(""),WS_CHILD|WS_VISIBLE|WS_TABSTOP,20,20,580,40,Some(hwnd),Some(HMENU(101usize as *mut _)),Some(instance.into()),None).unwrap();
        CreateWindowExW(WINDOW_EX_STYLE(0),w!("BUTTON"),w!("Acceptance option"),WS_CHILD|WS_VISIBLE|WS_TABSTOP|WINDOW_STYLE(BS_AUTOCHECKBOX as u32),
            20,80,260,30,Some(hwnd),Some(HMENU(102usize as *mut _)),Some(instance.into()),None).unwrap();
        CreateWindowExW(WINDOW_EX_STYLE(0),w!("BUTTON"),w!("Open dialog"),WS_CHILD|WS_VISIBLE|WS_TABSTOP,
            300,80,200,30,Some(hwnd),Some(HMENU(103usize as *mut _)),Some(instance.into()),None).unwrap();
        let _=SetForegroundWindow(hwnd);let _=::windows::Win32::UI::Input::KeyboardAndMouse::SetFocus(Some(edit));
        tx.send((GetCurrentThreadId(),edit.0 as isize)).unwrap();
        let mut msg=MSG::default();while GetMessageW(&mut msg,None,0,0).0>0{let _=TranslateMessage(&msg);DispatchMessageW(&msg);}
        let _=DestroyWindow(hwnd);
    });
    let (thread,edit)=rx.recv_timeout(Duration::from_secs(5)).unwrap();
    struct Cleanup(u32);impl Drop for Cleanup{fn drop(&mut self){session::configure(false);unsafe{let _=PostThreadMessageW(self.0,WM_QUIT,WPARAM(0),LPARAM(0));}}}
    let cleanup=Cleanup(thread);session::configure(true);session::resume_local();
    let mut number=0;
    let mut call=|mut args:Value|{number+=1;args["_control_scope"]=json!("f".repeat(64));args["_device_epoch"]=json!("enhanced-fixture");session::execute(&format!("enhanced-{number}"),&args)};
    let listed=call(json!({"action":"list_windows","query":"Akane Enhanced Acceptance"}));
    assert_eq!(listed["windows"].as_array().unwrap().len(),1);
    let mut observed=call(json!({"action":"select_window","window_id":listed["windows"][0]["window_id"],"device_epoch":"enhanced-fixture","mode":"hybrid"}));
    assert_eq!(observed["ok"],true,"{}",observed["reason"]);
    let steps=json!([
        {"action":"click","target":{"role":50004},"expect":{"kind":"focused","target":{"role":50004}}},
        {"action":"set_value","target":{"role":50004,"focused":true},"text":"Akane 连续步骤验收",
            "expect":{"kind":"value_equals","target":{"role":50004,"focused":true},"value":"Akane 连续步骤验收"}},
        {"action":"set_checked","target":{"name":"Acceptance option","role":50002},"checked":true,
            "expect":{"kind":"checked_equals","target":{"name":"Acceptance option","role":50002},"checked":true}},
        {"action":"set_checked","target":{"name":"Acceptance option","role":50002},"checked":true,
            "expect":{"kind":"checked_equals","target":{"name":"Acceptance option","role":50002},"checked":true}},
        {"action":"click","target":{"name":"Open dialog","role":50000},"expect":{"kind":"window_title","title":"Akane Flow Dialog"}},
        {"action":"set_value","target":{"role":50004,"focused":true},"text":"对话框内容",
            "expect":{"kind":"value_equals","target":{"role":50004,"focused":true},"value":"对话框内容"}},
        {"action":"press_key","key":"Alt+F4","expect":{"kind":"window_title","title":"Akane Enhanced Acceptance"}},
        {"action":"select_window","window_id":listed["windows"][0]["window_id"],"expect":{"kind":"window_title","title":"Akane Enhanced Acceptance"}}
    ]);
    let run=json!({"action":"run_steps","device_epoch":"enhanced-fixture","control_session_id":observed["control_session_id"],"observation_id":observed["observation_id"],"steps":steps});
    assert!(protocol::valid(run.as_object().unwrap()));observed=call(run);
    for _ in 0..9 {
        if observed["workflow_state"]=="completed"{break;}
        assert_eq!(observed["workflow_state"],"awaiting_approval","{}",observed["reason"]);
        let resume=json!({"action":"resume_steps","device_epoch":"enhanced-fixture","control_session_id":observed["control_session_id"],
            "observation_id":observed["observation_id"],"workflow_id":observed["workflow_id"],"_phase":"prepare"});
        let prepared=call(resume.clone());assert_eq!(prepared["ok"],true,"{}",prepared["reason"]);
        let mut approved=resume;approved["_phase"]=json!("execute");approved["_approval_binding"]=prepared["authorization"]["binding"].clone();observed=call(approved);
    }
    assert_eq!(observed["workflow_state"],"completed","{}",observed["reason"]);
    assert_eq!(observed["completed_steps"],8);assert_eq!(observed["steps"][3]["state"],"skipped");
    assert_eq!(POPUP.load(Ordering::SeqCst),0,"foreground returning to the owner alone does not prove a popup closed");
    unsafe {let mut value=[0u16;128];let count=GetWindowTextW(HWND(edit as *mut _),&mut value);assert_eq!(String::from_utf16_lossy(&value[..count as usize]),"Akane 连续步骤验收");}

    for (button,count) in [("right",1),("left",2)] {
        let left=observed["client_bounds"]["x"].as_i64().unwrap()-observed["window_bounds"]["x"].as_i64().unwrap();
        let top=observed["client_bounds"]["y"].as_i64().unwrap()-observed["window_bounds"]["y"].as_i64().unwrap();
        let args=json!({"action":"click","device_epoch":"enhanced-fixture","control_session_id":observed["control_session_id"],
            "observation_id":observed["observation_id"],"screenshot_id":observed["screenshots"][0]["screenshot_id"],
            "x":left+80,"y":top+200,"button":button,"click_count":count,"_phase":"prepare"});
        let prepared=call(args.clone());assert_eq!(prepared["ok"],true,"{}",prepared["reason"]);
        unsafe {
            let _dpi=super::windows::DpiGuard::new();let point=&prepared["authorization"]["preview"]["point"];
            let hit=WindowFromPoint(POINT{x:point[0].as_i64().unwrap() as i32,y:point[1].as_i64().unwrap() as i32});let mut class=[0u16;256];let len=GetClassNameW(hit,&mut class);
            println!("Mouse target: point={}, bounds={}, client={}, hit_class={}, hit_root={:?}, foreground={:?}",point,observed["window_bounds"],observed["client_bounds"],String::from_utf16_lossy(&class[..len as usize]),GetAncestor(hit,GA_ROOT),GetForegroundWindow());
        }
        let mut approved=args;approved["_phase"]=json!("execute");approved["_approval_binding"]=prepared["authorization"]["binding"].clone();observed=call(approved);
        if observed["action_state"]!="executed" {
            let _dpi=super::windows::DpiGuard::new();
            unsafe {let mut p=POINT::default();let _=GetPhysicalCursorPos(&mut p);let hit=WindowFromPoint(p);let mut class=[0u16;256];let len=GetClassNameW(hit,&mut class);
                println!("mouse fixture failure: button={button} count={count} actual=({},{}), hit_class={}, root={:?}, foreground={:?}, right_count={}, double_count={}",p.x,p.y,String::from_utf16_lossy(&class[..len as usize]),GetAncestor(hit,GA_ROOT),GetForegroundWindow(),RIGHT.load(Ordering::SeqCst),DOUBLE.load(Ordering::SeqCst));}
        }
        assert_eq!(observed["action_state"],"executed","{}",observed["reason"]);
    }
    assert_eq!(RIGHT.load(Ordering::SeqCst),1);assert_eq!(DOUBLE.load(Ordering::SeqCst),1);
    for interrupt in [false,true]{
        let args=json!({"action":"drag","device_epoch":"enhanced-fixture","control_session_id":observed["control_session_id"],
            "observation_id":observed["observation_id"],"screenshot_id":observed["screenshots"][0]["screenshot_id"],"x":100,"y":260,"to_x":350,"to_y":260,"duration_ms":600,"_phase":"prepare"});
        let prepared=call(args.clone());assert_eq!(prepared["ok"],true,"{}",prepared["reason"]);
        let stop=if interrupt{Some(std::thread::spawn(||{
            for _ in 0..600 {if unsafe{GetAsyncKeyState(VK_LBUTTON.0 as i32) as u16&0x8000!=0}{session::stop_local();return true;}std::thread::sleep(Duration::from_millis(5));}false
        }))}else{None};
        let mut approved=args;approved["_phase"]=json!("execute");approved["_approval_binding"]=prepared["authorization"]["binding"].clone();let out=call(approved);
        if let Some(stop)=stop{assert!(stop.join().unwrap());assert_eq!(out["action_state"],"unknown");}else{assert_eq!(out["action_state"],"executed","{}",out["reason"]);}
        unsafe{assert_eq!(GetAsyncKeyState(VK_LBUTTON.0 as i32) as u16&0x8000,0,"test must never leave mouse held");}
        observed=out;
    }
    assert!(DRAG_MOVES.load(Ordering::SeqCst)>0);
    println!("Native workflow: exact approvals, set_value, checked-state idempotence; right click, double click, drag, stop and button release verified.");
    drop(cleanup);worker.join().unwrap();
}
