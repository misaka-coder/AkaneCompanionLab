//! Real SendInput/WGC acceptance in a disposable window with no UIA editor.
use super::{session,protocol,monitor};
use serde_json::{json,Value};
use std::{sync::{Mutex,mpsc,atomic::{AtomicUsize,AtomicIsize,Ordering}},time::{Duration,Instant}};
use windows::{core::w,Win32::{Foundation::{HWND,LPARAM,LRESULT,WPARAM},Graphics::Gdi::{GetSysColorBrush,COLOR_WINDOW},
    System::{LibraryLoader::GetModuleHandleW,Threading::GetCurrentThreadId},UI::WindowsAndMessaging::*}};

static TEXT:Mutex<String>=Mutex::new(String::new());
static ENTERS:AtomicUsize=AtomicUsize::new(0);
static BLOCKER:AtomicIsize=AtomicIsize::new(0);
unsafe extern "system" fn procedure(hwnd:HWND,message:u32,w:WPARAM,l:LPARAM)->LRESULT{
    if message==WM_APP+1 {
        let mut rect=windows::Win32::Foundation::RECT::default();let _=GetWindowRect(hwnd,&mut rect);
        let blocker=CreateWindowExW(WS_EX_TOPMOST|WS_EX_NOACTIVATE,w!("STATIC"),w!("Akane Visual Occluder"),WS_OVERLAPPEDWINDOW,
            rect.left+20,rect.top+50,260,180,None,None,None,None).unwrap();
        let _=ShowWindow(blocker,SW_SHOWNOACTIVATE);BLOCKER.store(blocker.0 as isize,Ordering::SeqCst);return LRESULT(0);
    }
    if message==WM_APP+2 {
        let blocker=BLOCKER.swap(0,Ordering::SeqCst);if blocker!=0{let _=DestroyWindow(HWND(blocker as *mut _));}return LRESULT(0);
    }
    if message==WM_CHAR {
        match w.0 {
            1=>TEXT.lock().unwrap().clear(),13=>{ENTERS.fetch_add(1,Ordering::SeqCst);},
            _=>{if let Some(ch)=char::from_u32(w.0 as u32){TEXT.lock().unwrap().push(ch);}},
        }
        return LRESULT(0);
    }
    if message==WM_LBUTTONDOWN{let _=windows::Win32::UI::Input::KeyboardAndMouse::SetFocus(Some(hwnd));}
    DefWindowProcW(hwnd,message,w,l)
}
struct Fixture{thread:u32,window:isize,worker:Option<std::thread::JoinHandle<()>>}
impl Drop for Fixture{fn drop(&mut self){
    session::configure(false);
    unsafe{let _=PostThreadMessageW(self.thread,WM_QUIT,WPARAM(0),LPARAM(0));}
    if let Some(worker)=self.worker.take(){let _=worker.join();}
}}
fn fixture()->Fixture{
    let(tx,rx)=mpsc::sync_channel(1);
    let worker=std::thread::spawn(move||unsafe{
        let instance=GetModuleHandleW(None).unwrap();
        let class=WNDCLASSW{lpfnWndProc:Some(procedure),hInstance:instance.into(),hbrBackground:GetSysColorBrush(COLOR_WINDOW),lpszClassName:w!("AkaneVisualFixture"),..Default::default()};
        assert_ne!(RegisterClassW(&class),0);
        let hwnd=CreateWindowExW(WS_EX_TOPMOST,w!("AkaneVisualFixture"),w!("Akane Visual Batch Fixture"),WS_OVERLAPPEDWINDOW|WS_VISIBLE,
            240,180,520,280,None,None,Some(instance.into()),None).unwrap();
        let _=SetForegroundWindow(hwnd);let _=windows::Win32::UI::Input::KeyboardAndMouse::SetFocus(Some(hwnd));
        tx.send((GetCurrentThreadId(),hwnd.0 as isize)).unwrap();let mut msg=MSG::default();
        while GetMessageW(&mut msg,None,0,0).as_bool(){let _=TranslateMessage(&msg);DispatchMessageW(&msg);}
        let blocker=BLOCKER.swap(0,Ordering::SeqCst);if blocker!=0{let _=DestroyWindow(HWND(blocker as *mut _));}let _=DestroyWindow(hwnd);
    });
    let(thread,window)=rx.recv_timeout(Duration::from_secs(5)).unwrap();Fixture{thread,window,worker:Some(worker)}
}
fn call(id:&str,args:Value)->Value{invoke(id,args,"trusted_auto_allow","","")}
fn invoke(id:&str,mut args:Value,mode:&str,phase:&str,binding:&str)->Value{
    if args.get("context_ref").is_none(){args["device_epoch"]=json!("visual-fixture");}
    if matches!(args["action"].as_str(),Some("list_windows"|"status")){args.as_object_mut().unwrap().remove("device_epoch");}
    assert!(protocol::valid(args.as_object().unwrap()),"public arguments: {args}");
    args["_device_epoch"]=json!("visual-fixture");args["_control_scope"]=json!("v".repeat(64));args["_permission_mode"]=json!(mode);
    if !phase.is_empty(){args["_phase"]=json!(phase);args["_approval_binding"]=json!(binding);}
    session::execute(id,&args)
}
fn observed(id:&str,window:&Value)->Value{
    let out=call(id,json!({"action":"select_window","window_id":window,"mode":"visual"}));
    assert_eq!(out["ok"],true,"{}",out["reason"]);assert!(!out["screenshots"].as_array().unwrap().is_empty());out
}
fn batch(observation:&Value,actions:Value)->Value{
    if std::env::var("AKANE_CU_CONTEXT_TEST").as_deref()==Ok("1") {
        assert!(observation["context_ref"].is_string(),"device must return a real context binding");
        return json!({"action":"run_actions","context_ref":observation["context_ref"],"actions":actions});
    }
    json!({"action":"run_actions","control_session_id":observation["control_session_id"],"observation_id":observation["observation_id"],
        "screenshot_id":observation["screenshots"][0]["screenshot_id"],"actions":actions})
}

