/** Program calls keep the client identity; memory storage mapping belongs to the backend. */
export function createPluginServiceClient({ state, platform, scope, persistence, backend }) {
  function bind(capabilityId) {
    return {
      ticket: scope.capture(),
      url: backend.buildBackendEndpointUrl("plugin_service", `/admin/plugins/capabilities/${encodeURIComponent(capabilityId)}/invoke`, {
        user_id: state.sessionId,
        real_user_id: persistence.getProfileUserId(),
        character_pack_id: state.characterPackId,
        client_mode: "desktop_pet",
      }),
    };
  }

  function error(reason, message, content) {
    return Object.assign(new Error(message || reason), { reason, content });
  }

  async function invoke(binding, args) {
    if (!scope.isCurrent(binding.ticket)) throw error("request_scope_changed", "客户端会话已切换。");
    const response = await platform.invoke("backend_admin_request", { request: {
      url: binding.url, method: "POST", body: JSON.stringify(args),
    } });
    if (!scope.isCurrent(binding.ticket)) throw error("request_scope_changed", "客户端会话已切换。");
    let payload;
    try { payload = JSON.parse(response.body); }
    catch { throw error(response.reason || "invalid_service_response", "插件服务未返回有效 JSON。"); }
    if (!response.ok || payload?.ok !== true) {
      const reason = payload?.reason || response.reason || "plugin_service_failed";
      throw error(reason, payload?.content?.message || reason, payload?.content);
    }
    return payload.content;
  }

  return { bind, invoke };
}
