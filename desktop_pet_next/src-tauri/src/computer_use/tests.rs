use super::{input, protocol, session};
use serde_json::{json, Value};

#[test]
#[ignore="owner-requested QQ restore/foreground acceptance; never types or sends"]
fn live_qq_activate() {
    assert_eq!(std::env::var("AKANE_QQ_ACTIVATE_TEST").as_deref(),Ok("1"));
    struct Cleanup;impl Drop for Cleanup{fn drop(&mut self){session::configure(false);}}
    let _cleanup=Cleanup;session::configure(true);session::resume_local();
    let scope="q".repeat(64);let epoch="qq-activate-test";
    let listed=session::execute("qq-list",&json!({"action":"list_windows","_control_scope":scope,"_device_epoch":epoch,"limit":50}));
    let row=listed["windows"].as_array().unwrap().iter().find(|w|w["application"]=="qq.exe"||w["application"]=="qqnt.exe").expect("QQ window");
    let out=session::execute("qq-select",&json!({"action":"select_window","window_id":row["window_id"],"device_epoch":epoch,"_control_scope":scope,"_device_epoch":epoch,"mode":"hybrid"}));
    assert_eq!(out["ok"],true,"{}",out["reason"]);assert_eq!(out["foreground_matches"],true);
    println!("QQ restored/activated through select_window; foreground and WGC/UIA verified; no click, typing or send");
}

#[test]
#[ignore="requires owner-designated QQ group and explicit draft-only permission"]
fn live_qq_draft_only() {
    assert_eq!(std::env::var("AKANE_QQ_DRAFT_TEST").as_deref(),Ok("1"));
    let chat=std::env::var("AKANE_QQ_TEST_CHAT").expect("owner-designated visible conversation");
    let scope="q".repeat(64);let epoch="qq-draft-test";
    struct Cleanup;impl Drop for Cleanup{fn drop(&mut self){session::configure(false);}}
    let _cleanup=Cleanup;session::configure(true);session::resume_local();
    let mut number=0;let mut call=|mut args:Value|{number+=1;args["_control_scope"]=json!(scope);args["_device_epoch"]=json!(epoch);args["device_epoch"]=json!(epoch);session::execute(&format!("qq-draft-{number}"),&args)};
    let listed=call(json!({"action":"list_windows","limit":50}));
    let row=listed["windows"].as_array().unwrap().iter().find(|w|w["application"]=="qq.exe"||w["application"]=="qqnt.exe").expect("QQ window");
    let mut observed=call(json!({"action":"select_window","window_id":row["window_id"],"mode":"hybrid"}));
    assert_eq!(observed["ok"],true,"{}",observed["reason"]);
    let header_bottom=observed["window_bounds"]["y"].as_i64().unwrap()+200;
    assert!(observed["elements"].as_array().unwrap().iter().any(|e|e["name"].as_str().is_some_and(|name|name.starts_with(&chat))&&e["bounds"]["y"].as_i64().is_some_and(|y|y<header_bottom)),"Designated conversation header was not verified");
    if observed["focus_evidence"]["editable"]!=true {
        let lower=observed["window_bounds"]["y"].as_i64().unwrap()+observed["window_bounds"]["height"].as_i64().unwrap()/2;
        let editors:Vec<_>=observed["elements"].as_array().unwrap().iter().filter(|e|e["editable"]==true&&e["password"]==false&&e["bounds"]["y"].as_i64().is_some_and(|y|y>lower)).collect();
        assert_eq!(editors.len(),1,"one verified chat editor required");
        observed=call(json!({"action":"click","control_session_id":observed["control_session_id"],"observation_id":observed["observation_id"],"element_id":editors[0]["element_id"]}));
        assert_eq!(observed["action_state"],"executed","{}",observed["reason"]);
    }
    assert_eq!(observed["focus_evidence"]["editable"],true,"focused editor required");
    assert_eq!(observed["focus_evidence"]["password"],false);
    assert!(observed["focus_evidence"]["value"].as_str().is_some_and(|v|matches!(v,""|"\n"|"\r\n")),"will not overwrite an existing or unreadable draft");
    let draft="Akane 桌面操作验收草稿（未发送）";
    let typed=call(json!({"action":"type_text","control_session_id":observed["control_session_id"],"observation_id":observed["observation_id"],"text":draft}));
    assert_eq!(typed["action_state"],"executed","{}",typed["reason"]);
    assert_eq!(typed["effect_evidence"]["draft_verified"],true,"draft readback required");
    use base64::Engine;
    let png=base64::engine::general_purpose::STANDARD.decode(typed["screenshots"][0]["imageBase64"].as_str().unwrap()).unwrap();
    let folder=std::path::PathBuf::from(std::env::var("AKANE_QQ_TEST_DIR").unwrap());std::fs::write(folder.join("qq-draft-verified.png"),png).unwrap();
    println!("QQ: designated conversation header and empty editor verified; exact draft read back; no Enter or send action issued");
}

