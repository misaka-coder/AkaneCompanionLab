//! Bounded, declarative state machine. Device drivers supply observations and
//! existing approved actions; no script interpreter or speculative input queue.
use serde_json::{json,Value};
use sha2::{Digest,Sha256};
use std::time::{Duration,Instant};
use super::protocol::{string,fail,success};

pub fn valid_selector(v:&Value)->bool {
    let Some(obj)=v.as_object()else{return false;};
    !obj.is_empty()&&obj.iter().all(|(k,v)|match k.as_str(){
        "name"=>v.as_str().is_some_and(|v|v.chars().count()<=256),
        "automation_id"=>v.as_str().is_some_and(|v|!v.is_empty()&&v.chars().count()<=256),
        "role"=>v.as_u64().is_some_and(|v|(50000..=50100).contains(&v)),
        "editable"|"focused"=>v.is_boolean(),_=>false})
}
pub fn valid_condition(v:&Value)->bool {
    let Some(obj)=v.as_object()else{return false;};
    let fields:&[&str]=match string(v,"kind"){
        "focused"|"element_exists"|"element_absent"=>&["kind","target"],
        "value_equals"=>&["kind","target","value"],"checked_equals"=>&["kind","target","checked"],
        "window_title"=>&["kind","title"],_=>return false};
    obj.len()==fields.len()&&fields.iter().all(|field|obj.contains_key(*field))&&obj.iter().all(|(k,v)|match k.as_str(){
        "kind"=>true,"target"=>valid_selector(v),"checked"=>v.is_boolean(),
        "value"=>v.as_str().is_some_and(|s|s.chars().count()<=4000),
        "title"=>v.as_str().is_some_and(|s|!s.is_empty()&&s.chars().count()<=512),_=>false})
}
pub fn valid_steps(v:&Value)->bool {
    let Some(steps)=v.as_array()else{return false;};
    (1..=12).contains(&steps.len())&&steps.iter().all(|step|{
        let Some(obj)=step.as_object()else{return false;};
        let (fields,required):(&[&str],&[&str])=match string(step,"action"){
            "click"=>(&["target","button","click_count"],&["target"]),
            "scroll"=>(&["target","direction","amount"],&["target","direction"]),
            "type_text"=>(&["text"],&["text"]),"press_key"=>(&["key"],&["key"]),
            "set_value"=>(&["target","text"],&["target","text"]),
            "set_checked"=>(&["target","checked"],&["target","checked"]),
            "select_window"=>(&["window_id"],&["window_id"]),"wait_for"=>(&[],&[]),_=>return false};
        if !valid_condition(&step["expect"])||required.iter().any(|k|!obj.contains_key(*k)){return false;}
        obj.iter().all(|(k,v)|{
            if k=="action"||k=="expect"{return true;}
            if k=="wait_ms"{return v.as_u64().is_some_and(|v|v<=3000);}
            if !fields.contains(&k.as_str()){return false;}
            match k.as_str(){
                "target"=>valid_selector(v),"checked"=>v.is_boolean(),
                "button"=>matches!(v.as_str(),Some("left"|"right"|"middle")),
                "click_count"=>v.as_u64().is_some_and(|v|(1..=2).contains(&v)),
                "amount"=>v.as_u64().is_some_and(|v|(1..=10).contains(&v)),
                "direction"=>matches!(v.as_str(),Some("up"|"down")),
                "text"=>v.as_str().is_some_and(|v|v.chars().count()<=4000&&!v.contains(['\r','\n','\t','\0'])&&(string(step,"action")!="type_text"||!v.is_empty())),
                "key"|"window_id"=>v.as_str().is_some_and(|v|!v.is_empty()&&v.chars().count()<=if k=="key"{40}else{160}),_=>false}
        })
    })
}

