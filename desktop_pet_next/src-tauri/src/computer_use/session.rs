use std::{collections::HashMap, sync::{Arc, Mutex, OnceLock, atomic::{AtomicBool, AtomicU64, Ordering}}, time::{SystemTime, UNIX_EPOCH}};
use base64::Engine;
use serde_json::{json, Value};
use super::{capture, monitor, input, policy, workflow, protocol::{fail, success, string}, windows::{self, Geometry, Identity}};

static STATE: OnceLock<Mutex<State>> = OnceLock::new();
static STOPPED: AtomicBool = AtomicBool::new(false);
static COUNTER: AtomicU64 = AtomicU64::new(0);
static INPUT_ENABLED: AtomicBool = AtomicBool::new(false);
pub fn input_enabled() -> bool { INPUT_ENABLED.load(Ordering::SeqCst) }
static OWNER: OnceLock<Mutex<(String, String, String)>> = OnceLock::new();
static OPERATIONS: OnceLock<Mutex<HashMap<String, (String, Value)>>> = OnceLock::new();
static WORKFLOW_PROGRESS:OnceLock<Mutex<Value>>=OnceLock::new();
static CONTEXTS:OnceLock<Mutex<super::context::Contexts>>=OnceLock::new();
const OBSERVATION_TTL_MS:u128=60_000;
const VISUAL_RECHECK_AFTER_MS:u128=15_000;
fn now() -> u128 { SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() }
fn reference(prefix: &str) -> String { format!("{prefix}_{:x}_{:x}", now(), COUNTER.fetch_add(1, Ordering::Relaxed)) }
#[derive(Clone)]
struct Window { scope: String, identity: Identity }
struct Control { id: String, scope: String, window: String, root_window:String, generation: u64, observation: Option<Observation> }
#[derive(Clone)]
struct Observation { id: String, shot: String, geometry: Geometry, frame: Option<Arc<capture::Frame>>, region:Option<super::region::Region>, captured_at: u128, ax: Value }
#[derive(Clone)]
struct VisualAnchor { window:String, observation:Observation }
#[derive(Default)]
struct State { epoch: String, windows: HashMap<String, Window>, control: Option<Control>,flows:HashMap<String,workflow::Flow>, anchors:HashMap<String,VisualAnchor> }
impl State {
    fn pause(&mut self){if let Some(control)=self.control.as_mut(){control.observation=None;}}
}

pub fn stop_local() -> Value {
    crate::control_lease::stop();
    STOPPED.store(true, Ordering::SeqCst);
    // Retain the selected identity/checkpoint, never its input evidence. The
    // shared stopped lease still rejects all input until the owner's local resume.
    if let Ok(mut state) = STATE.get_or_init(|| Mutex::new(State::default())).try_lock() { state.pause(); }
    json!({"ok":true,"status":"stopped","action_state":"not_started","observation_state":"complete"})
}

pub fn configure(enabled: bool) -> Value {
    if !enabled { INPUT_ENABLED.store(false, Ordering::SeqCst); stop_local(); }
    else {
        if let Err(reason) = crate::control_lease::resume() { return fail(reason); }
        INPUT_ENABLED.store(true, Ordering::SeqCst);
        STOPPED.store(false, Ordering::SeqCst);
    }
    local_status()
}
pub fn connection_closed(epoch: &str) {
    let matches = OWNER.get_or_init(|| Mutex::new((String::new(),String::new(),String::new()))).lock()
        .map(|owner| owner.2 == epoch).unwrap_or(false);
    if matches { stop_local(); }
}
pub fn resume_local() -> Value {
    if !INPUT_ENABLED.load(Ordering::SeqCst) { return fail("desktop_input_disabled"); }
    let Ok(mut state) = STATE.get_or_init(|| Mutex::new(State::default())).try_lock() else { return fail("desktop_busy"); };
    if let Err(reason) = crate::control_lease::resume() { return fail(reason); }
    if let Some(control) = state.control.as_mut() { control.generation = monitor::input_generation(); control.observation = None; }
    STOPPED.store(false, Ordering::SeqCst);
    success()
}
pub fn local_status() -> Value {
    let mut out = json!({"ok":true,"input_enabled":INPUT_ENABLED.load(Ordering::SeqCst),
        "status":if STOPPED.load(Ordering::SeqCst) {"stopped"} else {"idle"}});
    if let Ok(state) = STATE.get_or_init(|| Mutex::new(State::default())).try_lock() {
        if let Some(control) = &state.control {
            out["status"] = json!(if STOPPED.load(Ordering::SeqCst) {"stopped"} else if monitor::input_generation() != control.generation {"paused"} else {"selected"});
            out["control_session_id"] = json!(control.id);
            if let Some(w) = state.windows.get(&control.window) { out["window_title"] = json!(windows::title(windows::handle(w.identity.hwnd))); }
        }
    } else { out["status"] = json!(if STOPPED.load(Ordering::SeqCst) {"stopping"} else {"running"}); }
    if let Some(scope)=super::targets::status().as_object(){for (key,value) in scope {out[key]=value.clone();}}
    if let Ok(progress)=WORKFLOW_PROGRESS.get_or_init(||Mutex::new(Value::Null)).try_lock(){if progress.is_object(){out["workflow"]=progress.clone();}}
    out
}

pub fn execute(invocation: &str, args: &Value) -> Value {
    let scope = string(args, "_control_scope"); let epoch = string(args, "_device_epoch");
    if scope.len() != 64 || epoch.is_empty() { return fail("caller_scope_missing"); }
    if !crate::control_lease::epoch_valid(epoch){return fail("device_epoch_expired");}
    let action = string(args, "action");
    if action == "status" {
        let operation = string(args, "operation_id");
        if !operation.is_empty() {
            return OPERATIONS.get_or_init(|| Mutex::new(HashMap::new())).lock().ok()
                .and_then(|ops| ops.get(&format!("{epoch}/{scope}/{operation}")).map(|(_, value)| value.clone()))
                .unwrap_or_else(|| fail("operation_unknown"));
        }
    }
    if action == "stop" {
        let expanded=match expand_context(args,scope,epoch){Ok(args)=>args,Err(reason)=>return fail(reason)};
        let args=&expanded;
        let authorized = OWNER.get_or_init(|| Mutex::new((String::new(),String::new(),String::new()))).lock()
            .map(|owner| owner.0 == scope && owner.1 == string(args,"control_session_id") && owner.2 == epoch && string(args,"device_epoch") == epoch).unwrap_or(false);
        return if authorized { stop_local() } else { fail("control_session_mismatch") };
    }
    let key = format!("{epoch}/{scope}/{invocation}"); let digest = args.to_string();
    let ledger = OPERATIONS.get_or_init(|| Mutex::new(HashMap::new()));
    {
        let Ok(mut ops) = ledger.lock() else { return fail("operation_ledger_unavailable"); };
        if let Some((previous, result)) = ops.get(&key) {
            return if previous == &digest { result.clone() } else { fail("invocation_id_request_conflict") };
        }
        if ops.len() >= 2048 { return fail("operation_budget_exhausted"); }
        ops.insert(key.clone(), (digest.clone(), json!({"ok":false,"operation_id":invocation,"device_epoch":epoch,
            "action_state":"unknown","observation_state":"failed","reason":"executing"})));
    }
    // The ledger is checked against the original request before resolving an
    // alias. A repeated invocation never becomes fresh input after eviction.
    let result = match expand_context(args,scope,epoch) {
        Ok(expanded)=>execute_locked(&expanded,scope,epoch),Err(reason)=>fail(reason),
    };
    let mut result = result;
    result["operation_id"] = json!(invocation); result["device_epoch"] = json!(epoch);
    if let Ok(mut contexts)=CONTEXTS.get_or_init(Default::default).lock(){contexts.publish(scope,epoch,&mut result);}
    let mut retained = result.clone();
    if let Some(shots) = retained.get_mut("screenshots").and_then(Value::as_array_mut) {
        for shot in shots { if let Some(obj) = shot.as_object_mut() { obj.remove("imageBase64"); } }
    }
    if let Ok(mut ops) = ledger.lock() { ops.insert(key, (digest, retained)); }
    result
}

