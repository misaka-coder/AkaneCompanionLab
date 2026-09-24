//! Fresh-process regression: UIA must not keep COM alive and hide capture teardown bugs.
use std::{sync::{mpsc, atomic::AtomicBool}, time::Duration};
use windows::{core::w, Win32::{Foundation::{LPARAM, WPARAM},
    System::{LibraryLoader::GetModuleHandleW, Threading::GetCurrentThreadId},
    UI::WindowsAndMessaging::*}};

struct Fixture { thread: u32, identity: super::windows::Identity, window: Option<std::thread::JoinHandle<()>> }
impl Drop for Fixture { fn drop(&mut self) {
    unsafe { let _ = PostThreadMessageW(self.thread, WM_QUIT, WPARAM(0), LPARAM(0)); }
    if let Some(window) = self.window.take() { window.join().unwrap(); }
} }
fn fixture() -> Fixture {
    fixture_with_edit(false)
}
fn fixture_with_edit(with_edit:bool) -> Fixture {
    let (tx, rx) = mpsc::sync_channel(1);
    let window = std::thread::spawn(move || unsafe {
        let instance = GetModuleHandleW(None).unwrap();
        let hwnd = CreateWindowExW(WS_EX_NOACTIVATE, w!("STATIC"), w!("Akane capture lifetime fixture"),
            WS_OVERLAPPEDWINDOW, 80, 80, 400, 240, None, None, Some(instance.into()), None).unwrap();
        if with_edit {
            CreateWindowExW(WINDOW_EX_STYLE(0),w!("EDIT"),w!("  synthetic local detail  "),WS_CHILD|WS_VISIBLE|WS_BORDER,
                10,10,300,40,Some(hwnd),None,Some(instance.into()),None).unwrap();
        }
        let _ = ShowWindow(hwnd, SW_SHOWNOACTIVATE);
        tx.send((GetCurrentThreadId(), super::windows::identity(hwnd).unwrap())).unwrap();
        let mut msg = MSG::default();
        while GetMessageW(&mut msg, None, 0, 0).as_bool() {
            let _ = TranslateMessage(&msg); DispatchMessageW(&msg);
        }
        let _ = DestroyWindow(hwnd);
    });
    let (thread, identity) = rx.recv_timeout(Duration::from_secs(5)).unwrap();
    Fixture { thread, identity, window: Some(window) }
}

#[test]
#[ignore="reads only its own nonactivating native fixture, never sends input"]
fn uia_read_element_details() {
    use serde_json::{json,Value};
    use super::session;
    let _fixture=fixture_with_edit(true);
    session::configure(false);
    let scope="d".repeat(64);let epoch="uia-detail-acceptance";let mut number=0;
    let mut call=|mut args:Value|{number+=1;args["_control_scope"]=json!(scope);args["_device_epoch"]=json!(epoch);
        if args.get("context_ref").is_none(){args["device_epoch"]=json!(epoch);}
        session::execute(&format!("detail-{number}"),&args)};
    let listed=call(json!({"action":"list_windows","query":"Akane capture lifetime fixture"}));
    let selected=call(json!({"action":"select_window","window_id":listed["windows"][0]["window_id"],"mode":"text"}));
    assert_eq!(selected["ok"],true,"{selected}");
    let editor=selected["elements"].as_array().unwrap().iter().find(|el|el["role"]==50004).expect("fixture edit");
    let request=json!({"action":"read_element","context_ref":selected["context_ref"],"element_id":editor["element_id"]});
    for _ in 0..2 {
        let read=call(request.clone());
        assert_eq!(read["ok"],true,"{read}");
        assert_eq!(read["action_state"],"not_started");
        assert_eq!(read["read_only_details"],true);
        assert!(read.get("observation_id").is_none());
        let rows=read["detail"]["elements"].as_array().unwrap();
        assert_eq!(rows[0]["value"],"  synthetic local detail  ");
        assert!(rows.iter().all(|row|row.get("element_id").is_none()));
    }
    let stale=call(json!({"action":"read_element","control_session_id":selected["control_session_id"],
        "observation_id":"old","element_id":editor["element_id"]}));
    assert_eq!(stale["reason"],"observation_expired");
}

