//! Device-owned classification; model prose cannot label a submit button harmless.
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use super::protocol::string;

fn stable_ref(element:&Value)->&str {string(element,"element_id").rsplit("_element_").next().unwrap_or("")}

/// Set only by the authenticated host transport, after resolving the actor's
/// existing capability/family policy. Never accepted by the public tool schema.
pub fn full_access(args:&Value)->bool { string(args,"_permission_mode")=="trusted_auto_allow" }

pub fn typing_evidence(args:&Value,previous:&Value,current:&Value,recent_visual:bool)->bool {
    (full_access(args) && recent_visual) ||
        (previous["editable"]==true && previous["password"]==false && current["editable"]==true
        && current["password"]==false && current["stable_identity"]==true
        && previous["element_id"]==current["element_id"] && previous["bounds"]==current["bounds"])
}

/// Narrow recognizer for the reviewed QQ desktop layout. Unknown/ambiguous
/// layouts remain a handoff; neither a model-supplied recipient nor window title
/// is sufficient. The actual focused rich editor, header and send button must
/// all be observed in this window, with a complete readable draft.
pub fn message_target(ax:&Value,bounds:[i32;4],dpi:u32)->Value {
    let focus=&ax["focus_evidence"];
    let unknown=||json!({"status":"unverified","reason":"message_recipient_unverified"});
    if focus["editable"]!=true||focus["password"]!=false||focus["stable_identity"]!=true{return unknown();}
    let Some(elements)=ax["elements"].as_array()else{return unknown();};
    let scale=(dpi as f64/96.0).max(1.0);
    let editor_left=focus["bounds"]["x"].as_f64().unwrap_or(f64::MAX);
    let editor_right=editor_left+focus["bounds"]["width"].as_f64().unwrap_or(0.0);
    let headers:Vec<_>=elements.iter().filter(|e|{
        let name=string(e,"name").trim();let x=e["bounds"]["x"].as_f64().unwrap_or(-1.0);let y=e["bounds"]["y"].as_f64().unwrap_or(-1.0);
        matches!(e["role"].as_i64(),Some(50020|50000))&&e["stable_identity"]==true&&e["offscreen"]==false&&!name.is_empty()
            &&!name.chars().all(|c|c.is_ascii_digit()||" ()（）".contains(c))
            // QQ NT's recipient heading is a Button. Limit the heading's left
            // edge to the editor's left side, excluding call/menu toolbar buttons.
            &&x>=editor_left-32.0*scale&&x<editor_right
            &&x<=editor_left+140.0*scale
            &&y>=bounds[1] as f64+32.0*scale&&y<bounds[1] as f64+110.0*scale
    }).collect();
    if headers.len()!=1{return unknown();}
    let header=string(headers[0],"name").trim();let editor=string(focus,"name").trim();
    if editor.is_empty(){return unknown();}
    let editor_top=focus["bounds"]["y"].as_f64().unwrap_or(f64::MAX);
    let buttons:Vec<_>=elements.iter().filter(|e|e["role"]==50000&&matches!(string(e,"name").trim(),"发送"|"发送(S)"|"发送 (S)")
        &&e["offscreen"]==false&&e["stable_identity"]==true
        &&e["bounds"]["y"].as_f64().is_some_and(|y|y>=editor_top&&y<bounds[3] as f64)
        &&e["bounds"]["x"].as_f64().is_some_and(|x|x>=(editor_left+editor_right)/2.0&&x<editor_right)).collect();
    // QQ NT reports IsEnabled=false even when the visible send button works.
    // Preserve the raw signal; it is not proof of application readiness.
    let send_state=if buttons.len()!=1{"unverified"}else{"identified"};
    // QQ can retain the previous chat's ARIA editor label after switching chats.
    // Never use that label alone, or silently resolve a disagreement by guessing.
    let matches=header==editor;
    json!({"status":if matches{"verified"}else{"conflict"},
        "reason":if matches{""}else{"message_recipient_mismatch"},
        "header_recipient":header,"editor_label":editor,
        "send_control_status":send_state,
        "send_identity":if buttons.len()==1{Some(stable_ref(buttons[0]))}else{None},
        "send_enabled_reported":if buttons.len()==1{buttons[0].get("enabled").cloned()}else{None},
        "recipient_identity":stable_ref(headers[0]),"editor_identity":stable_ref(focus)})
}