#[test]
#[ignore="creates and controls only its own disposable native window; requires interactive desktop"]
fn live_visual_batch_and_window_management(){
    assert_eq!(std::env::var("AKANE_CU_VISUAL_TEST").as_deref(),Ok("1"));
    let fixture=fixture();
    if std::env::var("AKANE_CU_VISUAL_WAIT_FOR_FOCUS").as_deref()==Ok("1") {
        println!("READY: activate Akane Visual Batch Fixture; the test controls only this window.");
        let start=Instant::now();
        while unsafe{GetForegroundWindow()}.0 as isize!=fixture.window {
            assert!(start.elapsed()<Duration::from_secs(90),"fixture was not activated");
            std::thread::sleep(Duration::from_millis(20));
        }
        std::thread::sleep(Duration::from_millis(700));
    }
    // Let initial WM_PAINT/compositor animation settle; the production patch
    // guard must still reject real changes rather than weakening it for a test.
    std::thread::sleep(Duration::from_millis(350));
    crate::control_lease::begin_connection("visual-fixture");session::configure(true);session::resume_local();
    let list=call("list",json!({"action":"list_windows","query":"Akane Visual Batch Fixture"}));
    assert_eq!(list["windows"].as_array().unwrap().len(),1);let window=list["windows"][0]["window_id"].clone();
    let observation=observed("select",&window);
    assert_eq!(observation["ax_status"],"unavailable");assert_eq!(observation["elements"],json!([]));
    session::age_observation_for_test(20_000);
    let request=batch(&observation,json!([{"action":"click","x":100,"y":100},{"action":"press_key","key":"Ctrl+A"},
        {"action":"type_text","text":"晴天 batch fixture"},{"action":"press_key","key":"Enter"}]));
    let started=Instant::now();let out=call("batch-once",request.clone());
    if out["workflow_state"]!="completed" {
        use base64::Engine;
        for(name,value)in [("before",&observation),("after",&out)]{
            if let Some(encoded)=value["screenshots"][0]["imageBase64"].as_str(){
                let path=std::env::temp_dir().join(format!("akane-visual-{}-{name}.png",std::process::id()));
                std::fs::write(&path,base64::engine::general_purpose::STANDARD.decode(encoded).unwrap()).unwrap();println!("Fixture diagnostic: {}",path.display());
            }
        }
        println!("Stopped at step {}: {}",out["next_step"],out["reason"]);
    }
    assert_eq!(out["workflow_state"],"completed","{}",out["reason"]);assert_eq!(out["ok"],true,"{}",out["reason"]);
    assert_eq!(out["completed_steps"],4);assert_eq!(out["task_verified"],false);assert_eq!(out["screenshots"].as_array().unwrap().len(),1);
    assert_eq!(&*TEXT.lock().unwrap(),"晴天 batch fixture");assert_eq!(ENTERS.load(Ordering::SeqCst),1);
    let replay=call("batch-once",request.clone());assert_eq!(replay["completed_steps"],4);assert_eq!(ENTERS.load(Ordering::SeqCst),1);
    println!("Visual click/select-all/type/Enter: {} ms; 4 ordered inputs, one final image, no UIA editor or duplicate Enter",started.elapsed().as_millis());

    let minimized=call("minimize",json!({"action":"manage_window","window_id":window,"window_action":"minimize"}));
    assert_eq!(minimized["ok"],true,"{}",minimized["reason"]);assert_eq!(minimized["window_state"]["minimized"],true);
    monitor::simulate_user_activity();
    let recovered=call("reconcile-minimized",json!({"action":"select_window","window_id":window,"mode":"visual"}));
    assert_eq!(recovered["ok"],true,"{}",recovered["reason"]);assert_eq!(recovered["next_action"],"select_window");
    let _restored=observed("restore-select",&window);
    let moved=call("move",json!({"action":"manage_window","window_id":window,"window_action":"move","desktop_x":330,"desktop_y":220}));
    assert_eq!(moved["ok"],true,"{}",moved["reason"]);
    let current=observed("after-move",&window);
    let stale=call("stale-plan",batch(&out,json!([{"action":"press_key","key":"Enter"}])));
    assert_eq!(stale["reason"],"observation_expired");assert_eq!(ENTERS.load(Ordering::SeqCst),1);
    session::stop_local();
    let stopped=call("stopped-plan",batch(&current,json!([{"action":"press_key","key":"Enter"}])));
    assert!(matches!(stopped["reason"].as_str(),Some("stopped_requires_local_resume"|"stopped")));
    assert_eq!(ENTERS.load(Ordering::SeqCst),1);
    println!("Verified minimize, quiet takeover recovery of minimized window, restore, move, stale evidence and explicit stop.");

    session::resume_local();let observation=observed("for-occlusion",&window);
    unsafe{SendMessageW(HWND(fixture.window as *mut _),WM_APP+1,None,None);}
    let blocked=call("occluded",batch(&observation,json!([{"action":"click","x":100,"y":100},{"action":"press_key","key":"Enter"}])));
    assert_eq!(blocked["reason"],"target_occluded");assert_eq!(blocked["action_state"],"not_started");
    assert_eq!(blocked["occluder"]["title"],"Akane Visual Occluder");assert!(blocked["occluder"]["window_id"].is_string());
    assert_eq!(blocked["steps"][1]["action_state"],"not_started");assert_eq!(ENTERS.load(Ordering::SeqCst),1);
    let cleared=call("clear-occlusion",json!({"action":"manage_window","window_id":blocked["occluder"]["window_id"],"window_action":"minimize"}));
    assert_eq!(cleared["ok"],true,"{}",cleared["reason"]);
    unsafe{SendMessageW(HWND(fixture.window as *mut _),WM_APP+2,None,None);}
    let observation=observed("for-approval",&window);
    let mut approved=invoke("ask-batch",batch(&observation,json!([{"action":"click","x":100,"y":100},{"action":"press_key","key":"Enter"}])),"ask_each_time","","");
    for step in 0..2 {
        assert_eq!(approved["workflow_state"],"awaiting_approval","{}",approved["reason"]);
        let request=json!({"action":"resume_steps","workflow_id":approved["workflow_id"],"control_session_id":approved["control_session_id"],"observation_id":approved["observation_id"]});
        let prepared=invoke(&format!("ask-prepare-{step}"),request.clone(),"ask_each_time","prepare","");
        assert_eq!(prepared["ok"],true,"{}",prepared["reason"]);assert_eq!(prepared["workflow_kind"],"visual_actions");
        approved=invoke(&format!("ask-execute-{step}"),request,"ask_each_time","execute",prepared["authorization"]["binding"].as_str().unwrap());
    }
    assert_eq!(approved["workflow_state"],"completed","{}",approved["reason"]);assert_eq!(ENTERS.load(Ordering::SeqCst),2);
    println!("Verified real occluder discovery/clearance and exact approval resume without preflight consuming the model observation.");

    // Reproduce master -> QQ group after idle, without touching any personal app.
    let other=|id:&str,mut args:Value|{
        args["_control_scope"]=json!("g".repeat(64));args["_device_epoch"]=json!("visual-fixture");
        if args.get("context_ref").is_none(){args["device_epoch"]=json!("visual-fixture");}args["_permission_mode"]=json!("trusted_auto_allow");
        session::execute(id,&args)
    };
    let listed=other("group-list",json!({"action":"list_windows","query":"Akane Visual Batch Fixture"}));
    let next_window=listed["windows"][0]["window_id"].clone();
    let selection=json!({"action":"select_window","window_id":next_window,"mode":"visual"});
    let busy=other("group-active-owner",selection.clone());
    assert_eq!(busy["reason"],"desktop_owned_by_other_session");assert!(busy["retry_after_ms"].as_u64().unwrap()>0);
    crate::control_lease::expire_idle_for_test();
    let selected=other("group-idle-owner",selection);
    assert_eq!(selected["ok"],true,"{}",selected["reason"]);
    assert_ne!(selected["control_session_id"],approved["control_session_id"]);
    let old=call("master-stale",batch(&approved,json!([{"action":"press_key","key":"Enter"}])));
    assert_eq!(old["reason"],"desktop_owned_by_other_session");assert_eq!(ENTERS.load(Ordering::SeqCst),2);
    let old_observe=call("master-stale-observe",json!({"action":"observe","control_session_id":approved["control_session_id"],"mode":"visual"}));
    assert_eq!(old_observe["reason"],"control_session_mismatch");
    let input=other("group-input",batch(&selected,json!([{"action":"type_text","text":" group resumed"}])));
    assert_eq!(input["workflow_state"],"completed","{}",input["reason"]);
    assert!(TEXT.lock().unwrap().ends_with(" group resumed"));assert_eq!(ENTERS.load(Ordering::SeqCst),2);
    println!("Verified active-owner rejection, idle cross-session selection, fresh input and stale old-session rejection without duplicate Enter.");

    session::age_observation_for_test(20_000);
    let clicked=other("aged-single-click",json!({"action":"click","control_session_id":input["control_session_id"],
        "observation_id":input["observation_id"],"screenshot_id":input["screenshots"][0]["screenshot_id"],"x":100,"y":100}));
    assert_eq!(clicked["ok"],true,"{}",clicked["reason"]);
    session::age_observation_for_test(20_000);
    let typed=other("aged-visual-type",json!({"action":"type_text","control_session_id":clicked["control_session_id"],
        "observation_id":clicked["observation_id"],"text":" verified"}));
    assert_eq!(typed["ok"],true,"{}",typed["reason"]);assert!(TEXT.lock().unwrap().ends_with(" verified"));
    session::age_observation_for_test(61_000);
    let expired=other("expired-input",json!({"action":"press_key","control_session_id":typed["control_session_id"],
        "observation_id":typed["observation_id"],"key":"Enter"}));
    assert_eq!(expired["reason"],"observation_expired");assert_eq!(ENTERS.load(Ordering::SeqCst),2);
    let observation=other("before-scene-change",json!({"action":"select_window","window_id":next_window,"mode":"visual"}));
    assert_eq!(observation["ok"],true);session::age_observation_for_test(20_000);
    unsafe{SetWindowTextW(HWND(fixture.window as *mut _),w!("Changed scene: reject the old plan")).unwrap();}
    std::thread::sleep(Duration::from_millis(150));
    let changed=other("changed-scene",batch(&observation,json!([{"action":"press_key","key":"Enter"}])));
    assert_eq!(changed["reason"],"visual_scene_changed");assert_eq!(changed["action_state"],"not_started");assert_eq!(ENTERS.load(Ordering::SeqCst),2);
    println!("Verified 20-second visual batch/click/type with scene revalidation, rejection of a changed scene, and the 60-second hard expiry.");
    if request.get("context_ref").is_some() {
        session::clear_contexts_for_test();
        let replay=call("batch-once",request.clone());
        assert_eq!(replay["completed_steps"],4);assert_eq!(ENTERS.load(Ordering::SeqCst),2);
        let fresh=call("lost-context-new-invocation",request);
        assert_eq!(fresh["reason"],"context_reference_expired");assert_eq!(fresh["action_state"],"not_started");
        assert_eq!(ENTERS.load(Ordering::SeqCst),2);
        println!("Lost aliases do not replay retained invocations or redirect a new request.");
    }
}
