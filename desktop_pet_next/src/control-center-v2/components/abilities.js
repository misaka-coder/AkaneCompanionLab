import { escapeHtml } from "../dom.js";
import { actionPhase } from "./action-button.js";

export function renderAbilities(state) {
  const vm = state.viewModel;
  const abilities = vm?.abilities;
  if (!abilities?.available) {
    return `
      <section class="empty-state glass-panel">
        <span aria-hidden="true">⌁</span>
        <h2>能力目录还没有同步</h2>
        <p>${escapeHtml(vm?.shell?.connected ? "后端已连接，但暂时没有返回能力目录。可以刷新后重试。" : "连接桌宠后，这里会显示真实能力、权限模式和配置状态。")}</p>
        <button class="action-button is-primary" type="button" data-refresh><span>↻</span><b>重新同步</b></button>
      </section>`;
  }

  const pending = ["pressed", "pending"].includes(actionPhase(state, "abilities.approvalPolicy.save"));
  const canChangePolicy = vm.actions["abilities.approvalPolicy.save"]?.available;
  return `
    <section class="ccv2-abilities" aria-labelledby="ccv2-page-title">
      <div class="abilities-heading">
        <div><p class="eyebrow">ABILITIES & PERMISSIONS</p><h2>她现在能做什么</h2><p>这里只展示真实注册的能力和当前权限策略；未接通的能力不会伪装成可用。</p></div>
        <button class="action-button" type="button" data-refresh><span>↻</span><b>刷新能力</b></button>
      </div>

      <section class="abilities-hero glass-panel">
        <div class="availability-ring" style="--availability:${Number(abilities.availability ?? 0)}%" aria-label="能力可用率 ${escapeHtml(String(abilities.availability ?? 0))}%">
          <strong>${escapeHtml(String(abilities.availability ?? "—"))}${abilities.availability == null ? "" : "%"}</strong><small>能力可用率</small>
        </div>
        <div class="abilities-hero-copy"><p class="eyebrow">LIVE CAPABILITY CATALOG</p><h3>${escapeHtml(abilities.note || "能力目录已同步")}</h3><p>模型只能调用宿主真实提供、并通过当前安全策略的能力。</p></div>
        <div class="abilities-stats">${abilities.stats.map((item) => `<span><strong>${escapeHtml(item.value)}</strong><small>${escapeHtml(item.label)}</small></span>`).join("")}</div>
      </section>

      <div class="abilities-layout">
        <div class="abilities-main">
          <section class="abilities-panel glass-panel">
            <div class="abilities-panel-head"><div><p class="eyebrow">CAPABILITY MODULES</p><h3>能力模块</h3></div><span class="mini-chip">${abilities.modules.length} 个模块</span></div>
            ${abilities.modules.length ? `<div class="ability-module-grid">${abilities.modules.map(renderModule).join("")}</div>` : renderInlineEmpty("当前没有可展示的能力模块")}
          </section>
          ${renderCapabilityCenter(state, abilities)}
          ${renderCalls(abilities)}
        </div>

        <aside class="permissions-inspector">
          <section class="permissions-card glass-panel">
            <div class="abilities-panel-head"><div><p class="eyebrow">APPROVAL POLICY</p><h3>权限模式</h3></div><span class="policy-status">${escapeHtml(abilities.safetyStatus)}</span></div>
            <p class="policy-summary">两组开关覆盖真实会产生影响的动作；搜索、读取公开页面、查看记忆和加载能力说明不需要审批。</p>
            <div class="permission-families">
              ${abilities.policy.families.map((family) => renderPolicyFamily(family, pending, canChangePolicy)).join("")}
            </div>
            <div class="hard-boundary-note"><i>✓</i><span><strong>硬安全边界始终保留</strong><small>完全访问不会跳过路径、密钥、URL 和本地边界校验。</small></span></div>
          </section>
          <section class="permissions-card glass-panel">
            <div class="abilities-panel-head"><div><p class="eyebrow">SAFETY STATUS</p><h3>当前保护状态</h3></div></div>
            ${abilities.safetyItems.length ? `<dl class="safety-list">${abilities.safetyItems.map((item) => `<div><dt>${escapeHtml(item.label)}</dt><dd>${escapeHtml(item.value)}</dd></div>`).join("")}</dl>` : renderInlineEmpty("安全状态尚未同步")}
          </section>
          ${renderApprovalQueue(state, abilities)}
        </aside>
      </div>
    </section>`;
}