/// Visual batches reuse the single-action wire validator, without accepting
/// model-provided session references, selectors, or private execution flags.
pub fn valid_actions(v:&Value)->bool {
    let Some(actions)=v.as_array()else{return false;};
    (1..=6).contains(&actions.len()) && actions.iter().all(|step|{
        let Some(mut args)=step.as_object().cloned()else{return false;};
        let action=string(step,"action");
        if !matches!(action,"click"|"type_text"|"press_key"|"scroll"|"drag")
            || args.keys().any(|key|!matches!(key.as_str(),"action"|"x"|"y"|"to_x"|"to_y"|"duration_ms"|"text"|"key"|"button"|"click_count"|"direction"|"amount")){return false;}
        for key in ["control_session_id","device_epoch","observation_id"]{args.insert(key.into(),json!("batch"));}
        if matches!(action,"click"|"scroll"|"drag"){args.insert("screenshot_id".into(),json!("batch"));}
        super::protocol::valid(&args)
    })
}

pub fn resolve<'a>(observation:&'a Value,selector:&Value)->Result<Option<&'a Value>,&'static str>{
    if !valid_selector(selector){return Err("selector_invalid");}
    // An exact globally focused RuntimeId is unique even when tree traversal is capped.
    let rows:Vec<&Value>=if selector["focused"]==true {
        let focus=&observation["focus_evidence"];
        if focus["source"]!="uia" {return Ok(None);}vec![focus]
    }else{
        if observation["text_complete"]!=true{return Err("selector_observation_incomplete");}
        observation["elements"].as_array().ok_or("selector_observation_unavailable")?.iter().collect()
    };
    let matches:Vec<_>=rows.into_iter().filter(|row|row["stable_identity"]==true&&row["offscreen"]==false
        &&selector.as_object().unwrap().iter().all(|(key,value)|row.get(key)==Some(value))).collect();
    match matches.len(){0=>Ok(None),1=>Ok(Some(matches[0])),_=>Err("selector_ambiguous")}
}

pub fn condition(observation:&Value,expected:&Value)->Result<bool,&'static str>{
    if !valid_condition(expected){return Err("condition_invalid");}
    if string(expected,"kind")=="window_title" {return Ok(observation["title"]==expected["title"]&&observation["foreground_matches"]==true);}
    let target=resolve(observation,&expected["target"])?;
    if string(expected,"kind")=="element_absent" {
        if observation["text_complete"]!=true{return Err("selector_observation_incomplete");}return Ok(target.is_none());
    }
    let Some(row)=target else{return Ok(false);};
    Ok(match string(expected,"kind"){
        "focused"=>row["focused"]==true,"element_exists"=>true,
        "value_equals"=>row["password"]==false&&row["value_complete"]==true&&row["value"]==expected["value"],
        "checked_equals"=>row["checked"].is_boolean()&&row["checked"]==expected["checked"],_=>false})
}

pub fn action_arguments(step:&Value,observation:&Value)->Result<Value,&'static str>{
    let mut args=json!({"action":step["action"],"control_session_id":observation["control_session_id"],
        "device_epoch":observation["device_epoch"],"observation_id":observation["observation_id"],"mode":"text"});
    if step.get("target").is_some(){
        let target=resolve(observation,&step["target"])?.ok_or("selector_not_found")?;
        // Resolve identity here; the shared device prepare path decides whether
        // a control is actionable. Some providers misreport IsEnabled (QQ NT).
        args["element_id"]=target["element_id"].clone();
    }
    for key in ["text","key","checked","button","click_count","direction","amount","window_id","x","y","to_x","to_y","duration_ms"]{
        if let Some(value)=step.get(key){args[key]=value.clone();}
    }
    if step.get("x").is_some(){
        args["screenshot_id"]=observation["screenshots"][0]["screenshot_id"].clone();
        // Internal snapshots retain the original anchor id without its pixels.
        if let Some(id)=observation.get("screenshot_id"){args["screenshot_id"]=id.clone();}
    }
    Ok(args)
}

