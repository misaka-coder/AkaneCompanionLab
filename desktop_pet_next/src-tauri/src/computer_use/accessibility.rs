//! One bounded MTA worker. A hung provider cannot block local stop or spawn more workers.
use std::{sync::{mpsc::{self, SyncSender}, OnceLock,Arc,atomic::{AtomicBool,Ordering}}, time::{Duration,Instant}};
use serde_json::{json, Value};
use windows::Win32::{System::Com::{CoCreateInstance, CoInitializeEx, CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED},
    UI::Accessibility::*};
use super::windows::{handle, Identity, bounds_json};

enum Operation { Snapshot, ReadElement {element:String}, SetEmpty {text:String,element:String,generation:u64,cancelled:Arc<AtomicBool>,deadline:Instant},
    SetState {kind:String,element:String,expected:Value,desired:Value,generation:u64,cancelled:Arc<AtomicBool>,deadline:Instant} }
struct Request { identity: Identity, observation: String, operation:Operation, reply: SyncSender<Value> }
static WORKER: OnceLock<SyncSender<Request>> = OnceLock::new();

fn worker() -> &'static SyncSender<Request> {
    WORKER.get_or_init(|| {
        let (tx, rx) = mpsc::sync_channel::<Request>(1);
        std::thread::Builder::new().name("akane-uia-mta".into()).spawn(move || {
            unsafe {
                if CoInitializeEx(None, COINIT_MULTITHREADED).is_err() { return; }
                let Ok(automation): Result<IUIAutomation, _> = CoCreateInstance(&CUIAutomation, None, CLSCTX_INPROC_SERVER) else { return; };
                while let Ok(request) = rx.recv() {
                    // If the requester already timed out, do not accumulate results.
                    let result = match &request.operation {
                        Operation::Snapshot => read(&automation, &request.identity, &request.observation),
                        Operation::ReadElement{element} => read_scope(&automation, &request.identity, &request.observation, Some(element)),
                        Operation::SetEmpty{text,element,generation,cancelled,deadline} =>
                            set_empty(&automation,&request.identity,&request.observation,element,text,*generation,cancelled,*deadline),
                        Operation::SetState{kind,element,expected,desired,generation,cancelled,deadline}=>
                            set_state(&automation,&request.identity,&request.observation,kind,element,expected,desired,*generation,cancelled,*deadline),
                    };
                    let _ = request.reply.try_send(result.unwrap_or_else(|reason| unavailable(reason)));
                }
            }
        }).expect("UIA worker thread");
        tx
    })
}
pub fn snapshot(identity: &Identity, observation: &str) -> Value {
    let worker=worker();
    let (tx, rx) = mpsc::sync_channel(1);
    if worker.try_send(Request { identity: identity.clone(), observation: observation.to_string(), operation:Operation::Snapshot, reply: tx }).is_err() {
        return unavailable("uia_worker_busy");
    }
    let mut result=rx.recv_timeout(Duration::from_millis(1500)).unwrap_or_else(|_| unavailable("uia_timeout"));
    project_references(&mut result);result
}

pub fn read_element(identity:&Identity,observation:&str,element:&str)->Value {
    let (tx,rx)=mpsc::sync_channel(1);
    if worker().try_send(Request{identity:identity.clone(),observation:observation.into(),
        operation:Operation::ReadElement{element:element.into()},reply:tx}).is_err(){return unavailable("uia_worker_busy");}
    let mut result=rx.recv_timeout(Duration::from_millis(1500)).unwrap_or_else(|_|unavailable("uia_timeout"));
    // Detail reads do not mint actionable references or replace the selected observation.
    if let Some(rows)=result["elements"].as_array_mut(){for row in rows{if let Some(row)=row.as_object_mut(){row.remove("element_id");}}}
    if let Some(focus)=result["focus_evidence"].as_object_mut(){focus.remove("element_id");}
    result["read_only_details"]=json!(true);result
}

