//! Real SendInput/WGC acceptance in a disposable window with no UIA editor.
use super::{monitor, protocol, session};
use base64::Engine;
use serde_json::{json, Value};
use std::{
    sync::{
        atomic::{AtomicIsize, AtomicUsize, Ordering},
        mpsc, Mutex,
    },
    time::{Duration, Instant},
};
use windows::{
    core::w,
    Win32::{
        Foundation::{COLORREF, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM},
        Graphics::Gdi::{
            BeginPaint, CreateSolidBrush, DeleteObject, EndPaint, FillRect, GetSysColorBrush,
            InvalidateRect, COLOR_WINDOW, PAINTSTRUCT,
        },
        System::{LibraryLoader::GetModuleHandleW, Threading::GetCurrentThreadId},
        UI::WindowsAndMessaging::*,
    },
};

static TEXT: Mutex<String> = Mutex::new(String::new());
static ENTERS: AtomicUsize = AtomicUsize::new(0);
static BLOCKER: AtomicIsize = AtomicIsize::new(0);
static ANIMATION: AtomicUsize = AtomicUsize::new(0);
static HIGHLIGHT: AtomicUsize = AtomicUsize::new(0);
static LAST_CLICK: Mutex<Option<(i32, i32)>> = Mutex::new(None);
unsafe extern "system" fn procedure(hwnd: HWND, message: u32, w: WPARAM, l: LPARAM) -> LRESULT {
    if message == WM_APP + 3 {
        SetTimer(Some(hwnd), 1, 75, None);
        return LRESULT(0);
    }
    if message == WM_APP + 4 {
        HIGHLIGHT.fetch_add(1, Ordering::SeqCst);
        let _ = InvalidateRect(Some(hwnd), None, false);
        return LRESULT(0);
    }
    if message == WM_TIMER {
        ANIMATION.fetch_add(1, Ordering::SeqCst);
        let _ = InvalidateRect(Some(hwnd), None, false);
        return LRESULT(0);
    }
    if message == WM_PAINT {
        let mut paint = PAINTSTRUCT::default();
        let dc = BeginPaint(hwnd, &mut paint);
        let mut client = RECT::default();
        let _ = GetClientRect(hwnd, &mut client);
        FillRect(dc, &client, GetSysColorBrush(COLOR_WINDOW));
        for (rect, on) in [
            (
                RECT {
                    left: 330,
                    top: 40,
                    right: 410,
                    bottom: 110,
                },
                ANIMATION.load(Ordering::SeqCst) % 2 == 0,
            ),
            (
                RECT {
                    left: 40,
                    top: 30,
                    right: 200,
                    bottom: 145,
                },
                HIGHLIGHT.load(Ordering::SeqCst) % 2 == 0,
            ),
        ] {
            let brush = CreateSolidBrush(COLORREF(if on { 0x00eeaa66 } else { 0x00884422 }));
            FillRect(dc, &rect, brush);
            let _ = DeleteObject(brush.into());
        }
        let _ = EndPaint(hwnd, &paint);
        return LRESULT(0);
    }
    if message == WM_APP + 1 {
        let mut rect = windows::Win32::Foundation::RECT::default();
        let _ = GetWindowRect(hwnd, &mut rect);
        let blocker = CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_NOACTIVATE,
            w!("STATIC"),
            w!("Akane Visual Occluder"),
            WS_OVERLAPPEDWINDOW,
            rect.left + 20,
            rect.top + 50,
            260,
            180,
            None,
            None,
            None,
            None,
        )
        .unwrap();
        let _ = ShowWindow(blocker, SW_SHOWNOACTIVATE);
        BLOCKER.store(blocker.0 as isize, Ordering::SeqCst);
        return LRESULT(0);
    }
    if message == WM_APP + 2 {
        let blocker = BLOCKER.swap(0, Ordering::SeqCst);
        if blocker != 0 {
            let _ = DestroyWindow(HWND(blocker as *mut _));
        }
        return LRESULT(0);
    }
    if message == WM_CHAR {
        match w.0 {
            1 => TEXT.lock().unwrap().clear(),
            13 => {
                ENTERS.fetch_add(1, Ordering::SeqCst);
            }
            _ => {
                if let Some(ch) = char::from_u32(w.0 as u32) {
                    TEXT.lock().unwrap().push(ch);
                }
            }
        }
        return LRESULT(0);
    }
    if message == WM_LBUTTONDOWN {
        let _dpi = super::windows::DpiGuard::new();
        let mut point = POINT::default();
        if GetCursorPos(&mut point).is_ok() {
            *LAST_CLICK.lock().unwrap() = Some((point.x, point.y));
        }
        let _ = windows::Win32::UI::Input::KeyboardAndMouse::SetFocus(Some(hwnd));
    }
    DefWindowProcW(hwnd, message, w, l)
}
struct Fixture {
    thread: u32,
    window: isize,
    worker: Option<std::thread::JoinHandle<()>>,
}
impl Drop for Fixture {
    fn drop(&mut self) {
        session::configure(false);
        unsafe {
            let _ = PostThreadMessageW(self.thread, WM_QUIT, WPARAM(0), LPARAM(0));
        }
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}
fn fixture() -> Fixture {
    let (tx, rx) = mpsc::sync_channel(1);
    let worker = std::thread::spawn(move || unsafe {
        let _dpi = super::windows::DpiGuard::new();
        let instance = GetModuleHandleW(None).unwrap();
        let class = WNDCLASSW {
            lpfnWndProc: Some(procedure),
            hInstance: instance.into(),
            hbrBackground: GetSysColorBrush(COLOR_WINDOW),
            lpszClassName: w!("AkaneVisualFixture"),
            ..Default::default()
        };
        assert_ne!(RegisterClassW(&class), 0);
        let hwnd = CreateWindowExW(
            WS_EX_TOPMOST,
            w!("AkaneVisualFixture"),
            w!("Akane Visual Batch Fixture"),
            WS_OVERLAPPEDWINDOW | WS_VISIBLE,
            240,
            180,
            520,
            280,
            None,
            None,
            Some(instance.into()),
            None,
        )
        .unwrap();
        let _ = SetForegroundWindow(hwnd);
        let _ = windows::Win32::UI::Input::KeyboardAndMouse::SetFocus(Some(hwnd));
        tx.send((GetCurrentThreadId(), hwnd.0 as isize)).unwrap();
        let mut msg = MSG::default();
        while GetMessageW(&mut msg, None, 0, 0).as_bool() {
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        let blocker = BLOCKER.swap(0, Ordering::SeqCst);
        if blocker != 0 {
            let _ = DestroyWindow(HWND(blocker as *mut _));
        }
        let _ = DestroyWindow(hwnd);
    });
    let (thread, window) = rx.recv_timeout(Duration::from_secs(5)).unwrap();
    Fixture {
        thread,
        window,
        worker: Some(worker),
    }
}
fn call(id: &str, args: Value) -> Value {
    invoke(id, args, "trusted_auto_allow", "", "")
}
fn invoke(id: &str, mut args: Value, mode: &str, phase: &str, binding: &str) -> Value {
    if args.get("context_ref").is_none() {
        args["device_epoch"] = json!("visual-fixture");
    }
    if matches!(args["action"].as_str(), Some("list_windows" | "status")) {
        args.as_object_mut().unwrap().remove("device_epoch");
    }
    assert!(
        protocol::valid(args.as_object().unwrap()),
        "public arguments: {args}"
    );
    args["_device_epoch"] = json!("visual-fixture");
    args["_control_scope"] = json!("v".repeat(64));
    args["_permission_mode"] = json!(mode);
    if !phase.is_empty() {
        args["_phase"] = json!(phase);
        args["_approval_binding"] = json!(binding);
    }
    session::execute(id, &args)
}
fn observed(id: &str, window: &Value) -> Value {
    let out = call(
        id,
        json!({"action":"select_window","window_id":window,"mode":"visual"}),
    );
    assert_eq!(out["ok"], true, "{}", out["reason"]);
    assert!(!out["screenshots"].as_array().unwrap().is_empty());
    out
}
fn batch(observation: &Value, actions: Value) -> Value {
    if std::env::var("AKANE_CU_CONTEXT_TEST").as_deref() == Ok("1") {
        assert!(
            observation["context_ref"].is_string(),
            "device must return a real context binding"
        );
        return json!({"action":"run_actions","context_ref":observation["context_ref"],"actions":actions});
    }
    json!({"action":"run_actions","control_session_id":observation["control_session_id"],"observation_id":observation["observation_id"],
        "screenshot_id":observation["screenshots"][0]["screenshot_id"],"actions":actions})
}

#[test]
#[ignore = "creates and controls only its own disposable native window; requires interactive desktop"]
fn live_visual_batch_and_window_management() {
    assert_eq!(std::env::var("AKANE_CU_VISUAL_TEST").as_deref(), Ok("1"));
    let fixture = fixture();
    if std::env::var("AKANE_CU_VISUAL_WAIT_FOR_FOCUS").as_deref() == Ok("1") {
        println!("READY: activate Akane Visual Batch Fixture; the test controls only this window.");
        let start = Instant::now();
        while unsafe { GetForegroundWindow() }.0 as isize != fixture.window {
            assert!(
                start.elapsed() < Duration::from_secs(90),
                "fixture was not activated"
            );
            std::thread::sleep(Duration::from_millis(20));
        }
        std::thread::sleep(Duration::from_millis(700));
    }
    // Let initial window creation settle before testing deliberate changes.
    std::thread::sleep(Duration::from_millis(350));
    crate::control_lease::begin_connection("visual-fixture");
    session::configure(true);
    session::resume_local();
    let list = call(
        "list",
        json!({"action":"list_windows","query":"Akane Visual Batch Fixture"}),
    );
    assert_eq!(list["windows"].as_array().unwrap().len(), 1);
    let window = list["windows"][0]["window_id"].clone();
    let observation = observed("select", &window);
    assert_eq!(observation["ax_status"], "unavailable");
    assert_eq!(observation["elements"], json!([]));
    session::age_observation_for_test(20_000);
    let request = batch(
        &observation,
        json!([{"action":"click","x":100,"y":100},{"action":"press_key","key":"Ctrl+A"},
        {"action":"type_text","text":"晴天 batch fixture"},{"action":"press_key","key":"Enter"}]),
    );
    let started = Instant::now();
    let out = call("batch-once", request.clone());
    if out["workflow_state"] != "completed" {
        use base64::Engine;
        for (name, value) in [("before", &observation), ("after", &out)] {
            if let Some(encoded) = value["screenshots"][0]["imageBase64"].as_str() {
                let path = std::env::temp_dir()
                    .join(format!("akane-visual-{}-{name}.png", std::process::id()));
                std::fs::write(
                    &path,
                    base64::engine::general_purpose::STANDARD
                        .decode(encoded)
                        .unwrap(),
                )
                .unwrap();
                println!("Fixture diagnostic: {}", path.display());
            }
        }
        println!("Stopped at step {}: {}", out["next_step"], out["reason"]);
    }
    assert_eq!(out["workflow_state"], "completed", "{}", out["reason"]);
    assert_eq!(out["ok"], true, "{}", out["reason"]);
    assert_eq!(out["completed_steps"], 4);
    assert_eq!(out["task_verified"], false);
    assert_eq!(out["screenshots"].as_array().unwrap().len(), 1);
    assert_eq!(&*TEXT.lock().unwrap(), "晴天 batch fixture");
    assert_eq!(ENTERS.load(Ordering::SeqCst), 1);
    let replay = call("batch-once", request.clone());
    assert_eq!(replay["completed_steps"], 4);
    assert_eq!(ENTERS.load(Ordering::SeqCst), 1);
    println!("Visual click/select-all/type/Enter: {} ms; 4 ordered inputs, one final image, no UIA editor or duplicate Enter",started.elapsed().as_millis());

    let minimized = call(
        "minimize",
        json!({"action":"manage_window","window_id":window,"window_action":"minimize"}),
    );
    assert_eq!(minimized["ok"], true, "{}", minimized["reason"]);
    assert_eq!(minimized["window_state"]["minimized"], true);
    monitor::simulate_user_activity();
    let recovered = call(
        "reconcile-minimized",
        json!({"action":"select_window","window_id":window,"mode":"visual"}),
    );
    assert_eq!(recovered["ok"], true, "{}", recovered["reason"]);
    assert_eq!(recovered["next_action"], "select_window");
    let _restored = observed("restore-select", &window);
    let moved = call(
        "move",
        json!({"action":"manage_window","window_id":window,"window_action":"move","desktop_x":330,"desktop_y":220}),
    );
    assert_eq!(moved["ok"], true, "{}", moved["reason"]);
    let current = observed("after-move", &window);
    let stale = call(
        "stale-plan",
        batch(&out, json!([{"action":"press_key","key":"Enter"}])),
    );
    assert_eq!(stale["reason"], "observation_expired");
    assert_eq!(ENTERS.load(Ordering::SeqCst), 1);
    session::stop_local();
    let stopped = call(
        "stopped-plan",
        batch(&current, json!([{"action":"press_key","key":"Enter"}])),
    );
    assert!(matches!(
        stopped["reason"].as_str(),
        Some("stopped_requires_local_resume" | "stopped")
    ));
    assert_eq!(ENTERS.load(Ordering::SeqCst), 1);
    println!("Verified minimize, quiet takeover recovery of minimized window, restore, move, stale evidence and explicit stop.");

    session::resume_local();
    let observation = observed("for-occlusion", &window);
    unsafe {
        SendMessageW(HWND(fixture.window as *mut _), WM_APP + 1, None, None);
    }
    let blocked = call(
        "occluded",
        batch(
            &observation,
            json!([{"action":"click","x":100,"y":100},{"action":"press_key","key":"Enter"}]),
        ),
    );
    assert_eq!(blocked["reason"], "target_occluded");
    assert_eq!(blocked["action_state"], "not_started");
    assert_eq!(blocked["occluder"]["title"], "Akane Visual Occluder");
    assert!(blocked["occluder"]["window_id"].is_string());
    assert_eq!(blocked["steps"][1]["action_state"], "not_started");
    assert_eq!(ENTERS.load(Ordering::SeqCst), 1);
    let cleared = call(
        "clear-occlusion",
        json!({"action":"manage_window","window_id":blocked["occluder"]["window_id"],"window_action":"minimize"}),
    );
    assert_eq!(cleared["ok"], true, "{}", cleared["reason"]);
    unsafe {
        SendMessageW(HWND(fixture.window as *mut _), WM_APP + 2, None, None);
    }
    let observation = observed("for-approval", &window);
    let mut approved = invoke(
        "ask-batch",
        batch(
            &observation,
            json!([{"action":"click","x":100,"y":100},{"action":"press_key","key":"Enter"}]),
        ),
        "ask_each_time",
        "",
        "",
    );
    for step in 0..2 {
        assert_eq!(
            approved["workflow_state"], "awaiting_approval",
            "{}",
            approved["reason"]
        );
        let request = json!({"action":"resume_steps","workflow_id":approved["workflow_id"],"control_session_id":approved["control_session_id"],"observation_id":approved["observation_id"]});
        let prepared = invoke(
            &format!("ask-prepare-{step}"),
            request.clone(),
            "ask_each_time",
            "prepare",
            "",
        );
        assert_eq!(prepared["ok"], true, "{}", prepared["reason"]);
        assert_eq!(prepared["workflow_kind"], "visual_actions");
        approved = invoke(
            &format!("ask-execute-{step}"),
            request,
            "ask_each_time",
            "execute",
            prepared["authorization"]["binding"].as_str().unwrap(),
        );
    }
    assert_eq!(
        approved["workflow_state"], "completed",
        "{}",
        approved["reason"]
    );
    assert_eq!(ENTERS.load(Ordering::SeqCst), 2);
    println!("Verified real occluder discovery/clearance and exact approval resume without preflight consuming the model observation.");

    // Reproduce master -> QQ group after idle, without touching any personal app.
    let other = |id: &str, mut args: Value| {
        args["_control_scope"] = json!("g".repeat(64));
        args["_device_epoch"] = json!("visual-fixture");
        if args.get("context_ref").is_none() {
            args["device_epoch"] = json!("visual-fixture");
        }
        args["_permission_mode"] = json!("trusted_auto_allow");
        session::execute(id, &args)
    };
    let listed = other(
        "group-list",
        json!({"action":"list_windows","query":"Akane Visual Batch Fixture"}),
    );
    let next_window = listed["windows"][0]["window_id"].clone();
    let selection = json!({"action":"select_window","window_id":next_window,"mode":"visual"});
    let busy = other("group-active-owner", selection.clone());
    assert_eq!(busy["reason"], "desktop_owned_by_other_session");
    assert!(busy["retry_after_ms"].as_u64().unwrap() > 0);
    crate::control_lease::expire_idle_for_test();
    let selected = other("group-idle-owner", selection);
    assert_eq!(selected["ok"], true, "{}", selected["reason"]);
    assert_ne!(
        selected["control_session_id"],
        approved["control_session_id"]
    );
    let old = call(
        "master-stale",
        batch(&approved, json!([{"action":"press_key","key":"Enter"}])),
    );
    assert_eq!(old["reason"], "desktop_owned_by_other_session");
    assert_eq!(ENTERS.load(Ordering::SeqCst), 2);
    let old_observe = call(
        "master-stale-observe",
        json!({"action":"observe","control_session_id":approved["control_session_id"],"mode":"visual"}),
    );
    assert_eq!(old_observe["reason"], "control_session_mismatch");
    let input = other(
        "group-input",
        batch(
            &selected,
            json!([{"action":"type_text","text":" group resumed"}]),
        ),
    );
    assert_eq!(input["workflow_state"], "completed", "{}", input["reason"]);
    assert!(TEXT.lock().unwrap().ends_with(" group resumed"));
    assert_eq!(ENTERS.load(Ordering::SeqCst), 2);
    println!("Verified active-owner rejection, idle cross-session selection, fresh input and stale old-session rejection without duplicate Enter.");

    session::age_observation_for_test(20_000);
    let clicked = other(
        "aged-single-click",
        json!({"action":"click","control_session_id":input["control_session_id"],
        "observation_id":input["observation_id"],"screenshot_id":input["screenshots"][0]["screenshot_id"],"x":100,"y":100}),
    );
    assert_eq!(clicked["ok"], true, "{}", clicked["reason"]);
    session::age_observation_for_test(20_000);
    let typed = other(
        "aged-visual-type",
        json!({"action":"type_text","control_session_id":clicked["control_session_id"],
        "observation_id":clicked["observation_id"],"text":" verified"}),
    );
    assert_eq!(typed["ok"], true, "{}", typed["reason"]);
    assert!(TEXT.lock().unwrap().ends_with(" verified"));
    session::age_observation_for_test(61_000);
    let expired = other(
        "expired-input",
        json!({"action":"press_key","control_session_id":typed["control_session_id"],
        "observation_id":typed["observation_id"],"key":"Enter"}),
    );
    assert_eq!(expired["reason"], "observation_expired");
    assert_eq!(expired["observation_state"], "complete");
    assert!(expired["screenshots"][0]["imageBase64"].is_string());
    assert_ne!(expired["context_ref"], typed["context_ref"]);
    assert_eq!(ENTERS.load(Ordering::SeqCst), 2);
    let observation = other(
        "before-scene-change",
        json!({"action":"select_window","window_id":next_window,"mode":"visual"}),
    );
    assert_eq!(observation["ok"], true);
    session::age_observation_for_test(20_000);
    unsafe {
        SetWindowTextW(
            HWND(fixture.window as *mut _),
            w!("Changed scene: reject the old plan"),
        )
        .unwrap();
    }
    std::thread::sleep(Duration::from_millis(150));
    let changed = other(
        "changed-scene",
        batch(&observation, json!([{"action":"press_key","key":"Enter"}])),
    );
    assert_eq!(changed["ok"], true, "{}", changed["reason"]);
    assert_eq!(changed["action_state"], "executed");
    assert_eq!(ENTERS.load(Ordering::SeqCst), 3);
    println!("Verified aged visual batch/click/type despite content change, and the 60-second expiry without input.");
    if request.get("context_ref").is_some() {
        session::clear_contexts_for_test();
        let replay = call("batch-once", request.clone());
        assert_eq!(replay["completed_steps"], 4);
        assert_eq!(ENTERS.load(Ordering::SeqCst), 3);
        let fresh = call("lost-context-new-invocation", request);
        assert_eq!(fresh["reason"], "context_reference_expired");
        assert_eq!(fresh["action_state"], "not_started");
        assert_eq!(ENTERS.load(Ordering::SeqCst), 3);
        println!("Lost aliases do not replay retained invocations or redirect a new request.");
    }

    // These age values exercise the guards without sleeping for a minute;
    // rendering, capture and input remain real Windows operations.
    unsafe {
        SendMessageW(HWND(fixture.window as *mut _), WM_APP + 3, None, None);
    }
    for age in [17_000, 30_000, 55_000] {
        let observation = other(
            &format!("dynamic-observe-{age}"),
            json!({"action":"observe","control_session_id":changed["control_session_id"],"mode":"visual"}),
        );
        session::age_observation_for_test(age);
        unsafe {
            SendMessageW(HWND(fixture.window as *mut _), WM_APP + 4, None, None);
        }
        std::thread::sleep(Duration::from_millis(100));
        let entered = format!(" dynamic-{age}");
        let out = other(
            &format!("dynamic-input-{age}"),
            batch(
                &observation,
                json!([
                    {"action":"click","x":100,"y":100}, {"action":"type_text","text":entered}, {"action":"press_key","key":"Enter"}
                ]),
            ),
        );
        assert_eq!(out["workflow_state"], "completed", "{}", out["reason"]);
        assert_eq!(out["completed_steps"], 3);
        assert!(TEXT.lock().unwrap().ends_with(&entered));
    }
    assert_eq!(ENTERS.load(Ordering::SeqCst), 6);
    assert!(ANIMATION.load(Ordering::SeqCst) > 0);
    println!("Dynamic animation and changed target pixels: 17/30/55-second observations all completed click/type/Enter.");

    let guarded_observation = other(
        "restricted-observe",
        json!({"action":"observe","control_session_id":changed["control_session_id"],"mode":"visual"}),
    );
    session::age_observation_for_test(20_000);
    unsafe {
        SendMessageW(HWND(fixture.window as *mut _), WM_APP + 4, None, None);
    }
    std::thread::sleep(Duration::from_millis(100));
    let mut guarded = batch(
        &guarded_observation,
        json!([{"action":"click","x":100,"y":100}]),
    );
    if guarded.get("context_ref").is_none() {
        guarded["device_epoch"] = json!("visual-fixture");
    }
    guarded["_device_epoch"] = json!("visual-fixture");
    guarded["_control_scope"] = json!("g".repeat(64));
    guarded["_permission_mode"] = json!("ask_each_time");
    guarded["_phase"] = json!("prepare");
    let guarded_result = session::execute("restricted-preflight", &guarded);
    assert_eq!(guarded_result["reason"], "visual_scene_changed");
    assert_eq!(guarded_result["action_state"], "not_started");
    assert_eq!(guarded_result["observation_state"], "complete");
    assert!(guarded_result["screenshots"][0]["imageBase64"].is_string());
    assert_ne!(
        guarded_result["context_ref"],
        guarded_observation["context_ref"]
    );
    println!(
        "Restricted mode kept its pixel check and returned new evidence on failure without input."
    );

    let old = other(
        "before-unannounced-move",
        json!({"action":"observe","control_session_id":changed["control_session_id"],"mode":"visual"}),
    );
    unsafe {
        SetWindowPos(
            HWND(fixture.window as *mut _),
            None,
            380,
            240,
            0,
            0,
            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
        )
        .unwrap();
    }
    let blocked = other("moved-preflight", {
        let mut args = batch(&old, json!([{"action":"click","x":100,"y":100}]));
        args["_phase"] = json!("prepare");
        args
    });
    assert_eq!(blocked["ok"], false);
    assert_eq!(blocked["reason"], "window_layout_changed");
    assert_eq!(blocked["action_state"], "not_started");
    assert_eq!(blocked["observation_state"], "complete");
    assert!(blocked["screenshots"][0]["imageBase64"].is_string());
    assert_ne!(blocked["context_ref"], old["context_ref"]);
    let resumed = other(
        "replan-with-returned-image",
        json!({"action":"click","context_ref":blocked["context_ref"],"x":100,"y":100}),
    );
    assert_eq!(resumed["ok"], true, "{}", resumed["reason"]);
    assert_eq!(
        *LAST_CLICK.lock().unwrap(),
        Some((
            blocked["window_bounds"]["x"].as_i64().unwrap() as i32 + 100,
            blocked["window_bounds"]["y"].as_i64().unwrap() as i32 + 100
        ))
    );
    println!("Moved-window preflight returned new pixels/reference without input; replanning used that reference directly.");

    let crop = other(
        "crop-zoom",
        json!({"action":"observe","context_ref":resumed["context_ref"],"mode":"visual",
        "region":{"x":50,"y":50,"width":150,"height":90},"scale":3}),
    );
    assert_eq!(crop["ok"], true, "{}", crop["reason"]);
    assert_eq!(crop["screenshots"][0]["width"], 450);
    assert_eq!(crop["screenshots"][0]["height"], 270);
    if let Ok(expected) = std::env::var("AKANE_CU_EXPECT_DPI") {
        assert_eq!(crop["dpi"].as_u64(), Some(expected.parse::<u64>().unwrap()));
    }
    let previous_click = *LAST_CLICK.lock().unwrap();
    let invalid = other(
        "crop-out-of-bounds",
        json!({"action":"click","context_ref":crop["context_ref"],"x":450,"y":0}),
    );
    assert_eq!(invalid["reason"], "coordinate_out_of_bounds");
    assert_eq!(invalid["action_state"], "not_started");
    assert_eq!(*LAST_CLICK.lock().unwrap(), previous_click);
    let cropped_click = other(
        "crop-edge-click",
        json!({"action":"click","context_ref":crop["context_ref"],"x":449,"y":269}),
    );
    assert_eq!(cropped_click["ok"], true, "{}", cropped_click["reason"]);
    assert_eq!(
        *LAST_CLICK.lock().unwrap(),
        Some((
            crop["window_bounds"]["x"].as_i64().unwrap() as i32 + 199,
            crop["window_bounds"]["y"].as_i64().unwrap() as i32 + 139
        ))
    );
    let png = base64::engine::general_purpose::STANDARD
        .decode(crop["screenshots"][0]["imageBase64"].as_str().unwrap())
        .unwrap();
    let reader = png::Decoder::new(std::io::Cursor::new(&png))
        .read_info()
        .unwrap();
    assert_eq!((reader.info().width, reader.info().height), (450, 270));
    println!("Coordinate chain: DPI={}, PNG=450x270, crop zoom=3, actual cursor matches inverse mapping at the image edge.",crop["dpi"]);

    let wait_start = Instant::now();
    let waited = other(
        "optional-wait",
        json!({"action":"observe","context_ref":cropped_click["context_ref"],"mode":"visual","wait_ms":150}),
    );
    assert_eq!(waited["ok"], true, "{}", waited["reason"]);
    assert!(wait_start.elapsed() >= Duration::from_millis(150));
    let taken_over = std::thread::scope(|threads| {
        let waiting = threads.spawn(|| other("wait-takeover", json!({"action":"observe","context_ref":waited["context_ref"],"mode":"visual","wait_ms":15000})));
        std::thread::sleep(Duration::from_millis(150));
        monitor::simulate_user_activity();
        waiting.join().unwrap()
    });
    assert_eq!(taken_over["reason"], "user_takeover");
    assert_eq!(taken_over["action_state"], "not_started");
    assert!(taken_over.get("context_ref").is_none());
    let refreshed = other(
        "after-wait-takeover",
        json!({"action":"observe","context_ref":waited["context_ref"],"mode":"visual"}),
    );
    assert_eq!(refreshed["ok"], true, "{}", refreshed["reason"]);
    let stop_start = Instant::now();
    let stopped = std::thread::scope(|threads| {
        let waiting = threads.spawn(|| other("wait-stop", json!({"action":"observe","context_ref":refreshed["context_ref"],"mode":"visual","wait_ms":15000})));
        std::thread::sleep(Duration::from_millis(150));
        session::stop_local();
        waiting.join().unwrap()
    });
    assert!(matches!(
        stopped["reason"].as_str(),
        Some("stopped" | "stopped_requires_local_resume")
    ));
    assert!(stop_start.elapsed() < Duration::from_secs(1));
    assert_eq!(ENTERS.load(Ordering::SeqCst), 6);
    println!("Optional waiting completed; takeover/stop interrupted 15-second waits without input or recovery grants.");
}
