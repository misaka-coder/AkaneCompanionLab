use std::{collections::HashMap, sync::{Mutex,OnceLock,atomic::{AtomicBool,AtomicU64,Ordering}}, time::{Instant,SystemTime,UNIX_EPOCH}};
use serde_json::{json,Value};
use sha2::{Digest,Sha256};
use super::{fail,mcp::Client};
use crate::computer_use::protocol::string;
static ENABLED: AtomicBool = AtomicBool::new(false);
static STATE: OnceLock<Mutex<Option<Session>>> = OnceLock::new();
static COUNTER: AtomicU64 = AtomicU64::new(0);
static LEDGER: OnceLock<Mutex<HashMap<String,(String,Value)>>> = OnceLock::new();
fn reference(kind: &str) -> String { format!("{kind}_{:x}_{:x}",SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis(),COUNTER.fetch_add(1,Ordering::Relaxed)) }
struct Observation { id:String, tree:Value, captured:Instant, lease_revision:u64 }
struct Session { scope:String, epoch:String, id:String, client:Option<Client>, tabs:HashMap<String,Value>, selected:String, observation:Option<Observation>, connection_status:&'static str }
pub fn configure(enabled: bool) -> Value {
    if enabled { if let Err(reason)=crate::control_lease::resume(){return fail(reason);} }
    ENABLED.store(enabled,Ordering::SeqCst);
    if !enabled { crate::control_lease::stop(); if let Ok(mut state)=STATE.get_or_init(Default::default).try_lock() { *state=None; } }
    status()
}
pub fn status() -> Value {
    let mut result=json!({"ok":true,"enabled":ENABLED.load(Ordering::SeqCst),"connected":false,"status":"idle"});
    if let Ok(state)=STATE.get_or_init(Default::default).try_lock() {
        if let Some(s)=state.as_ref() { result["connected"]=json!(s.connection_status=="connected"); result["status"]=json!(s.connection_status); }
    } else {result["status"]=json!("running");}
    result
}
pub fn connection_closed(epoch:&str) {
    if let Ok(mut state)=STATE.get_or_init(Default::default).try_lock() {
        if state.as_ref().is_some_and(|s|s.epoch==epoch) { *state=None; }
    }
    // If a request is in flight, the connection guard's global stop still
    // prevents the next effect; its next epoch cannot reuse the old session.
}
pub fn execute(invocation:&str,args:&Value)->Value {
    let scope=string(args,"_control_scope"); let epoch=string(args,"_device_epoch");
    if scope.len()!=64 || epoch.is_empty() { return fail("caller_scope_missing"); }
    if !crate::control_lease::epoch_valid(epoch){return fail("device_epoch_expired");}
    let key=format!("{epoch}/{scope}/{invocation}"); let digest=args.to_string();
    let ledger=LEDGER.get_or_init(Default::default);
    {
        let Ok(mut ledger)=ledger.lock() else {return fail("operation_ledger_unavailable");};
        if let Some((old,value))=ledger.get(&key) {return if old==&digest {value.clone()}else{fail("invocation_id_request_conflict")};}
        if ledger.len()>=2048 {return fail("operation_budget_exhausted");}
        ledger.insert(key.clone(),(digest.clone(),json!({"ok":false,"action_state":"unknown","observation_state":"failed","reason":"executing"})));
    }
    let mut result=run(args,scope,epoch);
    result["backend"]=json!("personal_chrome"); result["device_epoch"]=json!(epoch); result["operation_id"]=json!(invocation);
    let mut retained=result.clone();
    if let Some(shots)=retained["screenshots"].as_array_mut() { for shot in shots { if let Some(obj)=shot.as_object_mut(){obj.remove("imageBase64");} } }
    if let Ok(mut ledger)=ledger.lock() {ledger.insert(key,(digest,retained));}
    result
}
fn run(args:&Value,scope:&str,epoch:&str)->Value {
    let action=string(args,"action");
    if action=="disconnect" {
        let Ok(mut state)=STATE.get_or_init(Default::default).try_lock() else{return fail("browser_busy");};
        if !state.as_ref().is_some_and(|s|s.scope==scope && s.epoch==epoch && s.id==string(args,"browser_session_id")) || string(args,"device_epoch")!=epoch {return fail("browser_session_mismatch");}
        *state=None;crate::control_lease::release("browser_page",scope,epoch);
        return json!({"ok":true,"action_state":"not_started","observation_state":"complete","status":"disconnected"});
    }
    if !ENABLED.load(Ordering::SeqCst) && action!="disconnect" {return fail("personal_chrome_disabled");}
    if !crate::computer_use::start_input_monitor() {return fail("desktop_monitor_unavailable");}
    let readonly=matches!(action,"connect"|"list_tabs"|"select_tab"|"status"|"current"|"snapshot"|"elements"|"screenshot"|"read_text");
    let acquire=if readonly {crate::control_lease::acquire_read}else{crate::control_lease::acquire};
    let mut lease=match acquire("browser_page",scope,epoch,action=="connect") {Ok(v)=>v,Err(r)=>return fail(r)};
    let Ok(mut state)=STATE.get_or_init(Default::default).try_lock() else {return fail("browser_busy");};
    if action=="connect" {
        // Shared lease acquisition already guards active owners; an idle owner
        // may be replaced here, together with all of its tab/observation evidence.
        if !state.as_ref().is_some_and(|s|s.scope==scope && s.epoch==epoch && s.client.is_some()) {
            *state=None;
            let client=match Client::start(){Ok(v)=>v,Err(r)=>return fail(r)};
            *state=Some(Session{scope:scope.into(),epoch:epoch.into(),id:reference("browser"),client:Some(client),tabs:HashMap::new(),selected:String::new(),observation:None,connection_status:"adapter_ready"});
        }
        let mut out=success(state.as_ref().unwrap());
        out["capabilities"]=json!({"text":true,"screenshot":true,"element_actions":true,"coordinates":false,"download_transfer":false,"native_window_identity":false});
        out["next_action"]=json!("list_tabs_requires_chrome_connection_permission"); return out;
    }
    let Some(session)=state.as_mut() else {return fail("browser_not_connected");};
    if session.scope!=scope || session.epoch!=epoch || string(args,"device_epoch")!=epoch || string(args,"browser_session_id")!=session.id {return fail("browser_session_mismatch");}
    if action=="handoff" {session.observation=None;lease.handoff("computer_use");let mut out=success(session);out["next_action"]=json!("computer_use.list_windows_and_select_window");return out;}
    if action=="status" {return success(session);}
    if action=="list_tabs" {return match session.list_tabs() {Ok(v)=>v,Err(r)=>{
        let mut out=fail(r);
        if r=="chrome_connection_pending_check_browser" {
            out["browser_session_id"]=json!(session.id);out["status"]=json!("connecting");
            out["next_action"]=json!("check_chrome_connection_then_list_tabs_same_session");
        }
        out
    }};}
    if action=="select_tab" {
        let tab=string(args,"tab_id");
        if !session.tabs.contains_key(tab){return fail("tab_reference_expired");}
        session.selected=tab.into();session.observation=None;
        // Page-ID routing is explicit; selecting does not steal OS foreground.
        let page=session.page_id().unwrap();
        if let Err(r)=session.call("select_page",json!({"pageId":page,"bringToFront":false})) {return fail(r);}
        return observe_and_acknowledge(session,args,lease);
    }
    if session.selected.is_empty(){return fail("select_tab_required");}
    if matches!(action,"current"|"snapshot"|"elements"|"screenshot"|"read_text") {return observe_and_acknowledge(session,args,lease);}
    if !crate::computer_use::input_enabled(){return fail("desktop_input_disabled");}
    let Some(old)=session.observation.as_ref() else {return fail("observation_required");};
    if old.id!=string(args,"observation_id") || old.lease_revision!=lease.revision || old.captured.elapsed().as_secs()>60 {return fail("observation_expired");}
    let old_tree=old.tree.clone();
    let page=match session.page_id(){Ok(v)=>v,Err(r)=>return fail(r)};
    let fresh=match session.snapshot(page){Ok(v)=>v,Err(r)=>return fail(r)};
    if canonical(&old_tree)!=canonical(&fresh) {session.observation=None;return fail("page_changed_reobserve");}
    let target=if matches!(action,"click"|"fill") {
        let Some(path)=find_path(&old_tree,string(args,"ref")) else {return fail("element_reference_expired");};
        match node_at(&fresh,&path){Some(v)=>v.clone(),None=>return fail("element_reference_expired")}
    } else {Value::Null};
    if target["disabled"]==true || target["role"]=="password" || target["password"]==true {return fail("element_unavailable");}
    if action=="fill" && !matches!(target["role"].as_str(),Some("textbox"|"searchbox"|"combobox")) {return fail("editable_target_required");}
    let tab=session.tabs.get(&session.selected).cloned().unwrap_or(Value::Null);
    let preview=json!({"action":action,"browser_session_id":session.id,"device_epoch":epoch,"tab_id":session.selected,
        "url":tab["url"],"title":tab["title"],"target_role":target["role"],"target_name":target["name"],
        "text":args["text"],"key":args["key"],"destination":args["url"]});
    let binding=format!("{:x}",Sha256::digest(format!("{scope}\0{preview}\0{}",canonical(&target)).as_bytes()));
    let required=requires_approval(args,&target);
    if string(args,"_phase")=="prepare" {let mut out=success(session);out["authorization"]=json!({"required":required,"binding":binding,"preview":preview});return out;}
    if required && string(args,"_approval_binding")!=binding {return fail("specific_approval_required");}
    if let Err(r)=lease.check(){return fail(r);}
    if !ENABLED.load(Ordering::SeqCst){return fail("personal_chrome_disabled");}
    let (method,params)=match action {
        "click"=>("click",json!({"pageId":page,"uid":target["id"]})),
        "fill"=>("fill",json!({"pageId":page,"uid":target["id"],"value":args["text"]})),
        "press"=>("press_key",json!({"pageId":page,"key":args["key"]})),
        "scroll"=>("press_key",json!({"pageId":page,"key":if args["scroll_delta"].as_i64().unwrap_or(800)<0{"PageUp"}else{"PageDown"}})),
        "navigate"=>{
            let url=string(args,"url");
            if !public_url(url){return fail("url_not_allowed");}
            ("navigate_page",json!({"pageId":page,"type":"url","url":url,"handleBeforeUnload":"dismiss","timeout":8000}))
        },_=>return fail("chrome_action_unsupported"),
    };
    session.observation=None;
    match session.call(method,params) {
        Ok(_)=>{
            let mut out=session.observe(args,lease.revision);out["action_state"]=json!("executed");
            out["effect_evidence"]=json!({"stage":"action_executed","delivery_confirmed":"unavailable"});
            if action=="fill" {
                out["effect_evidence"]["value_match_at_observation"]=fill_value_match(&out,&target,args);
                if out["effect_evidence"]["value_match_at_observation"]==false {
                    out["next_action"]=json!("inspect_changed_field_value_do_not_replay");
                }
            }
            out
        },
        Err(r)=>{let mut out=fail(r);out["action_state"]=json!("unknown");out["next_action"]=json!("observe_do_not_replay");out},
    }
}
fn success(s:&Session)->Value {json!({"ok":true,"action_state":"not_started","observation_state":"complete","browser_session_id":s.id,"tab_id":s.selected})}
fn fill_value_match(observed:&Value,target:&Value,args:&Value)->Value {
    observed["elements"].as_array().and_then(|rows|rows.iter().find(|row|row["ref"]==target["id"]))
        .and_then(|row|row["value"].as_str()).map(|value|json!(Some(value)==args["text"].as_str())).unwrap_or(Value::Null)
}
fn observe_and_acknowledge(session:&mut Session,args:&Value,lease:crate::control_lease::LeaseGuard)->Value {
    let generation=crate::computer_use::user_input_generation();
    let mut out=session.observe(args,lease.revision);
    drop(lease);
    if out["ok"]==true && out["observation_state"]=="complete" && out["complete"]==true
        && crate::computer_use::input_enabled() && !crate::control_lease::stopped() {
        let ready=crate::control_lease::acknowledge_observation("browser_page",&session.scope,&session.epoch,generation)
            .and_then(|_|{
                let lease=crate::control_lease::acquire_read("browser_page",&session.scope,&session.epoch,false)?;
                if let Some(observation)=session.observation.as_mut(){observation.lease_revision=lease.revision;}
                Ok(())
            });
        out["input_ready"]=json!(ready.is_ok());
        if let Err(reason)=ready {out["input_readiness_reason"]=json!(reason);}
    } else {out["input_ready"]=json!(false);}
    out
}
fn requires_approval(args:&Value,target:&Value)->bool {
    if string(args,"_permission_mode")=="trusted_auto_allow" {return false;}
    match string(args,"action") {
        "click"=>target["role"]!="link",
        "press"=>!matches!(string(args,"key"),"Escape"|"Tab"|"ArrowDown"|"ArrowUp"|"ArrowLeft"|"ArrowRight"|"PageDown"|"PageUp"|"Home"|"End"),
        _=>false,
    }
}
impl Session {
    fn call(&mut self,name:&str,args:Value)->Result<Value,&'static str> {
        let result=self.client.as_mut().ok_or("browser_not_connected")?.call(name,args);
        match result {
            Err("chrome_connection_pending_check_browser")=>{self.observation=None;self.connection_status="connecting";Err("chrome_connection_pending_check_browser")},
            Err(r)=>{self.client=None;self.observation=None;self.connection_status="disconnected";Err(r)},
            Ok(value)=>{
                if value["structuredContent"]["reconnected"]==true {self.observation=None;self.tabs.clear();self.selected.clear();return Err("browser_reconnected_reselect");}
                if value["isError"]==true {if name=="list_pages" {self.connection_status="connection_failed";}return Err("chrome_action_failed_or_permission_required");}
                Ok(value)
            }
        }
    }
    fn list_tabs(&mut self)->Result<Value,&'static str> {
        let result=self.call("list_pages",json!({}))?;
        let pages=result["structuredContent"]["pages"].as_array().ok_or("chrome_pages_unavailable")?;
        self.connection_status="connected";
        let mut tabs=HashMap::new();let mut rows=Vec::new();
        for page in pages.iter().take(100) {
            if !public_url(page["url"].as_str().unwrap_or("")){continue;}
            let id=self.tabs.iter().find(|(_,old)|old["id"]==page["id"]).map(|(id,_)|id.clone()).unwrap_or_else(||reference("tab"));
            rows.push(json!({"tab_id":id,"url":page["url"],"title":page["title"]})); tabs.insert(id,page.clone());
        }
        self.tabs=tabs;
        if !self.tabs.contains_key(&self.selected) {self.selected.clear();self.observation=None;}
        let mut out=success(self);out["tabs"]=json!(rows);out["complete"]=json!(pages.len()<=100);Ok(out)
    }
    fn page_id(&self)->Result<u64,&'static str> {self.tabs.get(&self.selected).and_then(|p|p["id"].as_u64()).ok_or("tab_reference_expired")}
    fn verify_page(&mut self,page:u64)->Result<Value,&'static str> {
        let result=self.call("list_pages",json!({}))?;
        let current=result["structuredContent"]["pages"].as_array().and_then(|items|items.iter().find(|p|p["id"]==page)).ok_or("tab_closed")?.clone();
        if !public_url(current["url"].as_str().unwrap_or("")){return Err("url_not_allowed");}
        self.tabs.insert(self.selected.clone(),current.clone()); Ok(current)
    }
    fn snapshot(&mut self,page:u64)->Result<Value,&'static str> {
        let before=self.verify_page(page)?;
        let result=self.call("take_snapshot",json!({"pageId":page}))?;
        let mut tree=result["structuredContent"]["snapshot"].clone();
        if !tree.is_object(){return Err("chrome_snapshot_unavailable");}
        let after=self.verify_page(page)?;
        if before["url"]!=after["url"]{return Err("page_changed_reobserve");}
        tree["url"]=after["url"].clone();
        Ok(tree)
    }
    fn observe(&mut self,args:&Value,revision:u64)->Value {
        self.observation=None;
        let page=match self.page_id(){Ok(v)=>v,Err(r)=>return fail(r)};
        let tree=match self.snapshot(page){Ok(v)=>v,Err(r)=>return fail(r)};
        let id=reference("browser_obs");let mut out=success(self);
        out["observation_id"]=json!(id);out["url"]=tree["url"].clone();out["title"]=tree["name"].clone();
        let mut elements=Vec::new();flatten(&tree,&mut elements);
        out["complete"]=json!(elements.len()<128);out["elements"]=json!(elements);out["screenshots"]=json!([]);
        if string(args,"observation_mode")!="text" {
            match self.call("take_screenshot",json!({"pageId":page,"format":"png","fullPage":false})) {
                Ok(value)=>{
                    if self.verify_page(page).map(|p|p["url"]!=tree["url"]).unwrap_or(true) {return fail("page_changed_reobserve");}
                    let image=value["content"].as_array().and_then(|items|items.iter().find(|v|v["type"]=="image" && v["mimeType"]=="image/png"));
                    if let Some(image)=image {
                        use base64::Engine;
                        if let Some(encoded)=image["data"].as_str().filter(|v|v.len()<=12*1024*1024) {
                            if let Ok(raw)=base64::engine::general_purpose::STANDARD.decode(encoded) {
                                if raw.len()>=24 && raw.starts_with(b"\x89PNG\r\n\x1a\n") {
                                    let width=u32::from_be_bytes(raw[16..20].try_into().unwrap());let height=u32::from_be_bytes(raw[20..24].try_into().unwrap());
                                    out["screenshots"]=json!([{"screenshot_id":reference("browser_shot"),"width":width,"height":height,"mimeType":"image/png","imageBase64":encoded}]);
                                }
                            }
                        }
                    }
                    if out["screenshots"].as_array().is_none_or(|v|v.is_empty()){out["observation_state"]=json!("partial");out["reason"]=json!("chrome_screenshot_unavailable");}
                },Err(r)=>{out["observation_state"]=json!("partial");out["reason"]=json!(r);},
            }
        }
        if self.client.is_some() && !self.selected.is_empty(){self.observation=Some(Observation{id,tree,captured:Instant::now(),lease_revision:revision});}
        out
    }
}
fn canonical(value:&Value)->Value {
    match value {Value::Object(obj)=>Value::Object(obj.iter().filter(|(k,_)|k.as_str()!="id").map(|(k,v)|(k.clone(),canonical(v))).collect()),
        Value::Array(items)=>json!(items.iter().map(canonical).collect::<Vec<_>>()),_=>value.clone()}
}
fn find_path(tree:&Value,id:&str)->Option<Vec<usize>> {
    if tree["id"]==id{return Some(vec![]);}
    for (i,child) in tree["children"].as_array().into_iter().flatten().enumerate(){if let Some(mut path)=find_path(child,id){path.insert(0,i);return Some(path);}}
    None
}
fn node_at<'a>(mut tree:&'a Value,path:&[usize])->Option<&'a Value>{for index in path {tree=tree.get("children")?.get(*index)?;}Some(tree)}
fn flatten(tree:&Value,rows:&mut Vec<Value>){
    if rows.len()>=128{return;}
    let mut node=tree.clone();if let Some(obj)=node.as_object_mut(){obj.remove("children");obj.remove("value");if let Some(id)=obj.remove("id"){obj.insert("ref".into(),id);}}
    // Password values never leave the device; ordinary values remain draft evidence.
    if tree["password"]!=true && tree["role"]!="password" && tree.get("value").is_some(){node["value"]=tree["value"].clone();}
    rows.push(node);for child in tree["children"].as_array().into_iter().flatten(){flatten(child,rows);}
}
fn public_url(raw:&str)->bool {
    let Ok(url)=reqwest::Url::parse(raw)else{return false;};
    if !matches!(url.scheme(),"http"|"https") || !url.username().is_empty() || url.password().is_some(){return false;}
    let host=url.host_str().unwrap_or("").trim_matches(['[',']']);
    if host.is_empty() || host.eq_ignore_ascii_case("localhost") || host.ends_with(".local") || host.ends_with(".localhost"){return false;}
    if let Ok(ip)=host.parse::<std::net::IpAddr>() {match ip {
        std::net::IpAddr::V4(v)=>!v.is_private()&&!v.is_loopback()&&!v.is_link_local()&&!v.is_unspecified()&&!v.is_multicast()&&!v.is_broadcast(),
        std::net::IpAddr::V6(v)=>!v.is_loopback()&&!v.is_unspecified()&&!v.is_multicast()&&!v.is_unique_local()&&!v.is_unicast_link_local(),
    }}else{host.contains('.')}
}