fn project_references(snapshot:&mut Value){
    let mut referenceable=0;let mut read_only=0;
    if let Some(rows)=snapshot["elements"].as_array_mut(){for row in rows{
        if row["stable_identity"]==true {referenceable+=1;}
        else {if let Some(row)=row.as_object_mut(){row.remove("element_id");}read_only+=1;}
    }}
    snapshot["ax_references"]=json!({"referenceable":referenceable,"read_only":read_only});
}

#[cfg(test)]mod reference_tests{
    use super::*;
    #[test]fn unstable_elements_keep_readable_content_without_unusable_action_ids(){
        let mut value=json!({"elements":[
            {"element_id":"obs_element_stable","stable_identity":true,"name":"Editor"},
            {"element_id":"obs_element_unstable_3","stable_identity":false,"name":"Search","editable":true,
             "value_pattern":true,"bounds":{"x":10,"y":20,"width":100,"height":30}}]});
        project_references(&mut value);
        assert_eq!(value["elements"][0]["element_id"],"obs_element_stable");
        let read_only=&value["elements"][1];assert!(read_only.get("element_id").is_none());
        assert_eq!(read_only["name"],"Search");assert_eq!(read_only["value_pattern"],true);assert_eq!(read_only["bounds"]["x"],10);
        assert_eq!(value["ax_references"],json!({"referenceable":1,"read_only":1}));
    }
}

/// A timeout may have followed SetValue: callers must not fall back or replay.
pub fn set_empty_value(identity:&Identity,observation:&str,element:&str,text:&str,generation:u64)->Value {
    let cancelled=Arc::new(AtomicBool::new(false));let (tx,rx)=mpsc::sync_channel(1);
    let operation=Operation::SetEmpty{text:text.into(),element:element.into(),generation,cancelled:cancelled.clone(),deadline:Instant::now()+Duration::from_millis(1300)};
    if worker().try_send(Request{identity:identity.clone(),observation:observation.into(),operation,reply:tx}).is_err(){return json!({"value_state":"not_started","reason":"uia_worker_busy"});}
    match rx.recv_timeout(Duration::from_millis(1500)) {Ok(v)=>v,Err(_)=>{cancelled.store(true,Ordering::SeqCst);json!({"value_state":"unknown","reason":"uia_timeout"})}}
}

pub fn set_control_state(identity:&Identity,observation:&str,kind:&str,element:&str,expected:Value,desired:Value,generation:u64)->Value{
    let cancelled=Arc::new(AtomicBool::new(false));let (tx,rx)=mpsc::sync_channel(1);
    let operation=Operation::SetState{kind:kind.into(),element:element.into(),expected,desired,generation,
        cancelled:cancelled.clone(),deadline:Instant::now()+Duration::from_millis(1300)};
    if worker().try_send(Request{identity:identity.clone(),observation:observation.into(),operation,reply:tx}).is_err(){return json!({"value_state":"not_started","reason":"uia_worker_busy"});}
    match rx.recv_timeout(Duration::from_millis(1500)){Ok(v)=>v,Err(_)=>{cancelled.store(true,Ordering::SeqCst);json!({"value_state":"unknown","reason":"uia_timeout"})}}
}