#[test]
#[ignore="owner-designated QQ conversation read-only acceptance"]
fn live_qq_readonly() {
    assert_eq!(std::env::var("AKANE_QQ_READ_TEST").as_deref(),Ok("1"));
    session::configure(false);
    let scope="q".repeat(64);let epoch="qq-read-test";
    let listed=session::execute("qq-list",&json!({"action":"list_windows","_control_scope":scope,"_device_epoch":epoch,"limit":50}));
    let row=listed["windows"].as_array().unwrap().iter().find(|w|w["application"]=="qq.exe"||w["application"]=="qqnt.exe").expect("visible QQ window");
    let out=session::execute("qq-select",&json!({"action":"select_window","window_id":row["window_id"],"device_epoch":epoch,"_control_scope":scope,"_device_epoch":epoch,"mode":"hybrid"}));
    assert_eq!(out["ok"],true,"{}",out["reason"]);
    println!("QQ read-only: ax={} elements={} focus={} caret={}",out["ax_status"],out["elements"].as_array().map(|v|v.len()).unwrap_or(0),out["focus_status"],out["caret_evidence"]);
    let focus=&out["focus_evidence"];println!("QQ focus: role={} editable={} password={} bounds={} text_pattern={} text_edit_pattern={} value_pattern={}",focus["role"],focus["editable"],focus["password"],focus["bounds"],focus["text_pattern"],focus["text_edit_pattern"],focus["value_pattern"]);
    for el in out["elements"].as_array().into_iter().flatten().filter(|e|e["role"]==50000&&e["name"].as_str().is_some_and(|n|n.contains("发送"))){println!("QQ send control: name={} bounds={} enabled={}",el["name"],el["bounds"],el["enabled"]);}
    use base64::Engine;
    let png=base64::engine::general_purpose::STANDARD.decode(out["screenshots"][0]["imageBase64"].as_str().unwrap()).unwrap();
    let folder=std::path::PathBuf::from(std::env::var("AKANE_QQ_TEST_DIR").unwrap());std::fs::create_dir_all(&folder).unwrap();
    std::fs::write(folder.join("qq-readonly.png"),png).unwrap();
}

