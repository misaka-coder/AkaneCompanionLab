//! Local presentation -> execution-owner controls. No backend/model permission path.
//! Capability choices are persisted by the execution owner, independently of live sessions.
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::time::Duration;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

const MAX_FRAME: usize = 64 * 1024;

fn fail(reason: &str) -> Value {
    json!({"ok":false,"status":"unavailable","reason":reason})
}

fn local(action: &str, args: &Value) -> Value {
    let apply = || if action.starts_with("chrome_") {
        crate::personal_browser::local_control(action,args)
    } else { crate::computer_use::local_control(action,args) };
    let mut result = if matches!(action, "configure" | "chrome_configure") {
        crate::device_preferences::configure(action, args, apply)
    } else { apply() };
    crate::device_preferences::annotate(&mut result);
    result["executor_pid"] = json!(std::process::id());
    result
}

fn pipe_name(instance: &str, root: &str) -> String {
    let hash = Sha256::digest(format!("{instance}\0{root}").as_bytes());
    format!(r"\\.\pipe\Akane.DeviceControl.{hash:x}")
}

fn binding() -> Result<(String,String,String), &'static str> {
    let instance = crate::runtime_instance_id().map_err(|_|"local_control_binding_missing")?;
    let root = crate::akane_data_root().map_err(|_|"local_control_binding_missing")?
        .canonicalize().map_err(|_|"local_control_binding_missing")?;
    let token = std::env::var(crate::SATELLITE_TOKEN_ENV).map_err(|_|"local_control_binding_missing")?;
    if token.trim().is_empty() {return Err("local_control_binding_missing");}
    Ok((pipe_name(&instance,&root.to_string_lossy().to_lowercase()),instance,token))
}

async fn read_frame<S:AsyncRead+Unpin>(stream:&mut S)->Result<Value,&'static str>{
    let size=stream.read_u32().await.map_err(|_|"local_control_disconnected")? as usize;
    if size==0 || size>MAX_FRAME {return Err("local_control_frame_invalid");}
    let mut bytes=vec![0;size];
    stream.read_exact(&mut bytes).await.map_err(|_|"local_control_disconnected")?;
    serde_json::from_slice(&bytes).map_err(|_|"local_control_frame_invalid")
}
async fn write_frame<S:AsyncWrite+Unpin>(stream:&mut S,value:&Value)->Result<(),&'static str>{
    let bytes=serde_json::to_vec(value).map_err(|_|"local_control_frame_invalid")?;
    if bytes.len()>MAX_FRAME {return Err("local_control_frame_invalid");}
    stream.write_u32(bytes.len() as u32).await.map_err(|_|"local_control_disconnected")?;
    stream.write_all(&bytes).await.map_err(|_|"local_control_disconnected")
}

fn authenticated(frame:&Value,instance:&str,token:&str)->bool {
    !token.is_empty() && frame["version"]==1 && frame["instance"].as_str()==Some(instance)
        && frame["token"].as_str()==Some(token)
}

pub async fn request(action:&str,args:Value)->Value {
    if !crate::env_flag_enabled(crate::EXTERNAL_DEVICE_EXECUTOR_ENV) || crate::is_device_only_mode() {
        return local(action,&args);
    }
    #[cfg(windows)] {
        let (pipe,instance,token)=match binding(){Ok(v)=>v,Err(r)=>return fail(r)};
        exchange(&pipe,&instance,&token,action,args).await
    }
    #[cfg(not(windows))] {fail("unsupported_platform")}
}

#[cfg(windows)]
async fn exchange(pipe:&str,instance:&str,token:&str,action:&str,args:Value)->Value {
    use tokio::net::windows::named_pipe::ClientOptions;
    // Never retry a mutation after timeout: execution may already have happened.
    let result=tokio::time::timeout(Duration::from_secs(5),async {
        let mut stream=loop {
            match ClientOptions::new().open(pipe) {
                Ok(stream)=>break stream,
                // No request bytes have been written. Parallel status reads may
                // arrive before the accept loop installs its next listener.
                Err(error) if error.raw_os_error()==Some(231)=>tokio::time::sleep(Duration::from_millis(10)).await,
                Err(_)=>return Err("local_executor_unavailable"),
            }
        };
        write_frame(&mut stream,&json!({"version":1,"instance":instance,"token":token,"action":action,"arguments":args})).await?;
        let result=read_frame(&mut stream).await?;
        if result["version"]!=1 || result["instance"].as_str()!=Some(instance) {return Err("local_control_response_invalid");}
        result.get("result").cloned().ok_or("local_control_response_invalid")
    }).await;
    match result {Ok(Ok(v))=>v,Ok(Err(r))=>fail(r),Err(_)=>fail("local_control_timeout_state_unknown")}
}