unsafe fn set_state(automation:&IUIAutomation,identity:&Identity,observation:&str,kind:&str,expected_id:&str,
    expected:&Value,desired:&Value,generation:u64,cancelled:&AtomicBool,deadline:Instant)->Result<Value,&'static str>{
    let permitted=||!cancelled.load(Ordering::SeqCst)&&Instant::now()<deadline&&!crate::control_lease::stopped()
        &&super::monitor::input_generation()==generation&&super::windows::foreground(identity)
        &&super::windows::identity(handle(identity.hwnd)).as_ref()==Some(identity);
    let not_started=|reason|json!({"value_state":"not_started","reason":reason});
    if !permitted(){return Ok(not_started("input_interrupted"));}
    let root=automation.ElementFromHandle(handle(identity.hwnd)).map_err(|_|"uia_root_unavailable")?;
    let walker=automation.ControlViewWalker().map_err(|_|"uia_walker_unavailable")?;
    let root_key=runtime_key(&root);
    let mut queue=std::collections::VecDeque::from([root]);let mut visited=0;
    if kind=="set_value" {
        if let Ok(focused)=automation.GetFocusedElement(){
            if runtime_key(&focused).map(|key|format!("{observation}_element_{key}")).as_deref()==Some(expected_id)&&root_key.is_some(){
                let mut current=Some(focused.clone());
                for _ in 0..24 {
                    let Some(node)=current else{break;};if !permitted(){break;}
                    if runtime_key(&node)==root_key{queue.push_front(focused);break;}
                    current=walker.GetParentElement(&node).ok();
                }
            }
        }
    }
    while let Some(element)=queue.pop_front(){
        if !permitted(){return Ok(not_started("input_interrupted"));}
        visited+=1;if visited>160{return Ok(not_started("element_expired"));}
        if runtime_key(&element).map(|key|format!("{observation}_element_{key}")).as_deref()==Some(expected_id){
            if element.CurrentIsPassword().map(|v|v.as_bool()).unwrap_or(true)
                || !element.CurrentIsEnabled().map(|v|v.as_bool()).unwrap_or(false)
                || element.CurrentIsOffscreen().map(|v|v.as_bool()).unwrap_or(true){return Ok(not_started("element_unavailable"));}
            if kind=="set_value" {
                let Ok(pattern)=element.GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId)else{return Ok(not_started("value_pattern_unavailable"));};
                if pattern.CurrentIsReadOnly().map(|v|v.as_bool()).unwrap_or(true){return Ok(not_started("value_readonly"));}
                let current=pattern.CurrentValue().map_err(|_|"value_unavailable")?.to_string();
                if expected.as_str()!=Some(current.as_str()){return Ok(not_started("value_changed"));}
                let Some(value)=desired.as_str()else{return Ok(not_started("value_invalid"));};
                if value==current{return Ok(json!({"value_state":"unchanged"}));}
                if !permitted(){return Ok(not_started("input_interrupted"));}
                return Ok(match pattern.SetValue(&windows::core::BSTR::from(value)){Ok(_)=>json!({"value_state":"executed"}),Err(_)=>json!({"value_state":"unknown","reason":"uia_set_value_failed"})});
            }
            let Ok(pattern)=element.GetCurrentPatternAs::<IUIAutomationTogglePattern>(UIA_TogglePatternId)else{return Ok(not_started("toggle_pattern_unavailable"));};
            let current=pattern.CurrentToggleState().map_err(|_|"toggle_state_unavailable")?;
            if !matches!(current.0,0|1){return Ok(not_started("toggle_indeterminate"));}
            if expected.as_bool()!=Some(current==ToggleState_On){return Ok(not_started("checked_changed"));}
            if desired.as_bool()==Some(current==ToggleState_On){return Ok(json!({"value_state":"unchanged"}));}
            if !permitted(){return Ok(not_started("input_interrupted"));}
            return Ok(match pattern.Toggle(){Ok(_)=>json!({"value_state":"executed"}),Err(_)=>json!({"value_state":"unknown","reason":"uia_toggle_failed"})});
        }
        let mut child=walker.GetFirstChildElement(&element).ok();
        while let Some(node)=child {if queue.len()+visited>=160||!permitted(){break;}child=walker.GetNextSiblingElement(&node).ok();queue.push_back(node);}
    }
    Ok(not_started("element_expired"))
}