fn expand_context(args:&Value,scope:&str,epoch:&str)->Result<Value,&'static str>{
    if args.get("context_ref").is_none(){return Ok(args.clone());}
    CONTEXTS.get_or_init(Default::default).lock().map_err(|_|"context_registry_unavailable")?.expand(args,scope,epoch)
}

#[cfg(test)]
pub(super) fn clear_contexts_for_test() {
    *CONTEXTS.get_or_init(Default::default).lock().unwrap() = Default::default();
}

fn execute_locked(args: &Value, scope: &str, epoch: &str) -> Value {
    let action = string(args,"action");
    // Only an explicit model observation can refresh a paused task. Internal
    // post-input/workflow observations never silently absorb human activity.
    if matches!(action,"observe"|"select_window") && input_enabled() { monitor::wait_for_quiet(); }
    let mut refresh_selection=false;
    let _lease = if matches!(action,"list_windows"|"status"|"observe"|"read_element") || (action=="select_window" && !input_enabled()) { None } else {
        match crate::control_lease::acquire("computer_use",scope,epoch,matches!(action,"select_window"|"launch_app"|"manage_window")) {
            Ok(mut lease) => {
                if action == "handoff" {
                    let Ok(mut state) = STATE.get_or_init(|| Mutex::new(State::default())).try_lock() else {return fail("desktop_busy");};
                    if !state.control.as_ref().is_some_and(|c|c.scope==scope && c.id==string(args,"control_session_id"))
                        || string(args,"device_epoch")!=epoch {return fail("control_session_mismatch");}
                    lease.handoff("browser_page"); state.control = None;
                    let mut out = success(); out["next_action"] = json!("browser_page.select_tab"); return out;
                }
                Some(lease)
            },
            Err("user_takeover") if action=="select_window"=>{
                // There may be no control_session_id yet. Inspect the explicitly
                // selected target first, without activating it; a subsequent call
                // can act on this fresh evidence after the lease is reconciled.
                refresh_selection=true;None
            },
            Err(reason) => return fail(reason),
        }
    };
    let Ok(mut state) = STATE.get_or_init(|| Mutex::new(State::default())).try_lock() else { return fail("desktop_busy"); };
    if state.epoch != epoch {
        *state = State { epoch: epoch.to_string(), ..State::default() };
    }
    if _lease.is_some() && state.control.as_ref().is_some_and(|c|c.scope!=scope) {
        // The shared lease admitted this caller only after idle ownership expired.
        // Retire the old control evidence/checkpoints before selecting a new target.
        state.control=None;state.flows.clear();state.anchors.clear();
        if let Ok(mut owner)=OWNER.get_or_init(Default::default).lock(){*owner=Default::default();}
    }
    if let Some(lease)=_lease.as_ref(){if let Err(reason)=lease.check(){return fail(reason);}}
    let observed_generation=monitor::input_generation();
    let mut out=dispatch(&mut state, args, scope,_lease.as_ref().map(|lease|lease.input_generation()));
    if (action=="observe" || refresh_selection) && input_enabled() && out["ok"]==true {
        let recovery=if STOPPED.load(Ordering::SeqCst){Err("stopped_requires_local_resume")}
            else{crate::control_lease::acknowledge_observation("computer_use",scope,epoch,observed_generation)};
        match recovery {
            Ok(())=>{
                if let Some(control)=state.control.as_mut(){control.generation=observed_generation;}
                out["control_status"]=json!("selected");
                if refresh_selection && out["foreground_matches"]!=true {
                    out["next_action"]=json!("select_window");
                    out["recovery_hint"]=json!("已重新观察所选窗口并刷新控制凭据；本次没有切换前台。确认仍是任务目标后，再 select_window 激活并取得新观察。");
                }
            },
            Err(reason)=>{
                state.pause();
                out["control_status"]=json!(if reason=="stopped_requires_local_resume"{"stopped"}else{"paused"});
                out["observation_state"]=json!("partial");
                for (key,value) in fail(reason).as_object().unwrap(){
                    if !matches!(key.as_str(),"action_state"|"observation_state"){out[key]=value.clone();}
                }
            },
        }
    }
    out
}

