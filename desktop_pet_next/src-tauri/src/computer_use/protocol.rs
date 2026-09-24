use serde_json::{json, Value};
pub const TOOL_ID: &str = "computer_use";
pub const SPEC_VERSION: &str = "1.3.0";
pub const SCHEMA_VERSION: u64 = 4;
pub const SCHEMA_HASH: &str = "sha256:349847d4f6d9da485e2cbd8d68cdf12ec6bc6c26a908a96f909cbf938645f14c";
pub fn fail(reason: &str) -> Value {
    let mut out=json!({"ok":false,"action_state":"not_started","observation_state":"failed","reason":reason});
    let recovery=match reason{
        "context_reference_expired"|"context_scope_mismatch"=>Some(("list_windows_then_select","context_ref 在当前设备代次、契约或调用会话不可用；没有替换为最新窗口。本次尚未开始。重新枚举并选择实际目标，保留已执行/结果未知的操作，不自动重放。")),
        "context_evidence_unavailable"=>Some(("observe_then_review","该 context_ref 没有所需的截图证据，或混用了完整引用字段。本次尚未开始；使用同一 context_ref observe(mode=visual/hybrid)，查看实际附图后再规划。")),
        "coordinate_out_of_bounds"=>Some(("use_screenshot_relative_coordinates","坐标超出本张图片。x/y 从截图左上角起算，须小于 coordinate_bounds 的宽高；裁剪/放大图也使用图内坐标，不能填屏幕或原窗口坐标。")),
        "screenshot_region_invalid"=>Some(("observe_without_region","裁剪区域超出窗口或超过图像大小限制。先不传 region/scale 重新 observe，再按实际窗口尺寸裁剪；不以桌面尺寸代替窗口尺寸。")),
        "visual_scene_changed"=>Some(("observe_then_replan","较旧截图的画面一致性复核未通过，本次输入尚未开始。重新 observe 后按新画面判断；工具不会把新截图偷偷绑定到旧计划。")),
        "coordinate_expired"|"observation_expired"|"screenshot_expired"|"target_pixels_changed"|"window_layout_changed"=>Some(("observe_then_replan","观察已过期或现场发生变化，本次输入尚未开始。重新 observe，核对目标后只规划尚未执行的动作。")),
        "element_identity_unavailable"=>Some(("observe_visual_then_target","该控件没有可稳定复核的无障碍身份，只能用于读取。观察截图后按画面定位，不要反复提交同一元素引用。")),
        "element_expired"|"element_changed"=>Some(("observe_then_review_element","元素引用失效或控件状态变化。重新观察，仅使用 stable_identity=true 的当前 element_id；缺少稳定引用时按截图定位。")),
        "desktop_busy"=>Some(("wait_for_running_operation","另一个桌面请求正在执行；本次尚未开始。等待该请求结束后再检查状态，不要连续重试，也不要解释成用户碰了鼠标键盘。")),
        "desktop_owned_by_other_session"=>Some(("wait_then_reselect","其他会话近期仍持有桌面控制权；这不表示用户正在操作。距持有者最后一次操作结束闲置满 60 秒后可重新 list_windows/select_window（个人浏览器用 connect）取得新控制。按 retry_after_ms 等待，期间不要循环调用；明确停止仍须本机解除。")),
        "control_reselection_required"=>Some(("list_windows_then_select","旧控制权已失效；重新 list_windows/select_window 取得新会话和观察（个人浏览器用 connect），不要重用旧动作、截图或流程。")),
        "desktop_input_disabled"=>Some(("owner_enable_desktop_input","请在电脑上的 Akane Next 设置 → 系统 → 电脑操作，点击「允许电脑输入」。开关会记住本机选择；模型不能自行开启。开启后重新选择目标窗口。")),
        "activation_denied"=>Some(("owner_activate_target_then_select","Windows 未允许切换目标窗口；尚未向目标发送输入。这不表示截图或整个电脑操作不可用。请用户将目标切到前台后重新 select_window，不要用旧观察继续输入或循环重试。")),
        "foreground_mismatch"=>Some(("select_target_then_observe","当前前台不是所选窗口，本次输入未开始。确认仍是任务目标后重新 select_window 取得新观察；若激活被拒绝，按 activation_denied 指引处理。不要继续使用旧截图输入。")),
        "window_minimized"=>Some(("restore_window_then_select","窗口已最小化。输入已开启时使用 manage_window(window_action=restore) 或重新 select_window；输入是否关闭以 input_enabled/desktop_input_disabled 为准。")),
        "user_takeover"|"user_input_active"=>Some(("observe_then_review_remaining_steps","检测到鼠标或键盘活动，旧观察已失效。调用 observe 重新观察；工具会短暂等待输入停下，稳定后刷新本任务控制凭据，无需用户点击恢复。若仍在活动就暂停等待，不连续抢控。按最新现场检查剩余步骤，已执行或结果不明的动作不得重放。")),
        "stopped"|"stopped_requires_local_resume"=>Some(("owner_resume_then_observe","控制已明确停止。请在 Akane Next 设置 → 系统 → 电脑操作点击「解除停止」，然后由模型重新 observe。普通鼠标键盘干扰可通过 observe 恢复，但显式停止不能自行解除。")),
        "message_recipient_mismatch"|"message_recipient_changed"=>Some(("verify_recipient_before_editing","聊天页头与编辑框标签冲突或收件人发生变化。QQ 编辑框标签可能滞留在旧会话；以当前页头与画面核查，停止写入和发送。不要把草稿所在位置或发送成功当作已确认。")),
        "message_recipient_unverified"=>Some(("observe_recipient_before_editing","尚未核实当前聊天收件人。重新观察页头与编辑框；只有编辑框名称不足以证明收件人。")),
        "target_occluded"=>Some(("observe_occlusion_then_review_action","目标位置属于覆盖它的其他窗口，所选窗口截图可能看不到这层遮挡。核对 occluder 及所属关系，处理具体窗口后重新观察；drag 只支持同一窗口内拖动。是否已有输入以 action_state 为准，unknown 时不得重放。")),
        "typing_observation_required"=>Some(("observe_visual_then_type","当前没有可核验的编辑框，也没有足够新的截图。调用 observe(mode=hybrid) 或 visual，按画面聚焦目标后 type_text；完全访问支持截图输入，不要求 UIA 一定识别编辑框。")),
        "message_workflow_unverified"=>Some(("verify_message_workflow_or_handoff","当前收件人、完整草稿或发送控件未核实，尚未生成具体发送审批。重新观察并核查，无法核实时由用户接管；不是批准后即可恢复的状态。")),
        _=>None,
    };
    if let Some((next,hint))=recovery{out["next_action"]=json!(next);out["recovery_hint"]=json!(hint);}
    if reason=="desktop_owned_by_other_session"{if let Some(ms)=crate::control_lease::retry_after_ms(){out["retry_after_ms"]=json!(ms);}}
    out
}
pub fn success() -> Value { json!({"ok":true,"action_state":"not_started","observation_state":"complete"}) }
pub fn string<'a>(v: &'a Value, key: &str) -> &'a str { v.get(key).and_then(Value::as_str).unwrap_or("") }
pub fn valid(args: &serde_json::Map<String, Value>) -> bool {
    if let Some(reference)=args.get("context_ref") {
        if !reference.as_str().is_some_and(|s|!s.is_empty()&&s.chars().count()<=160){return false;}
        let binding=json!({"control_session_id":"context","device_epoch":"context","observation_id":"context","screenshot_id":"context"});
        return context_arguments(args,&binding).is_some_and(|expanded|valid(&expanded));
    }
    let Some(action) = args.get("action").and_then(Value::as_str) else { return false; };
    let fields: &[&str] = match action {
        "list_windows" => &["query", "offset", "limit"], "launch_app" => &["app"],
        "select_window" => &["window_id", "device_epoch"],
        "manage_window" => &["window_id", "device_epoch", "window_action", "desktop_x", "desktop_y"],
        "observe" => &["control_session_id", "device_epoch", "region", "scale"],
        "read_element" => &["control_session_id", "device_epoch", "observation_id", "element_id"],
        "stop" | "handoff" => &["control_session_id", "device_epoch"],
        "status" => &["control_session_id", "device_epoch", "operation_id"],
        "click" => &["control_session_id","device_epoch","observation_id","element_id","screenshot_id","x","y","button","click_count"],
        "set_value"=>&["control_session_id","device_epoch","observation_id","element_id","text"],
        "set_checked"=>&["control_session_id","device_epoch","observation_id","element_id","checked"],
        "drag"=>&["control_session_id","device_epoch","observation_id","screenshot_id","x","y","to_x","to_y","duration_ms"],
        "run_steps"=>&["control_session_id","device_epoch","observation_id","steps","budget_ms"],
        "run_actions"=>&["control_session_id","device_epoch","observation_id","screenshot_id","actions","budget_ms"],
        "resume_steps"=>&["control_session_id","device_epoch","observation_id","workflow_id","budget_ms"],
        "type_text" => &["control_session_id","device_epoch","observation_id","text"],
        "press_key" => &["control_session_id","device_epoch","observation_id","key"],
        "scroll" => &["control_session_id","device_epoch","observation_id","element_id","screenshot_id","x","y","direction","amount"],
        _ => return false,
    };
    let required: &[&str] = match action {
        "select_window" => &["window_id","device_epoch"],
        "manage_window" => &["window_id","device_epoch","window_action"],
        "observe" | "stop" | "handoff" => &["control_session_id","device_epoch"],
        "read_element" => &["control_session_id","device_epoch","observation_id","element_id"],
        "click" => &["control_session_id","device_epoch","observation_id"],
        "set_value"=>&["control_session_id","device_epoch","observation_id","element_id","text"],
        "set_checked"=>&["control_session_id","device_epoch","observation_id","element_id","checked"],
        "drag"=>&["control_session_id","device_epoch","observation_id","screenshot_id","x","y","to_x","to_y"],
        "run_steps"=>&["control_session_id","device_epoch","observation_id","steps"],
        "run_actions"=>&["control_session_id","device_epoch","observation_id","screenshot_id","actions"],
        "resume_steps"=>&["control_session_id","device_epoch","observation_id","workflow_id"],
        "type_text" => &["control_session_id","device_epoch","observation_id","text"],
        "press_key" => &["control_session_id","device_epoch","observation_id","key"],
        "scroll" => &["control_session_id","device_epoch","observation_id","direction"],
        "launch_app" => &["app"], _ => &[],
    };
    if required.iter().any(|key| !args.contains_key(*key)) { return false; }
    if action=="manage_window" {
        let count=["desktop_x","desktop_y"].iter().filter(|k|args.contains_key(**k)).count();
        if count!=if args.get("window_action").and_then(Value::as_str)==Some("move"){2}else{0}{return false;}
    }
    if matches!(action,"click"|"scroll") {
        let element = args.contains_key("element_id");
        let coords = ["screenshot_id","x","y"].iter().filter(|key| args.contains_key(**key)).count();
        if !((element && coords == 0) || (!element && coords == 3)) { return false; }
    }
    args.iter().all(|(key, value)| {
        if key == "action" { return true; }
        if key == "mode" { return action!="read_element" && matches!(value.as_str(), Some("visual" | "text" | "hybrid")); }
        if !fields.contains(&key.as_str()) { return false; }
        match key.as_str() {
            "steps"=>super::workflow::valid_steps(value),
            "actions"=>super::workflow::valid_actions(value),
            "window_action"=>matches!(value.as_str(),Some("activate"|"restore"|"minimize"|"move")),
            "desktop_x"|"desktop_y"=>value.as_i64().is_some_and(|v|(-32768..=32767).contains(&v)),
            "budget_ms"=>value.as_u64().is_some_and(|v|(1000..=15000).contains(&v)),
            "duration_ms"=>value.as_u64().is_some_and(|v|(100..=2000).contains(&v)),
            "click_count"=>value.as_u64().is_some_and(|v|(1..=2).contains(&v)),
            "button"=>matches!(value.as_str(),Some("left"|"right"|"middle")),
            "checked"=>value.is_boolean(),
            "scale" => value.as_u64().is_some_and(|v|(1..=3).contains(&v)),
            "region" => value.as_object().is_some_and(|region|region.len()==4 && ["x","y","width","height"].iter().all(|k|
                region.get(*k).and_then(Value::as_u64).is_some_and(|v|v<=32767 && (matches!(*k,"x"|"y") || v>0)))),
            "x" | "y" | "to_x" | "to_y" => value.as_u64().is_some_and(|v| v <= 32767),
            "offset" => value.as_u64().is_some_and(|v| v <= 10000),
            "limit" => value.as_u64().is_some_and(|v| (1..=50).contains(&v)),
            "amount" => value.as_u64().is_some_and(|v| (1..=10).contains(&v)),
            "direction" => matches!(value.as_str(), Some("up" | "down")),
            "text" => value.as_str().is_some_and(|v| (action=="set_value"||!v.is_empty()) && v.chars().count() <= 4000 && !v.contains(['\r','\n','\t','\0'])),
            // Match the published host bounds. Key support remains a separate
            // input-driver check; accepting its wire shape never sends a key.
            "query" => value.as_str().is_some_and(|v| v.chars().count() <= 100),
            "key" => value.as_str().is_some_and(|v| v.chars().count() <= 40),
            _ => value.as_str().is_some_and(|v| !v.is_empty() && v.chars().count() <= if key == "app" {260} else {160}),
        }
    })
}