#[derive(Clone)]
pub struct Flow {
    pub id:String,pub scope:String,pub session:String,pub steps:Vec<Value>,
    pub states:Vec<&'static str>,pub acted:Vec<bool>,pub index:usize,pub created:Instant,pub state:&'static str,
    pub visual:bool,
}
impl Flow {
    pub fn new(id:String,scope:String,session:String,steps:Vec<Value>)->Self{
        Self{id,scope,session,states:vec!["pending";steps.len()],acted:vec![false;steps.len()],steps,index:0,created:Instant::now(),state:"ready",visual:false}
    }
    pub fn report(&self,out:&mut Value,reason:&str){
        out["workflow_id"]=json!(self.id);out["workflow_state"]=json!(self.state);out["next_step"]=json!(self.index);
        out["completed_steps"]=json!(self.states.iter().filter(|s|matches!(**s,"verified"|"skipped") || (self.visual && **s=="executed")).count());
        out["workflow_kind"]=json!(if self.visual{"visual_actions"}else{"verified_steps"});
        out["task_verified"]=json!(false);
        out["steps"]=json!(self.states.iter().enumerate().map(|(i,state)|json!({"index":i,"action":self.steps[i]["action"],"state":state,"input_attempted":self.acted[i],
            "action_state":if *state=="unknown"{"unknown"}else if self.acted[i]{"executed"}else{"not_started"}})).collect::<Vec<_>>());
        if !reason.is_empty(){out["reason"]=json!(reason);}
        let recovery=fail(reason);
        out["next_action"]=recovery.get("next_action").cloned().unwrap_or_else(||json!(match self.state{"completed" if self.visual=>"verify_ui_effect_do_not_replay","completed"=>"done","unknown"=>"observe_or_status_do_not_replay",_=>"resume_steps_after_review"}));
        if let Some(hint)=recovery.get("recovery_hint"){out["recovery_hint"]=hint.clone();}
    }
}

pub trait Driver {
    fn observe(&mut self,visual:bool)->Value;
    fn step_observation(&mut self,visual_batch:bool)->Value {self.observe(visual_batch)}
    fn prepare(&mut self,args:&Value)->Value;
    fn execute(&mut self,args:&Value,binding:&str)->Value;
    fn select(&mut self,window:&str)->Value;
    fn interrupted(&self)->Option<&'static str>;
    fn progress(&self,_flow:&Flow){}
}

pub fn authorization(flow:&Flow,raw:&Value)->Value{
    let mut preview=raw["preview"].clone();preview["workflow"]=json!({"id":flow.id,"step":flow.index});
    let binding=format!("{:x}",Sha256::digest(format!("{}\0{}",string(raw,"binding"),preview).as_bytes()));
    json!({"required":raw["required"],"binding":binding,"preview":preview})
}

/// Read-only preflight for a retained checkpoint. It never changes step progress.
pub fn prepare_resume(flow:&Flow,driver:&mut impl Driver)->Value{
    if flow.state=="unknown"{return fail("workflow_unknown_requires_review");}
    if flow.created.elapsed()>Duration::from_secs(900){return fail("workflow_expired");}
    if let Some(reason)=driver.interrupted(){return fail(reason);}
    if flow.index<flow.steps.len()&&!matches!(flow.states[flow.index],"executed"|"verified"|"skipped")
        &&!matches!(string(&flow.steps[flow.index],"action"),"wait_for"|"select_window") {
        let observed=driver.step_observation(flow.visual);if observed["ok"]!=true{return observed;}
        let args=match action_arguments(&flow.steps[flow.index],&observed){Ok(args)=>args,Err(reason)=>return fail(reason)};
        let mut prepared=driver.prepare(&args);
        if prepared["ok"]==true{prepared["authorization"]=authorization(flow,&prepared["authorization"]);}
        return prepared;
    }
    let mut out=success();out["authorization"]=json!({"required":false,"binding":format!("{:x}",Sha256::digest(format!("{}:{}",flow.id,flow.index).as_bytes())),"preview":{"workflow_id":flow.id}});out
}

fn finish(flow:&mut Flow,driver:&mut impl Driver,reason:&str,auth:Option<Value>)->Value{
    let mut out=driver.observe(true);
    let observation_ok=out["ok"]==true;
    if !observation_ok {out["observation_reason"]=out["reason"].clone();}
    out["ok"]=json!(observation_ok && matches!(flow.state,"completed"|"awaiting_approval"|"paused"));
    out["action_state"]=json!(if flow.states.contains(&"unknown"){"unknown"}else if flow.acted.iter().any(|v|*v){"executed"}else{"not_started"});
    if let Some(id)=out["control_session_id"].as_str(){flow.session=id.into();}
    if let Some(auth)=auth{out["authorization"]=auth;}
    flow.report(&mut out,reason);driver.progress(flow);out
}