unsafe fn add_control_state(element:&IUIAutomationElement,row:&mut Value){
    if row["password"]!=false{return;}
    row["automation_id"]=json!(element.CurrentAutomationId().map(|v|v.to_string().chars().take(256).collect::<String>()).unwrap_or_default());
    if let Ok(toggle)=element.GetCurrentPatternAs::<IUIAutomationTogglePattern>(UIA_TogglePatternId){
        if let Ok(state)=toggle.CurrentToggleState(){row["checked"]=match state.0 {0=>json!(false),1=>json!(true),_=>Value::Null};}
    }
}
unsafe fn runtime_key(element:&IUIAutomationElement)->Option<String>{
    use windows::Win32::System::Ole::{SafeArrayDestroy,SafeArrayGetElement,SafeArrayGetLBound,SafeArrayGetUBound};
    use sha2::{Digest,Sha256};
    let array=element.GetRuntimeId().ok()?;if array.is_null(){return None;}
    let result=(||{
        let lower=SafeArrayGetLBound(array,1).ok()?;let upper=SafeArrayGetUBound(array,1).ok()?;
        if upper<lower || upper as i64-lower as i64>32{return None;}
        let mut bytes=Vec::new();for i in lower..=upper{let mut value=0i32;SafeArrayGetElement(array,&i,&mut value as *mut _ as *mut _).ok()?;bytes.extend_from_slice(&value.to_le_bytes());}
        Some(format!("{:x}",Sha256::digest(bytes))[..20].to_string())
    })();let _=SafeArrayDestroy(array);result
}
unsafe fn set_empty(automation:&IUIAutomation,identity:&Identity,observation:&str,expected:&str,text:&str,generation:u64,cancelled:&AtomicBool,deadline:Instant)->Result<Value,&'static str>{
    let permitted=||!cancelled.load(Ordering::SeqCst)&&Instant::now()<deadline&&!crate::control_lease::stopped()
        &&super::monitor::input_generation()==generation&&super::windows::foreground(identity)
        &&super::windows::identity(handle(identity.hwnd)).as_ref()==Some(identity);
    if !permitted(){return Ok(json!({"value_state":"not_started","reason":"input_interrupted"}));}
    let focused=automation.GetFocusedElement().map_err(|_|"uia_focus_unavailable")?;
    if runtime_key(&focused).map(|key|format!("{observation}_element_{key}"))!=Some(expected.into())
        ||focused.CurrentIsPassword().map(|v|v.as_bool()).unwrap_or(true){return Ok(json!({"value_state":"not_started","reason":"focus_changed"}));}
    let Ok(pattern)=focused.GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId)else{return Ok(json!({"value_state":"unsupported"}));};
    if pattern.CurrentIsReadOnly().map(|v|v.as_bool()).unwrap_or(true)||!pattern.CurrentValue().map(|v|v.is_empty()).unwrap_or(false){return Ok(json!({"value_state":"not_started","reason":"value_changed"}));}
    if !permitted(){return Ok(json!({"value_state":"not_started","reason":"input_interrupted"}));}
    match pattern.SetValue(&windows::core::BSTR::from(text)) {
        Ok(_)=>Ok(json!({"value_state":"executed"})),Err(_)=>Ok(json!({"value_state":"unknown","reason":"uia_set_value_failed"})),
    }
}

fn unavailable(reason: &str) -> Value {
    json!({"ax_status":"unavailable","ax_reason":reason,"elements":[],"focus_evidence":{"source":"unknown"},"text_complete":false})
}

unsafe fn read_text_edit(element:&IUIAutomationElement,row:&mut Value,read_value:bool) {
    if row["password"]!=false{return;}
    let text_edit=element.GetCurrentPatternAs::<IUIAutomationTextEditPattern>(UIA_TextEditPatternId).is_ok();
    row["text_edit_pattern"]=json!(text_edit);
    let Ok(pattern)=element.GetCurrentPatternAs::<IUIAutomationTextPattern>(UIA_TextPatternId)else{return;};
    row["text_pattern"]=json!(true);
    if !text_edit || row["value_pattern"]==true{return;}
    let Ok(range)=pattern.DocumentRange()else{return;};
    use windows::Win32::System::Variant::{VariantClear,VT_BOOL};
    let Ok(mut attribute)=range.GetAttributeValue(UIA_IsReadOnlyAttributeId)else{return;};
    let readonly=if attribute.Anonymous.Anonymous.vt==VT_BOOL {Some(attribute.Anonymous.Anonymous.Anonymous.boolVal.0!=0)}else{None};
    let _=VariantClear(&mut attribute);
    row["text_readonly"]=json!(readonly);
    if readonly==Some(false) {
        row["editable"]=json!(true);
        if read_value {if let Ok(text)=range.GetText(4001){let text=text.to_string();row["value_complete"]=json!(text.chars().count()<=4000);row["value"]=json!(text.chars().take(4000).collect::<String>());}}
    }
}