#[test]
#[ignore = "read-only inspection of the unique acceptance document"]
fn live_notepad_diagnostic() {
    assert_eq!(std::env::var("AKANE_NOTEPAD_LIVE_TEST").as_deref(),Ok("1"));
    let candidates=super::windows::enumerate();
    let (id,_)=candidates.iter().filter(|(_,title)|title.contains("akane-acceptance-"))
        .find(|(id,_)|super::windows::foreground_owned_by(id).is_some())
        .or_else(||candidates.iter().find(|(id,title)|title.contains("akane-acceptance-")&&super::windows::geometry(id).is_ok()))
        .expect("visible acceptance document");
    let observed_id=super::windows::foreground_owned_by(id).unwrap_or_else(||id.clone());
    let snapshot=super::accessibility::snapshot(&observed_id,"diagnostic");
    println!("AX={} reason={} focus={} complete={}",snapshot["ax_status"],snapshot["ax_reason"],snapshot["focus_evidence"],snapshot["text_complete"]);
    for el in snapshot["elements"].as_array().into_iter().flatten().filter(|e|e["editable"]==true||e["focused"]==true){println!("role={} editable={} focus={} value_pattern={} bounds={}",el["role"],el["editable"],el["focused"],el["value_pattern"],el["bounds"]);}
    if let Ok(frame)=super::capture::capture(&observed_id,&std::sync::atomic::AtomicBool::new(false)) {
        let path=std::path::PathBuf::from(std::env::var("AKANE_NOTEPAD_TEST_DIR").unwrap()).join("notepad-diagnostic.png");
        std::fs::write(path,frame.png).unwrap();
    }
}

/// Uses a uniquely named document, never an existing user's tab. Opt-in only.
#[test]
#[ignore = "requires explicit hands-free permission for real Notepad save acceptance"]
fn live_notepad_save_as() {
    use std::{time::Duration, path::PathBuf};
    assert_eq!(std::env::var("AKANE_NOTEPAD_LIVE_TEST").as_deref(),Ok("1"));
    let folder=PathBuf::from(std::env::var("AKANE_NOTEPAD_TEST_DIR").expect("explicit test output directory"));
    assert!(folder.is_absolute()); std::fs::create_dir_all(&folder).unwrap();
    let token=format!("akane-acceptance-{}",std::process::id());
    let source=folder.join(format!("{token}-source.txt"));let destination=folder.join(format!("{token}-saved.txt"));
    assert!(!source.exists()&&!destination.exists());std::fs::write(&source,"").unwrap();
    let executable=PathBuf::from(std::env::var_os("SystemRoot").unwrap()).join("System32/notepad.exe");
    std::process::Command::new(executable).arg(&source).spawn().unwrap();
    struct Cleanup;impl Drop for Cleanup{fn drop(&mut self){session::configure(false);}}
    let _cleanup=Cleanup;session::configure(true);session::resume_local();
    let epoch="notepad-save-test";let mut number=0;
    let mut call=|mut args:Value|{number+=1;args["_control_scope"]=json!("b".repeat(64));args["_device_epoch"]=json!(epoch);args["device_epoch"]=json!(epoch);session::execute(&format!("notepad-{number}"),&args)};
    let mut window=Value::Null;
    for _ in 0..25 {let rows=call(json!({"action":"list_windows","query":token}));
        if let Some(row)=rows["windows"].as_array().and_then(|r|r.iter().find(|w|w["application"]=="notepad.exe")){window=row["window_id"].clone();break;}
        std::thread::sleep(Duration::from_millis(200));}
    assert!(window.is_string(),"Unique Notepad document did not appear");
    let mut observed=call(json!({"action":"select_window","window_id":window,"mode":"hybrid"}));
    assert_eq!(observed["ok"],true,"{}",observed["reason"]);
    let edit=observed["elements"].as_array().unwrap().iter().find(|e|e["editable"]==true&&e["password"]==false).expect("Notepad editor")["element_id"].clone();
    let mut step=|mut args:Value,previous:&Value|{args["control_session_id"]=previous["control_session_id"].clone();args["observation_id"]=previous["observation_id"].clone();let out=call(args.clone());if out["action_state"]!="executed" {use base64::Engine; if let Some(png)=previous["screenshots"][0]["imageBase64"].as_str(){if let Ok(bytes)=base64::engine::general_purpose::STANDARD.decode(png){let _=std::fs::write(folder.join("failed-save-observation.png"),bytes);}} println!("focus status: previous={} current={}",out["previous_focus_status"],out["current_focus_status"]);}assert_eq!(out["action_state"],"executed","action={} reason={} previous={} current={}",args["action"],out["reason"],out["previous_focus"],out["current_focus"]);out};
    observed=step(json!({"action":"click","element_id":edit}),&observed);
    let expected="Akane 本机记事本保存验收 2026";
    observed=step(json!({"action":"type_text","text":expected}),&observed);
    assert_eq!(observed["effect_evidence"]["draft_verified"],true);
    observed=step(json!({"action":"press_key","key":"Ctrl+Shift+S"}),&observed);
    std::thread::sleep(Duration::from_millis(700));
    // Reobserve because a save dialog may be created after the key returns.
    drop(step);
    observed=call(json!({"action":"observe","control_session_id":observed["control_session_id"],"mode":"hybrid"}));
    assert_eq!(observed["ok"],true,"{}",observed["reason"]);
    let filename=observed["elements"].as_array().unwrap().iter().find(|e|e["editable"]==true && e["name"].as_str().is_some_and(|n|n.contains("文件名")||n.to_lowercase().contains("file name"))).expect("Owned save dialog filename field")["element_id"].clone();
    let mut step=|mut args:Value,previous:&Value|{args["control_session_id"]=previous["control_session_id"].clone();args["observation_id"]=previous["observation_id"].clone();let out=call(args.clone());if out["action_state"]!="executed" {use base64::Engine; if let Some(png)=previous["screenshots"][0]["imageBase64"].as_str(){if let Ok(bytes)=base64::engine::general_purpose::STANDARD.decode(png){let _=std::fs::write(folder.join("failed-save-observation.png"),bytes);}} println!("focus status: previous={} current={}",out["previous_focus_status"],out["current_focus_status"]);}assert_eq!(out["action_state"],"executed","action={} reason={} previous={} current={}",args["action"],out["reason"],out["previous_focus"],out["current_focus"]);out};
    observed=step(json!({"action":"click","element_id":filename}),&observed);
    observed=step(json!({"action":"press_key","key":"Ctrl+A"}),&observed);
    observed=step(json!({"action":"type_text","text":destination.to_str().unwrap()}),&observed);
    observed=step(json!({"action":"press_key","key":"Enter"}),&observed);
    for _ in 0..20 {if destination.is_file(){break;}std::thread::sleep(Duration::from_millis(100));}
    let actual=std::fs::read_to_string(&destination).expect("GUI must create saved file");
    assert_eq!(actual.trim_start_matches('\u{feff}'),expected);
    // Close only this verified saved document; keep any other Notepad tabs.
    let _=step(json!({"action":"press_key","key":"Ctrl+W"}),&observed);
    println!("Real Notepad: observed editor, typed draft, owned Save As dialog, exact file content verified");
}

