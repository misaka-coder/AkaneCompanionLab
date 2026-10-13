import { CONTROL_CENTER_ACTIONS } from "../../action-router.js";
import { buildBackendUrl, readActionResponse } from "../http.js";
import { formatDataSourceError } from "../shared.js";

export async function runPluginBackendAction(fetchImpl, baseUrl, actionId, payload = {}, params = {}) {
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesPluginConnectionSave) {
    return runPluginConnectionBackendAction(fetchImpl, baseUrl, actionId, payload, params);
  }
  if ([CONTROL_CENTER_ACTIONS.abilitiesPluginStageSource, CONTROL_CENTER_ACTIONS.abilitiesPluginStageWheel, CONTROL_CENTER_ACTIONS.abilitiesPluginStageMarket].includes(actionId)) {
    return runPluginStageBackendAction(fetchImpl, baseUrl, actionId, payload, params);
  }
  if (actionId === CONTROL_CENTER_ACTIONS.abilitiesPluginInstall) {
    return runPluginInstallBackendAction(fetchImpl, baseUrl, actionId, payload, params);
  }
  if ([CONTROL_CENTER_ACTIONS.abilitiesPluginDiscardStage, CONTROL_CENTER_ACTIONS.abilitiesPluginUninstall].includes(actionId)) {
    return runPluginDeleteBackendAction(fetchImpl, baseUrl, actionId, payload, params);
  }
  const pluginId = String(payload.pluginId || payload.plugin_id || payload.value || "").trim();
  if (!pluginId) {
    return { ok: false, status: "invalid-payload", actionId, refresh: false, error: "pluginId is required" };
  }
  const rollback = actionId === CONTROL_CENTER_ACTIONS.abilitiesPluginRollback;
  const enabled = actionId === CONTROL_CENTER_ACTIONS.abilitiesPluginEnable;
  const path = rollback
    ? `/admin/plugins/${encodeURIComponent(pluginId)}/rollback`
    : `/admin/plugins/${encodeURIComponent(pluginId)}/enabled`;
  try {
    const response = await fetchImpl(
      buildBackendUrl(baseUrl, path, params),
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(rollback ? {} : { enabled }),
        cache: "no-store"
      }
    );
    const result = await readActionResponse(response);
    const ok = response.ok && Boolean(result?.ok);
    return {
      ...result,
      ok,
      status: String(result?.status || (ok ? (rollback ? "rolled_back" : enabled ? "enabled" : "disabled") : `http-${response.status}`)),
      reason: ok ? "" : pluginActionFailureDetail(result, response.status),
      actionId,
      pluginId,
      refresh: true
    };
  } catch (error) {
    return {
      ok: false,
      status: "request-failed",
      actionId,
      pluginId,
      refresh: true,
      error: formatDataSourceError(error)
    };
  }
}

async function runPluginConnectionBackendAction(fetchImpl, baseUrl, actionId, payload = {}, params = {}) {
  const pluginId = String(payload.pluginId || payload.plugin_id || "").trim();
  const connectionName = String(payload.connectionName || payload.connection_name || "").trim();
  const expectedRevision = Number(payload.expectedRevision ?? payload.expected_revision);
  const enabled = typeof payload.enabled === "boolean" ? payload.enabled : true;
  const values = payload.values && typeof payload.values === "object" && !Array.isArray(payload.values) ? payload.values : {};
  const clearFields = Array.isArray(payload.clearFields)
    ? payload.clearFields.map((item) => String(item || "").trim()).filter(Boolean)
    : Array.isArray(payload.clear_fields)
      ? payload.clear_fields.map((item) => String(item || "").trim()).filter(Boolean)
      : [];
  if (!pluginId || !connectionName || !Number.isInteger(expectedRevision) || expectedRevision < 0) {
    return { ok: false, status: "invalid-payload", actionId, refresh: false, error: "pluginId, connectionName and expectedRevision are required" };
  }
  try {
    const response = await fetchImpl(
      buildBackendUrl(baseUrl, `/admin/plugins/${encodeURIComponent(pluginId)}/connections/${encodeURIComponent(connectionName)}`, {
        ...params,
        profile_user_id: params.profile_user_id || "master"
      }),
      {
        method: "PUT",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          expected_revision: expectedRevision,
          enabled,
          values,
          clear_fields: clearFields
        }),
        cache: "no-store"
      }
    );
    const result = await readActionResponse(response);
    const ok = response.ok && Boolean(result?.ok);
    return {
      ...result,
      ok,
      status: String(result?.status || (ok ? "configured" : `http-${response.status}`)),
      reason: ok ? "" : pluginConnectionFailureDetail(result, response.status),
      actionId,
      pluginId,
      connectionName,
      refresh: true
    };
  } catch (error) {
    return {
      ok: false,
      status: "request-failed",
      actionId,
      pluginId,
      connectionName,
      refresh: true,
      error: formatDataSourceError(error)
    };
  }
}

