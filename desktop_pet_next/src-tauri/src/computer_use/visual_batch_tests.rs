use super::{workflow::*,protocol::{fail,success}};
use serde_json::{json,Value};

#[derive(Default)]
struct Device {
    inputs:Vec<String>,shots:usize,fail_at:Option<usize>,unknown_at:Option<usize>,
    stop_after:Option<usize>,capture_failed:bool,ask:bool,
}
impl Driver for Device {
    fn observe(&mut self,visual:bool)->Value{
        if visual{self.shots+=1;}
        if self.capture_failed{return fail("capture_timeout");}
        json!({"ok":true,"observation_state":"complete","control_session_id":"session","observation_id":"current",
            "device_epoch":"epoch","screenshot_id":"anchor","elements":[],"text_complete":false})
    }
    fn step_observation(&mut self,_visual:bool)->Value{self.observe(false)}
    fn prepare(&mut self,args:&Value)->Value{
        if self.fail_at==Some(self.inputs.len()){return fail("target_occluded");}
        json!({"ok":true,"authorization":{"required":self.ask,"binding":"a".repeat(64),"preview":{"action":args["action"]}}})
    }
    fn execute(&mut self,args:&Value,_binding:&str)->Value{
        self.inputs.push(args["action"].as_str().unwrap().into());
        if self.unknown_at==Some(self.inputs.len()-1){let mut out=fail("input_uncertain");out["action_state"]=json!("unknown");return out;}
        let mut out=success();out["action_state"]=json!("executed");out
    }
    fn select(&mut self,_window:&str)->Value{panic!("visual batches cannot silently select a new window")}
    fn interrupted(&self)->Option<&'static str>{if self.stop_after==Some(self.inputs.len()){Some("user_takeover")}else{None}}
}
fn flow()->Flow{
    let plan=json!([{"action":"click","x":100,"y":30},{"action":"type_text","text":"fixture"},{"action":"press_key","key":"Enter"}]);
    assert!(valid_actions(&plan));
    let mut f=Flow::new("batch".into(),"scope".into(),"session".into(),plan.as_array().unwrap().clone());f.visual=true;f
}
#[test]fn visual_sequence_executes_without_uia_and_only_returns_final_image(){
    let(mut f,mut d)=(flow(),Device::default());let out=run(&mut f,&mut d,"",5000);
    assert_eq!(d.inputs,vec!["click","type_text","press_key"]);assert_eq!(d.shots,1);
    assert_eq!(out["workflow_state"],"completed");assert_eq!(out["task_verified"],false);
    assert_eq!(out["completed_steps"],3);assert_eq!(out["steps"][1]["state"],"executed");
    let _=run(&mut f,&mut d,"",5000);assert_eq!(d.inputs.len(),3,"completed batch never replays");
}
#[test]fn partial_failure_and_resume_keep_already_executed_input(){
    let(mut f,mut d)=(flow(),Device{fail_at:Some(1),..Default::default()});
    let out=run(&mut f,&mut d,"",5000);assert_eq!(out["workflow_state"],"paused");
    assert_eq!(out["steps"][0]["action_state"],"executed");assert_eq!(out["steps"][1]["action_state"],"not_started");
    assert_eq!(d.inputs,vec!["click"]);d.fail_at=None;
    assert_eq!(run(&mut f,&mut d,"",5000)["completed_steps"],3);assert_eq!(d.inputs,vec!["click","type_text","press_key"]);
}
#[test]fn takeover_stops_remaining_inputs(){
    let(mut f,mut d)=(flow(),Device{stop_after:Some(1),..Default::default()});let out=run(&mut f,&mut d,"",5000);
    assert_eq!(out["reason"],"user_takeover");assert_eq!(d.inputs.len(),1);assert_eq!(out["steps"][2]["action_state"],"not_started");
}
#[test]fn unknown_input_is_terminal_and_never_replayed(){
    let(mut f,mut d)=(flow(),Device{unknown_at:Some(1),..Default::default()});let out=run(&mut f,&mut d,"",5000);
    assert_eq!(out["action_state"],"unknown");assert_eq!(d.inputs.len(),2);
    assert_eq!(prepare_resume(&f,&mut d)["reason"],"workflow_unknown_requires_review");
    assert_eq!(run(&mut f,&mut d,"",5000)["reason"],"workflow_unknown_requires_review");assert_eq!(d.inputs.len(),2);
}
#[test]fn final_capture_failure_preserves_completed_inputs_without_success(){
    let(mut f,mut d)=(flow(),Device::default());let _=run(&mut f,&mut d,"",5000);d.capture_failed=true;
    let out=run(&mut f,&mut d,"",5000);assert_eq!(out["ok"],false);assert_eq!(out["action_state"],"executed");
    assert_eq!(out["observation_reason"],"capture_timeout");assert_eq!(out["completed_steps"],3);assert_eq!(out["task_verified"],false);
}
#[test]fn visual_approval_is_bound_to_each_pending_step(){
    let(mut f,mut d)=(flow(),Device{ask:true,..Default::default()});let out=run(&mut f,&mut d,"",5000);
    assert_eq!(out["workflow_state"],"awaiting_approval");assert!(d.inputs.is_empty());
    let first=prepare_resume(&f,&mut d)["authorization"]["binding"].as_str().unwrap().to_string();
    let out=run(&mut f,&mut d,&first,5000);assert_eq!(d.inputs.len(),1);assert_eq!(out["workflow_state"],"awaiting_approval");
    let second=prepare_resume(&f,&mut d)["authorization"]["binding"].as_str().unwrap().to_string();assert_ne!(first,second);
    let out=run(&mut f,&mut d,&first,5000);assert_eq!(out["completed_steps"],1);assert_eq!(d.inputs.len(),1);
}
#[test]fn visual_contract_reuses_strict_single_action_validation(){
    for bad in [json!([]),json!([{"action":"run_actions"}]),json!([{"action":"click","x":1}]),
        json!([{"action":"type_text","text":"a\n"}]),json!([{"action":"click","x":1,"y":2,"_batch_input":true}]),
        json!([{"action":"press_key","key":"Enter","observation_id":"future"}])]{assert!(!valid_actions(&bad));}
    let mut args=json!({"action":"manage_window","window_id":"w","device_epoch":"e","window_action":"move","desktop_x":-500,"desktop_y":20});
    assert!(super::protocol::valid(args.as_object().unwrap()));args["window_action"]=json!("minimize");assert!(!super::protocol::valid(args.as_object().unwrap()));
}