pub fn message_context(ax:&Value,bounds:[i32;4],dpi:u32)->Value {
    let target=message_target(ax,bounds,dpi);
    if target["status"]!="verified"{return Value::Null;}
    let focus=&ax["focus_evidence"];
    if focus["value_complete"]!=true{return Value::Null;}
    let Some(draft)=focus["value"].as_str().filter(|text|!text.trim().is_empty())else{return Value::Null;};
    if target["send_control_status"]!="identified"{return Value::Null;}
    json!({"recipient":target["header_recipient"],"draft":draft,"recipient_identity":target["recipient_identity"],
        "editor_identity":stable_ref(focus),"send_identity":target["send_identity"],
        "send_enabled_reported":target["send_enabled_reported"],"verification":"qq_uia_header_editor_send_v2"})
}

/// Only this recognized send action may disregard the provider's enabled bit.
/// The caller still requires fresh identity, visibility, hit testing and approval.
pub fn reviewed_send_click(app:&str,args:&Value,context:&Value,target:&Value)->bool {
    matches!(app,"qq.exe"|"qqnt.exe") && context["verification"]=="qq_uia_header_editor_send_v2"
        && string(args,"action")=="click" && args["button"].as_str().unwrap_or("left")=="left"
        && args["click_count"].as_u64().unwrap_or(1)==1
        && !stable_ref(target).is_empty() && stable_ref(target)==string(context,"send_identity")
}

pub fn target_enabled(app:&str,args:&Value,context:&Value,target:&Value)->bool {
    target["enabled"]==true || (target["enabled"]==false &&
        ((full_access(args) && string(args,"action")=="click") || reviewed_send_click(app,args,context,target)))
}

pub fn classify(app: &str, args: &Value, focus: &Value, target: Option<&Value>) -> Result<bool, &'static str> {
    let action = string(args, "action");
    // Full access delegates application semantics to the model. UIA recognizers
    // are useful diagnostics, not a second permission system for visual input.
    // Identity, evidence freshness, stop/takeover and hit testing run in session.
    if full_access(args) {return Ok(false);}
    // This reviewed workflow is an ordinary local text editor, including its
    // owned save dialog. Unknown applications remain usable with exact approval.
    if app == "notepad.exe" { return Ok(false); }
    if matches!(app, "qq.exe" | "qqnt.exe") {
        if action=="drag" {return Err("attachment_workflow_unverified");}
        let editable = focus["editable"] == true && focus["password"] == false;
        if editable && matches!(action,"type_text"|"set_value"|"press_key") {
            match focus["message_target"]["status"].as_str(){
                Some("conflict")=>return Err("message_recipient_mismatch"),
                Some("verified")=>{}, _=>return Err("message_recipient_unverified"),
            }
        }
        if action == "type_text" && editable {
            if string(args,"text").contains(['\r','\n','\t']){return Err("message_multiline_input_unverified");}
            return Ok(false);
        }
        if action == "click" && args["button"].as_str().unwrap_or("left")=="left" && target.is_some_and(|t| t["editable"] == true && t["password"] == false) { return Ok(false); }
        if action == "press_key" && editable && matches!(string(args, "key").to_ascii_lowercase().as_str(), "left"|"right"|"up"|"down"|"home"|"end"|"backspace"|"delete"|"ctrl+a"|"ctrl+z"|"ctrl+y"|"ctrl+home"|"ctrl+end"|"shift+left"|"shift+right"|"shift+home"|"shift+end"|"escape"|"esc") { return Ok(false); }
        if focus["message_context"]["verification"]=="qq_uia_header_editor_send_v2"
            && (target.is_some_and(|target|reviewed_send_click(app,args,&focus["message_context"],target))
                ||(action=="press_key"&&editable&&matches!(string(args,"key").to_ascii_lowercase().as_str(),"enter"|"ctrl+enter"))) {return Ok(true);}
        // RootWebArea alone cannot identify both recipient and complete draft.
        // Do not turn an unverified send into a generic mouse permission.
        return Err("message_workflow_unverified");
    }
    Ok(true)
}