fn dispatch(state: &mut State, args: &Value, scope: &str,generation:Option<u64>) -> Value {
    let action = string(args, "action");
    if action == "launch_app" { return launch(args, scope, &state.epoch); }
    if action == "list_windows" {
        if !monitor::start() { return fail("desktop_monitor_unavailable"); }
        let live = windows::enumerate();
        state.windows.retain(|_, old| live.iter().any(|(id, _)| id == &old.identity));
        let query = string(args, "query").to_lowercase();
        let mut rows = Vec::new();
        for (identity, title) in live {
            if !super::targets::allows(&identity){continue;}
            let application=windows::process_name(&identity);
            if !title.to_lowercase().contains(&query) && !application.contains(&query) { continue; }
            let key = state.windows.iter().find(|(_, w)| w.scope == scope && w.identity == identity).map(|(key, _)| key.clone())
                .unwrap_or_else(|| reference("window"));
            state.windows.insert(key.clone(), Window { scope: scope.to_string(), identity: identity.clone() });
            let mut row=windows::summary(&identity);row["window_id"]=json!(key);rows.push(row);
        }
        let offset = args["offset"].as_u64().unwrap_or(0) as usize; let limit = args["limit"].as_u64().unwrap_or(20) as usize;
        let total = rows.len(); let page: Vec<_> = rows.into_iter().skip(offset).take(limit).collect();
        let mut out = success(); out["windows"] = json!(page); out["total"] = json!(total);
        out["input_enabled"]=json!(input_enabled());
        out["next_offset"] = if offset + limit < total { json!(offset + limit) } else { Value::Null };
        return out;
    }
    if action == "status" {
        let mut out = success();
        out["input_enabled"] = json!(input_enabled());
        out["status"] = json!(if STOPPED.load(Ordering::SeqCst) || crate::control_lease::stopped() { "stopped" }
            else if state.control.as_ref().is_some_and(|control|control.generation!=monitor::input_generation()) {"paused"}
            else if state.control.is_some() { "selected" } else { "idle" });
        if let Some(c) = &state.control { if c.scope == scope { out["control_session_id"] = json!(c.id); out["window_id"] = json!(c.window); } }
        return out;
    }
    if string(args, "device_epoch") != state.epoch { return fail("device_epoch_expired"); }
    if action=="manage_window"{return manage_window(state,args,scope,generation);}
    if action == "select_window" {
        if let Some(c) = &state.control { if c.scope != scope && !STOPPED.load(Ordering::SeqCst) { return fail("desktop_owned_by_other_session"); } }
        let window = string(args, "window_id");
        let Some(w) = state.windows.get(window) else { return fail("window_reference_expired"); };
        if w.scope != scope { return fail("window_scope_mismatch"); }
        if !super::targets::allows(&w.identity){return fail("local_window_scope_mismatch");}
        if !monitor::start() { return fail("desktop_monitor_unavailable"); }
        if INPUT_ENABLED.load(Ordering::SeqCst) && generation.is_some() && (!windows::foreground(&w.identity)||windows::minimized(&w.identity)) {
            if let Err(reason) = windows::activate(&w.identity,generation.unwrap_or_else(monitor::input_generation)) { return fail(reason); }
        }
        if let Err(reason) = windows::geometry(&w.identity) {
            // A minimized target still provides stable identity evidence for
            // reconciling a quiet user takeover. It must not require a screenshot
            // that can only be obtained after activation in the next call.
            if reason=="window_minimized" && input_enabled() && generation.is_none(){
                let mut out=success();out["window_id"]=json!(window);out["window_state"]=windows::summary(&w.identity);
                out["observation_state"]=json!("partial");out["input_enabled"]=json!(true);return out;
            }
            return fail(reason);
        }
        STOPPED.store(false, Ordering::SeqCst);
        // A local resume followed by reselecting the same target keeps concrete
        // approvals bound to this session. Choosing a different window does not.
        let id = state.control.as_ref().filter(|c|c.scope==scope&&c.window==window).map(|c|c.id.clone()).unwrap_or_else(||reference("control"));
        if let Ok(mut owner) = OWNER.get_or_init(|| Mutex::new((String::new(),String::new(),String::new()))).lock() {
            *owner = (scope.to_string(), id.clone(), state.epoch.clone());
        }
        state.control = Some(Control { id, scope: scope.to_string(), window: window.to_string(), root_window:window.to_string(), generation: monitor::input_generation(), observation: None });
        return observe(state, args);
    }
    let Some(control) = state.control.as_ref() else { return fail("no_control_session"); };
    if control.scope != scope || control.id != string(args, "control_session_id") { return fail("control_session_mismatch"); }
    if STOPPED.load(Ordering::SeqCst) { return fail("stopped"); }
    if action == "observe" { return observe(state, args); }
    if action == "read_element" {
        let Some(observation)=control.observation.as_ref()else{return fail("observation_expired");};
        if observation.id!=string(args,"observation_id"){return fail("observation_expired");}
        let element=string(args,"element_id");
        let row=observation.ax["elements"].as_array().and_then(|rows|rows.iter().find(|row|row["element_id"]==element));
        if !row.is_some_and(|row|row["stable_identity"]==true){return fail("element_identity_unavailable");}
        let Some(window)=state.windows.get(&control.window)else{return fail("window_reference_expired");};
        if !super::targets::allows(&window.identity){return fail("local_window_scope_mismatch");}
        let detail=super::accessibility::read_element(&window.identity,&observation.id,element);
        let mut out=success();out["source_observation_id"]=json!(observation.id);out["source_element_id"]=json!(element);
        out["captured_at"]=json!(now() as u64);out["read_only_details"]=json!(true);
        out["observation_state"]=json!(if detail["ax_status"]=="unavailable"{"failed"}else if detail["text_complete"]==true{"complete"}else{"partial"});
        if detail["ax_status"]=="unavailable"{out["ok"]=json!(false);out["reason"]=detail["ax_reason"].clone();}
        out["detail"]=detail;return out;
    }
    if matches!(action,"run_actions"|"run_steps"|"resume_steps"){return execute_workflow(state,args,scope);}
    if matches!(action, "click"|"type_text"|"press_key"|"scroll"|"set_value"|"set_checked"|"drag") { return perform(state, args); }
    fail("action_not_implemented")
}

struct NativeDriver<'a>{state:&'a mut State,scope:&'a str,generation:u64,preflight:bool,permission_mode:&'a str,anchor:Option<VisualAnchor>}
impl workflow::Driver for NativeDriver<'_>{
    fn observe(&mut self,visual:bool)->Value{
        let mut out=if self.preflight {
            self.state.control.as_ref().and_then(|c|c.observation.as_ref()).map(|o|o.ax.clone()).unwrap_or_else(||fail("observation_required"))
        }else{observe(self.state,&json!({"mode":if visual{if self.anchor.is_some(){"visual"}else{"hybrid"}}else{"text"}}))};
        out["device_epoch"]=json!(self.state.epoch);out
    }
    fn step_observation(&mut self,visual_batch:bool)->Value{
        if !visual_batch{return self.observe(false);}
        if let Some(reason)=self.interrupted(){return fail(reason);}
        let Some(anchor)=self.anchor.as_ref()else{return fail("visual_anchor_expired");};
        let Some(control)=self.state.control.as_mut()else{return fail("no_control_session");};
        if control.window!=anchor.window{return fail("visual_batch_window_changed");}
        let Some(window)=self.state.windows.get(&control.window)else{return fail("window_reference_expired");};
        if windows::geometry(&window.identity).ok().as_ref()!=Some(&anchor.observation.geometry){return fail("window_layout_changed");}
        if !windows::foreground(&window.identity){return fail("foreground_mismatch");}
        let mut observation=anchor.observation.clone();observation.id=reference("observation");
        let mut out=success();out["control_session_id"]=json!(control.id);out["device_epoch"]=json!(self.state.epoch);
        out["observation_id"]=json!(observation.id);out["screenshot_id"]=json!(observation.shot);
        if self.permission_mode!="trusted_auto_allow" {
            observation.ax=super::accessibility::snapshot(&window.identity,&observation.id);
        }
        control.observation=Some(observation);out
    }
    fn prepare(&mut self,args:&Value)->Value {let mut args=args.clone();args["_phase"]=json!("prepare");args["_permission_mode"]=json!(self.permission_mode);args["_batch_input"]=json!(self.anchor.is_some());perform(self.state,&args)}
    fn execute(&mut self,args:&Value,binding:&str)->Value{
        let mut args=args.clone();args["_phase"]=json!("execute");args["_approval_binding"]=json!(binding);
        args["_permission_mode"]=json!(self.permission_mode);
        args["_batch_input"]=json!(self.anchor.is_some());
        args["_workflow_text_observation"]=json!(true);perform(self.state,&args)
    }
    fn select(&mut self,window:&str)->Value{
        let args=json!({"action":"select_window","window_id":window,"device_epoch":self.state.epoch,"mode":"text"});
        dispatch(self.state,&args,self.scope,Some(self.generation))
    }
    fn interrupted(&self)->Option<&'static str>{
        if STOPPED.load(Ordering::SeqCst)||crate::control_lease::stopped(){Some("stopped_requires_local_resume")}
        else if monitor::input_generation()!=self.generation{Some("user_takeover")}
        else if !INPUT_ENABLED.load(Ordering::SeqCst){Some("desktop_input_disabled")}else{None}
    }
    fn progress(&self,flow:&workflow::Flow){
        if let Ok(mut value)=WORKFLOW_PROGRESS.get_or_init(||Mutex::new(Value::Null)).lock(){
            *value=json!({"id":flow.id,"state":flow.state,"next_step":flow.index,"total_steps":flow.steps.len()});
        }
    }
}