function renderPolicyFamily(family, pending, available) {
  return `<section class="permission-family"><div><strong>${escapeHtml(family.label)}</strong><small>${escapeHtml(family.summary)}</small></div><div class="policy-options" role="group" aria-label="${escapeHtml(family.label)}审批策略">${family.availableModes.map((mode) => renderPolicyOption(mode, family.mode, pending, available, family.id)).join("")}</div></section>`;
}

function renderPolicyOption(mode, currentMode, pending, available, familyId = "") {
  const selected = mode.id === currentMode;
  return `<button class="policy-option${selected ? " is-selected" : ""}" type="button" data-approval-mode="${escapeHtml(mode.id)}" data-approval-family="${escapeHtml(familyId)}" aria-pressed="${selected ? "true" : "false"}"${selected || pending || !available ? " disabled" : ""}><span><strong>${escapeHtml(mode.label)}</strong><small>${escapeHtml(mode.summary)}</small></span><i>${selected ? "当前" : pending ? "保存中" : "选择"}</i></button>`;
}

function renderModule(item) {
  return `<article class="ability-module" data-tone="${escapeHtml(item.tone)}"><span class="ability-module-mark">${moduleGlyph(item.tone)}</span><div><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.description)}</p><small>${escapeHtml(item.permission)}</small></div><span class="module-state is-${escapeHtml(item.statusTone)}">${escapeHtml(item.statusLabel)}</span><em>${escapeHtml(item.count)}</em></article>`;
}

function renderCapabilityCenter(state, abilities) {
  const hasSkills = abilities.skills.status !== "unavailable" || abilities.skills.entries.length > 0;
  const total = abilities.providers.length + abilities.mcpServers.length + abilities.workflows.length + (hasSkills ? 1 : 0);
  if (!total) return "";
  return `<section class="abilities-panel capability-center glass-panel">
    <div class="abilities-panel-head"><div><p class="eyebrow">CAPABILITY CONNECTIONS</p><h3>服务与扩展</h3></div><span class="mini-chip">${total} 个配置入口</span></div>
    <p class="capability-center-intro">常用状态一眼确认，地址、命令与工作流绑定按需展开；保存后会重新读取真实运行状态。</p>
    ${hasSkills ? renderSkillLibrary(state, abilities.skills) : ""}
    <div class="capability-config-stack">
      ${abilities.providers.map((item) => renderProviderConfig(state, item)).join("")}
      ${abilities.mcpServers.map((item) => renderMcpConfig(state, item)).join("")}
      ${abilities.workflows.map((item) => renderWorkflowConfig(state, item)).join("")}
    </div>
  </section>`;
}

function renderSkillLibrary(state, skills) {
  const phase = actionPhase(state, "abilities.skills.openFolder");
  const pending = ["pressed", "pending"].includes(phase);
  const visibleEntries = skills.entries;
  return `<section class="skill-library-card">
    <div class="skill-library-head">
      <span class="capability-summary-mark" aria-hidden="true">⌘</span>
      <div><small>PROGRESSIVE SKILLS</small><strong>Skill 操作手册</strong><p>常驻上下文只显示名称与用途，任务匹配时模型才加载完整说明。</p></div>
      <button class="action-button" type="button" data-action="abilities.skills.openFolder"${pending ? " disabled" : ""}><span>▱</span><b>${pending ? "打开中" : "用户 Skill 目录"}</b></button>
    </div>
    <div class="skill-library-stats"><span><strong>${skills.total}</strong><small>当前可用</small></span><span><strong>${skills.bundled}</strong><small>随版本内置</small></span><span><strong>${skills.managed}</strong><small>用户安装</small></span><em>目录版本 ${escapeHtml(skills.catalogRevision || "待同步")}</em></div>
    ${visibleEntries.length ? `<div class="skill-library-list">${visibleEntries.map((item) => `<article><div><strong>${escapeHtml(item.name)}</strong><span class="skill-source is-${escapeHtml(item.source)}">${item.source === "managed" ? "用户" : "内置"}</span></div><p>${escapeHtml(item.description)}</p><small>${item.requiredTools.length ? `需要工具：${escapeHtml(item.requiredTools.join(" · "))}` : "无额外工具要求"}${item.resourceCount ? ` · ${item.resourceCount} 个附加资源` : ""}</small></article>`).join("")}</div>` : renderInlineEmpty("当前没有已安装的 Skill")}
    ${skills.diagnostics.length ? `<div class="skill-diagnostics"><strong>热重载诊断</strong>${skills.diagnostics.map((item) => `<span>${escapeHtml(item.name || "未知 Skill")} · ${escapeHtml(item.fallback === "last_good" ? "继续使用上一有效版本" : "无效更新未加载")} · ${escapeHtml(item.reason)}</span>`).join("")}</div>` : ""}
    <p class="skill-library-note">每个子目录需要包含合法的 SKILL.md。复制或修改后无需重启；下一次模型请求会自动读取新目录。Skill 不会获得额外权限。</p>
  </section>`;
}