pub fn binding(scope: &str, epoch: &str, window: &str, session: &str, app: &str, title: &str,
               args: &Value, focus: &Value, point: Option<(i32, i32)>, target: Option<&Value>) -> (String, Value) {
    let preview = json!({"device_epoch":epoch,"control_session_id":session,"window_id":window,
        "application":app,"window_title":title,"action":args["action"],"text":args["text"],"key":args["key"],
        "direction":args["direction"],"amount":args["amount"],"point":point,
        "button":args["button"],"click_count":args["click_count"],"checked":args["checked"],
        "drag_to":[args["to_x"],args["to_y"]],"duration_ms":args["duration_ms"],
        "focus": {"source":focus["source"],"identity":stable_ref(focus),"editable":focus["editable"],"bounds":focus["bounds"],
            "value":focus["value"],"value_complete":focus["value_complete"]},
        "message":focus["message_context"],
        "target":target.map(|t| json!({"identity":stable_ref(t),"name":t["name"],"role":t["role"],"bounds":t["bounds"],"value":t["value"],"checked":t["checked"],"enabled_reported":t["enabled"]}))});
    let digest = format!("{:x}", Sha256::digest(format!("{scope}\0{preview}").as_bytes()));
    (digest, preview)
}

#[cfg(test)]mod tests {
    use super::*;
    #[test]fn full_access_removes_application_semantic_gates_but_is_host_only(){
        for app in ["qq.exe","qqnt.exe","unknown.exe","notepad.exe"] {
            for action in ["click","type_text","press_key","scroll","drag","set_value","set_checked"] {
                let mut args=json!({"action":action,"key":"Enter","text":"draft","_permission_mode":"trusted_auto_allow"});
                assert_eq!(classify(app,&args,&Value::Null,None),Ok(false));
                assert!(!super::super::protocol::valid(args.as_object().unwrap()),"model cannot supply host policy");
                args.as_object_mut().unwrap().remove("_permission_mode");
                args["permission_mode"]=json!("trusted_auto_allow");
                assert!(!full_access(&args));
            }
        }
        let args=json!({"action":"click","_permission_mode":"trusted_auto_allow"});
        assert!(target_enabled("unknown.exe",&args,&Value::Null,&json!({"enabled":false})));
        assert!(!target_enabled("unknown.exe",&json!({"action":"set_value","_permission_mode":"trusted_auto_allow"}),&Value::Null,&json!({"enabled":false})));
        assert!(classify("qq.exe",&json!({"action":"click","_permission_mode":"ask_each_time"}),&Value::Null,None).is_err());
    }
    #[test]fn visual_typing_is_available_without_a_uia_editor_in_full_access(){
        let full=json!({"_permission_mode":"trusted_auto_allow"});
        assert!(typing_evidence(&full,&Value::Null,&Value::Null,true));
        assert!(!typing_evidence(&full,&Value::Null,&Value::Null,false));
        assert!(!typing_evidence(&json!({}),&Value::Null,&Value::Null,true));
        let focus=json!({"editable":true,"password":false,"stable_identity":true,"element_id":"field","bounds":[1,2,3,4]});
        assert!(typing_evidence(&full,&focus,&focus,false));
        assert!(typing_evidence(&json!({}),&focus,&focus,false));
        let mut changed=focus.clone();changed["element_id"]=json!("other");
        assert!(!typing_evidence(&full,&focus,&changed,false));
    }
    #[test]fn qq_send_requires_complete_context_and_binds_recipient_and_draft(){
        let header=json!({"element_id":"old_element_header","role":50020,"name":"Acceptance group","offscreen":false,"stable_identity":true,"bounds":{"x":320,"y":60,"width":260,"height":30}});
        let send=json!({"element_id":"old_element_send","role":50000,"name":"发送","enabled":true,"offscreen":false,"stable_identity":true,"bounds":{"x":900,"y":760,"width":80,"height":30}});
        let mut focus=json!({"element_id":"old_element_edit","source":"uia","name":"Acceptance group","editable":true,"password":false,"stable_identity":true,"value":"Approved draft","value_complete":true,"bounds":{"x":300,"y":600,"width":800,"height":150}});
        let mut ax=json!({"elements":[header,send.clone()],"focus_evidence":focus});
        let context=message_context(&ax,[0,0,1200,900],96);assert!(context.is_object());focus["message_context"]=context;
        focus["message_target"]=message_target(&ax,[0,0,1200,900],96);
        let args=json!({"action":"click"});assert_eq!(classify("qq.exe",&args,&focus,Some(&send)),Ok(true));
        let bind=|f:&Value|binding("owner","epoch","window","session","qq.exe","QQ",&args,f,None,Some(&send)).0;
        let approved=bind(&focus);let mut changed=focus.clone();changed["message_context"]["draft"]=json!("Different draft");assert_ne!(approved,bind(&changed));
        changed=focus.clone();changed["message_context"]["recipient"]=json!("Different group");assert_ne!(approved,bind(&changed));
        ax["focus_evidence"]["value_complete"]=json!(false);assert!(message_context(&ax,[0,0,1200,900],96).is_null());
        focus["message_context"]=Value::Null;
        assert_eq!(classify("qq.exe",&json!({"action":"click","recipient":"forged","draft":"forged"}),&focus,Some(&send)),Err("message_workflow_unverified"));
        assert_eq!(classify("qq.exe",&json!({"action":"type_text","text":"a\nb"}),&focus,None),Err("message_multiline_input_unverified"));
    }
    #[test]fn qq_stale_editor_label_is_blocked_before_typing(){
        let mut ax=json!({"focus_evidence":{"element_id":"obs_element_editor","source":"uia","name":"Test group",
            "editable":true,"password":false,"stable_identity":true,"value":"\n","value_complete":true,
            "bounds":{"x":1070,"y":861,"width":1300,"height":111}},
            "elements":[{"element_id":"obs_element_header","role":50000,"name":"My phone",
                "enabled":true,"stable_identity":true,"offscreen":false,"bounds":{"x":1086,"y":56,"width":68,"height":23}},
                {"element_id":"obs_element_send","role":50000,"name":"发送","enabled":false,
                "offscreen":false,"stable_identity":true,"bounds":{"x":2272,"y":986,"width":28,"height":28}}]});
        let evidence=message_target(&ax,[918,0,2374,1032],96);
        assert_eq!(evidence["status"],"conflict");assert_eq!(evidence["header_recipient"],"My phone");
        assert_eq!(evidence["send_control_status"],"identified");
        assert_eq!(evidence["send_enabled_reported"],false);
        let mut focus=ax["focus_evidence"].clone();focus["message_target"]=evidence;
        for args in [json!({"action":"type_text","text":"Hello"}),json!({"action":"press_key","key":"Enter"}),json!({"action":"press_key","key":"Backspace"})]{
            assert_eq!(classify("qq.exe",&args,&focus,None),Err("message_recipient_mismatch"));
        }
        ax["elements"][0]["name"]=json!("Test group");
        focus["message_target"]=message_target(&ax,[918,0,2374,1032],96);
        assert_eq!(classify("qq.exe",&json!({"action":"type_text","text":"Hello"}),&focus,None),Ok(false));
        ax["focus_evidence"]["value"]=json!("Hello");
        focus["message_context"]=message_context(&ax,[918,0,2374,1032],96);
        assert_eq!(focus["message_context"]["send_enabled_reported"],false);
        assert_eq!(classify("qq.exe",&json!({"action":"press_key","key":"Enter"}),&focus,None),Ok(true));
        ax["elements"][1]["enabled"]=json!(true);
        focus["message_target"]=message_target(&ax,[918,0,2374,1032],96);
        focus["message_context"]=message_context(&ax,[918,0,2374,1032],96);
        assert_eq!(focus["message_context"]["recipient"],"Test group");
        assert_eq!(classify("qq.exe",&json!({"action":"press_key","key":"Enter"}),&focus,None),Ok(true));
    }
    #[test]fn qq_missing_heading_never_uses_editor_label_as_recipient(){
        let ax=json!({"elements":[],"focus_evidence":{"editable":true,"password":false,"stable_identity":true,"name":"Old group"}});
        let evidence=message_target(&ax,[0,0,1200,900],96);assert_eq!(evidence["status"],"unverified");
        let mut focus=ax["focus_evidence"].clone();focus["message_target"]=evidence;
        assert_eq!(classify("qq.exe",&json!({"action":"type_text","text":"Hello"}),&focus,None),Err("message_recipient_unverified"));
    }

    #[test]fn qq_disabled_signal_does_not_bypass_context_or_exact_send_approval(){
        let mut ax=json!({"focus_evidence":{"element_id":"o_element_edit","source":"uia","name":"Test group",
            "editable":true,"password":false,"stable_identity":true,"value":"Approved draft","value_complete":true,
            "bounds":{"x":1070,"y":861,"width":1300,"height":111}},"elements":[
            {"element_id":"o_element_header","role":50000,"name":"Test group","stable_identity":true,"offscreen":false,
                "bounds":{"x":1086,"y":56,"width":68,"height":23}},
            {"element_id":"o_element_send","role":50000,"name":"发送","enabled":false,"offscreen":false,"stable_identity":true,
                "bounds":{"x":2272,"y":986,"width":28,"height":28}}]});
        let bounds=[918,0,2374,1032];let args=json!({"action":"click"});let send=ax["elements"][1].clone();
        let context=message_context(&ax,bounds,96);assert!(context.is_object());
        let mut focus=ax["focus_evidence"].clone();focus["message_target"]=message_target(&ax,bounds,96);focus["message_context"]=context.clone();
        assert!(target_enabled("qq.exe",&args,&context,&send));
        assert_eq!(classify("qq.exe",&args,&focus,Some(&send)),Ok(true),"requires external-send approval");
        ax["text_complete"]=json!(true);
        let step=json!({"action":"click","target":{"role":50000,"name":"发送"}});
        let flow_args=super::super::workflow::action_arguments(&step,&ax).expect("workflow must reach the same device policy");
        assert_eq!(flow_args["element_id"],send["element_id"]);
        assert!(target_enabled("qq.exe",&flow_args,&context,&send));
        assert_eq!(classify("qq.exe",&flow_args,&focus,Some(&send)),Ok(true));
        for app in ["unknown.exe","notepad.exe"] {assert!(!target_enabled(app,&args,&context,&send));}
        for other in [json!({"action":"click","button":"right"}),json!({"action":"click","click_count":2}),json!({"action":"set_value"})] {
            assert!(!target_enabled("qq.exe",&other,&context,&send));
            assert_eq!(classify("qq.exe",&other,&focus,Some(&send)),Err("message_workflow_unverified"));
        }
        let mut unrelated=send.clone();unrelated["element_id"]=json!("o_element_other");
        assert!(!target_enabled("qq.exe",&args,&context,&unrelated));
        let bind=|f:&Value,t:&Value|binding("scope","epoch","window","session","qq.exe","QQ",&args,f,None,Some(t)).0;
        let original=bind(&focus,&send);
        for (field,value) in [("draft",json!("Other draft")),("recipient",json!("Other group")),("send_identity",json!("other")),("send_enabled_reported",json!(true))] {
            let mut changed=focus.clone();changed["message_context"][field]=value;assert_ne!(original,bind(&changed,&send));
        }
        let mut enabled=send.clone();enabled["enabled"]=json!(true);assert_ne!(original,bind(&focus,&enabled));
        for (field,value) in [("value",json!("  \n")),("value_complete",json!(false)),("password",json!(true)),("stable_identity",json!(false)),("name",json!("Old group"))] {
            let mut changed=ax.clone();changed["focus_evidence"][field]=value;
            let context=message_context(&changed,bounds,96);assert!(context.is_null());assert!(!target_enabled("qq.exe",&args,&context,&send));
        }
        for (field,value) in [("offscreen",json!(true)),("stable_identity",json!(false)),("bounds",json!({"x":2400,"y":986,"width":28,"height":28}))] {
            let mut changed=ax.clone();changed["elements"][1][field]=value;assert!(message_context(&changed,bounds,96).is_null());
        }
        ax["elements"].as_array_mut().unwrap().push(send.clone());
        assert!(message_context(&ax,bounds,96).is_null(),"ambiguous send controls cannot authorize");
    }
}