#[cfg(windows)]
pub fn start_server()->Result<(), &'static str> {
    let (pipe,instance,token)=binding()?;
    spawn_server(pipe,instance,token,local)?;
    Ok(())
}
#[cfg(not(windows))]
pub fn start_server()->Result<(), &'static str> {Err("unsupported_platform")}

#[cfg(windows)]
fn spawn_server(pipe:String,instance:String,token:String,dispatch:fn(&str,&Value)->Value)
    ->Result<tokio::task::JoinHandle<()>, &'static str> {
    spawn_peer_server(pipe,instance,token,move |action,args,_|dispatch(action,args))
}

#[cfg(windows)]
fn spawn_peer_server<F>(pipe:String,instance:String,token:String,dispatch:F)
    ->Result<tokio::task::JoinHandle<()>, &'static str>
where F:Fn(&str,&Value,u32)->Value+Copy+Send+Sync+'static {
    use tokio::net::windows::named_pipe::ServerOptions;
    let mut server=ServerOptions::new().first_pipe_instance(true).reject_remote_clients(true)
        .create(&pipe).map_err(|_|"local_control_listen_failed")?;
    Ok(tokio::spawn(async move {
        let slots=std::sync::Arc::new(tokio::sync::Semaphore::new(16));
        loop {
            if server.connect().await.is_err(){break;}
            // Always leave a listener available before dispatching a slow control.
            let next=match ServerOptions::new().reject_remote_clients(true).create(&pipe){Ok(v)=>v,Err(_)=>break};
            let mut stream=std::mem::replace(&mut server,next);
            let Ok(permit)=slots.clone().try_acquire_owned() else {continue;};
            let instance=instance.clone();let token=token.clone();
            tokio::spawn(async move {
                let _permit=permit;
                let _=tokio::time::timeout(Duration::from_secs(4),async {
                    use std::os::windows::io::AsRawHandle;
                    use windows::Win32::{Foundation::HANDLE,System::Pipes::GetNamedPipeClientProcessId};
                    let mut peer=0;
                    unsafe {GetNamedPipeClientProcessId(HANDLE(stream.as_raw_handle()),&mut peer)}
                        .map_err(|_|"local_control_peer_unavailable")?;
                    let frame=read_frame(&mut stream).await?;
                    let result=if !authenticated(&frame,&instance,&token){fail("local_control_unauthorized")}
                    else {
                        let action=frame["action"].as_str().unwrap_or("").to_string();
                        let args=frame["arguments"].clone();
                        // UIA enumeration must not hold up local emergency stop or Satellite.
                        tokio::task::spawn_blocking(move||dispatch(&action,&args,peer)).await
                            .unwrap_or_else(|_|fail("local_control_worker_failed"))
                    };
                    write_frame(&mut stream,&json!({"version":1,"instance":instance,"result":result})).await
                }).await;
            });
        }
    }))
}

// The presentation process owns Windows' foreground permission after a user
// chats with the pet. Delegate only to the authenticated pipe caller, never to
// a PID supplied by a model or to ASFW_ANY. This does not focus/hide any window.
#[cfg(windows)]
fn grant_foreground(action:&str,_args:&Value,peer:u32)->Value {
    use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow,GetWindowThreadProcessId,AllowSetForegroundWindow};
    if action!="grant_foreground" || peer==0 {return fail("foreground_handoff_invalid");}
    unsafe {
        let mut owner=0;
        GetWindowThreadProcessId(GetForegroundWindow(),Some(&mut owner));
        if owner!=std::process::id(){return fail("presentation_not_foreground");}
        if AllowSetForegroundWindow(peer).is_err(){return fail("foreground_handoff_denied");}
    }
    json!({"ok":true})
}

pub fn start_foreground_server() {
    #[cfg(windows)]
    if crate::env_flag_enabled(crate::EXTERNAL_DEVICE_EXECUTOR_ENV) && !crate::is_device_only_mode() {
        tauri::async_runtime::spawn(async {
            let result=binding().and_then(|(pipe,instance,token)|
                spawn_peer_server(format!("{pipe}.Foreground"),instance,token,grant_foreground));
            if let Err(reason)=result {eprintln!("Akane foreground handoff unavailable: {reason}");}
        });
    }
}