#[cfg(test)] mod tests {
    use super::*;
    #[test]fn fill_evidence_reports_exact_difference_without_guessing_its_cause(){
        let observed=json!({"elements":[{"ref":"field","value":"changed"}]});
        let target=json!({"id":"field"});
        assert_eq!(fill_value_match(&observed,&target,&json!({"text":" changed "})),false);
        assert_eq!(fill_value_match(&observed,&target,&json!({"text":"changed"})),true);
        assert_eq!(fill_value_match(&json!({"elements":[]}),&target,&json!({"text":"changed"})),Value::Null);
    }
    #[test]fn full_access_uses_only_host_metadata(){
        let target=json!({"role":"button"});
        assert!(requires_approval(&json!({"action":"click"}),&target));
        assert!(requires_approval(&json!({"action":"click","permission_mode":"trusted_auto_allow"}),&target));
        assert!(!requires_approval(&json!({"action":"click","_permission_mode":"trusted_auto_allow"}),&target));
        for field in ["permission_mode","_permission_mode"] {
            let mut args=json!({"action":"connect"});args[field]=json!("trusted_auto_allow");
            assert!(!super::super::valid(args.as_object().unwrap()));
        }
    }
    #[test]fn snapshot_refs_are_rebound_only_for_identical_semantics(){
        let old=json!({"id":"1_0","role":"RootWebArea","children":[{"id":"1_1","role":"button","name":"Send"}]});
        let new=json!({"id":"2_0","role":"RootWebArea","children":[{"id":"2_1","role":"button","name":"Send"}]});
        assert_eq!(canonical(&old),canonical(&new));assert_eq!(node_at(&new,&find_path(&old,"1_1").unwrap()).unwrap()["id"],"2_1");
        let mut changed=new;changed["children"][0]["name"]=json!("Delete");assert_ne!(canonical(&old),canonical(&changed));
        assert!(!public_url("http://127.0.0.1/"));assert!(!public_url("chrome://settings"));assert!(public_url("https://example.com/"));
    }
    #[test]#[ignore="Starts the installed official MCP adapter; discovery only, never calls Chrome"]
    fn official_adapter_discovery(){let c=Client::start().unwrap();assert!(c.tools.contains("take_snapshot"));assert!(!c.tools.contains("evaluate_script"));}
    #[test]#[ignore="Owner-authorized real Chrome test; only the separately opened Selenium fixture tab"]
    fn chrome_actions_acceptance(){
        assert_eq!(std::env::var("AKANE_CHROME_ACTION_TEST").as_deref(),Ok("1"));
        const URL:&str="https://www.selenium.dev/selenium/web/web-form.html";
        assert_eq!(crate::computer_use::local_control("configure",&json!({"enabled":true}))["ok"],true);
        assert_eq!(configure(true)["ok"],true);
        struct Cleanup;impl Drop for Cleanup{fn drop(&mut self){configure(false);crate::computer_use::local_control("configure",&json!({"enabled":false}));}}
        let _cleanup=Cleanup;
        let mut number=0;let mut call=|mut args:Value|{number+=1;args["_control_scope"]=json!("d".repeat(64));args["_device_epoch"]=json!("chrome-actions-test");args["device_epoch"]=json!("chrome-actions-test");args["_permission_mode"]=json!("trusted_auto_allow");execute(&format!("chrome-action-{number}"),&args)};
        let connected=call(json!({"action":"connect"}));assert_eq!(connected["ok"],true,"{}",connected["reason"]);
        let session=connected["browser_session_id"].clone();
        let mut listed=call(json!({"action":"list_tabs","browser_session_id":session}));
        for _ in 0..17 {
            if listed["reason"]!="chrome_connection_pending_check_browser" {break;}
            println!("Chrome connection pending: accept the visible Chrome prompt; the same adapter remains alive");
            listed=call(json!({"action":"list_tabs","browser_session_id":session}));
        }
        assert_eq!(listed["ok"],true,"{}",listed["reason"]);
        let pages:Vec<_>=listed["tabs"].as_array().unwrap().iter().filter(|p|p["url"]==URL).collect();
        assert_eq!(pages.len(),1,"Open exactly one disposable Selenium form tab first");
        let mut observed=call(json!({"action":"select_tab","browser_session_id":session,"tab_id":pages[0]["tab_id"],"observation_mode":"text"}));
        assert_eq!(observed["ok"],true,"{}",observed["reason"]);
        let input=observed["elements"].as_array().unwrap().iter().find(|e|e["role"]=="textbox"&&e["name"]=="Text input").expect("fixture textbox")["ref"].clone();
        let value=" Akane browser acceptance 中文 ";
        observed=call(json!({"action":"fill","browser_session_id":session,"observation_id":observed["observation_id"],"ref":input,"text":value,"observation_mode":"text"}));
        assert_eq!(observed["action_state"],"executed","{}",observed["reason"]);
        let field=observed["elements"].as_array().unwrap().iter().find(|e|e["name"]=="Text input"&&e["role"]=="textbox").cloned().unwrap_or(Value::Null);
        let exact_value=field["value"]==value;
        let check_row=observed["elements"].as_array().unwrap().iter().find(|e|e["role"]=="checkbox"&&e["name"]=="Default checkbox").expect("fixture checkbox");
        let expected_checked=check_row["checked"]!=true;
        let check=check_row["ref"].clone();
        let args=json!({"action":"click","browser_session_id":session,"observation_id":observed["observation_id"],"ref":check,"observation_mode":"text"});
        let mut prepare=args.clone();prepare["_phase"]=json!("prepare");
        let prepared=call(prepare);assert_eq!(prepared["authorization"]["required"],false,"full access must reach the device");
        observed=call(args);assert_eq!(observed["action_state"],"executed","{}",observed["reason"]);
        assert!(observed["elements"].as_array().unwrap().iter().any(|e|e["name"]=="Default checkbox"&&(e["checked"]==true)==expected_checked));
        let invalid=call(json!({"action":"click","browser_session_id":session,"observation_id":"expired","ref":check,"observation_mode":"text"}));
        assert_eq!(invalid["reason"],"observation_expired");
        let shot=call(json!({"action":"screenshot","browser_session_id":session,"observation_mode":"visual"}));
        assert!(shot["screenshots"].as_array().is_some_and(|v|!v.is_empty()),"{}",shot["reason"]);
        crate::control_lease::stop();
        let stopped=call(json!({"action":"snapshot","browser_session_id":session,"observation_mode":"text"}));
        assert_eq!(stopped["ok"],true);assert_eq!(stopped["input_ready"],false);
        let blocked=call(json!({"action":"click","browser_session_id":session,"observation_id":stopped["observation_id"],"ref":check}));
        assert_eq!(blocked["reason"],"stopped_requires_local_resume");
        let closed=call(json!({"action":"disconnect","browser_session_id":session}));assert_eq!(closed["ok"],true);
        assert!(exact_value,"actual field value must preserve whitespace");
        println!("PASS real Chrome: exact fixture selection, AX refs, fill including spaces/unicode, checkbox, full-access preflight, stale observation rejection, screenshot and detach; no form submission");
    }
    #[test]#[ignore="Requires the owner's explicit Chrome connection consent; read-only"]
    fn chrome_readonly_acceptance(){
        assert_eq!(std::env::var("AKANE_CHROME_READ_TEST").as_deref(),Ok("1"));
        assert_eq!(configure(true)["ok"],true);
        struct Cleanup;impl Drop for Cleanup{fn drop(&mut self){configure(false);}}
        let _cleanup=Cleanup;
        let mut number=0;let mut call=|mut args:Value|{number+=1;args["_control_scope"]=json!("c".repeat(64));args["_device_epoch"]=json!("chrome-readonly-test");args["device_epoch"]=json!("chrome-readonly-test");execute(&format!("chrome-read-{number}"),&args)};
        let connected=call(json!({"action":"connect"}));assert_eq!(connected["ok"],true,"{}",connected["reason"]);
        let session=connected["browser_session_id"].clone();
        let listed=call(json!({"action":"list_tabs","browser_session_id":session}));
        assert_eq!(listed["ok"],true,"{}",listed["reason"]);
        let pages=listed["tabs"].as_array().expect("structured pages");
        println!("Chrome connected; {} tabs listed (titles and URLs omitted)",pages.len());
        // Read-only test preference, not an HWND/tab identity mapping or an
        // authority for input. MCP's selected flag means pages[0], not OS focus.
        #[cfg(windows)]let visible=crate::computer_use::visible_chrome_titles_for_readonly_test();
        #[cfg(not(windows))]let visible:Vec<String>=Vec::new();
        let page=pages.iter().find(|p|p["title"].as_str().is_some_and(|t|!t.is_empty()&&visible.iter().any(|v|v==&format!("{t} - Google Chrome"))))
            .or_else(||pages.first());
        if let Some(page)=page {
            let snapshot=call(json!({"action":"select_tab","browser_session_id":session,"tab_id":page["tab_id"],"observation_mode":"text"}));
            assert_eq!(snapshot["ok"],true,"{}",snapshot["reason"]);
            assert!(!snapshot["elements"].as_array().unwrap().is_empty());
            crate::control_lease::stop();
            let paused=call(json!({"action":"snapshot","browser_session_id":session,"observation_mode":"text"}));
            assert_eq!(paused["ok"],true,"Read-only observation must work while input is stopped: {}",paused["reason"]);
            let shot=call(json!({"action":"screenshot","browser_session_id":session,"observation_mode":"visual"}));
            assert!(shot["screenshots"].as_array().is_some_and(|a|!a.is_empty()),"{}",shot["reason"]);
            println!("Real Session facade: tab reference, AX, stopped-state read and PNG returned; no page action or file export");
        }else{println!("No HTTP(S) tab eligible for content inspection; connection verified only");}
        let disconnected=call(json!({"action":"disconnect","browser_session_id":session}));assert_eq!(disconnected["ok"],true);
        println!("Adapter detached; personal Chrome was not closed");
    }
}