fn execute_workflow(state:&mut State,args:&Value,scope:&str)->Value{
    let control=state.control.as_ref().unwrap();
    if !INPUT_ENABLED.load(Ordering::SeqCst){return fail("desktop_input_disabled");}
    if monitor::input_generation()!=control.generation{return fail("user_takeover");}
    if control.observation.as_ref().is_none_or(|o|o.id!=string(args,"observation_id")||now().saturating_sub(o.captured_at)>OBSERVATION_TTL_MS){return fail("observation_expired");}
    let generation=control.generation;let session=control.id.clone();
    let preparing=string(args,"_phase")=="prepare";
    if matches!(string(args,"action"),"run_steps"|"run_actions") {
        let visual=string(args,"action")=="run_actions";
        let plan=&args[if visual{"actions"}else{"steps"}];
        if !(if visual{workflow::valid_actions(plan)}else{workflow::valid_steps(plan)}){return fail("workflow_plan_invalid");}
        let anchor=if visual {
            let observation=control.observation.as_ref().unwrap();
            if observation.shot!=string(args,"screenshot_id")||observation.frame.is_none(){return fail("screenshot_expired");}
            let Some(window)=state.windows.get(&control.window)else{return fail("window_reference_expired");};
            if let Err(reason)=revalidate_aged_visual(observation,&window.identity){return fail(reason);}
            Some(VisualAnchor{window:control.window.clone(),observation:observation.clone()})
        }else{None};
        if preparing {
            use sha2::{Digest,Sha256};
            let preview=json!({"steps":plan,"visual":visual,"control_session_id":session,"device_epoch":state.epoch});
            let mut out=success();out["authorization"]=json!({"required":false,"binding":format!("{:x}",Sha256::digest(format!("{scope}\0{preview}").as_bytes())),"preview":preview});return out;
        }
        state.flows.retain(|_,flow|flow.created.elapsed()<=std::time::Duration::from_secs(900));
        state.anchors.retain(|id,_|state.flows.contains_key(id));
        if state.flows.len()>=64{return fail("workflow_budget_exhausted");}
        if visual&&state.anchors.len()>=8{return fail("visual_anchor_budget_exhausted");}
        let mut flow=workflow::Flow::new(reference("workflow"),scope.into(),session,plan.as_array().unwrap().clone());flow.visual=visual;
        let mut driver=NativeDriver{state,scope,generation,preflight:false,permission_mode:string(args,"_permission_mode"),anchor:anchor.clone()};
        let out=workflow::run(&mut flow,&mut driver,"",args["budget_ms"].as_u64().unwrap_or(8000));
        if !matches!(flow.state,"completed"|"unknown"){if let Some(anchor)=anchor{state.anchors.insert(flow.id.clone(),anchor);}}
        state.flows.insert(flow.id.clone(),flow);out
    }else{
        let id=string(args,"workflow_id");
        let Some(mut flow)=state.flows.remove(id)else{return fail("workflow_unknown");};
        let result=if flow.scope!=scope||flow.session!=session {fail("workflow_session_mismatch")}
        else if flow.created.elapsed()>std::time::Duration::from_secs(900){fail("workflow_expired")}
        else{
            if flow.visual {
                let control=state.control.as_ref().unwrap();
                let checked=state.windows.get(&control.window).ok_or("window_reference_expired")
                    .and_then(|window|revalidate_aged_visual(control.observation.as_ref().unwrap(),&window.identity));
                if let Err(reason)=checked {state.flows.insert(id.into(),flow);return fail(reason);}
            }
            let mut anchor=state.anchors.get(id).cloned();
            if let Some(anchor)=anchor.as_mut(){
                // A new explicit model observation may renew the time budget,
                // but coordinates still compare to the original pixels/geometry.
                let current=state.control.as_ref().unwrap();
                if current.window==anchor.window {
                    if let Some(observation)=current.observation.as_ref().filter(|o|o.frame.is_some()&&o.geometry==anchor.observation.geometry){
                        anchor.observation.captured_at=observation.captured_at;
                    }
                }
            }
            let saved_observation=if preparing{state.control.as_ref().and_then(|c|c.observation.clone())}else{None};
            let mut driver=NativeDriver{state,scope,generation,preflight:preparing,permission_mode:string(args,"_permission_mode"),anchor};
            let mut out=if preparing {workflow::prepare_resume(&flow,&mut driver)}else{workflow::run(&mut flow,&mut driver,string(args,"_approval_binding"),args["budget_ms"].as_u64().unwrap_or(8000))};
            if preparing {
                state.control.as_mut().unwrap().observation=saved_observation;
                out["workflow_kind"]=json!(if flow.visual{"visual_actions"}else{"verified_steps"});
            }
            out
        };
        if matches!(flow.state,"completed"|"unknown"){state.anchors.remove(id);}
        state.flows.insert(id.into(),flow);result
    }
}