/// Called only by a permitted activation, from the native execution worker.
/// An unavailable/background presentation is a bounded no-op, not permission
/// to steal foreground from unrelated apps or weaken the input guards.
#[cfg(windows)]
pub fn request_foreground_handoff()->bool {
    if !crate::is_device_only_mode(){return false;}
    let Ok((pipe,instance,token))=binding() else {return false;};
    std::thread::spawn(move || {
        let Ok(runtime)=tokio::runtime::Builder::new_current_thread().enable_all().build() else{return false;};
        runtime.block_on(async {
            tokio::time::timeout(Duration::from_millis(700),
                exchange(&format!("{pipe}.Foreground"),&instance,&token,"grant_foreground",json!({})))
                .await.is_ok_and(|result|result["ok"]==true)
        })
    }).join().unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn endpoint_and_auth_are_instance_bound(){
        assert_ne!(pipe_name("one","root"),pipe_name("two","root"));
        assert_ne!(pipe_name("one","root"),pipe_name("one","other"));
        let frame=json!({"version":1,"instance":"one","token":"secret"});
        assert!(authenticated(&frame,"one","secret"));
        assert!(!authenticated(&frame,"two","secret"));
        assert!(!authenticated(&frame,"one","wrong"));
        assert!(!authenticated(&json!({"version":1,"instance":"one","token":""}),"one",""));
    }
    #[cfg(windows)]
    #[tokio::test] async fn handoff_uses_authenticated_os_peer_not_requested_pid(){
        let pipe=pipe_name("peer",&format!("{}-{}",std::process::id(),crate::current_time_millis()));
        let server=spawn_peer_server(pipe.clone(),"test".into(),"secret".into(),
            |_,_,peer|json!({"ok":true,"peer":peer})).unwrap();
        assert_eq!(exchange(&pipe,"test","wrong","grant_foreground",json!({})).await["reason"],"local_control_unauthorized");
        let result=exchange(&pipe,"test","secret","grant_foreground",json!({"pid":u32::MAX})).await;
        assert_eq!(result["peer"],std::process::id());
        assert_eq!(grant_foreground("configure",&json!({}),std::process::id())["ok"],false);
        assert_eq!(grant_foreground("grant_foreground",&json!({}),0)["ok"],false);
        server.abort();
    }
    #[tokio::test] async fn oversized_frame_is_rejected_before_allocation(){
        let (mut a,mut b)=tokio::io::duplex(16);
        a.write_u32(MAX_FRAME as u32+1).await.unwrap();
        assert_eq!(read_frame(&mut b).await.unwrap_err(),"local_control_frame_invalid");
    }
    #[cfg(windows)]
    #[test]
    #[ignore = "child helper for the isolated execution-owner test"]
    fn child_execution_owner(){
        let Ok(pipe)=std::env::var("AKANE_TEST_CONTROL_PIPE") else {return;};
        if std::env::var_os("AKANE_TEST_PREFERENCES").is_some() {crate::device_preferences::initialize();}
        let runtime=tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        runtime.block_on(async {
            crate::control_lease::begin_connection("control-test-epoch");
            fn dispatch(action:&str,args:&Value)->Value {
                if action=="tool_status" {
                    // Exactly the executor's tool entry point, separate from UI status.
                    static NEXT:std::sync::atomic::AtomicU64=std::sync::atomic::AtomicU64::new(0);
                    let id=format!("probe-{}",NEXT.fetch_add(1,std::sync::atomic::Ordering::SeqCst));
                    crate::computer_use::execute(&id,&json!({"action":"status",
                        "_control_scope":"a".repeat(64),"_device_epoch":"control-test-epoch"}))
                } else {local(action,args)}
            }
            let _server=spawn_server(pipe,"isolated".into(),"test-token".into(),dispatch).unwrap();
            tokio::time::sleep(Duration::from_secs(20)).await;
        });
    }
    #[cfg(windows)]
    #[tokio::test]
    async fn ui_and_tool_status_share_the_separate_execution_owner(){
        use std::process::{Command,Stdio};
        struct Child(std::process::Child);
        impl Drop for Child {fn drop(&mut self){let _=self.0.kill();let _=self.0.wait();}}
        let pipe=pipe_name("child",&format!("{}-{}",std::process::id(),crate::current_time_millis()));
        let child=Child(Command::new(std::env::current_exe().unwrap())
            .args(["--exact","device_control::tests::child_execution_owner","--ignored"])
            .env("AKANE_TEST_CONTROL_PIPE",&pipe).stdout(Stdio::null()).stderr(Stdio::null()).spawn().unwrap());
        let mut ready=false;
        for _ in 0..100 {
            if exchange(&pipe,"isolated","test-token","status",json!({})).await["ok"]==true {ready=true;break;}
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        assert!(ready,"separate executor did not start");
        let call=|action,args|exchange(&pipe,"isolated","test-token",action,args);
        let initial=call("status",json!({})).await;
        assert_eq!(initial["executor_pid"],child.0.id());
        assert_ne!(initial["executor_pid"],std::process::id());
        assert_eq!(initial["input_enabled"],false);
        assert_eq!(call("tool_status",json!({})).await["input_enabled"],false);
        assert_eq!(call("configure",json!({"enabled":true})).await["input_enabled"],true);
        assert_eq!(call("tool_status",json!({})).await["input_enabled"],true);
        assert_eq!(call("stop",json!({})).await["status"],"stopped");
        assert_eq!(call("tool_status",json!({})).await["status"],"stopped");
        assert_eq!(call("resume",json!({})).await["ok"],true);
        assert_eq!(call("configure",json!({"enabled":false})).await["input_enabled"],false);
        assert_eq!(call("tool_status",json!({})).await["input_enabled"],false);
        assert_eq!(call("configure",json!({"enabled":true})).await["status"],"idle");
        assert_eq!(call("tool_status",json!({})).await["input_enabled"],true);
    }
    #[cfg(windows)]
    #[tokio::test] async fn choices_survive_execution_owner_restart(){
        use std::process::{Command,Stdio};
        struct Child(std::process::Child);
        impl Drop for Child {fn drop(&mut self){let _=self.0.kill();let _=self.0.wait();}}
        let root=std::env::temp_dir().join(format!("akane-owner-restart-{}-{}",std::process::id(),crate::current_time_millis()));
        for round in 0..3 {
            let pipe=pipe_name("restart",&format!("{}-{round}",root.display()));
            let child=Child(Command::new(std::env::current_exe().unwrap())
                .args(["--exact","device_control::tests::child_execution_owner","--ignored"])
                .env("AKANE_TEST_CONTROL_PIPE",&pipe).env("AKANE_TEST_PREFERENCES","1")
                .env("AKANE_DATA_ROOT",&root).env("AKANE_INSTANCE_ID","isolated")
                .env_remove(crate::EXTERNAL_DEVICE_EXECUTOR_ENV)
                .stdout(Stdio::null()).stderr(Stdio::null()).spawn().unwrap());
            let call=|action,args|exchange(&pipe,"isolated","test-token",action,args);
            let mut ready=false;
            for _ in 0..100 {
                if call("status",json!({})).await["ok"]==true {ready=true;break;}
                tokio::time::sleep(Duration::from_millis(50)).await;
            }
            assert!(ready);
            assert_eq!(call("status",json!({})).await["input_enabled"],round!=1);
            assert_eq!(call("chrome_status",json!({})).await["enabled"],round!=2);
            assert!(call("status",json!({})).await.get("control_session_id").is_none());
            if round<2 {
                assert_eq!(call("configure",json!({"enabled":round==1})).await["preference_saved"],true);
                assert_eq!(call("chrome_configure",json!({"enabled":round==0})).await["preference_saved"],true);
                // Emergency stop is a live-session control, not a setting change.
                assert_eq!(call("stop",json!({})).await["ok"],true);
            }
            drop(child);
        }
        std::fs::remove_dir_all(root).unwrap();
    }
    #[cfg(windows)]
    #[tokio::test] async fn real_pipe_routes_controls_and_fails_closed(){
        use std::sync::atomic::{AtomicBool,Ordering};
        static ENABLED:AtomicBool=AtomicBool::new(false);
        fn dispatch(action:&str,args:&Value)->Value {
            if action=="configure" {ENABLED.store(args["enabled"]==true,Ordering::SeqCst);}
            if action=="slow" {std::thread::sleep(Duration::from_millis(250));}
            json!({"ok":true,"input_enabled":ENABLED.load(Ordering::SeqCst)})
        }
        let pipe=pipe_name("test",&format!("{}-{}",std::process::id(),crate::current_time_millis()));
        let server=spawn_server(pipe.clone(),"test".into(),"secret".into(),dispatch).unwrap();
        assert_eq!(exchange(&pipe,"test","wrong","configure",json!({"enabled":true})).await["ok"],false);
        assert_eq!(exchange(&pipe,"test","secret","status",json!({})).await["input_enabled"],false);
        assert_eq!(exchange(&pipe,"test","secret","configure",json!({"enabled":true})).await["input_enabled"],true);
        assert_eq!(exchange(&pipe,"test","secret","status",json!({})).await["input_enabled"],true);
        let (a,b)=tokio::join!(exchange(&pipe,"test","secret","status",json!({})),
            exchange(&pipe,"test","secret","status",json!({})));
        assert_eq!(a["ok"],true);assert_eq!(b["ok"],true);
        let slow=tokio::spawn({let pipe=pipe.clone();async move {exchange(&pipe,"test","secret","slow",json!({})).await}});
        tokio::time::sleep(Duration::from_millis(30)).await;
        let quick=tokio::time::timeout(Duration::from_millis(150),exchange(&pipe,"test","secret","configure",json!({"enabled":false}))).await.unwrap();
        assert_eq!(quick["input_enabled"],false);
        slow.await.unwrap();server.abort();let _=server.await;
        assert_eq!(exchange(&pipe,"test","secret","status",json!({})).await["reason"],"local_executor_unavailable");
    }
}

#[cfg(all(test,windows))]
#[path="device_control_foreground_tests.rs"]
mod foreground_tests;