fn finish_failure(flow:&mut Flow,driver:&mut impl Driver,cause:&Value)->Value{
    let mut out=finish(flow,driver,string(cause,"reason"),None);
    for key in ["occluder","window_state"] {if let Some(value)=cause.get(key){out[key]=value.clone();}}
    out
}

/// Execute each input at most once. A resumed executed step only checks its
/// postcondition. Unknown input is a terminal checkpoint, never a retry signal.
pub fn run(flow:&mut Flow,driver:&mut impl Driver,approved:&str,budget_ms:u64)->Value{
    if flow.state=="unknown"{return fail("workflow_unknown_requires_review");}
    let started=Instant::now();let budget=Duration::from_millis(budget_ms.clamp(1000,15000));
    let mut approval=approved.to_string();flow.state="running";
    while flow.index<flow.steps.len(){
        driver.progress(flow);
        if let Some(reason)=driver.interrupted(){flow.state="paused";return finish(flow,driver,reason,None);}
        if started.elapsed()>=budget{flow.state="paused";return finish(flow,driver,"workflow_budget_exhausted",None);}
        let step=flow.steps[flow.index].clone();let action=string(&step,"action");
        if flow.states[flow.index]=="pending" {
            let observed=driver.step_observation(flow.visual);
            if observed["ok"]!=true{flow.state="paused";return finish_failure(flow,driver,&observed);}
            if let Some(id)=observed["control_session_id"].as_str(){flow.session=id.into();}
            if action=="select_window" {
                let selected=driver.select(string(&step,"window_id"));
                if selected["ok"]!=true{flow.state="paused";return finish(flow,driver,string(&selected,"reason"),None);}
                flow.states[flow.index]="executed";flow.acted[flow.index]=true;
            }else if action!="wait_for" {
                let args=match action_arguments(&step,&observed){Ok(v)=>v,Err(reason)=>{flow.state="paused";return finish(flow,driver,reason,None);}};
                let already=if matches!(action,"set_value"|"set_checked"){
                    resolve(&observed,&step["target"]).ok().flatten().is_some_and(|row|if action=="set_value"{row["value_complete"]==true&&row["value"]==step["text"]}else{row["checked"].is_boolean()&&row["checked"]==step["checked"]})
                }else{false};
                if already {flow.states[flow.index]="skipped";}else{
                    let prepared=driver.prepare(&args);
                    if prepared["ok"]!=true{flow.state="paused";return finish_failure(flow,driver,&prepared);}
                    let auth=authorization(flow,&prepared["authorization"]);
                    if auth["required"]==true&&string(&auth,"binding")!=approval{
                        flow.state="awaiting_approval";return finish(flow,driver,"specific_approval_required",Some(auth));
                    }
                    let result=driver.execute(&args,string(&prepared["authorization"],"binding"));approval.clear();
                    flow.acted[flow.index]=matches!(string(&result,"action_state"),"executed"|"unknown");
                    match string(&result,"action_state"){
                        "executed"=>flow.states[flow.index]="executed",
                        "unknown"=>{flow.states[flow.index]="unknown";flow.state="unknown";return finish_failure(flow,driver,&result);},
                        _=>{flow.state="paused";return finish_failure(flow,driver,&result);}
                    }
                    if result["ok"]!=true{
                        if flow.visual && flow.states[flow.index]=="executed"{flow.index+=1;}
                        flow.state="paused";return finish_failure(flow,driver,&result);
                    }
                }
            }
        }
        if flow.visual {flow.index+=1;continue;}
        let waiting=Instant::now();let wait=Duration::from_millis(step["wait_ms"].as_u64().unwrap_or(1000));
        loop {
            if let Some(reason)=driver.interrupted(){flow.state="paused";return finish(flow,driver,reason,None);}
            let observed=driver.observe(false);
            if let Some(id)=observed["control_session_id"].as_str(){flow.session=id.into();}
            let checked=if observed["ok"]==true{condition(&observed,&step["expect"])}else{Ok(false)};
            match checked {
                Ok(true)=>{if flow.states[flow.index]!="skipped"{flow.states[flow.index]="verified";}flow.index+=1;break;},
                Err(reason)=>{flow.state="paused";return finish(flow,driver,reason,None);},_=>{}
            }
            if started.elapsed()>=budget||waiting.elapsed()>=wait{
                flow.state="paused";return finish(flow,driver,if started.elapsed()>=budget{"workflow_budget_exhausted"}else{"postcondition_not_met"},None);
            }
            std::thread::sleep(Duration::from_millis(50));
        }
    }
    flow.state="completed";finish(flow,driver,"",None)
}