fn observe(state: &mut State, args: &Value) -> Value {
    let control = state.control.as_mut().unwrap();
    control.observation = None;
    if let Some(root)=state.windows.get(&control.root_window){
        if let Some(identity)=windows::foreground_owned_by(&root.identity){
            let key=state.windows.iter().find(|(_,w)|w.scope==control.scope && w.identity==identity).map(|(k,_)|k.clone()).unwrap_or_else(||reference("window"));
            state.windows.insert(key.clone(),Window{scope:control.scope.clone(),identity});control.window=key;
        }
    }
    let Some(window) = state.windows.get(&control.window) else { return fail("window_reference_expired"); };
    if !super::targets::allows(&window.identity){return fail("local_window_scope_mismatch");}
    let visual = string(args, "mode") != "text";
    // A single retry bounds a resize/move race without inventing usable coordinates.
    for attempt in 0..2 {
        let before = match windows::geometry(&window.identity) { Ok(v) => v, Err(r) => return fail(r) };
        let frame = if visual { match capture::capture(&window.identity, &STOPPED) {
            Ok(v) => Some(v), Err("capture_size_changed") if attempt == 0 => continue,
            Err(reason) => return fail(reason),
        }} else { None };
        let after = match windows::geometry(&window.identity) { Ok(v) => v, Err(r) => return fail(r) };
        if before != after {
            if attempt == 0 { continue; }
            let mut out = fail("window_layout_changed"); out["observation_state"] = json!("unstable"); return out;
        }
        let id = reference("observation"); let shot = reference("screenshot"); let captured = now();
        let mut out = success();
        out["control_session_id"] = json!(control.id); out["window_id"] = json!(control.window);
        out["title"]=json!(windows::title(windows::handle(window.identity.hwnd)));
        if control.window!=control.root_window {out["owner_window_id"]=json!(control.root_window);}
        out["observation_id"] = json!(id); out["captured_at"] = json!(captured as u64);
        out["foreground_matches"] = json!(windows::foreground(&window.identity));
        out["window_bounds"] = windows::bounds_json(before.bounds); out["client_bounds"] = windows::bounds_json(before.client);
        out["dpi"] = json!(before.dpi); out["coordinate_space"] = json!("screenshot_pixels");
        out["ax_status"] = json!("unavailable"); out["focus_evidence"] = json!({"source":"unknown"});
        out["text_complete"] = json!(false); out["elements"] = json!([]); out["screenshots"] = json!([]);
        if matches!(string(args, "mode"), "text" | "hybrid") {
            let mut ax = super::accessibility::snapshot(&window.identity, &id);
            if matches!(windows::process_name(&window.identity).as_str(),"qq.exe"|"qqnt.exe") {
                ax["message_target"]=policy::message_target(&ax,before.bounds,before.dpi);
            }
            if let Some(fields) = ax.as_object() { for (key, value) in fields { out[key] = value.clone(); } }
            if out["ax_status"] == "unavailable" { out["observation_state"] = json!("partial"); }
        }
        let region=if let Some(frame) = &frame {
            let region=match super::region::Region::from_args(args,frame.width,frame.height){Ok(v)=>v,Err(r)=>{
                let mut out=fail(r);out["coordinate_bounds"]=json!({"space":"window_pixels","width":frame.width,"height":frame.height});return out;
            }};
            let png=match region.png(frame){Ok(v)=>v,Err(r)=>return fail(r)};
            out["screenshots"] = json!([{"screenshot_id":shot,"region_id":"window","width":region.width*region.scale,"height":region.height*region.scale,
                "origin":{"x":before.bounds[0]+region.x as i32,"y":before.bounds[1]+region.y as i32},"scale":region.scale,"mimeType":"image/png",
                "imageBase64":base64::engine::general_purpose::STANDARD.encode(&png)}]); Some(region)
        }else{None};
        // UIA may outlive the first frame: never bless evidence after a move or takeover.
        if windows::geometry(&window.identity).ok().as_ref() != Some(&before) {
            if attempt == 0 { continue; }
            let mut changed = fail("window_layout_changed"); changed["observation_state"] = json!("unstable"); return changed;
        }
        out["control_status"] = json!(if monitor::input_generation() != control.generation {"paused"} else {"selected"});
        let mut ax = out.clone(); ax["screenshots"] = json!([]);
        control.observation = Some(Observation { id, shot, geometry: before, frame:frame.map(Arc::new), region, captured_at: captured, ax });
        return out;
    }
    fail("observation_unstable")
}