function renderProviderConfig(state, item) {
  const savePhase = actionPhase(state, "abilities.provider.config.save");
  const healthPhase = actionPhase(state, "abilities.provider.healthCheck");
  const pending = [savePhase, healthPhase].some((phase) => ["pressed", "pending"].includes(phase));
  return `<details class="capability-config-card" data-capability-kind="provider" data-capability-key="provider:${escapeHtml(item.id)}">
    <summary>${capabilitySummary("◉", "本地服务", item.title, item.statusLabel, item.statusTone, item.reason || item.description)}</summary>
    <form class="capability-config-form" data-capability-form="provider" data-provider-id="${escapeHtml(item.id)}">
      <label class="capability-toggle"><input type="checkbox" name="enabled"${item.enabled ? " checked" : ""}><span><strong>启用服务</strong><small>${escapeHtml(item.usedByLabel || "供已绑定的能力使用")}</small></span></label>
      <label class="capability-field"><span>服务地址</span><input name="endpoint" type="url" value="${escapeHtml(item.endpoint || item.defaultEndpoint)}" placeholder="http://127.0.0.1:..." autocomplete="off"></label>
      <div class="capability-form-actions">
        <button class="action-button" type="submit" data-action="abilities.provider.healthCheck"${pending || !item.actionsEnabled ? " disabled" : ""}><span>⌁</span><b>${healthPhase === "pending" ? "检查中" : "检查连接"}</b></button>
        <button class="action-button is-primary" type="submit" data-action="abilities.provider.config.save"${pending || !item.actionsEnabled ? " disabled" : ""}><span>✓</span><b>${savePhase === "pending" ? "保存中" : "保存配置"}</b></button>
      </div>
    </form>
  </details>`;
}

