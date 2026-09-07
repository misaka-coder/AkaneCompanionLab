// Instance-scoped onboarding state. UI components never invoke Tauri or HTTP themselves.
export const QQ_SETUP_ACTIONS = Object.freeze({
  detect: "qq.setup.detect", select: "qq.setup.select", start: "qq.setup.start",
  openLogin: "qq.setup.openLogin", openFolder: "qq.setup.openFolder", check: "abilities.qq.selfCheck"
});

export function isQqSetupAction(action) {
  // The existing backend self-check also serves remote Bots; never intercept it.
  return action !== QQ_SETUP_ACTIONS.check && Object.values(QQ_SETUP_ACTIONS).includes(action);
}

const REASONS = {
  local_qq_requires_loopback: "当前是远程连接，请在 NapCat 所在设备处理登录；这里不会启动本机服务。",
  local_qq_binding_changed: "当前 Bot 与本地启动配置不一致，请切回本地 QQ Bot 后重试。",
  local_qq_launch_profile_missing: "请通过本地 QQ 启动入口打开桌宠；当前进程未绑定 QQ 接入配置。",
  local_qq_binding_unavailable: "无法读取当前桌宠绑定，请重新打开控制中心。",
  local_qq_invalid_url: "本地 QQ 连接地址尚未配置，请使用本地 QQ 启动入口。",
  local_qq_settings_only: "请在桌面控制中心操作。",
  local_qq_windows_only: "本地启动目前支持 Windows NapCat Shell。",
  local_qq_busy: "另一项 QQ 接入操作正在进行。",
  napcat_not_found: "未找到 NapCat，请选择已经安装的 NapCat Shell 目录。",
  napcat_multiple_installations: "发现多份 NapCat，请明确选择本次要使用的目录。",
  napcat_installation_incomplete: "该目录不是完整的 Windows NapCat Shell 安装目录。",
  napcat_path_escape: "安装文件指向目录外，无法安全使用这份安装。",
  napcat_config_missing: "NapCat 配置文件不存在，请检查安装或重新选择目录。",
  napcat_config_invalid: "NapCat 配置无法识别，原配置没有被覆盖。",
  napcat_config_unreadable: "无法读取 NapCat 配置，请检查文件权限。",
  napcat_config_write_failed: "配置未能保存，请检查权限后重试。",
  napcat_backup_failed: "未能建立配置备份，本次没有覆盖原配置。",
  napcat_account_config_missing: "此 QQ 的 OneBot 配置尚未生成，请先在 NapCat 完成该账号的首次登录。",
  napcat_webui_disabled: "NapCat 未启用登录管理页，请检查 WebUI 配置。",
  napcat_webui_port_conflict: "登录管理端口被其他服务占用或已变化，请在 NapCat 中确认端口后重试。",
  napcat_webui_unavailable: "登录管理页尚未响应，请稍后重新检查。",
  napcat_restart_required: "NapCat 正在运行，但连接配置不匹配。请先退出这份 NapCat，再点配置并启动；不会自动终止 QQ。",
  napcat_not_started: "登录管理页尚未就绪，请先启动 NapCat。",
  napcat_start_failed: "NapCat 启动失败，请确认 QQ 已安装、NapCat 完整可用。",
  napcat_open_failed: "无法打开页面或目录，请检查系统默认浏览器或文件管理器。",
  napcat_webui_token_missing: "NapCat 登录密钥缺失，请在 NapCat 中修复 WebUI 配置。",
  napcat_selection_failed: "无法打开目录选择窗口，请重试。",
  napcat_selection_running: "本次启动的 NapCat 仍在运行，请退出后再更换安装目录。",
  napcat_onebot_port_in_use: "OneBot 端口已被占用，可能已有 NapCat 正在运行。请检查原实例，不会重复启动。",
  local_qq_account_mismatch: "实际登录 QQ 与此 Bot 绑定的账号不一致，请退出错误账号后重新扫码。",
  account_identity_unknown: "OneBot 没有返回实际登录 QQ，不能确认账号身份。",
  local_qq_expected_account_unknown: "尚未读取本地目标 QQ，不能确认接入成功。请先检测 NapCat。",
  local_qq_source_changed: "Bot 已切换，本次结果已丢弃。",
  desktop_required: "请在 Windows 桌面控制中心使用本地扫码接入，浏览器预览不会启动本机程序。"
};

export function qqSetupDetail(reason) {
  return REASONS[reason] || ({ unreachable: "OneBot 尚未连接，请先完成扫码，再重新检查。", auth_failed: "OneBot 鉴权不匹配，请重新配置并启动。", account_offline: "QQ 未在线，请打开登录页重新扫码。", account_status_unknown: "QQ 在线状态未确认，请稍后再检查。", bridge_disabled: "当前 Bot 尚未启用 QQ 接入。", "request-failed": "后端请求失败，请检查本地 Bot 是否运行。" }[reason]) || "检查未完成，请确认本地 Bot 与 NapCat 的运行状态后重试。";
}