#[test]
#[ignore = "creates and captures only its own nonactivating native window; run in a fresh process"]
fn capture_lifetime_without_uia() {
    let fixture = fixture();
    let identity = &fixture.identity;
    for number in 0..20 {
        assert!(matches!(super::capture::capture(identity, &AtomicBool::new(true)), Err("stopped")));
        let mut invalid = identity.clone(); invalid.started = 0;
        assert!(matches!(super::capture::capture(&invalid, &AtomicBool::new(false)), Err("window_identity_expired")));
        let id = identity.clone();
        let dimensions = std::thread::spawn(move || {
            let frame = super::capture::capture(&id, &AtomicBool::new(false)).expect("real WGC frame");
            assert!(frame.png.starts_with(b"\x89PNG\r\n\x1a\n"));
            assert_eq!(frame.rgba.len(), (frame.width * frame.height * 4) as usize);
            (frame.width, frame.height)
        }).join().expect("capture caller must survive");
        println!("capture {number}: {}x{}", dimensions.0, dimensions.1);
        std::thread::sleep(Duration::from_millis(150));
    }
    // A result being returned is insufficient: teardown crashed after the result in production.
    std::thread::sleep(Duration::from_secs(2));
    let callers: Vec<_> = (0..8).map(|_| {
        let id = identity.clone();
        std::thread::spawn(move || super::capture::capture(&id, &AtomicBool::new(false)))
    }).collect();
    let mut completed = 0;
    for caller in callers { match caller.join().unwrap() {
        Ok(_) => completed += 1, Err("capture_worker_busy") => {}, Err(reason) => panic!("unexpected error: {reason}"),
    } }
    assert!(completed > 0);
    assert!(super::capture::capture(identity, &AtomicBool::new(false)).is_ok());
}

#[test]
#[ignore = "real observations of own window; activity is simulated at the input monitor, no OS input"]
fn activity_recovers_after_observation() {
    use super::{session,monitor};
    use serde_json::{json,Value};
    let _fixture=fixture();
    let scope="a".repeat(64);let epoch="activity-test";
    crate::control_lease::begin_connection(epoch);
    let mut number=0;
    let mut call=|mut args:Value|{
        number+=1;args["_control_scope"]=json!(scope);args["_device_epoch"]=json!(epoch);args["device_epoch"]=json!(epoch);
        session::execute(&format!("activity-{number}"),&args)
    };
    let list=call(json!({"action":"list_windows","query":"Akane capture lifetime fixture"}));
    session::configure(true);
    drop(crate::control_lease::acquire("computer_use",&scope,epoch,true).unwrap());
    monitor::simulate_user_activity();
    let selected=call(json!({"action":"select_window","window_id":list["windows"][0]["window_id"],"mode":"visual"}));
    assert_eq!(selected["ok"],true,"{}",selected["reason"]);
    assert_eq!(selected["foreground_matches"],false,"recovery selection must not activate its window");
    let observe=json!({"action":"observe","control_session_id":selected["control_session_id"],"mode":"visual"});
    for _ in 0..2 {
        monitor::simulate_user_activity();
        assert!(matches!(crate::control_lease::acquire("computer_use",&scope,epoch,false),Err("user_takeover")));
        assert_eq!(session::local_status()["status"],"paused");
        assert_eq!(call(json!({"action":"status"}))["status"],"paused");
        let refreshed=call(observe.clone());
        assert_eq!(refreshed["ok"],true,"{}",refreshed["reason"]);
        assert_eq!(refreshed["control_status"],"selected");
        assert_ne!(refreshed["observation_id"],selected["observation_id"]);
        assert_eq!(session::local_status()["status"],"selected");
        drop(crate::control_lease::acquire("computer_use",&scope,epoch,false).unwrap());
        let stale=call(json!({"action":"run_steps","control_session_id":selected["control_session_id"],
            "observation_id":selected["observation_id"],"steps":[{"action":"wait_for","expect":{"kind":"window_title","title":"Akane capture lifetime fixture"}}],"_phase":"prepare"}));
        assert_eq!(stale["reason"],"observation_expired");
        let ready=call(json!({"action":"run_steps","control_session_id":refreshed["control_session_id"],
            "observation_id":refreshed["observation_id"],"steps":[{"action":"wait_for","expect":{"kind":"window_title","title":"Akane capture lifetime fixture"}}],"_phase":"prepare"}));
        assert_eq!(ready["ok"],true,"{}",ready["reason"]);
        assert!(matches!(crate::control_lease::acquire("computer_use",&"b".repeat(64),epoch,true),Err("desktop_owned_by_other_session")));
        // Clicking local resume and then moving again must also recover with observe.
        assert_eq!(session::resume_local()["ok"],true);
    }
    let active=std::thread::spawn(|| {
        for _ in 0..110 {monitor::simulate_user_activity();std::thread::sleep(Duration::from_millis(20));}
    });
    std::thread::sleep(Duration::from_millis(40));
    let during=call(observe.clone());
    assert_eq!(during["ok"],false);
    assert!(matches!(during["reason"].as_str(),Some("user_takeover"|"user_input_active")));
    active.join().unwrap();
    assert_eq!(call(observe.clone())["ok"],true);
    session::stop_local();
    let mut stopped_args=observe.clone();stopped_args["mode"]=json!("text");
    let stopped=call(stopped_args);
    assert_eq!(stopped["ok"],false);
    assert!(crate::control_lease::stopped());
    assert!(matches!(crate::control_lease::acquire("computer_use",&scope,epoch,false),Err("stopped_requires_local_resume")));
    assert_eq!(session::resume_local()["ok"],true);
    monitor::simulate_user_activity();
    assert_eq!(call(observe.clone())["ok"],true);
    {let mut lease=crate::control_lease::acquire("computer_use",&scope,epoch,false).unwrap();lease.handoff("browser_page");}
    assert_eq!(call(observe.clone())["reason"],"tool_handoff_required");
    session::configure(false);
    let _=call(observe);
    assert!(!session::input_enabled());
    println!("Activity refresh, repeated activity, stale evidence, explicit stop, disabled input and scope/tool isolation passed; no OS input issued");
}

