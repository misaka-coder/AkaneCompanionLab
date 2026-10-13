export function chatScope(viewModel) {
  return JSON.stringify([viewModel?.shell?.instanceLabel, viewModel?.bots?.activeId,
    viewModel?.character?.packId, viewModel?.chat?.sessionId]);
}

export function serviceScope(viewModel) {
  return JSON.stringify([viewModel?.shell?.instanceLabel, viewModel?.bots?.activeId]);
}

export function friendlyError(error) {
  const message = error instanceof Error ? error.message : String(error || "unknown");
  if (message.startsWith("tauri_control_center_bootstrap_failed:")) {
    return `桌宠运行时初始化失败：${message.slice("tauri_control_center_bootstrap_failed:".length)}`;
  }
  return {
    "instance-health-unavailable": "后端健康检查未通过，请确认桌宠和本地服务已经启动。",
    "snapshot_unavailable": "没有读取到控制中心快照，请稍后重试。",
    "all-backend-endpoints-failed": "后端已连接，但控制中心数据暂时不可用。"
  }[message] || message;
}
