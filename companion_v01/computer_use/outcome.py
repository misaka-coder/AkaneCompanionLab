"""Preserve transport failures alongside device input facts in model feedback."""


def complete_outcome(data, *, status, reason, action):
    data = dict(data)
    complete = type(data.get("ok")) is bool and all(
        isinstance(data.get(key), str) and data[key] in values for key, values in (
            ("action_state", {"not_started", "executed", "unknown"}),
            ("observation_state", {"complete", "partial", "failed", "unstable", "pending"})))
    if status == "succeeded" and not complete:
        status, reason = "execution_unknown", "desktop_result_incomplete"
    if status != "succeeded":
        data["ok"] = False
        if not data.get("reason"):
            data["reason"] = reason or "executor_failed"
        data["execution_status"] = status
        no_input = (status in {"unavailable_before_dispatch", "rejected"}
                    or data.get("dispatch_phase") == "prepare"
                    or action in {"list_windows", "observe", "status", "read_element"})
        if data.get("action_state") not in ("not_started", "executed", "unknown"):
            data["action_state"] = "not_started" if no_input else "unknown"
        if data.get("observation_state") not in ("complete", "partial", "failed", "unstable", "pending"):
            data["observation_state"] = "failed"
        if not data.get("next_action"):
            if data["action_state"] == "unknown":
                data["next_action"] = "status_or_observe_do_not_replay"
                data["recovery_hint"] = "执行结果未确认。连接恢复后按 operation_id/device_epoch 查询 status 或重新观察；设备换代后旧状态可能不可查，不得重放结果不明的输入。"
            elif reason in {"offer_expired", "missing_execution_receipt", "executor_unavailable"}:
                data["next_action"] = "reload_capability_then_reselect"
                data["recovery_hint"] = "本次输入尚未开始。重新加载 computer_use 获取可用执行凭据，按当前窗口重新建立观察。"
            else:
                data["next_action"] = "inspect_failure_then_observe"
                data["recovery_hint"] = "按 reason 排查后重新观察。action_state 保留实际执行事实，不能把缺失截图解释为输入未执行。"
    return data, status, reason