function pluginConnectionFailureDetail(result, httpStatus) {
  const status = String(result?.status || "").trim();
  const reason = String(result?.reason || "").trim();
  if (status === "conflict" || reason === "connection_revision_conflict") return "配置已经被其他窗口更新，请刷新后再保存";
  if (reason === "connection_values_invalid") return "配置值不符合插件声明，请检查字段";
  if (reason === "connection_clear_update_conflict") return "同一个字段不能同时填写和清除";
  if (reason === "admin_auth_required" || httpStatus === 401 || httpStatus === 403) return "此操作只能从受信控制中心执行";
  return reason || `http-${httpStatus}`;
}

async function runPluginDeleteBackendAction(fetchImpl, baseUrl, actionId, payload = {}, params = {}) {
  const discardStage = actionId === CONTROL_CENTER_ACTIONS.abilitiesPluginDiscardStage;
  const targetId = String(discardStage
    ? payload.stageId || payload.stage_id || payload.value || ""
    : payload.pluginId || payload.plugin_id || payload.value || "").trim();
  if (!targetId) {
    return { ok: false, status: "invalid-payload", actionId, refresh: false, error: discardStage ? "stageId is required" : "pluginId is required" };
  }
  const path = discardStage
    ? `/admin/plugins/stages/${encodeURIComponent(targetId)}`
    : `/admin/plugins/${encodeURIComponent(targetId)}`;
  try {
    const response = await fetchImpl(buildBackendUrl(baseUrl, path, params), {
      method: "DELETE",
      headers: { Accept: "application/json" },
      cache: "no-store"
    });
    const result = await readActionResponse(response);
    const ok = response.ok && Boolean(result?.ok);
    return {
      ...result,
      ok,
      status: String(result?.status || (ok ? (discardStage ? "discarded" : "removed") : `http-${response.status}`)),
      reason: ok ? "" : pluginDeleteFailureDetail(result, response.status, discardStage),
      actionId,
      ...(discardStage ? { stageId: targetId } : { pluginId: targetId }),
      refresh: true
    };
  } catch (error) {
    return {
      ok: false,
      status: "request-failed",
      actionId,
      ...(discardStage ? { stageId: targetId } : { pluginId: targetId }),
      refresh: true,
      error: formatDataSourceError(error)
    };
  }
}

function pluginDeleteFailureDetail(result, httpStatus, discardStage) {
  const status = String(result?.status || "").trim();
  const reason = String(result?.reason || "").trim();
  if (status === "not_found") return discardStage ? "这个暂存候选已经不存在" : "当前 Bot 没有安装这个插件";
  if (status === "deactivation_failed") return "插件未能安全停用，当前版本保持不变";
  if (status === "persist_failed") return "卸载状态未能保存，当前版本已经恢复";
  if (status === "rollback_failed") return "卸载失败且自动恢复未完成，请检查插件运行状态";
  if (status === "removed_runtime_degraded") return "制品已移除，但插件运行时代次需要重新检查";
  if (httpStatus === 401 || httpStatus === 403) return "此操作只能从受信控制中心执行";
  return reason || `http-${httpStatus}`;
}

async function runPluginInstallBackendAction(fetchImpl, baseUrl, actionId, payload = {}, params = {}) {
  const stageId = String(payload.stageId || payload.stage_id || payload.value || "").trim();
  const approvedPermissions = Array.isArray(payload.approvedPermissions)
    ? payload.approvedPermissions.map((item) => String(item || "").trim()).filter(Boolean)
    : Array.isArray(payload.approved_permissions)
      ? payload.approved_permissions.map((item) => String(item || "").trim()).filter(Boolean)
      : null;
  if (!stageId || approvedPermissions === null) {
    return { ok: false, status: "invalid-payload", actionId, refresh: false, error: "stageId and approvedPermissions are required" };
  }
  try {
    const response = await fetchImpl(
      buildBackendUrl(baseUrl, `/admin/plugins/stages/${encodeURIComponent(stageId)}/install`, params),
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ approved_permissions: approvedPermissions }),
        cache: "no-store"
      }
    );
    const result = await readActionResponse(response);
    const ok = response.ok && Boolean(result?.ok);
    return {
      ...result,
      ok,
      status: String(result?.status || (ok ? "active" : `http-${response.status}`)),
      reason: ok ? "" : pluginInstallFailureDetail(result, response.status),
      actionId,
      stageId,
      refresh: true
    };
  } catch (error) {
    return {
      ok: false,
      status: "request-failed",
      actionId,
      stageId,
      refresh: true,
      error: formatDataSourceError(error)
    };
  }
}

