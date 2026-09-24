//! Device-local scope selected by the owner, never by model arguments.
use std::{collections::HashMap,sync::{Mutex,OnceLock,atomic::{AtomicU64,Ordering}}};
use serde_json::{json,Value};
use super::{windows::{self,Identity},protocol};
#[derive(Default)]struct Scope {known:HashMap<String,Identity>,selected:Option<(String,Identity)>}
static SCOPE:OnceLock<Mutex<Scope>>=OnceLock::new();
static NEXT:AtomicU64=AtomicU64::new(1);
pub fn list()->Value {
    let live=windows::enumerate();
    let Ok(mut scope)=SCOPE.get_or_init(Default::default).lock()else{return protocol::fail("target_scope_unavailable");};
    scope.known.retain(|_,id|live.iter().any(|(current,_)|current==id));
    let mut rows=Vec::new();
    for (id,title) in live {
        let key=scope.known.iter().find(|(_,current)|*current==&id).map(|(key,_)|key.clone())
            .unwrap_or_else(||format!("local_target_{}",NEXT.fetch_add(1,Ordering::Relaxed)));
        rows.push(json!({"id":key,"title":title,"application":windows::process_name(&id)}));scope.known.insert(key,id);
    }
    json!({"ok":true,"targets":rows})
}
pub fn configure(key:&str)->Value {
    let selected={
        let Ok(scope)=SCOPE.get_or_init(Default::default).lock()else{return protocol::fail("target_scope_unavailable");};
        if key.is_empty(){None}else{
            let Some(id)=scope.known.get(key)else{return protocol::fail("local_target_expired");};
            if windows::identity(windows::handle(id.hwnd)).as_ref()!=Some(id){return protocol::fail("local_target_expired");}
            Some((key.to_owned(),id.clone()))
        }
    };
    // Revoke current input before changing the local restriction.
    super::session::stop_local();
    let Ok(mut scope)=SCOPE.get_or_init(Default::default).lock()else{return protocol::fail("target_scope_unavailable");};
    scope.selected=selected;json!({"ok":true})
}
pub fn status()->Value {
    let Ok(scope)=SCOPE.get_or_init(Default::default).lock()else{return json!({"target_scope":"unavailable"});};
    match &scope.selected {
        Some((key,id))=>json!({"target_scope":"window","target_id":key,
            "target_title":if windows::identity(windows::handle(id.hwnd)).as_ref()==Some(id){windows::title(windows::handle(id.hwnd))}else{"目标窗口已关闭".into()}}),
        None=>json!({"target_scope":"task","target_id":"","target_title":"按任务选择窗口"}),
    }
}
pub fn allows(id:&Identity)->bool {
    SCOPE.get_or_init(Default::default).lock().map(|scope|scope.selected.as_ref().is_none_or(|(_,root)|windows::owned_by(root,id))).unwrap_or(false)
}
pub fn unrestricted()->bool {SCOPE.get_or_init(Default::default).lock().map(|scope|scope.selected.is_none()).unwrap_or(false)}