function renderMcpConfig(state, item) {
  const savePhase = actionPhase(state, "abilities.mcp.config.save");
  const discoverPhase = actionPhase(state, "abilities.mcp.discover");
  const lifecycleActions = ["enable", "disable", "restart", "remove"];
  const lifecyclePending = lifecycleActions.some((action) => ["pressed", "pending"].includes(actionPhase(state, `abilities.mcp.${action}`)));
  const pending = [savePhase, discoverPhase].some((phase) => ["pressed", "pending"].includes(phase)) || lifecyclePending;
  const tools = item.safeToolLabels.length ? item.safeToolLabels : ["等待工具发现"];
  const isRemote = item.executionLocation === "remote" || item.transport === "streamable_http";
  const locationLabel = item.executionLocationLabel || (isRemote ? "远程服务" : "Akane Host");
  const locationDetail = item.executionLocationDetail || (isRemote
    ? "通过网络连接独立 MCP 服务。"
    : "MCP 进程由 Akane Host 启动。Host 在本机时运行于本机，在云端时运行于云端。");
  const configFields = isRemote
    ? `<div class="capability-toggle"><span><strong>${item.enabled ? "远程 MCP 已启用" : "远程 MCP 未启用"}</strong><small>远程地址与认证不会在控制中心回显或替换</small></span></div>
      <p class="capability-caution">运行位置：${escapeHtml(locationLabel)}。${escapeHtml(locationDetail)}远程配置请通过受信配置文件或后端配置接口管理。</p>`
    : `<label class="capability-toggle"><input type="checkbox" name="enabled"${item.enabled ? " checked" : ""}><span><strong>启用 MCP 服务</strong><small>发现到的工具仍受当前审批策略约束</small></span></label>
      <label class="capability-field"><span>Host 启动命令</span><input name="command" value="" placeholder="${escapeHtml(item.commandName || "例如 npx / python")}" autocomplete="off"></label>
      <div class="capability-field-grid">
        <label class="capability-field"><span>显示名称</span><input name="displayName" value="${escapeHtml(item.title)}" autocomplete="off"></label>
        <label class="capability-field"><span>Host 工作目录 <small>可选</small></span><input name="cwd" value="" placeholder="留空则使用 Host 默认目录" autocomplete="off"></label>
      </div>
      <label class="capability-field"><span>启动参数 <small>每行一个</small></span><textarea name="args" rows="2" placeholder="例如：&#10;-m&#10;my_mcp_server"></textarea></label>
      <p class="capability-caution">运行位置：${escapeHtml(locationLabel)}。${escapeHtml(locationDetail)}安全起见不回显完整 Host 命令；只有填写新命令并保存时才会替换现有配置。</p>`;
  return `<details class="capability-config-card" data-capability-kind="mcp" data-capability-key="mcp:${escapeHtml(item.serverId)}">
    <summary>${capabilitySummary("◇", "MCP 扩展", item.title, item.statusLabel, item.statusTone, item.reason || item.lastDiscoveryLabel)}</summary>
    <form class="capability-config-form" data-capability-form="mcp" data-server-id="${escapeHtml(item.serverId)}">
      <div class="capability-tool-row">${tools.map((label) => `<span>${escapeHtml(label)}</span>`).join("")}<em>${item.toolCount} 个工具 · ${escapeHtml(item.approvalLabel || "遵循权限策略")} · ${escapeHtml(locationLabel)}</em></div>
      ${configFields}
      <div class="capability-form-actions">
        <button class="action-button" type="submit" data-action="abilities.mcp.discover"${pending || !item.actionsEnabled || !item.configured ? " disabled" : ""}><span>↻</span><b>${discoverPhase === "pending" ? "发现中" : "发现工具"}</b></button>
        <button class="action-button" type="submit" data-action="abilities.mcp.${item.enabled ? "disable" : "enable"}"${pending || !item.actionsEnabled || !item.configured ? " disabled" : ""}><span>${item.enabled ? "Ⅱ" : "▷"}</span><b>${item.enabled ? "停用连接" : "启用连接"}</b></button>
        <button class="action-button" type="submit" data-action="abilities.mcp.restart"${pending || !item.actionsEnabled || !item.configured || !item.enabled ? " disabled" : ""}><span>↻</span><b>重启会话</b></button>
        <button class="action-button" type="submit" data-action="abilities.mcp.remove"${pending || !item.actionsEnabled || !item.configured ? " disabled" : ""} title="只移除 Akane 连接，不卸载外部软件"><span>×</span><b>移除连接</b></button>
        ${isRemote ? "" : `<button class="action-button is-primary" type="submit" data-action="abilities.mcp.config.save"${pending || !item.actionsEnabled ? " disabled" : ""}><span>✓</span><b>${savePhase === "pending" ? "保存中" : "替换配置"}</b></button>`}
      </div>
    </form>
  </details>`;
}