export function createQqSetupController({ onChange = () => {} } = {}) {
  let source = null;
  let epoch = 0;
  let active = null;
  let state = { phase: "idle", native: null, connection: null, notice: "" };
  function publish() { onChange({ ...state }); }
  return {
    bind(next) {
      if (next === source) return;
      source = next; epoch += 1; active = null;
      state = { phase: "idle", native: null, connection: null, notice: "" };
      publish();
    },
    stop() { source = null; epoch += 1; active = null; },
    async run(actionId) {
      if (!source) return { ok: false, status: "not-available", reason: "local_qq_source_changed" };
      if (active) return { ok: false, status: "busy", reason: qqSetupDetail("local_qq_busy") };
      const requestSource = source;
      const version = epoch;
      const operation = {};
      active = operation;
      state = { ...state, phase: "pending", notice: "", connection: null };
      publish();
      try {
        const native = await requestSource.runAction(actionId === QQ_SETUP_ACTIONS.check ? QQ_SETUP_ACTIONS.detect : actionId);
        if (source !== requestSource || epoch !== version) return stale();
        if (native?.status === "cancelled") {
          state = { ...state, phase: "idle", notice: "已取消目录选择。" };
          return native;
        }
        // Explicit allowlist: paths, login URLs, tokens and arbitrary native fields cannot reach render state.
        state.native = safeNative(native);
        let result = native;
        if (native?.ok && native.webuiReady && [QQ_SETUP_ACTIONS.detect, QQ_SETUP_ACTIONS.check].includes(actionId)) {
          const check = await requestSource.runAction(QQ_SETUP_ACTIONS.check);
          if (source !== requestSource || epoch !== version) return stale();
          const actual = String(check?.payload?.botQq || "");
          const expected = state.native.botQq;
          const matched = Boolean(expected && actual === expected);
          const reason = check?.ok && !matched ? expected ? "local_qq_account_mismatch" : "local_qq_expected_account_unknown" : String(check?.status || "request-failed");
          state.connection = { ok: check?.ok === true && matched && check.status === "connected", actualQq: /^\d{5,20}$/.test(actual) ? actual : "", reason };
          result = { ok: state.connection.ok, status: state.connection.ok ? "connected" : "failed", reason: state.connection.ok ? "" : reason };
        }
        state.notice = !native?.ok ? qqSetupDetail(native?.reason || native?.status)
          : actionId === QQ_SETUP_ACTIONS.openLogin ? "已请求打开登录页。请扫码并确认账号，完成后点重新检查；打开页面不代表登录成功。"
          : actionId === QQ_SETUP_ACTIONS.openFolder ? "已请求打开所选安装目录。"
          : actionId === QQ_SETUP_ACTIONS.start ? !native.configured ? "首次登录还需生成账号配置。请先扫码，退出这份 NapCat 后再配置并启动；当前尚未完成接入。" : native.webuiReady ? "登录页已就绪，请打开扫码登录页。" : "启动请求已发出，尚未确认登录页就绪。请稍后重新检查；若持续无响应，请打开目录排查。"
          : state.connection ? state.connection.ok ? "QQ 在线且账号匹配。尚未进行真实收发消息测试。" : qqSetupDetail(state.connection.reason)
          : "检测已完成。请启动 NapCat，并在登录页扫码。";
        return { ...result, actionId, refresh: false, ...(result?.ok ? {} : { reason: state.notice }) };
      } catch {
        if (source !== requestSource || epoch !== version) return stale();
        state.notice = qqSetupDetail("request-failed");
        return { ok: false, status: "failed", reason: state.notice, refresh: false };
      } finally {
        if (active === operation && source === requestSource && epoch === version) {
          active = null; state.phase = "idle"; publish();
        }
      }
    }
  };
}

function stale() { return { ok: false, status: "stale", reason: "local_qq_source_changed", refresh: false }; }
function safeNative(value) {
  return { ok: value?.ok === true, installed: value?.installed === true, configured: value?.configured === true,
    webuiReady: value?.webuiReady === true, starting: value?.starting === true,
    reason: String(value?.reason || value?.status || ""), botQq: /^\d{5,20}$/.test(String(value?.botQq || "")) ? String(value.botQq) : "",
    backendPort: Number(value?.backendPort) || null, onebotPort: Number(value?.onebotPort) || null };
}

export function normalizeQqSetup(runtime, desktop, backendUrl) {
  let local = false;
  try { const url = new URL(backendUrl); local = url.protocol === "http:" && ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname); } catch { /* Unbound is not local. */ }
  const native = safeNative(runtime?.native);
  const blocked = !desktop || !local || native.reason.startsWith("local_qq_");
  return { ...runtime, native, supported: desktop && local, blocked, busy: runtime?.phase === "pending",
    detail: runtime?.notice || (!desktop ? qqSetupDetail("desktop_required") : !local ? qqSetupDetail("local_qq_requires_loopback") : native.reason && !native.ok ? qqSetupDetail(native.reason) : "检测本地 NapCat，然后扫码连接这个 Bot。") };
}