#[tokio::test]
#[ignore = "fresh-process real Satellite + WGC regression against own nonactivating window"]
async fn satellite_capture_lifetime() {
    use futures_util::{SinkExt, StreamExt};
    use serde_json::{json, Value};
    use tokio_tungstenite::tungstenite::Message;
    let _fixture = fixture();
    assert!(!super::input_enabled());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
        socket.send(Message::Text(json!({"type":"hello", "protocol_version":crate::SATELLITE_PROTOCOL_VERSION,
            "instance_id":"capture-test"}).to_string().into())).await.unwrap();
        let registration = crate::satellite_message_json(socket.next().await.unwrap().unwrap()).unwrap();
        let contract = registration["offers"].as_array().unwrap().iter().find(|v| v["tool_id"]=="computer_use").unwrap().clone();
        socket.send(Message::Text(json!({"type":"registered", "protocol_version":crate::SATELLITE_PROTOCOL_VERSION,
            "instance_id":"capture-test", "bot_id":"capture-test", "lease_epoch":"capture-epoch",
            "offer_ids":{"computer_use":"capture-offer"}}).to_string().into())).await.unwrap();
        let mut session = Value::Null;
        let mut window = Value::Null;
        let mut heartbeats = 0;
        for number in 0..12 {
            let args = match number {
                0 => json!({"action":"list_windows", "query":"Akane capture lifetime fixture"}),
                1 => json!({"action":"select_window", "window_id":window, "device_epoch":"capture-epoch", "mode":"visual"}),
                11 => json!({"action":"status"}),
                _ => json!({"action":"observe", "control_session_id":session, "device_epoch":"capture-epoch",
                    "mode":if number % 2 == 0 {"hybrid"} else {"visual"}}),
            };
            // Leave the first screenshot idle long enough to observe native teardown and a heartbeat.
            if number == 2 { tokio::time::sleep(Duration::from_secs(9)).await; }
            let mut invoke = contract.clone();
            for (key, value) in json!({"type":"invoke", "protocol_version":crate::SATELLITE_PROTOCOL_VERSION,
                "instance_id":"capture-test", "lease_epoch":"capture-epoch", "offer_id":"capture-offer",
                "invocation_id":format!("capture-{number}"), "control_scope":"a".repeat(64), "arguments":args}).as_object().unwrap() {
                invoke[key] = value.clone();
            }
            assert!(super::protocol::valid(invoke["arguments"].as_object().unwrap()), "invalid fixture call: {}", invoke["arguments"]);
            assert!(crate::validate_satellite_invocation(&invoke, "capture-test", "capture-epoch",
                &std::collections::HashMap::from([("computer_use".into(), "capture-offer".into())])).is_some());
            socket.send(Message::Text(invoke.to_string().into())).await.unwrap();
            let deadline = tokio::time::Instant::now() + Duration::from_secs(15);
            let result = loop {
                let message = tokio::time::timeout_at(deadline, socket.next()).await.unwrap().unwrap().unwrap();
                // The device's inbound parser is limited to small commands. Host
                // results include PNG payloads and use the larger media envelope.
                let Message::Text(text) = message else { continue; };
                assert!(text.len() < 16 * 1024 * 1024);
                let value: Value = serde_json::from_str(text.as_ref()).unwrap();
                if value["type"] == "heartbeat" { heartbeats += 1; continue; }
                if value["type"] == "result" { break value; }
                assert!(value["type"] == "accepted" || value["type"] == "running");
            };
            let data = &result["data"];
            assert_eq!(data["ok"], true, "call {number}: {}", result["reason"]);
            if number == 0 {
                assert_eq!(data["windows"].as_array().unwrap().len(), 1);
                window = data["windows"][0]["window_id"].clone();
            } else if number == 11 { assert_eq!(data["input_enabled"], false); }
            else {
                assert!(data["screenshots"][0]["imageBase64"].as_str().unwrap().len() > 100);
                session = data["control_session_id"].clone();
            }
            println!("Satellite call {number} succeeded");
        }
        assert!(heartbeats > 0, "connection must still heartbeat after first screenshot");
        socket.close(None).await.unwrap();
    };
    let url = format!("http://{address}");
    let client = async { let result = crate::run_desktop_satellite_session(&url, "capture-test", "test-token").await; println!("Satellite closed: {result:?}"); result };
    let (_, result) = tokio::join!(server, client);
    assert!(matches!(result, Err("satellite_closed") | Err("satellite_receive_failed")));
}