function pluginInstallFailureDetail(result, httpStatus) {
  const status = String(result?.status || "").trim();
  const reason = String(result?.reason || "").trim();
  if (status === "approval_required" || reason === "plugin_permissions_not_approved") {
    return "候选权限已经变化，请重新审查后再安装";
  }
  if (status === "activation_failed") return "新版本未能激活，原有插件状态已经恢复";
  if (status === "persist_failed") return "安装状态未能保存，原有插件状态已经恢复";
  if (status === "rollback_failed") return "安装失败且自动恢复未完成，请检查插件运行状态";
  if (status === "not_found") return "这个暂存候选已经不存在，请重新探测";
  if (httpStatus === 401 || httpStatus === 403) return "此操作只能从受信控制中心执行";
  return reason || `http-${httpStatus}`;
}

async function runPluginStageBackendAction(fetchImpl, baseUrl, actionId, payload = {}, params = {}) {
  const marketStage = actionId === CONTROL_CENTER_ACTIONS.abilitiesPluginStageMarket;
  const pluginId = String(payload.pluginId || "").trim();
  const digest = String(payload.digest || "");
  if (marketStage && (!pluginId || !/^[a-f0-9]{64}$/.test(digest))) {
    return { ok: false, status: "invalid-payload", actionId, refresh: false, error: "reviewed market entry required" };
  }
  const sourcePath = String(payload.path || payload.sourcePath || payload.source_path || payload.wheelPath || payload.wheel_path || "").trim();
  if (!marketStage && !sourcePath) {
    return { ok: false, status: "invalid-payload", actionId, refresh: false, error: "plugin path is required" };
  }
  const sourceStage = actionId === CONTROL_CENTER_ACTIONS.abilitiesPluginStageSource;
  const path = marketStage ? `/admin/plugins/market/${encodeURIComponent(pluginId)}/stage`
    : sourceStage ? "/admin/plugins/stages/source" : "/admin/plugins/stages";
  const body = marketStage ? { digest } : sourceStage ? { source_path: sourcePath } : { wheel_path: sourcePath };
  try {
    const response = await fetchImpl(buildBackendUrl(baseUrl, path, params), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
      cache: "no-store"
    });
    const result = await readActionResponse(response);
    const ok = response.ok && Boolean(result?.ok);
    return {
      ...result,
      ok,
      status: String(result?.status || (ok ? "staged" : `http-${response.status}`)),
      reason: ok ? "" : pluginStageFailureDetail(result, response.status),
      actionId,
      refresh: true
    };
  } catch (error) {
    return {
      ok: false,
      status: "request-failed",
      actionId,
      refresh: true,
      error: formatDataSourceError(error)
    };
  }
}

function pluginStageFailureDetail(result, httpStatus) {
  const reason = String(result?.reason || "").trim();
  const messages = {
    market_selection_changed: "市场版本已经变化，请刷新并重新审查条目",
    market_wheel_digest_mismatch: "下载文件校验不一致，未执行插件代码",
    market_wheel_size_mismatch: "下载文件大小不一致，未执行插件代码",
    market_manifest_mismatch: "插件实际声明与市场条目不一致，候选已丢弃",
    market_wheel_unavailable: "市场制品暂时不可获取，请重试或联系目录维护者",
    ffmpeg_not_found: "未找到 FFmpeg，请按市场条目的依赖说明配置宿主",
    ffprobe_not_found: "未找到 FFprobe，请按市场条目的依赖说明配置宿主",
    plugin_source_unavailable: "找不到这个宿主目录",
    plugin_source_project_required: "目录中缺少可构建的 pyproject.toml",
    plugin_source_build_failed: "插件源码构建失败，请先修正项目",
    plugin_source_tests_required: "插件源码项目需要提供可运行的测试",
    plugin_source_test_failed: "插件项目测试没有通过",
    plugin_probe_failed: "插件未通过隔离激活探测",
    plugin_wheel_unavailable: "找不到这个 wheel 文件",
    plugin_wheel_required: "请选择有效的 .whl 文件"
  };
  if (messages[reason]) return messages[reason];
  if (httpStatus === 401 || httpStatus === 403) return "此操作只能从受信控制中心执行";
  return reason || `http-${httpStatus}`;
}

function pluginActionFailureDetail(result, httpStatus) {
  const status = String(result?.status || "").trim();
  if (status === "activation_failed") {
    return result?.rollback_status && result.rollback_status !== "not_required"
      ? "插件未能切换，宿主已恢复上一有效版本"
      : "插件未能通过激活检查，当前有效版本保持不变";
  }
  if (status === "persist_failed") return "插件状态未能保存，宿主已恢复原状态";
  if (status === "not_found") return "当前 Bot 没有安装这个插件";
  if (httpStatus === 401 || httpStatus === 403) return "此操作只能从受信控制中心执行";
  return String(result?.reason || `http-${httpStatus}`);
}
