//! Opt-in acceptance bridge for a real text model, restricted to its own EDIT.
//! Test-only stdin protocol; never compiled into the product executable.
use serde_json::{json,Value};
use std::{io::{self,BufRead,Write},sync::mpsc,time::Duration};
use windows::{core::w,Win32::{Foundation::{HWND,LPARAM,WPARAM},System::Threading::GetCurrentThreadId,
    UI::WindowsAndMessaging::*}};
use super::{protocol,session};

#[test]
#[ignore="explicit model acceptance; opens only its own disposable edit window"]
fn local_model_fixture(){
    assert_eq!(std::env::var("AKANE_CU_MODEL_TEST").as_deref(),Ok("1"));
    let (tx,rx)=mpsc::sync_channel(1);
    let worker=std::thread::spawn(move||unsafe{
        let hwnd=CreateWindowExW(WS_EX_TOPMOST,w!("STATIC"),w!("Akane Model Fixture"),WS_OVERLAPPEDWINDOW|WS_VISIBLE,
            240,160,560,260,None,None,None,None).unwrap();
        let edit=CreateWindowExW(WS_EX_CLIENTEDGE,w!("EDIT"),w!(""),WS_CHILD|WS_VISIBLE|WS_TABSTOP,
            20,40,460,40,Some(hwnd),None,None,None).unwrap();
        let _=SetForegroundWindow(hwnd);let _=windows::Win32::UI::Input::KeyboardAndMouse::SetFocus(Some(edit));
        tx.send((GetCurrentThreadId(),edit.0 as isize)).unwrap();
        let mut msg=MSG::default();while GetMessageW(&mut msg,None,0,0).as_bool(){let _=TranslateMessage(&msg);DispatchMessageW(&msg);}
        let _=DestroyWindow(hwnd);
    });
    let(thread,edit)=rx.recv_timeout(Duration::from_secs(5)).unwrap();
    struct Cleanup(u32);impl Drop for Cleanup{fn drop(&mut self){session::configure(false);unsafe{let _=PostThreadMessageW(self.0,WM_QUIT,WPARAM(0),LPARAM(0));}}}
    let cleanup=Cleanup(thread);
    std::thread::sleep(Duration::from_millis(350));
    crate::control_lease::begin_connection("model-fixture");session::configure(true);session::resume_local();
    let mut serial=0;
    let mut call=|mut args:Value|{
        serial+=1;
        args["_device_epoch"]=json!("model-fixture");args["_control_scope"]=json!("m".repeat(64));
        args["_permission_mode"]=json!("trusted_auto_allow");
        session::execute(&format!("model-eval-{serial}"),&args)
    };
    let listed=call(json!({"action":"list_windows","query":"Akane Model Fixture"}));
    assert_eq!(listed["windows"].as_array().unwrap().len(),1);
    let window=listed["windows"][0]["window_id"].clone();
    let initial=call(json!({"action":"select_window","window_id":window,"device_epoch":"model-fixture","mode":"text"}));
    if initial["ok"]!=true {
        println!("AKANE_MODEL_EVAL {}",json!({"fixture_error":initial["reason"]}));
        let _=io::stdout().flush();drop(cleanup);worker.join().unwrap();return;
    }
    emit(initial,edit);
    for line in io::stdin().lock().lines(){
        let line=line.unwrap();if line=="quit"{break;}
        let args:Value=serde_json::from_str(&line).unwrap();
        // Test authorization is confined to this fixture. No launching, window
        // management, arbitrary hotkeys or selecting other personal windows.
        let action=protocol::string(&args,"action");
        let permitted=matches!(action,"observe"|"click"|"type_text"|"press_key"|"set_value"|"run_steps"|"status")
            && safe_keys(&args) && protocol::valid(args.as_object().unwrap());
        let out=if permitted{call(args)}else{protocol::fail("fixture_action_not_allowed")};
        emit(out,edit);
    }
    drop(cleanup);worker.join().unwrap();
}
fn safe_keys(args:&Value)->bool{
    if args.get("key").is_some()&&!matches!(protocol::string(args,"key"),"Ctrl+A"|"Enter"|"Tab"|"Backspace"|"Home"|"End"){return false;}
    if let Some(steps)=args["steps"].as_array(){
        return steps.iter().all(|s|matches!(protocol::string(s,"action"),"click"|"type_text"|"press_key"|"set_value"|"wait_for")&&safe_keys(s));
    }
    true
}
fn emit(mut out:Value,edit:isize){
    if let Some(shots)=out["screenshots"].as_array_mut(){for shot in shots{shot.as_object_mut().unwrap().remove("imageBase64");}}
    let value=unsafe{let mut chars=[0u16;4096];let len=GetWindowTextW(HWND(edit as *mut _),&mut chars);String::from_utf16_lossy(&chars[..len as usize])};
    println!("AKANE_MODEL_EVAL {}",json!({"result":out,"fixture_value":value}));let _=io::stdout().flush();
}