fn perform(state: &mut State, args: &Value) -> Value {
    if !INPUT_ENABLED.load(Ordering::SeqCst) { return fail("desktop_input_disabled"); }
    let control = state.control.as_ref().unwrap();
    if monitor::input_generation() != control.generation { return fail("user_takeover"); }
    let Some(window) = state.windows.get(&control.window) else { return fail("window_reference_expired"); };
    let identity = window.identity.clone();
    if !super::targets::allows(&identity){return fail("local_window_scope_mismatch");}
    if !windows::foreground(&identity) { return fail("foreground_mismatch"); }
    let Some(observation) = control.observation.as_ref() else { return fail("observation_required"); };
    if string(args,"observation_id") != observation.id { return fail("observation_expired"); }
    if now().saturating_sub(observation.captured_at) > OBSERVATION_TTL_MS { return fail("observation_expired"); }
    if windows::geometry(&identity).ok().as_ref() != Some(&observation.geometry) { return fail("window_layout_changed"); }
    let action = string(args,"action");
    let mut point = None; let mut target = None;let mut destination=None;
    let mut fresh_ax = Value::Null;
    if !string(args,"element_id").is_empty() || (matches!(action,"type_text"|"press_key") && !(args["_batch_input"]==true&&policy::full_access(args))) {
        fresh_ax = super::accessibility::snapshot(&identity, &observation.id);
    }
    if matches!(action,"click"|"scroll"|"set_value"|"set_checked"|"drag") {
        if !string(args,"element_id").is_empty() {
            let requested = string(args,"element_id");
            let old = observation.ax["elements"].as_array().and_then(|items| items.iter().find(|item| item["element_id"] == requested));
            let current = fresh_ax["elements"].as_array().and_then(|items| items.iter().find(|item| item["element_id"] == requested));
            if old.is_some_and(|row|row["stable_identity"]!=true){return fail("element_identity_unavailable");}
            let (Some(old), Some(current)) = (old, current) else { return fail("element_expired"); };
            let context=policy::message_context(&fresh_ax,observation.geometry.bounds,observation.geometry.dpi);
            if ["name","role","bounds","password"].iter().any(|key| old[key] != current[key])
                || !policy::target_enabled(&windows::process_name(&identity),args,&context,current)
                || current["offscreen"] != false || current["stable_identity"] != true { return fail("element_changed"); }
            let rect = &current["bounds"];
            point = Some(((rect["x"].as_i64().unwrap_or(0) + rect["width"].as_i64().unwrap_or(0)/2) as i32,
                          (rect["y"].as_i64().unwrap_or(0) + rect["height"].as_i64().unwrap_or(0)/2) as i32));
            target = Some(current.clone());
        } else {
            if string(args,"screenshot_id") != observation.shot { return fail("screenshot_expired"); }
            let Some(frame) = &observation.frame else { return fail("screenshot_required"); };
            let (Some(x), Some(y)) = (args["x"].as_u64(), args["y"].as_u64()) else { return fail("coordinate_required"); };
            let Some((x,y))=observation.region.and_then(|region|region.point(x,y)) else{return coordinate_error(observation);};
            let rect = observation.geometry.bounds;
            if rect[2]-rect[0] != frame.width as i32 || rect[3]-rect[1] != frame.height as i32 { return fail("coordinate_mapping_unverified"); }
            // Batches revalidate their starting scene before entering the loop;
            // later steps intentionally change it but still compare target pixels.
            let rechecked=if args["_batch_input"]==true{None}else{
                match revalidate_aged_visual(observation,&identity){Ok(frame)=>frame,Err(reason)=>return fail(reason)}
            };
            let current = match rechecked {Some(frame)=>frame,None=>match capture::capture(&identity, &STOPPED) { Ok(v) => v, Err(reason) => return fail(reason) }};
            if !patch_matches(frame, &current, x as u32, y as u32) { return fail("target_pixels_changed"); }
            point = Some((rect[0] + x as i32, rect[1] + y as i32));
            if action=="drag" {
                let (Some(to_x),Some(to_y))=(args["to_x"].as_u64(),args["to_y"].as_u64())else{return fail("drag_destination_required");};
                let Some((to_x,to_y))=observation.region.and_then(|region|region.point(to_x,to_y))else{return coordinate_error(observation);};
                if !patch_matches(frame,&current,to_x as u32,to_y as u32){return fail("target_pixels_changed");}
                destination=Some((rect[0]+to_x as i32,rect[1]+to_y as i32));
            }
        }
    }
    let app = windows::process_name(&identity); let title = windows::title(windows::handle(identity.hwnd));
    let mut focus = fresh_ax["focus_evidence"].clone();
    if matches!(app.as_str(),"qq.exe"|"qqnt.exe") && focus.is_object(){
        focus["message_target"]=policy::message_target(&fresh_ax,observation.geometry.bounds,observation.geometry.dpi);
        focus["message_context"]=policy::message_context(&fresh_ax,observation.geometry.bounds,observation.geometry.dpi);
        if !policy::full_access(args)&&action=="type_text"&&observation.ax["message_target"]["status"]=="verified"
            &&focus["message_target"]["header_recipient"]!=observation.ax["message_target"]["header_recipient"] {
            return fail("message_recipient_changed");
        }
    }
    if action == "type_text" {
        let previous = &observation.ax["focus_evidence"];
        let visual=observation.frame.is_some() && now().saturating_sub(observation.captured_at)<=OBSERVATION_TTL_MS;
        if policy::full_access(args)&&visual&&args["_batch_input"]!=true&&!policy::typing_evidence(args,previous,&focus,false){
            if let Err(reason)=revalidate_aged_visual(observation,&identity){return fail(reason);}
        }
        if !policy::typing_evidence(args,previous,&focus,visual) {
                let mut out=fail(if policy::full_access(args){"typing_observation_required"}else{"focus_unverified"});
                out["previous_focus_status"]=observation.ax["focus_status"].clone();out["current_focus_status"]=fresh_ax["focus_status"].clone();
                // Explain the failed proof without retaining draft contents.
                for (key,value) in [("previous_focus",previous),("current_focus",&focus)] {
                    out[key]=json!({"source":value["source"],"editable":value["editable"],"password":value["password"],
                        "stable_identity":value["stable_identity"],"element_id":value["element_id"],"bounds":value["bounds"]});
                }
                return out;
            }
    }
    if action == "press_key" && input::key_codes(string(args,"key")).is_none() { return fail("key_unsupported"); }
    if action=="set_value" {
        let Some(t)=target.as_ref()else{return fail("element_required");};
        if t["value_pattern"]!=true||t["editable"]!=true||t["password"]!=false||t["focused"]!=true||t["value_complete"]!=true{return fail("value_pattern_unavailable");}
        let old=observation.ax["elements"].as_array().and_then(|items|items.iter().find(|el|el["element_id"]==t["element_id"]));
        if old.is_none_or(|old|old["value_complete"]!=true||old["value"]!=t["value"]){return fail("value_changed");}
    }
    if action=="set_checked" {
        let Some(t)=target.as_ref()else{return fail("element_required");};
        if !t["checked"].is_boolean()||t["password"]!=false{return fail("toggle_pattern_unavailable");}
        let old=observation.ax["elements"].as_array().and_then(|items|items.iter().find(|el|el["element_id"]==t["element_id"]));
        if old.is_none_or(|old|old["checked"]!=t["checked"]){return fail("checked_changed");}
    }
    let needs_approval = match policy::classify(&app,args,&focus,target.as_ref()) { Ok(v) => v, Err(reason) => {
        let mut out=fail(reason);if focus["message_target"].is_object(){out["message_target"]=focus["message_target"].clone();}return out;
    } };
    // This check precedes approval and evidence consumption. A covered point
    // has not received input; later failures after injection remain unknown.
    if let Some((x,y))=point {
        if let Err(reason)=input::point_available(&identity,x,y){return occlusion_result(state,&identity,x,y,reason);}
    }
    let (binding, preview) = policy::binding(&control.scope,&state.epoch,&control.window,&control.id,&app,&title,args,&focus,point,target.as_ref());
    if string(args,"_phase") == "prepare" {
        let mut out = success(); out["authorization"] = json!({"required":needs_approval,"binding":binding,"preview":preview}); return out;
    }
    if needs_approval && string(args,"_approval_binding") != binding { return fail("specific_approval_required"); }
    if monitor::input_generation() != control.generation || STOPPED.load(Ordering::SeqCst) { return fail("user_takeover"); }
    if windows::geometry(&identity).ok().as_ref() != Some(&observation.geometry) || !windows::foreground(&identity) { return fail("window_changed_before_input"); }
    let generation = control.generation;
    let value_pattern=action=="type_text" && focus["value_pattern"]==true && focus["value"]=="";
    let value_element=focus["element_id"].as_str().unwrap_or("").to_string();
    let value_observation=observation.id.clone();
    // Consumed before the first OS call, even if the subsequent observation fails.
    state.control.as_mut().unwrap().observation = None;
    let injected = match action {
        "click" => { let (x,y) = point.unwrap(); input::point_action(&identity,x,y,None,args["button"].as_str().unwrap_or("left"),args["click_count"].as_u64().unwrap_or(1),generation,&STOPPED) },
        "drag" => input::drag(&identity,point.unwrap(),destination.unwrap(),args["duration_ms"].as_u64().unwrap_or(500),generation,&STOPPED),
        "set_value"|"set_checked"=>{
            let target=target.as_ref().unwrap();let field=if action=="set_value"{"value"}else{"checked"};
            let desired=if action=="set_value"{args["text"].clone()}else{args["checked"].clone()};
            let result=super::accessibility::set_control_state(&identity,&value_observation,action,string(target,"element_id"),target[field].clone(),desired,generation);
            match result["value_state"].as_str(){Some("executed"|"unchanged")=>Ok(()),Some("not_started")=>return fail(string(&result,"reason")),_=>Err("uia_mutation_result_unknown")}
        },
        "scroll" => { let (x,y) = point.unwrap(); let amount = args["amount"].as_i64().unwrap_or(3) as i32;
            input::point(&identity,x,y,Some(amount * 120 * if string(args,"direction") == "up" {1} else {-1}),generation,&STOPPED) },
        "type_text" => {
            if value_pattern {
                let value=super::accessibility::set_empty_value(&identity,&value_observation,&value_element,string(args,"text"),generation);
                match value["value_state"].as_str(){
                    Some("executed")=>Ok(()),Some("unsupported")=>input::text(&identity,string(args,"text"),generation,&STOPPED),
                    Some("not_started")=>return fail(value["reason"].as_str().unwrap_or("uia_input_not_started")),
                    _=>Err("uia_input_result_unknown"),
                }
            }else{input::text(&identity,string(args,"text"),generation,&STOPPED)}
        },
        "press_key" => input::press(&identity,string(args,"key"),generation,&STOPPED),
        _ => Err("action_unsupported"),
    };
    if let Err(reason) = injected {
        let mut out = fail(reason); out["action_state"] = json!("unknown"); out["next_action"] = json!("observe_or_status_do_not_replay"); return out;
    }
    std::thread::sleep(std::time::Duration::from_millis(if args["_batch_input"]==true{60}else{120}));
    if args["_batch_input"]==true {
        let mut out=success();out["action_state"]=json!("executed");out["observation_state"]=json!("pending");return out;
    }
    let mut observation_args = args.clone(); observation_args["mode"] = json!(if args["_workflow_text_observation"]==true{"text"}else{"hybrid"});
    let mut out = observe(state, &observation_args);
    out["action_state"] = json!("executed");
    if action == "type_text" {
        let expected = string(args,"text");
        let verified = out["elements"].as_array().is_some_and(|elements| elements.iter().any(|el|
            el["focused"] == true && el["value"].as_str().is_some_and(|text| text.contains(expected))));
        out["effect_evidence"] = json!({"stage":if verified {"typed"} else {"input_executed"},"draft_verified":verified,"delivery_confirmed":"unavailable"});
    }
    if matches!(app.as_str(),"qq.exe"|"qqnt.exe") && matches!(action,"click"|"press_key") {
        // Sending input is not delivery. The next observation (including its
        // image) must establish a new message in this chat; never replay blindly.
        let recognized_send=focus["message_context"].is_object() &&
            (target.as_ref().is_some_and(|t|policy::reviewed_send_click(&app,args,&focus["message_context"],t)) ||
             (action=="press_key"&&matches!(string(args,"key").to_ascii_lowercase().as_str(),"enter"|"ctrl+enter")));
        out["effect_evidence"]=json!({"stage":if recognized_send{"send_attempted"}else{"input_executed"},"delivery_confirmed":"unavailable"});
        out["next_action"]=json!(if recognized_send{"verify_chat_delivery_do_not_replay"}else{"verify_ui_effect_do_not_replay"});
    }
    out
}

