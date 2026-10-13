import { escapeHtml } from "../dom.js";

const SETTINGS = [
  ["OWNER_PRIVATE_SESSION_SHARED", "桌宠与主人 QQ 私聊共用记忆", "两端沿用同一份私人记忆"],
  ["OWNER_PRIVATE_GROUP_MEMORY_MIRROR", "将主人私人交流写入指定群", "只分享新完成对话中的提问与最终回复"]
];
const ERRORS = {
  admin_auth_required: "需要管理员身份，请通过本地 QQ 启动入口重新打开控制中心。",
  backend_unavailable: "暂时连不上后端，请稍后刷新。",
  private_memory_preference_save_failed: "群设置保存失败，原来的接收状态已保留。",
  memory_settings_read_failed: "记忆设置读取失败，请刷新重试。",
  managed_launcher_unavailable: "当前连接不支持在这里重启，请使用启动入口重启后端。",
  backend_restart_binding_mismatch: "当前 Bot 不属于本地启动配置，请切回本地 Bot 后重启。",
  backend_restart_wait_timeout: "当前任务仍在执行，未强制重启。设置已保存，可稍后再试。",
  backend_restart_drain_failed: "后端未能完成收尾，请查看系统诊断并通过启动入口重启。",
  backend_restart_launch_failed: "后端启动失败，请通过启动入口恢复；设置已保存。",
  backend_restart_launch_timeout: "启动器未按时结束，请检查服务连接；设置已保存。",
  backend_restart_not_confirmed: "尚未确认新的后端启动成功，请检查服务连接。",
  settings_apply_not_confirmed: "尚未确认设置生效，请刷新检查。",
  unknown_qq_group: "该群尚未被这个 Bot 识别，请刷新群列表。"
};

export function renderMemorySettings(state) {
  const memory = state.viewModel?.system?.memorySettings;
  if (!memory) return "";
  const data = memory.data;
  const busy = ["loading", "saving", "restarting"].includes(memory.phase);
  const blocked = busy || memory.phase !== "ready";
  const pending = data?.settings?.some(row => row.pendingRestart);
  const filter = String(state.memoryGroupFilter || "");
  const groups = (data?.groups || []).filter(row => `${row.name} ${row.groupId}`.toLowerCase().includes(filter.trim().toLowerCase()));
  const characterName = state.viewModel?.character?.packId === data?.characterPackId
    ? state.viewModel.character.displayName : data?.characterPackId;
  const groupIcon = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="9" cy="8" r="3"/><path d="M3 20v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6M18 14a5 5 0 0 1 3 4v2"/></svg>';
  const toggle = (action, enabled, label, disabled) => `<button type="button" class="memory-toggle${enabled ? " is-on" : ""}" role="switch" aria-checked="${enabled}" aria-label="${escapeHtml(label)}" data-action="${escapeHtml(action)}" data-action-value="${!enabled}" data-action-value-type="boolean" ${disabled ? "disabled" : ""}><i></i></button>`;
  return `<section class="memory-settings system-panel glass-panel" aria-labelledby="memory-settings-title">
    <div class="system-panel-head"><div><p class="eyebrow">MEMORY & QQ</p><h3 id="memory-settings-title">记忆与 QQ</h3><p>让两端的交流自然接续</p></div>
      <button type="button" class="action-button is-soft" data-action="memory.refresh" ${busy ? "disabled" : ""}><span>↻</span><b>刷新</b></button></div>
    ${data ? `<div class="memory-setting-rows">${SETTINGS.map(([key, title, description]) => {
      const row = data.settings.find(item => item.key === key);
      if (!row) return "";
      const active = key === "OWNER_PRIVATE_SESSION_SHARED" ? data.privateMemoryReady !== false : data.groupMemoryRunning;
      return `<div class="memory-setting-row"><div><strong>${title}</strong><small>${description}</small></div>
        <span class="memory-state${row.pendingRestart ? " is-pending" : row.current && active ? " is-on" : ""}">${row.pendingRestart ? `待重启 · 当前${row.current ? "开" : "关"}` : row.current ? active ? "已生效" : "已开启 · 待配置" : "已关闭"}</span>
        ${toggle(`memory.setting.${key}`, row.desired, title, blocked)}</div>`;
    }).join("")}</div>` : `<p class="memory-empty">${memory.phase === "loading" ? "正在读取记忆设置与群列表…" : "连接后端后，可在这里选择接收群。"}</p>`}
    ${pending || memory.phase === "restarting" ? `<div class="memory-restart-notice"><span>${memory.phase === "restarting" ? "正在等待当前任务结束并重启后端…" : "设置已保存，重启后端后生效。"}</span>
      <button type="button" class="action-button is-primary" data-action="memory.restart" ${blocked || !memory.restartAvailable ? "disabled" : ""}><b>重启后端并应用</b></button>
      ${!memory.restartAvailable && !busy ? `<small>请使用本地启动入口重启后端。</small>` : ""}</div>` : ""}
    ${data ? `<div class="memory-groups-heading"><div><h4>接收私人记忆的群</h4><small>${data.groups.filter(row => row.enabled).length} 个群已选择 · 当前角色 ${escapeHtml(characterName)}</small></div>
      <input class="memory-group-search" type="search" aria-label="筛选接收群" placeholder="搜索群名或群号" data-memory-group-filter value="${escapeHtml(filter)}"></div>
      ${!data.groupMemoryRunning ? `<p class="memory-paused">${pending ? "重启应用总开关后，所选群才会接收。" : "接收暂未运行：需开启两个总开关，并配置主人 QQ 与 MemCore。"}</p>` : ""}
      <div class="memory-group-list">${groups.length ? groups.map(row => `<div class="memory-group-row"><span class="memory-group-icon" aria-hidden="true">${groupIcon}</span><div><strong>${escapeHtml(row.name || `群 ${row.groupId}`)}</strong><small>${row.name ? `群号 ${escapeHtml(row.groupId)}` : "群名称暂不可用"}${row.hasCharacterMemory ? "" : " · 此角色开始群聊后接收"}</small></div>
        ${toggle(`memory.group.${row.groupId}`, row.enabled, `${row.name || row.groupId} 接收私人记忆`, blocked || !data.qqAvailable)}</div>`).join("") : `<p class="memory-empty">${filter ? "没有匹配的群。" : "还没有可选择的群。连接 QQ 或开始一次群聊后刷新。"}</p>`}</div>
      <p class="memory-settings-footnote">群设置保存后立即生效，无需在群里发指令。关闭只停止后续接收，已存记忆会保留。${!data.groupNamesAvailable ? " QQ 群名称暂不可用，仍可修改已有群。" : ""}</p>` : ""}
    ${memory.error ? `<div class="memory-feedback is-error" role="alert">${escapeHtml(ERRORS[memory.error] || "操作未完成，请刷新后重试。")}${ERRORS[memory.error] ? "" : `<small>${escapeHtml(memory.error)}</small>`}</div>`
      : memory.notice ? `<p class="memory-feedback" role="status">${escapeHtml(memory.notice)}</p>` : ""}
  </section>`;
}