#[cfg(test)] mod tests {
    use super::*;
    #[test]fn unverified_message_is_not_reported_as_awaiting_approval(){
        let flow=Flow::new("test".into(),"owner".into(),"session".into(),vec![]);
        let mut out=success();flow.report(&mut out,"message_workflow_unverified");
        assert_eq!(out["next_action"],"verify_message_workflow_or_handoff");
        assert!(out["recovery_hint"].as_str().unwrap().contains("尚未生成"));
        assert!(out.get("authorization").is_none());
    }
    struct Fake {
        value:String,focused:bool,checked:bool,title:String,inputs:Vec<String>,observe_count:usize,visual_count:usize,
        miss_click:bool,unknown:bool,require_approval:bool,stopped:bool,
    }
    impl Default for Fake {
        fn default()->Self{Self{value:String::new(),focused:false,checked:false,title:"Editor".into(),inputs:Vec::new(),observe_count:0,visual_count:0,miss_click:false,unknown:false,require_approval:false,stopped:false}}
    }
    impl Fake {
        fn snapshot(&self)->Value {
            let edit=json!({"element_id":format!("o{}_element_edit",self.observe_count),"role":50004,"name":"Field","editable":true,
                "stable_identity":true,"enabled":true,"offscreen":false,"password":false,"focused":self.focused,"value_complete":true,"value":self.value});
            let check=json!({"element_id":format!("o{}_element_check",self.observe_count),"role":50002,"name":"Option",
                "stable_identity":true,"enabled":true,"offscreen":false,"checked":self.checked,"password":false});
            let focus=if self.focused{let mut e=edit.clone();e["source"]=json!("uia");e}else{json!({"source":"unknown"})};
            json!({"ok":true,"action_state":"not_started","observation_state":"complete","device_epoch":"epoch","control_session_id":"session",
                "observation_id":format!("o{}",self.observe_count),"title":self.title,"foreground_matches":true,"text_complete":true,
                "elements":[edit,check],"focus_evidence":focus})
        }
    }
    impl Driver for Fake {
        fn observe(&mut self,visual:bool)->Value{self.observe_count+=1;if visual{self.visual_count+=1;}self.snapshot()}
        fn prepare(&mut self,args:&Value)->Value{
            let preview=json!({"action":args["action"],"text":args["text"],"key":args["key"],"value":self.value,"title":self.title});
            json!({"ok":true,"authorization":{"required":self.require_approval,"binding":format!("{:x}",Sha256::digest(preview.to_string().as_bytes())),"preview":preview}})
        }
        fn execute(&mut self,args:&Value,_binding:&str)->Value{
            let action=string(args,"action");self.inputs.push(action.into());
            if self.unknown{return json!({"ok":false,"action_state":"unknown","reason":"input_partial"});}
            match action{
                "click"=>if !self.miss_click{self.focused=true;},
                "type_text"=>self.value.push_str(string(args,"text")),
                "set_value"=>self.value=string(args,"text").into(),
                "set_checked"=>self.checked=args["checked"].as_bool().unwrap(),
                "press_key"=>if string(args,"key")=="Ctrl+Shift+S"{self.title="Save As".into();self.value.clear();},_=>{}
            }
            json!({"ok":true,"action_state":"executed","observation_state":"complete"})
        }
        fn select(&mut self,window:&str)->Value{self.inputs.push("select_window".into());self.title=window.into();self.snapshot()}
        fn interrupted(&self)->Option<&'static str>{if self.stopped{Some("user_takeover")}else{None}}
    }
    fn edit()->Value{json!({"name":"Field","role":50004})}
    fn flow(steps:Value)->Flow {assert!(valid_steps(&steps));Flow::new("flow".into(),"scope".into(),"session".into(),steps.as_array().unwrap().clone())}
    fn click_type()->Value{json!([
        {"action":"click","target":edit(),"expect":{"kind":"focused","target":edit()},"wait_ms":0},
        {"action":"type_text","text":"hello","expect":{"kind":"value_equals","target":edit(),"value":"hello"},"wait_ms":0}])}

    #[test]fn failed_click_stops_input_and_resume_only_checks_its_effect(){
        let mut f=flow(click_type());let mut driver=Fake{miss_click:true,..Default::default()};
        let out=run(&mut f,&mut driver,"",1000);
        assert_eq!(out["reason"],"postcondition_not_met");assert_eq!(driver.inputs,vec!["click"]);assert_eq!(f.states[0],"executed");
        driver.focused=true;
        let out=run(&mut f,&mut driver,"",1000);
        assert_eq!(out["workflow_state"],"completed");assert_eq!(driver.inputs,vec!["click","type_text"]);assert_eq!(driver.value,"hello");
        assert_eq!(driver.visual_count,2,"only final/interrupted observations are images");
    }
    #[test]fn predictable_dialog_and_window_transitions_continue_without_model_round(){
        let mut f=flow(json!([
            {"action":"press_key","key":"Ctrl+Shift+S","expect":{"kind":"window_title","title":"Save As"}},
            {"action":"type_text","text":"report.txt","expect":{"kind":"value_equals","target":edit(),"value":"report.txt"}},
            {"action":"select_window","window_id":"Other Editor","expect":{"kind":"window_title","title":"Other Editor"}}
        ]));let mut driver=Fake{focused:true,..Default::default()};
        let out=run(&mut f,&mut driver,"",1000);assert_eq!(out["workflow_state"],"completed");assert_eq!(out["completed_steps"],3);assert_eq!(driver.visual_count,1);
    }
    #[test]fn unknown_input_never_replays_on_resume(){
        let mut f=flow(click_type());let mut driver=Fake{unknown:true,..Default::default()};
        let out=run(&mut f,&mut driver,"",1000);assert_eq!(out["action_state"],"unknown");assert_eq!(f.state,"unknown");
        assert_eq!(run(&mut f,&mut driver,"",1000)["reason"],"workflow_unknown_requires_review");
        assert_eq!(prepare_resume(&f,&mut driver)["reason"],"workflow_unknown_requires_review");assert_eq!(driver.inputs.len(),1);
    }
    #[test]fn exact_step_approval_changes_with_current_content_and_is_not_reused(){
        let mut f=flow(click_type());let mut driver=Fake{require_approval:true,..Default::default()};
        let out=run(&mut f,&mut driver,"",1000);assert_eq!(out["workflow_state"],"awaiting_approval");assert!(driver.inputs.is_empty());
        let original=out["authorization"]["binding"].as_str().unwrap().to_string();
        driver.value="changed".into();let preflight=prepare_resume(&f,&mut driver);assert_ne!(preflight["authorization"]["binding"],original);
        let out=run(&mut f,&mut driver,&original,1000);assert_eq!(out["workflow_state"],"awaiting_approval");assert!(driver.inputs.is_empty());
        driver.value.clear();let prepared=prepare_resume(&f,&mut driver);let approved=string(&prepared["authorization"],"binding").to_owned();
        let out=run(&mut f,&mut driver,&approved,1000);assert_eq!(out["next_step"],1);assert_eq!(out["workflow_state"],"awaiting_approval");assert_eq!(driver.inputs,vec!["click"]);
    }
    #[cfg(windows)]
    #[test]fn full_access_runs_later_submission_steps_and_resumes_without_approval(){
        struct Full(Fake);
        impl Driver for Full {
            fn observe(&mut self,visual:bool)->Value{self.0.observe(visual)}
            fn prepare(&mut self,args:&Value)->Value{
                let mut args=args.clone();args["_permission_mode"]=json!("trusted_auto_allow");
                let required=super::super::policy::classify("qq.exe",&args,&Value::Null,None).unwrap();
                let mut out=self.0.prepare(&args);out["authorization"]["required"]=json!(required);out
            }
            fn execute(&mut self,args:&Value,binding:&str)->Value{self.0.execute(args,binding)}
            fn select(&mut self,window:&str)->Value{self.0.select(window)}
            fn interrupted(&self)->Option<&'static str>{self.0.interrupted()}
        }
        let steps=json!([
            {"action":"press_key","key":"Enter","expect":{"kind":"window_title","title":"Editor"}},
            {"action":"press_key","key":"Ctrl+Enter","expect":{"kind":"window_title","title":"Editor"}}
        ]);
        let mut f=flow(steps.clone());let mut driver=Full(Fake{require_approval:true,..Default::default()});
        assert_eq!(run(&mut f,&mut driver,"",1000)["workflow_state"],"completed");
        assert_eq!(driver.0.inputs,vec!["press_key","press_key"]);
        let mut f=flow(steps);f.index=1;f.states[0]="verified";f.acted[0]=true;f.state="paused";
        driver.0.inputs.clear();
        let prepared=prepare_resume(&f,&mut driver);assert_eq!(prepared["authorization"]["required"],false);
        assert_eq!(run(&mut f,&mut driver,"",1000)["workflow_state"],"completed");
        assert_eq!(driver.0.inputs,vec!["press_key"],"completed step must not replay");
    }
    #[test]fn set_checked_skips_satisfied_target_without_toggle(){
        let mut f=flow(json!([{"action":"set_checked","target":{"name":"Option"},"checked":true,
            "expect":{"kind":"checked_equals","target":{"name":"Option"},"checked":true}}]));
        let mut driver=Fake{checked:true,..Default::default()};let out=run(&mut f,&mut driver,"",1000);
        assert_eq!(out["workflow_state"],"completed");assert_eq!(f.states,vec!["skipped"]);assert!(driver.inputs.is_empty());
        assert_eq!(out["action_state"],"not_started");
    }
    #[test]fn waiting_does_not_claim_input_and_timeout_retains_pending_checkpoint(){
        let mut f=flow(json!([{"action":"wait_for","expect":{"kind":"window_title","title":"Ready"},"wait_ms":0}]));
        let mut driver=Fake::default();let out=run(&mut f,&mut driver,"",1000);
        assert_eq!(out["workflow_state"],"paused");assert_eq!(out["action_state"],"not_started");
        driver.title="Ready".into();let out=run(&mut f,&mut driver,"",1000);
        assert_eq!(out["workflow_state"],"completed");assert_eq!(out["action_state"],"not_started");assert!(driver.inputs.is_empty());
    }
    #[test]fn semantic_ambiguity_incomplete_tree_and_takeover_fail_closed(){
        let mut driver=Fake::default();let mut snapshot=driver.snapshot();let duplicate=snapshot["elements"][0].clone();snapshot["elements"].as_array_mut().unwrap().push(duplicate);
        assert_eq!(resolve(&snapshot,&edit()),Err("selector_ambiguous"));
        snapshot["text_complete"]=json!(false);assert_eq!(resolve(&snapshot,&edit()),Err("selector_observation_incomplete"));
        let mut f=flow(click_type());driver.stopped=true;let out=run(&mut f,&mut driver,"",1000);
        assert_eq!(out["reason"],"user_takeover");assert!(driver.inputs.is_empty());
    }
    #[test]fn rejects_hidden_branches_unbounded_wait_and_future_evidence(){
        for (key,value) in [("loop",json!(true)),("observation_id",json!("future")),("wait_ms",json!(4000)),("approved",json!(true))]{
            let mut plan=click_type();plan[0][key]=value;assert!(!valid_steps(&plan));
        }
    }
}