#[test]
fn keys_and_evidence_reject_ambiguous_input() {
    for key in ["Win+R","Ctrl+Alt+Delete+X","Ctrl+Ctrl+A","Enter\n"] { assert!(input::key_codes(key).is_none()); }
    assert!(input::key_codes("Ctrl+Shift+S").is_some());
    assert!(!protocol::valid(json!({"action":"click","x":1,"y":2,"screenshot_id":"s"}).as_object().unwrap()));
    assert!(!protocol::valid(json!({"action":"click","control_session_id":"c","device_epoch":"e","observation_id":"o",
        "element_id":"e","x":1,"y":2,"screenshot_id":"s"}).as_object().unwrap()));
}

/// Explicit desktop acceptance; creates and destroys its own native edit window.
/// It never attaches to an existing application or sends data over the network.
#[test]
#[ignore = "requires an unlocked interactive Windows desktop"]
fn live_controlled_edit_window() {
    use ::windows::{core::w, Win32::{Foundation::{LPARAM, WPARAM},
        System::Threading::GetCurrentThreadId, UI::WindowsAndMessaging::*}};
    use std::{sync::mpsc, time::Duration};
    assert_eq!(std::env::var("AKANE_CU_LIVE_TEST").as_deref(), Ok("1"));
    let (tx, rx) = mpsc::sync_channel(1);
    let (popup_tx,popup_rx)=mpsc::sync_channel(1);
    let worker = std::thread::spawn(move || unsafe {
        let hwnd = CreateWindowExW(WINDOW_EX_STYLE(0), w!("STATIC"), w!("Akane Computer Use Acceptance"),
            WS_OVERLAPPEDWINDOW | WS_VISIBLE, 140, 120, 720, 460, None, None, None, None).unwrap();
        let edit = CreateWindowExW(WS_EX_CLIENTEDGE, w!("EDIT"), w!(""),
            WS_CHILD | WS_VISIBLE | WS_TABSTOP | WINDOW_STYLE(ES_MULTILINE as u32), 20, 20, 650, 330,
            Some(hwnd), None, None, None).unwrap();
        let _ = ::windows::Win32::UI::Input::KeyboardAndMouse::SetFocus(Some(edit));
        tx.send((hwnd.0 as isize, GetCurrentThreadId())).unwrap();
        let mut message = MSG::default();
        let mut popup=None;
        while GetMessageW(&mut message, None, 0, 0).0 > 0 {
            if message.message==WM_APP+1 {
                let child=CreateWindowExW(WINDOW_EX_STYLE(0),w!("STATIC"),w!("Akane Owned Popup Acceptance"),WS_OVERLAPPEDWINDOW|WS_VISIBLE,220,180,480,260,Some(hwnd),None,None,None).unwrap();
                let _=SetForegroundWindow(child);popup=Some(child);let _=popup_tx.send(());continue;
            }
            if message.message==WM_APP+2 {if let Some(child)=popup.take(){let _=DestroyWindow(child);}let _=SetForegroundWindow(hwnd);let _=popup_tx.send(());continue;}
            let _ = TranslateMessage(&message); DispatchMessageW(&message);
        }
        if let Some(child)=popup {let _=DestroyWindow(child);}
        let _ = DestroyWindow(hwnd);
    });
    let (hwnd, thread_id) = rx.recv_timeout(Duration::from_secs(5)).unwrap();
    struct Cleanup(u32);
    impl Drop for Cleanup { fn drop(&mut self) { unsafe { let _ = PostThreadMessageW(self.0, WM_QUIT, WPARAM(0), LPARAM(0)); } session::configure(false); } }
    let cleanup = Cleanup(thread_id);
    session::configure(true);
    let mut number = 0;
    let mut call = |mut args: Value| {
        number += 1;
        args["_control_scope"] = json!("a".repeat(64)); args["_device_epoch"] = json!("live-fixture-epoch");
        session::execute(&format!("live-{number}"), &args)
    };
    let windows = call(json!({"action":"list_windows","query":"Akane Computer Use Acceptance"}));
    assert_eq!(windows["ok"], true, "{windows}");
    let window_id = windows["windows"][0]["window_id"].clone();
    assert!(window_id.is_string());
    let observed = call(json!({"action":"select_window","window_id":window_id,"device_epoch":"live-fixture-epoch","mode":"hybrid"}));
    // No screenshot bytes in panic output.
    assert_eq!(observed["ok"], true, "{}", observed["reason"]);
    assert_eq!(observed["foreground_matches"], true);
    assert_eq!(observed["screenshots"].as_array().unwrap().len(), 1);
    println!("WGC {}x{}; UIA {}; editable focus={}",observed["screenshots"][0]["width"],observed["screenshots"][0]["height"],observed["ax_status"],observed["focus_evidence"]["editable"]);
    let edit = observed["elements"].as_array().unwrap().iter().find(|el| el["editable"] == true).expect("real editable UIA element");
    let click = json!({"action":"click","device_epoch":"live-fixture-epoch","control_session_id":observed["control_session_id"],
        "observation_id":observed["observation_id"],"element_id":edit["element_id"],"_phase":"prepare"});
    let prepared = call(click.clone()); assert_eq!(prepared["ok"],true,"{prepared}");
    assert_eq!(prepared["authorization"]["required"],true);
    let mut approved = click; approved["_phase"] = json!("execute"); approved["_approval_binding"] = prepared["authorization"]["binding"].clone();
    let focused = call(approved);
    assert_eq!(focused["action_state"],"executed","{}",focused["reason"]);
    assert_eq!(focused["focus_evidence"]["editable"],true);
    let mut typing = json!({"action":"type_text","device_epoch":"live-fixture-epoch","control_session_id":focused["control_session_id"],
        "observation_id":focused["observation_id"],"text":"Akane 验收 2026","_phase":"prepare"});
    let prepared = call(typing.clone()); assert_eq!(prepared["ok"],true,"{prepared}");
    typing["_phase"] = json!("execute"); typing["_approval_binding"] = prepared["authorization"]["binding"].clone();
    let typed = call(typing.clone());
    assert_eq!(typed["action_state"],"executed","{}",typed["reason"]);
    assert_eq!(typed["effect_evidence"]["draft_verified"],true,"{}",typed["effect_evidence"]);
    let mut repeated=typing.clone();repeated["_control_scope"]=json!("a".repeat(64));repeated["_device_epoch"]=json!("live-fixture-epoch");
    let duplicate=session::execute(typed["operation_id"].as_str().unwrap(),&repeated);
    assert_eq!(duplicate["action_state"],"executed");assert!(duplicate["screenshots"][0].get("imageBase64").is_none());
    let stale = call(typing); assert_eq!(stale["action_state"],"not_started"); assert_eq!(stale["reason"],"observation_expired");
    unsafe{PostThreadMessageW(thread_id,WM_APP+1,WPARAM(0),LPARAM(0)).unwrap();}popup_rx.recv_timeout(Duration::from_secs(2)).unwrap();
    let popup=call(json!({"action":"observe","device_epoch":"live-fixture-epoch","control_session_id":typed["control_session_id"],"mode":"hybrid"}));
    assert_eq!(popup["ok"],true,"{}",popup["reason"]);assert_eq!(popup["owner_window_id"],typed["window_id"]);assert_ne!(popup["window_id"],typed["window_id"]);
    unsafe{PostThreadMessageW(thread_id,WM_APP+2,WPARAM(0),LPARAM(0)).unwrap();}popup_rx.recv_timeout(Duration::from_secs(2)).unwrap();
    let restored=call(json!({"action":"observe","device_epoch":"live-fixture-epoch","control_session_id":typed["control_session_id"],"mode":"hybrid","region":{"x":0,"y":0,"width":200,"height":100},"scale":2}));
    assert_eq!(restored["ok"],true,"{}",restored["reason"]);assert_eq!(restored["window_id"],typed["window_id"]);assert_eq!(restored["screenshots"][0]["width"],400);
    let close=json!({"action":"press_key","device_epoch":"live-fixture-epoch","control_session_id":restored["control_session_id"],"observation_id":restored["observation_id"],"key":"Alt+F4","_phase":"prepare"});
    let prepared=call(close.clone());assert_eq!(prepared["ok"],true,"{prepared}");
    let mut approved=close;approved["_phase"]=json!("execute");approved["_approval_binding"]=prepared["authorization"]["binding"].clone();
    let closed=call(approved);assert_eq!(closed["action_state"],"executed","{}",closed["reason"]);assert_eq!(closed["observation_state"],"failed");
    let stopped = call(json!({"action":"stop","device_epoch":"live-fixture-epoch","control_session_id":typed["control_session_id"]}));
    assert_eq!(stopped["ok"],true);
    println!("Native edit and duplicate verified; owned popup/crop verified; closed-window observation failed after executed; stop acknowledged. hwnd={} (fixture only)",hwnd);
    drop(cleanup); worker.join().unwrap();
}