/// Expand only a requested immutable context. Never consult the current window.
pub fn context_arguments(args:&serde_json::Map<String,Value>, binding:&Value)->Option<serde_json::Map<String,Value>> {
    let action=args.get("action")?.as_str()?;
    let input=matches!(action,"click"|"type_text"|"press_key"|"scroll"|"set_value"|"set_checked"|"drag"|"run_actions"|"run_steps"|"resume_steps");
    if !input&&!matches!(action,"observe"|"stop"|"handoff"|"read_element"){return None;}
    if ["control_session_id","device_epoch","observation_id","screenshot_id"].iter().any(|k|args.contains_key(*k)){return None;}
    let mut expanded=args.clone();expanded.remove("context_ref");
    let mut fields=vec!["control_session_id","device_epoch"];
    if input || action=="read_element" {fields.push("observation_id");}
    if matches!(action,"drag"|"run_actions") || (matches!(action,"click"|"scroll")&&(args.contains_key("x")||args.contains_key("y"))) {
        fields.push("screenshot_id");
    }
    for key in fields {
        if string(binding,key).is_empty(){return None;}
        expanded.insert(key.into(),binding[key].clone());
    }
    Some(expanded)
}

#[cfg(test)]
mod contract_cases {
    #[test]
    fn shared_host_device_acceptance() {
        #[cfg(windows)]
        assert!(super::super::input::key_codes("").is_none());
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../tests/fixtures/computer_use_contract_cases.json"
        )).unwrap();
        let contexts: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../tests/fixtures/computer_use_context_cases.json"
        )).unwrap();
        for row in fixture["cases"].as_array().unwrap().iter().chain(contexts.as_array().unwrap()) {
            let actual = row["arguments"].as_object().is_some_and(super::valid);
            assert_eq!(actual, row["accepted"].as_bool().unwrap(), "{}", row["id"]);
        }
    }
}