function renderWorkflowConfig(state, item) {
  const savePhase = actionPhase(state, "abilities.workflow.config.save");
  const validatePhase = actionPhase(state, "abilities.workflow.validate");
  const pending = [savePhase, validatePhase].some((phase) => ["pressed", "pending"].includes(phase));
  return `<details class="capability-config-card" data-capability-kind="workflow" data-capability-key="workflow:${escapeHtml(item.workflowId)}">
    <summary>${capabilitySummary("↳", "本地工作流", item.title, item.statusLabel, item.statusTone, item.detail)}</summary>
    <form class="capability-config-form" data-capability-form="workflow" data-workflow-id="${escapeHtml(item.workflowId)}">
      <label class="capability-toggle"><input type="checkbox" name="enabled"${item.enabled ? " checked" : ""}><span><strong>启用工作流</strong><small>${item.executionReady ? "已具备执行条件" : "保存后请先验证绑定"}</small></span></label>
      <label class="capability-field"><span>工作流文件</span><input name="workflowPath" value="${escapeHtml(item.workflowPath || item.defaultWorkflowPath)}" placeholder="选择或粘贴工作流文件路径" autocomplete="off"></label>
      <div class="capability-field-grid">
        <label class="capability-field"><span>输入图像槽位</span><input name="inputImageSlot" value="${escapeHtml(item.inputImageSlot)}" placeholder="input_image"></label>
        <label class="capability-field"><span>输出图像槽位</span><input name="outputImageSlot" value="${escapeHtml(item.outputImageSlot)}" placeholder="output_image"></label>
      </div>
      <div class="capability-form-actions">
        <button class="action-button" type="submit" data-action="abilities.workflow.validate"${pending || !item.actionsEnabled || !item.configured ? " disabled" : ""}><span>⌁</span><b>${validatePhase === "pending" ? "验证中" : "验证绑定"}</b></button>
        <button class="action-button is-primary" type="submit" data-action="abilities.workflow.config.save"${pending || !item.actionsEnabled ? " disabled" : ""}><span>✓</span><b>${savePhase === "pending" ? "保存中" : "保存绑定"}</b></button>
      </div>
    </form>
  </details>`;
}

function capabilitySummary(glyph, group, title, status, tone, detail) {
  return `<span class="capability-summary-mark" aria-hidden="true">${glyph}</span><span class="capability-summary-copy"><small>${escapeHtml(group)}</small><strong>${escapeHtml(title)}</strong><em>${escapeHtml(detail || "展开查看配置")}</em></span><span class="module-state is-${escapeHtml(tone)}">${escapeHtml(status)}</span><i aria-hidden="true">⌄</i>`;
}

function renderApprovalQueue(state, abilities) {
  if (!abilities.approvalRequests.length) return "";
  const phase = actionPhase(state, "abilities.approvalRequest.decide");
  const pending = ["pressed", "pending"].includes(phase);
  return `<section class="permissions-card approval-queue glass-panel">
    <div class="abilities-panel-head"><div><p class="eyebrow">PENDING APPROVALS</p><h3>等待你的确认</h3></div><span class="policy-status is-attention">${abilities.approvalRequests.length} 项</span></div>
    <div class="approval-request-list">${abilities.approvalRequests.map((item) => `<article><span class="risk-dot is-${escapeHtml(item.risk)}"></span><div><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.summary || `${item.requestedBy} 请求执行这项能力`)}</p></div><div class="approval-request-actions"><button type="button" data-approval-request="${escapeHtml(item.requestId)}" data-decision="denied"${pending ? " disabled" : ""}>拒绝</button><button class="is-approve" type="button" data-approval-request="${escapeHtml(item.requestId)}" data-decision="approved"${pending ? " disabled" : ""}>允许</button></div></article>`).join("")}</div>
  </section>`;
}

function renderCalls(abilities) {
  if (!abilities.calls.length) return "";
  return `<section class="abilities-panel glass-panel"><div class="abilities-panel-head"><div><p class="eyebrow">RUNTIME STATUS</p><h3>最近状态</h3></div><span class="mini-chip">真实诊断</span></div><div class="ability-call-list">${abilities.calls.map((item) => `<div><time>${escapeHtml(item.time || "—")}</time><span><strong>${escapeHtml(item.module || "运行状态")}</strong><small>${escapeHtml(item.description)}</small></span><em>${escapeHtml(item.status || item.method)}</em></div>`).join("")}</div></section>`;
}

function renderInlineEmpty(label) {
  return `<div class="empty-inline"><span>${escapeHtml(label)}</span><small>刷新后仍为空时，请检查本地服务配置。</small></div>`;
}

function moduleGlyph(tone) {
  return { blue: "◌", orange: "▱", purple: "◇", green: "♪", pink: "✦" }[tone] || "·";
}
