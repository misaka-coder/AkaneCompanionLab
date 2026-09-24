//! Interactive two-process foreground regression. Only disposable test windows.
use super::*;
use std::{sync::{mpsc,atomic::{AtomicBool,Ordering}},path::PathBuf};
use windows::{core::w,Win32::{Foundation::{HWND,LPARAM,LRESULT,WPARAM},
    System::Threading::GetCurrentThreadId,UI::WindowsAndMessaging::*}};
static CLICKED:AtomicBool=AtomicBool::new(false);
unsafe extern "system" fn procedure(hwnd:HWND,msg:u32,w:WPARAM,l:LPARAM)->LRESULT {
    if msg==WM_LBUTTONUP {CLICKED.store(true,Ordering::SeqCst);}
    DefWindowProcW(hwnd,msg,w,l)
}
struct Fixture {hwnd:isize,thread:u32,worker:Option<std::thread::JoinHandle<()>>}
impl Drop for Fixture {fn drop(&mut self){unsafe{let _=PostThreadMessageW(self.thread,WM_QUIT,WPARAM(0),LPARAM(0));}
    if let Some(worker)=self.worker.take(){worker.join().unwrap();}}}
fn fixture(presentation:bool)->Fixture {
    let(tx,rx)=mpsc::channel();let worker=std::thread::spawn(move||unsafe{
        let class=WNDCLASSW{lpfnWndProc:Some(procedure),lpszClassName:w!("AkaneForegroundFixture"),
            hbrBackground:windows::Win32::Graphics::Gdi::GetSysColorBrush(windows::Win32::Graphics::Gdi::COLOR_WINDOW),..Default::default()};
        RegisterClassW(&class);
        let title=if presentation{w!("Akane Foreground Test - click white area")}else{w!("Akane Foreground Target")};
        let hwnd=CreateWindowExW(WINDOW_EX_STYLE(0),w!("AkaneForegroundFixture"),title,WS_OVERLAPPEDWINDOW,
            if presentation{200}else{820},220,580,300,None,None,None,None).unwrap();
        let _=ShowWindow(hwnd,SW_SHOWNOACTIVATE);
        tx.send((hwnd.0 as isize,GetCurrentThreadId())).unwrap();let mut msg=MSG::default();
        while GetMessageW(&mut msg,None,0,0).as_bool(){let _=TranslateMessage(&msg);DispatchMessageW(&msg);}
        let _=DestroyWindow(hwnd);
    });
    let(hwnd,thread)=rx.recv_timeout(Duration::from_secs(5)).unwrap();Fixture{hwnd,thread,worker:Some(worker)}
}
#[test]
#[ignore="subprocess of live_foreground_handoff"]
fn foreground_child(){
    let Ok(root)=std::env::var("AKANE_HANDOFF_TEST_ROOT") else{return;};let root=PathBuf::from(root);
    let target=fixture(false);std::fs::write(root.join("ready"),b"ready").unwrap();
    let start=std::time::Instant::now();
    while !root.join("go").exists(){assert!(start.elapsed()<Duration::from_secs(90));std::thread::sleep(Duration::from_millis(20));}
    let hwnd=HWND(target.hwnd as *mut _);
    assert!(!unsafe{SetForegroundWindow(hwnd)}.as_bool(),"baseline must reproduce activation_denied");
    let rt=tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
    let pipe=std::env::var("AKANE_HANDOFF_TEST_PIPE").unwrap();
    let grant=rt.block_on(exchange(&pipe,"fixture","secret","grant_foreground",json!({"pid":u32::MAX})));
    assert_eq!(grant["ok"],true,"{grant}");
    assert!(unsafe{SetForegroundWindow(hwnd)}.as_bool());
    for _ in 0..30{if unsafe{GetForegroundWindow()}==hwnd{break;}std::thread::sleep(Duration::from_millis(10));}
    assert_eq!(unsafe{GetForegroundWindow()},hwnd);
    // A background presentation must not hand control back or grant another request.
    let denied=rt.block_on(exchange(&pipe,"fixture","secret","grant_foreground",json!({})));
    assert_eq!(denied["reason"],"presentation_not_foreground");
    println!("Verified: baseline denied; authenticated foreground handoff activated target; background presentation refused.");
}
#[tokio::test]
#[ignore="interactive: click only the disposable white presentation fixture"]
async fn live_foreground_handoff(){
    use std::process::{Command,Stdio};
    assert_eq!(std::env::var("AKANE_HANDOFF_LIVE").as_deref(),Ok("1"));
    let root=std::env::temp_dir().join(format!("akane-handoff-{}-{}",std::process::id(),crate::current_time_millis()));
    std::fs::create_dir_all(&root).unwrap();
    let pipe=pipe_name("fixture",&root.to_string_lossy());
    let server=spawn_peer_server(pipe.clone(),"fixture".into(),"secret".into(),grant_foreground).unwrap();
    struct Child(std::process::Child);impl Drop for Child{fn drop(&mut self){let _=self.0.kill();let _=self.0.wait();}}
    let mut child=Child(Command::new(std::env::current_exe().unwrap())
        .args(["--exact","device_control::foreground_tests::foreground_child","--ignored","--nocapture"])
        .env("AKANE_HANDOFF_TEST_ROOT",&root).env("AKANE_HANDOFF_TEST_PIPE",&pipe)
        .stdout(Stdio::inherit()).stderr(Stdio::inherit()).spawn().unwrap());
    for _ in 0..200 {if root.join("ready").exists(){break;}tokio::time::sleep(Duration::from_millis(20)).await;}
    assert!(root.join("ready").exists());let ui=fixture(true);
    println!("READY: click the white area of Akane Foreground Test.");
    for _ in 0..4500 {if CLICKED.load(Ordering::SeqCst){break;}tokio::time::sleep(Duration::from_millis(20)).await;}
    assert!(CLICKED.load(Ordering::SeqCst),"fixture was not clicked");
    assert_eq!(unsafe{GetForegroundWindow()},HWND(ui.hwnd as *mut _));
    std::fs::write(root.join("go"),b"go").unwrap();
    let mut status=None;
    for _ in 0..400 {status=child.0.try_wait().unwrap();if status.is_some(){break;}tokio::time::sleep(Duration::from_millis(20)).await;}
    assert!(status.is_some_and(|s|s.success()));server.abort();drop(ui);
    std::fs::remove_dir_all(root).unwrap();
}