fn coordinate_error(observation:&Observation)->Value{
    let mut out=fail("coordinate_out_of_bounds");
    if let Some(region)=observation.region {out["coordinate_bounds"]=json!({"space":"screenshot_pixels",
        "screenshot_id":observation.shot,"width":region.width*region.scale,"height":region.height*region.scale});}
    out
}

// This proves that the original scene is still usable; it does not replace its
// screenshot/reference or pretend the model has inspected a new image.
fn revalidate_aged_visual(observation:&Observation,identity:&Identity)->Result<Option<capture::Frame>,&'static str>{
    let age=now().saturating_sub(observation.captured_at);
    if age>OBSERVATION_TTL_MS{return Err("observation_expired");}
    if age<=VISUAL_RECHECK_AFTER_MS{return Ok(None);}
    if windows::geometry(identity).ok().as_ref()!=Some(&observation.geometry){return Err("window_layout_changed");}
    if !windows::foreground(identity){return Err("foreground_mismatch");}
    let before=observation.frame.as_ref().ok_or("screenshot_required")?;
    let after=capture::capture(identity,&STOPPED)?;
    if !scene_matches(before,&after){return Err("visual_scene_changed");}
    Ok(Some(after))
}

fn scene_matches(before:&capture::Frame,after:&capture::Frame)->bool{
    if before.width==0||before.height==0||before.width!=after.width||before.height!=after.height{return false;}
    // Apply the existing patch tolerance across the entire scene, rather than
    // letting a small changed dialog disappear in a whole-window average.
    (0..before.height).step_by(49).all(|y|(0..before.width).step_by(49).all(|x|
        patch_matches(before,after,(x+24).min(before.width-1),(y+24).min(before.height-1))))
}

fn patch_matches(before: &capture::Frame, after: &capture::Frame, x: u32, y: u32) -> bool {
    if before.width != after.width || before.height != after.height { return false; }
    let mut changed = 0usize; let mut count = 0usize;
    for row in y.saturating_sub(24)..=(y+24).min(before.height-1) {
        for col in x.saturating_sub(24)..=(x+24).min(before.width-1) {
            let start = ((row * before.width + col) * 4) as usize;
            if (0..3).any(|channel| before.rgba[start+channel].abs_diff(after.rgba[start+channel]) > 24) { changed += 1; }
            count += 1;
        }
    }
    count > 0 && changed * 100 <= count * 3
}

fn occlusion_result(state:&mut State,target:&Identity,x:i32,y:i32,reason:&str)->Value{
    let mut out=fail(reason);
    if reason!="target_occluded"{return out;}
    if let Some(identity)=windows::at_point(x,y){
        let mut blocker=windows::summary(&identity);
        blocker["relationship"]=json!(if windows::owned_by(target,&identity){"owned_popup"}
            else if identity.pid==target.pid&&identity.started==target.started{"same_application"}else{"other_application"});
        let allowed=super::targets::allows(&identity);blocker["within_control_scope"]=json!(allowed);
        if allowed {
            let scope=state.control.as_ref().map(|c|c.scope.clone()).unwrap_or_default();
            let key=state.windows.iter().find(|(_,w)|w.scope==scope&&w.identity==identity).map(|(k,_)|k.clone()).unwrap_or_else(||reference("window"));
            state.windows.insert(key.clone(),Window{scope,identity});blocker["window_id"]=json!(key);
        }
        out["occluder"]=blocker;
    }
    out
}