unsafe fn read(automation: &IUIAutomation, identity: &Identity, observation: &str) -> Result<Value, &'static str> {
    read_scope(automation,identity,observation,None)
}

unsafe fn read_scope(automation: &IUIAutomation, identity: &Identity, observation: &str, expected:Option<&str>) -> Result<Value, &'static str> {
    let _dpi=super::windows::DpiGuard::new();
    if super::windows::identity(handle(identity.hwnd)).as_ref() != Some(identity) { return Err("window_identity_expired"); }
    let mut root = automation.ElementFromHandle(handle(identity.hwnd)).map_err(|_| "uia_root_unavailable")?;
    let walker = automation.ControlViewWalker().map_err(|_| "uia_walker_unavailable")?;
    let deadline=Instant::now()+Duration::from_millis(1100);
    if let Some(expected)=expected {
        let mut queue=std::collections::VecDeque::from([root.clone()]);let mut visited=0;let mut found=None;
        while let Some(element)=queue.pop_front(){
            if visited>=160||Instant::now()>=deadline{break;}visited+=1;
            if runtime_key(&element).map(|key|format!("{observation}_element_{key}")).as_deref()==Some(expected){found=Some(element);break;}
            let mut child=walker.GetFirstChildElement(&element).ok();
            while let Some(node)=child{if queue.len()+visited>=160||Instant::now()>=deadline{break;}
                child=walker.GetNextSiblingElement(&node).ok();queue.push_back(node);}
        }
        root=found.ok_or("element_not_found_within_read_budget")?;
    }
    // Multiple nested providers can report HasKeyboardFocus. Resolve the global
    // focused element once and match its runtime identity inside this window.
    let focused_element=automation.GetFocusedElement().ok();
    let focused_runtime=focused_element.as_ref().and_then(|element|runtime_key(element));
    let root_runtime=runtime_key(&root);
    let focus_status=if focused_element.is_none(){"focus_provider_unavailable"}else if focused_runtime.is_none(){"focus_identity_unavailable"}else{"focus_not_in_window_tree"};
    // Incremental bounded traversal avoids asking a provider to materialize its entire tree.
    let mut stack = vec![(root, 0usize)]; let mut elements = Vec::new(); let mut focus = json!({"source":"unknown"});
    let mut complete = true;
    while let Some((element, depth)) = stack.pop() {
        if elements.len() >= 120 || std::time::Instant::now() >= deadline { complete = false; break; }
        let password = element.CurrentIsPassword().map(|v| v.as_bool()).unwrap_or(true);
        let rect = element.CurrentBoundingRectangle().map_err(|_| "uia_element_unavailable")?;
        let role = element.CurrentControlType().map(|v| v.0).unwrap_or(0);
        let enabled = element.CurrentIsEnabled().map(|v| v.as_bool()).unwrap_or(false);
        let offscreen = element.CurrentIsOffscreen().map(|v| v.as_bool()).unwrap_or(true);
        let runtime=runtime_key(&element);
        let focused=runtime.is_some() && runtime==focused_runtime;
        let element_id = format!("{observation}_element_{}", runtime.clone().unwrap_or_else(||format!("unstable_{}",elements.len())));
        let mut row = json!({"element_id":element_id,"role":role,"enabled":enabled,"offscreen":offscreen,
            "password":password,"focused":focused,"bounds":bounds_json([rect.left,rect.top,rect.right,rect.bottom]),"depth":depth,"stable_identity":runtime.is_some()});
        let mut editable = false;
        if !password {
            row["name"] = json!(element.CurrentName().map(|v| v.to_string().chars().take(256).collect::<String>()).unwrap_or_default());
            if let Ok(value) = element.GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId) {
                row["value_pattern"]=json!(true);
                editable = value.CurrentIsReadOnly().map(|v| !v.as_bool()).unwrap_or(false);
                if focused || (expected.is_some() && depth==0) { if let Ok(value)=value.CurrentValue(){let value=value.to_string();row["value_complete"]=json!(value.chars().count()<=4000);row["value"]=json!(value.chars().take(4000).collect::<String>());} }
            }
            if expected.is_some() && depth==0 {
                if let Ok(pattern)=element.GetCurrentPatternAs::<IUIAutomationTextPattern>(UIA_TextPatternId){
                    if let Ok(range)=pattern.DocumentRange(){if let Ok(text)=range.GetText(4001){let text=text.to_string();
                        row["text_complete"]=json!(text.chars().count()<=4000);row["text"]=json!(text.chars().take(4000).collect::<String>());
                    }}
                }
            }
        }
        row["editable"] = json!(editable);
        add_control_state(&element,&mut row);
        if focused || (matches!(role,50004|50026|50030)&&row["value_pattern"]!=true) {read_text_edit(&element,&mut row,focused);}
        if focused {
            focus=row.clone();focus["source"]=json!("uia");
        }
        elements.push(row);
        if depth < 12 {
            let mut children = Vec::new(); let mut child = walker.GetFirstChildElement(&element).ok();
            while let Some(current) = child {
                if children.len() + stack.len() + elements.len() >= 120 { complete = false; break; }
                child = walker.GetNextSiblingElement(&current).ok(); children.push((current, depth+1));
            }
            stack.extend(children.into_iter().rev());
        } else { complete = false; }
    }
    // Common file dialogs expose a focused edit below a combo-box, sometimes
    // outside the bounded ControlView traversal. Include that exact focus only
    // after proving its ancestry reaches this window's root.
    if expected.is_none() && focus["source"]=="unknown" && focused_runtime.is_some() && root_runtime.is_some() {
        if let Some(element)=focused_element {
            let mut current=Some(element.clone());let mut belongs=false;
            for _ in 0..24 {
                let Some(node)=current else{break;};
                if runtime_key(&node)==root_runtime {belongs=true;break;}
                current=walker.GetParentElement(&node).ok();
            }
            if belongs {
                let password=element.CurrentIsPassword().map(|v|v.as_bool()).unwrap_or(true);
                let rect=element.CurrentBoundingRectangle().map_err(|_|"uia_focus_unavailable")?;
                let key=format!("{observation}_element_{}",focused_runtime.unwrap());
                let role=element.CurrentControlType().map(|v|v.0).unwrap_or(0);
                let mut row=json!({"element_id":key,"role":role,"focused":true,"password":password,"stable_identity":true,
                    "enabled":element.CurrentIsEnabled().map(|v|v.as_bool()).unwrap_or(false),
                    "offscreen":element.CurrentIsOffscreen().map(|v|v.as_bool()).unwrap_or(true),
                    "bounds":bounds_json([rect.left,rect.top,rect.right,rect.bottom]),"editable":false});
                if !password {
                    row["name"]=json!(element.CurrentName().map(|v|v.to_string().chars().take(256).collect::<String>()).unwrap_or_default());
                    if let Ok(pattern)=element.GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId){
                        row["editable"]=json!(!pattern.CurrentIsReadOnly().map(|v|v.as_bool()).unwrap_or(true));
                        row["value_pattern"]=json!(true);if let Ok(value)=pattern.CurrentValue(){let value=value.to_string();row["value_complete"]=json!(value.chars().count()<=4000);row["value"]=json!(value.chars().take(4000).collect::<String>());}
                    }
                    read_text_edit(&element,&mut row,true);
                }
                add_control_state(&element,&mut row);
                focus=row.clone();focus["source"]=json!("uia");
                if elements.len()>=120 {elements.pop();complete=false;}
                elements.push(row);
            }
        }
    }
    Ok(json!({"ax_status":if elements.len() <= 2 {"limited"} else {"available"},
        "caret_evidence":super::windows::caret_evidence(identity),
        "focus_status":if focus["source"]=="uia"{"verified"}else{focus_status},
        "elements":elements,"focus_evidence":focus,"text_complete":complete}))
}