fn manage_window(state:&mut State,args:&Value,scope:&str,generation:Option<u64>)->Value{
    if !input_enabled(){return fail("desktop_input_disabled");}
    let Some(generation)=generation else{return fail("user_takeover");};
    let window=string(args,"window_id");
    let Some(target)=state.windows.get(window).filter(|w|w.scope==scope)else{return fail("window_reference_expired");};
    let identity=target.identity.clone();
    if windows::identity(windows::handle(identity.hwnd)).as_ref()!=Some(&identity){return fail("window_identity_expired");}
    if !super::targets::allows(&identity){return fail("local_window_scope_mismatch");}
    let action=string(args,"window_action");
    if action=="move"&&windows::minimized(&identity){return fail("window_minimized");}
    let before=windows::summary(&identity);
    let preview=json!({"action":"manage_window","window_id":window,"window_action":action,
        "desktop_x":args["desktop_x"],"desktop_y":args["desktop_y"],"device_epoch":state.epoch,"window":before});
    use sha2::{Digest,Sha256};
    let binding=format!("{:x}",Sha256::digest(format!("{scope}\0{preview}").as_bytes()));
    let required=!policy::full_access(args);
    if string(args,"_phase")=="prepare"{
        let mut out=success();out["authorization"]=json!({"required":required,"binding":binding,"preview":preview});return out;
    }
    if required&&string(args,"_approval_binding")!=binding{return fail("specific_approval_required");}
    // Any window manipulation invalidates the selected surface's coordinates,
    // including moving/minimizing an unrelated occluder.
    state.pause();
    let result=windows::manage(&identity,action,args["desktop_x"].as_i64().unwrap_or(0) as i32,args["desktop_y"].as_i64().unwrap_or(0) as i32,generation);
    let mut out=match result{Ok(())=>success(),Err(reason)=>fail(reason)};
    out["action_state"]=json!(if result.is_ok(){"executed"}else{"unknown"});
    out["window_id"]=json!(window);out["window_state"]=windows::summary(&identity);
    out["next_action"]=json!("select_window_or_observe");out
}

fn launch(args: &Value, scope: &str, epoch: &str) -> Value {
    if !super::targets::unrestricted(){return fail("local_window_scope_disallows_launch");}
    if !INPUT_ENABLED.load(Ordering::SeqCst) { return fail("desktop_input_disabled"); }
    let app = string(args,"app");
    let known = matches!(app.to_ascii_lowercase().as_str(), "notepad"|"notepad.exe");
    let path = if known {
        std::path::PathBuf::from(std::env::var_os("SystemRoot").unwrap_or_else(|| "C:\\Windows".into())).join("System32/notepad.exe")
    } else { std::path::PathBuf::from(app) };
    if !path.is_absolute() || !path.is_file() || !path.extension().is_some_and(|e| e.eq_ignore_ascii_case("exe")) {
        return fail("app_requires_explicit_executable");
    }
    let metadata = match path.metadata() { Ok(v) => v, Err(_) => return fail("app_unavailable") };
    let preview = json!({"action":"launch_app","application":app,"device_epoch":epoch,"size":metadata.len(),
        "modified":metadata.modified().ok().and_then(|v|v.duration_since(UNIX_EPOCH).ok()).map(|v|v.as_nanos().to_string())});
    use sha2::{Digest,Sha256};
    let binding = format!("{:x}",Sha256::digest(format!("{scope}\0{preview}").as_bytes()));
    if string(args,"_phase") == "prepare" {
        let mut out = success(); out["authorization"] = json!({"required":!known&&!policy::full_access(args),"binding":binding,"preview":preview}); return out;
    }
    if !known && !policy::full_access(args) && string(args,"_approval_binding") != binding { return fail("specific_approval_required"); }
    match std::process::Command::new(path).spawn() {
        Ok(child) => json!({"ok":true,"action_state":"executed","observation_state":"partial","process_started":true,
            "pid":child.id(),"reason":"window_not_yet_verified","next_action":"list_windows"}),
        Err(_) => fail("app_launch_failed"),
    }
}

#[cfg(test)]mod checkpoint_tests {
    use super::*;
    #[test]fn pause_keeps_workflow_identity_and_discards_input_evidence(){
        let flow=workflow::Flow::new("flow".into(),"scope".into(),"control".into(),vec![]);
        let mut state=State::default();state.flows.insert("flow".into(),flow);
        state.control=Some(Control{id:"control".into(),scope:"scope".into(),window:"window".into(),root_window:"window".into(),generation:1,
            observation:Some(Observation{id:"old".into(),shot:"old-shot".into(),geometry:Geometry{bounds:[0,0,10,10],client:[0,0,10,10],dpi:96},frame:None,region:None,captured_at:now(),ax:json!({})})});
        state.pause();
        assert!(state.control.as_ref().unwrap().observation.is_none());
        assert_eq!(state.control.as_ref().unwrap().id,state.flows["flow"].session);
    }
    #[test]fn aged_scene_check_detects_changes_away_from_click_target(){
        let frame=||capture::Frame{width:128,height:128,rgba:vec![255;128*128*4],png:vec![]};
        let before=frame();let mut after=frame();
        assert!(scene_matches(&before,&after));
        // A tiny caret-like change is tolerated, as in the existing patch guard.
        after.rgba[0]=0;assert!(scene_matches(&before,&after));
        for y in 80..100 {for x in 100..120 {after.rgba[(y*128+x)*4]=0;}}
        assert!(patch_matches(&before,&after,10,10));
        assert!(!scene_matches(&before,&after),"unchanged button pixels cannot prove a changed scene");
        after.width=127;assert!(!scene_matches(&before,&after));
    }
    #[test]fn coordinate_error_reports_the_shown_crop_dimensions(){
        let observation=Observation{id:"obs".into(),shot:"crop".into(),geometry:Geometry{bounds:[0,0,400,300],client:[0,0,400,300],dpi:96},frame:None,
            region:Some(super::super::region::Region{x:40,y:20,width:100,height:50,scale:3}),captured_at:now(),ax:json!({})};
        let out=coordinate_error(&observation);
        assert_eq!(out["coordinate_bounds"],json!({"space":"screenshot_pixels","screenshot_id":"crop","width":300,"height":150}));
        assert_eq!(out["action_state"],"not_started");
    }
}

#[cfg(test)]
pub(crate) fn age_observation_for_test(age_ms:u128){
    let mut state=STATE.get_or_init(Default::default).lock().unwrap();
    state.control.as_mut().unwrap().observation.as_mut().unwrap().captured_at=now().saturating_sub(age_ms);
}
