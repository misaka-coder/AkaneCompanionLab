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
          ${renderToolExposure(abilities.toolExposure)}
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

export function renderToolExposure(runtime = {}) {
  const { data, phase = "idle", error = "", notice = "" } = runtime;
  const pending = phase === "saving" || phase === "loading";
  const ready = phase === "ready" && data?.ok;
  const disabled = !ready ? " disabled" : "";
  const labels = { resident: "常驻原生", on_demand: "按需加载", not_published: "待下一轮发布" };
  const modeButtons = (attributes, mode) => ["resident", "on_demand"].map(value =>
    `<button type="button" class="exposure-mode${mode === value ? " is-selected" : ""}" data-exposure-mode="${value}" ${attributes} aria-pressed="${mode === value}"${disabled || (mode === value ? " disabled" : "")}>${labels[value]}</button>`).join("");
  const groups = new Map();
  for (const tool of data?.tools || []) {
    if (!groups.has(tool.source)) groups.set(tool.source, []);
    groups.get(tool.source).push(tool);
  }
  const boundary = data?.boundarySupported
    ? "常驻、按需和搜索设置从下一次模型请求生效，无需等待记忆压缩。设置不变时，安装升级的原生声明在压缩时合并；新工具可先加载调用。"
    : data?.backend && data.backend !== "memcore"
      ? `${data.backend} 模式不支持按 MemCore 压缩边界合并；暴露偏好在后续请求即时呈现。`
      : "MemCore 权威投影暂不可用，当前原生暴露状态无法确认。";
  const problem = error === "tool_exposure_revision_conflict"
    ? "偏好已在别处修改。请刷新后重新选择，避免覆盖新设置。" : error;
  return `<section class="abilities-panel glass-panel tool-exposure-panel" data-tool-exposure-phase="${escapeHtml(phase)}">
    <div class="abilities-panel-head"><div><p class="eyebrow">TOOL EXPOSURE</p><h3>工具常驻与按需</h3></div><button class="action-button" type="button" data-exposure-refresh${pending ? " disabled" : ""}>${phase === "saving" ? "保存中…" : phase === "loading" ? "读取中…" : "刷新状态"}</button></div>
    <p class="capability-center-intro">常驻工具直接调用；按需工具在简表中可见，可按精确 ID 加载完整契约。此设置与执行权限独立。</p>
    <p class="exposure-boundary">${escapeHtml(boundary)}</p>
    ${notice ? `<p class="exposure-notice" role="status">${escapeHtml(notice)}</p>` : ""}
    ${problem ? `<p class="exposure-error" role="alert">${escapeHtml(problem)}</p>` : ""}
    ${data?.preferences ? `<div class="exposure-defaults"><div><strong>宿主／插件默认方式</strong><small>已有单独选择优先，MCP 保留 pinned 工具映射。${data.preferences.defaultSource === "legacy_g1" ? "默认按需来自旧 G1 配置；保存后使用新偏好。" : ""}</small><span class="exposure-options">${modeButtons('data-exposure-default="true"', data.preferences.defaultMode)}</span></div><div><strong>可选目录搜索</strong><small>关闭后仍可分页浏览和精确加载。${data.searchPending ? "搜索入口待下一轮生效。" : ""}</small><button type="button" class="exposure-mode" data-exposure-search="${!data.preferences.searchEnabled}"${disabled}>${data.preferences.searchEnabled ? "关闭搜索入口" : "启用搜索入口"}</button></div></div>` : ""}
    <div class="exposure-groups">${[...groups].map(([source, tools]) => `<details class="exposure-group" open><summary><strong>${escapeHtml(source)}</strong><span>${tools.length} 个工具</span></summary><div class="exposure-batch"><small>整组目标偏好</small><span class="exposure-options">${modeButtons(`data-exposure-source="${escapeHtml(source)}"`, "")}</span></div>${tools.map(tool => `<article class="exposure-tool"><div class="exposure-tool-name"><strong>${escapeHtml(tool.name)}</strong><code>${escapeHtml(tool.id)}</code><p>${escapeHtml(tool.description)}</p></div><div class="exposure-tool-state"><span>执行资格：${tool.executionAvailable ? "可用，调用时校验权限" : "当前不可用"}</span><span>本配置声明：${escapeHtml(labels[tool.currentMode] || "未知")}${tool.currentMode === "resident" && tool.nativeContractValid === false ? " · 契约已失效，需重新加载" : ""}</span><span class="${tool.pending ? "exposure-pending" : ""}">目标偏好：${escapeHtml(labels[tool.targetMode] || "未知")}${tool.pending ? (tool.pendingReason === "next_request" ? " · 待下一轮生效" : " · 声明待压缩合并") : ""}</span><span class="exposure-options">${modeButtons(`data-exposure-id="${escapeHtml(tool.id)}"`, tool.targetMode)}</span></div></article>`).join("")}</details>`).join("")}</div>
    ${data?.ok && !groups.size ? '<p class="execution-policy-empty">当前没有模型可见的业务工具。提示词、Skill 和程序服务的设置独立保留。</p>' : ""}
  </section>`;
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
  const hasPluginCatalog = abilities.plugins?.status !== "not-requested";
  const hasExecutionRuntime = abilities.execution?.available === true;
  const total = abilities.providers.length + abilities.mcpServers.length + abilities.workflows.length + (hasSkills ? 1 : 0) + (hasPluginCatalog ? 1 : 0) + (hasExecutionRuntime ? 1 : 0);
  if (!total) return "";
  return `<section class="abilities-panel capability-center glass-panel">
    <div class="abilities-panel-head"><div><p class="eyebrow">CAPABILITY CONNECTIONS</p><h3>服务与扩展</h3></div><span class="mini-chip">${total} 个配置入口</span></div>
    <p class="capability-center-intro">常用状态一眼确认，地址、命令与工作流绑定按需展开；保存后会重新读取真实运行状态。</p>
    ${renderExecutionPolicies(abilities.execution)}
    ${hasPluginCatalog ? renderPluginLibrary(state, abilities.plugins) : ""}
    ${hasSkills ? renderSkillLibrary(state, abilities.skills) : ""}
    <div class="capability-config-stack">
      ${abilities.providers.map((item) => renderProviderConfig(state, item)).join("")}
      ${abilities.mcpServers.map((item) => renderMcpConfig(state, item)).join("")}
      ${abilities.workflows.map((item) => renderWorkflowConfig(state, item)).join("")}
    </div>
  </section>`;
}

function renderExecutionPolicies(execution) {
  const current = execution && typeof execution === "object" ? execution : {};
  const policy = current.policy && typeof current.policy === "object" ? current.policy : {};
  const resource = current.resource && typeof current.resource === "object" ? current.resource : {};
  const available = policy.available === true || resource.available === true;
  const status = available ? "available" : "unavailable";
  const policyRows = policy.entries?.length
    ? policy.entries.map((entry) => `<div class="execution-policy-row"><span><strong>${escapeHtml(entry.policyId || entry.serviceId || "未命名策略")}</strong><small>${escapeHtml(entry.stages.length ? `阶段：${entry.stages.join("、")}` : "没有可用回调阶段")}</small></span><em class="is-${escapeHtml(entry.status || "unavailable")}">${escapeHtml(executionStatusLabel(entry.status))}</em></div>`).join("")
    : `<p class="execution-policy-empty">${policy.available ? "当前没有选中的执行策略。" : "执行策略快照暂不可用。"}</p>`;
  const limitRows = resource.limits?.length
    ? resource.limits.map((limit) => `<div class="execution-limit-row"><span>${escapeHtml(executionLimitLabel(limit.key))}</span><strong>${escapeHtml(formatExecutionLimit(limit.key, limit.value))}</strong><small>${escapeHtml(limit.source === "deployment_config" ? "部署配置" : "默认值")}</small></div>`).join("")
    : `<p class="execution-policy-empty">资源限额快照暂不可用。</p>`;
  return `<section class="execution-policy-card" data-execution-policy-status="${status}">
    <div class="execution-policy-head"><div><p class="eyebrow">EXECUTION GUARDRAILS</p><h4>执行策略与资源限额</h4><p>这里显示当前宿主真正生效的策略和额度；启动级部署配置需要在宿主配置中修改。</p></div><span class="policy-status${available ? "" : " is-attention"}">${available ? "已同步" : "待同步"}</span></div>
    <div class="execution-policy-grid">
      <div class="execution-policy-column"><div class="execution-policy-column-head"><strong>策略服务</strong><small>${escapeHtml(policy.status === "available" ? `${policy.entries.length} 项已选` : "状态不可用")}</small></div><div class="execution-policy-list">${policyRows}</div></div>
      <div class="execution-policy-column"><div class="execution-policy-column-head"><strong>部署额度</strong><small>${escapeHtml(resource.status === "available" ? "共享调用预算" : "状态不可用")}</small></div><div class="execution-limit-list">${limitRows}</div></div>
    </div>
  </section>`;
}

function executionStatusLabel(status) {
  return { available: "可用", unavailable: "不可用", failed: "失败" }[status] || "待检查";
}

function executionLimitLabel(key) {
  return {
    max_input_bytes: "单次输入大小",
    max_dependency_depth: "依赖调用深度",
    max_dependency_calls: "依赖调用次数"
  }[key] || key;
}

function formatExecutionLimit(key, value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "不限";
  const number = Number(value);
  if (key === "max_input_bytes") {
    if (number >= 1024 * 1024) return `${(number / (1024 * 1024)).toFixed(number % (1024 * 1024) ? 1 : 0)} MB`;
    if (number >= 1024) return `${(number / 1024).toFixed(number % 1024 ? 1 : 0)} KB`;
    return `${number} B`;
  }
  return `${number}${key === "max_dependency_depth" ? " 层" : " 次"}`;
}


function renderPluginLibrary(state, plugins) {
  const statusCopy = plugins.status === "loading"
    ? "正在同步已安装插件…"
    : plugins.status === "unavailable"
      ? `插件目录暂不可用${plugins.reason ? `：${escapeHtml(plugins.reason)}` : ""}`
      : "当前没有已安装插件";
  return `<section class="plugin-library-card" data-plugin-catalog-status="${escapeHtml(plugins.status)}">
    <div class="plugin-library-head">
      <span class="capability-summary-mark" aria-hidden="true">◇</span>
      <div><small>INSTALLED PLUGINS</small><strong>插件</strong><p>安装与适用渠道分离；这里只展示宿主确认过的运行状态和贡献。</p></div>
      <span class="plugin-library-count"><strong>${plugins.active}</strong><small>运行中 / ${plugins.total}</small></span>
    </div>
    ${renderPluginDependencySupply(plugins)}
    ${renderPluginMarket(state, plugins)}
    ${renderPluginStaging(state, plugins)}
    ${plugins.stages.length ? `<div class="plugin-stage-list">${plugins.stages.map((stage) => renderPluginStage(state, stage)).join("")}</div>` : ""}
    ${plugins.entries.length
      ? `<div class="plugin-library-list">${plugins.entries.map((plugin) => renderPluginCard(state, plugin)).join("")}</div>`
      : `<div class="plugin-library-empty is-${escapeHtml(plugins.status)}">${statusCopy}</div>`}
  </section>`;
}

function renderPluginDependencySupply(plugins) {
  const supply = plugins.dependencySupply;
  if (!supply) return "";
  const detail = supply.kind === "host_only"
    ? "插件只使用宿主自带依赖；第三方运行依赖不会自动联网下载。"
    : supply.kind === "wheelhouse"
      ? "第三方依赖从已配置的离线 wheelhouse 安装到插件隔离环境。"
      : supply.kind === "package_index"
        ? "第三方依赖从受控包索引安装到插件隔离环境。"
        : "插件第三方依赖使用宿主声明的供应方式。";
  return `<div class="plugin-dependency-supply is-${escapeHtml(supply.tone)}" data-plugin-dependency-status="${escapeHtml(supply.status || "unknown")}" role="status"><span class="capability-summary-mark" aria-hidden="true">↗</span><div><small>DEPENDENCY SUPPLY</small><strong>${escapeHtml(supply.kindLabel)}</strong><p>${escapeHtml(detail)}</p></div><em>${escapeHtml(supply.statusLabel)}</em></div>`;
}

function renderPluginMarket(state, plugins) {
  const market = plugins.market;
  if (!market || market.status === "not-requested") return "";
  const pending = ["stageMarket", "stageSource", "stageWheel", "install", "discardStage"]
    .some((name) => ["pressed", "pending"].includes(actionPhase(state, `abilities.plugin.${name}`)));
  const available = state.viewModel?.actions?.["abilities.plugin.stageMarket"]?.available;
  const feedback = state.actionStates?.["abilities.plugin.stageMarket"];
  const reason = market.reason === "market_index_unavailable"
    ? "尚无可读取的市场目录；管理员可构建本地市场或配置可信 HTTPS 目录。"
    : market.reason === "not-supported" ? "当前后端尚不支持市场浏览。" : market.reason;
  return `<details class="plugin-stage-panel" data-plugin-market-status="${escapeHtml(market.status)}" data-capability-key="plugin:market" open>
    <summary><span><strong>可选插件市场</strong><small>${market.sourceKind === "https" ? "HTTPS 目录" : market.sourceKind === "local_release" ? "本地发行目录" : "分发目录"}</small></span><i aria-hidden="true">⌄</i></summary>
    <div class="capability-config-form">
    <p class="plugin-stage-note">先获取并校验制品，再审查真实贡献和权限。插件是可信 Python 代码；哈希校验不等于安全沙箱。系统依赖需要在宿主准备。</p>
    ${feedback ? `<p class="plugin-stage-note" role="status">${escapeHtml(feedback.detail || feedback.label || "正在获取并检查…")}</p>` : ""}
    ${market.entries.length ? `<div class="plugin-stage-list">${market.entries.map((entry) => `<article class="plugin-stage-item">
      <div class="plugin-stage-heading"><span><strong>${escapeHtml(entry.displayName)}</strong><small>${escapeHtml(entry.pluginId)} · v${escapeHtml(entry.version)} · ${(entry.sizeBytes / 1024).toFixed(1)} KiB</small></span><em>${entry.installedVersion ? `已安装 v${escapeHtml(entry.installedVersion)}` : "未安装"}</em></div>
      <p>${escapeHtml(entry.summary)}</p>
      <div class="plugin-detail-section"><strong>运行依赖</strong>${entry.requirements.map((requirement) => `<p>${escapeHtml(requirement)}</p>`).join("")}</div>
      <div class="plugin-permission-list">${entry.permissions.map((permission) => `<code>${escapeHtml(permission)}</code>`).join("")}</div>
      <details class="plugin-card-details"><summary><span>SHA-256 制品校验</span><i aria-hidden="true">⌄</i></summary><div class="plugin-permission-list"><code>${escapeHtml(entry.digest.slice(0, 32))}<wbr>${escapeHtml(entry.digest.slice(32))}</code></div></details>
      <div class="plugin-stage-confirm"><small>获取会隔离探测，但还不会启用。</small><button class="action-button is-primary" type="button" data-plugin-market-stage="${escapeHtml(entry.pluginId)}"${pending || !available ? " disabled" : ""}><span>↓</span><b>${pending ? "处理中" : "获取并检查"}</b></button></div>
    </article>`).join("")}</div>` : `<p class="plugin-library-empty">${market.status === "loading" ? "正在读取市场目录…" : market.status === "available" ? "此目录当前没有可选插件" : `市场暂不可用：${escapeHtml(reason || "请刷新重试")}`}</p>`}
    </div>
  </details>`;
}

function renderPluginStaging(state, plugins) {
  const sourcePending = ["pressed", "pending"].includes(actionPhase(state, "abilities.plugin.stageSource"));
  const wheelPending = ["pressed", "pending"].includes(actionPhase(state, "abilities.plugin.stageWheel"));
  const installPending = ["pressed", "pending"].includes(actionPhase(state, "abilities.plugin.install"));
  const discardPending = ["pressed", "pending"].includes(actionPhase(state, "abilities.plugin.discardStage"));
  const pickSourcePending = ["pressed", "pending"].includes(actionPhase(state, "abilities.plugin.pickSource"));
  const pickWheelPending = ["pressed", "pending"].includes(actionPhase(state, "abilities.plugin.pickWheel"));
  const pending = sourcePending || wheelPending || installPending || discardPending || pickSourcePending || pickWheelPending
    || ["pressed", "pending"].includes(actionPhase(state, "abilities.plugin.stageMarket"));
  const sourceAvailable = state.viewModel?.actions?.["abilities.plugin.stageSource"]?.available;
  const wheelAvailable = state.viewModel?.actions?.["abilities.plugin.stageWheel"]?.available;
  const pickerAvailable = plugins.localPickerAvailable;
  const managementCopy = plugins.managementStatus === "loading"
    ? "正在连接插件管理服务…"
    : plugins.managementAvailable
      ? pickerAvailable
        ? "选择本机源码目录或 wheel；暂存只构建并隔离探测，不会启用代码。"
        : "当前连接远端 Bot，请填写远端宿主可以读取的路径。"
      : "插件管理服务当前不可用，已安装插件仍可正常查看。";
  return `<details class="plugin-stage-panel" data-capability-key="plugin:installer">
    <summary><span><strong>安装本地插件</strong><small>${escapeHtml(managementCopy)}</small></span><i aria-hidden="true">⌄</i></summary>
    <form class="capability-config-form" data-capability-form="plugin-stage">
      <div class="plugin-path-entry">
        <label class="capability-field"><span>${pickerAvailable ? "本机路径" : "远端宿主路径"}</span><input name="pluginPath" value="" placeholder="插件源码目录或 .whl 文件" autocomplete="off"></label>
        ${pickerAvailable ? `<div class="plugin-path-pickers"><button class="action-button" type="button" data-plugin-path-picker="abilities.plugin.pickSource"${pending ? " disabled" : ""}><span>▱</span><b>${pickSourcePending ? "选择中" : "选择源码目录"}</b></button><button class="action-button" type="button" data-plugin-path-picker="abilities.plugin.pickWheel"${pending ? " disabled" : ""}><span>◫</span><b>${pickWheelPending ? "选择中" : "选择 wheel"}</b></button></div>` : ""}
      </div>
      <p class="plugin-stage-note">源码目录需要包含 pyproject.toml 和可独立运行的测试；wheel 与源码最终经过同一套探测。</p>
      <div class="capability-form-actions">
        <button class="action-button" type="submit" data-action="abilities.plugin.stageWheel"${pending || !wheelAvailable ? " disabled" : ""}><span>◫</span><b>${wheelPending ? "探测中" : "检查 wheel"}</b></button>
        <button class="action-button is-primary" type="submit" data-action="abilities.plugin.stageSource"${pending || !sourceAvailable ? " disabled" : ""}><span>⌁</span><b>${sourcePending ? "构建并探测中" : "检查源码"}</b></button>
      </div>
    </form>
  </details>`;
}

function renderPluginStage(state, stage) {
  const installPending = ["pressed", "pending"].includes(actionPhase(state, "abilities.plugin.install"));
  const discardPending = ["pressed", "pending"].includes(actionPhase(state, "abilities.plugin.discardStage"));
  const discardAvailable = state.viewModel?.actions?.["abilities.plugin.discardStage"]?.available;
  if (!stage.ok) {
    return `<article class="plugin-stage-item is-invalid"><div><strong>无效暂存候选</strong><small>${escapeHtml(stage.reason || "候选数据不可读取")}</small></div><div class="plugin-stage-confirm"><small>可以安全移除这份未发布候选。</small>${renderPluginStageDiscardButton(stage, installPending || discardPending, discardAvailable)}</div></article>`;
  }
  const groups = pluginContributionGroups(stage.contributions);
  const installAvailable = state.viewModel?.actions?.["abilities.plugin.install"]?.available;
  return `<article class="plugin-stage-item">
    <div class="plugin-stage-heading"><span><strong>${escapeHtml(stage.pluginId)}</strong><small>${stage.version ? `v${escapeHtml(stage.version)}` : escapeHtml(stage.distributionName || "已通过探测")}</small></span><em>待确认</em></div>
    <p>${stage.contributionCount ? `${stage.contributionCount} 项贡献已通过隔离探测` : "插件没有声明面向模型或渠道的贡献"}</p>
    ${groups.length ? `<div class="plugin-contribution-list">${renderPluginContributionGroups(groups, stage.contributions)}</div>` : ""}
    <div class="plugin-detail-section"><strong>申请权限</strong>${stage.permissions.length
      ? `<div class="plugin-permission-list">${stage.permissions.map((item) => `<code>${escapeHtml(item)}</code>`).join("")}</div>`
      : `<p>没有申请额外权限。</p>`}</div>
    <div class="plugin-stage-confirm"><small>确认后宿主才会发布制品并原子切换插件代次。</small><span class="plugin-stage-actions">${renderPluginStageDiscardButton(stage, installPending || discardPending, discardAvailable)}<button class="action-button is-primary" type="button" data-plugin-stage-install="${escapeHtml(stage.stageId)}"${installPending || discardPending || !installAvailable ? " disabled" : ""}><span>✓</span><b>${installPending ? "安装并激活中" : "确认权限并安装"}</b></button></span></div>
  </article>`;
}

function renderPluginStageDiscardButton(stage, pending, available) {
  return `<button class="action-button" type="button" data-plugin-stage-discard="${escapeHtml(stage.stageId)}"${pending || !available ? " disabled" : ""}><span>×</span><b>${pending ? "丢弃中" : "丢弃候选"}</b></button>`;
}

function renderPluginCard(state, plugin) {
  const actionId = plugin.enabled ? "abilities.plugin.disable" : "abilities.plugin.enable";
  const pending = ["pressed", "pending"].includes(actionPhase(state, actionId));
  const rollbackPending = ["pressed", "pending"].includes(actionPhase(state, "abilities.plugin.rollback"));
  const uninstallPending = ["pressed", "pending"].includes(actionPhase(state, "abilities.plugin.uninstall"));
  const actionAvailable = plugin.actionsEnabled && state.viewModel?.actions?.[actionId]?.available;
  const rollbackAvailable = plugin.actionsEnabled && plugin.rollbackAvailable && state.viewModel?.actions?.["abilities.plugin.rollback"]?.available;
  const surfaces = plugin.surfaces.length
    ? plugin.surfaces.map((surface) => `<span class="plugin-surface is-${escapeHtml(surface)}">${surface === "desktop" ? "桌宠" : "QQ"}</span>`).join("")
    : `<span class="plugin-surface is-internal">后台</span>`;
  const contributionSummary = plugin.contributionCount
    ? `${plugin.contributionCount} 项运行贡献`
    : plugin.declaredOnly ? "声明已安装，运行贡献待激活" : "没有面向模型或渠道的贡献";
  return `<article class="plugin-card" data-plugin-id="${escapeHtml(plugin.pluginId)}">
    <div class="plugin-card-main">
      <span class="plugin-card-icon" aria-hidden="true">⌘</span>
      <div><strong>${escapeHtml(plugin.pluginId)}</strong><p>${escapeHtml(contributionSummary)}</p></div>
      <span class="module-state is-${escapeHtml(plugin.statusTone)}">${escapeHtml(plugin.statusLabel)}</span>
    </div>
    <div class="plugin-card-meta">
      <span>${plugin.source === "managed" ? "用户安装" : "随版本内置"}${plugin.version ? ` · v${escapeHtml(plugin.version)}` : ""}</span>
      <span class="plugin-surfaces">${surfaces}</span>
    </div>
    ${plugin.reason ? `<p class="plugin-card-reason">${escapeHtml(plugin.reason)}</p>` : ""}
    ${renderPluginDependencyErrors(plugin)}
    ${renderPluginDetails(state, plugin)}
    ${plugin.source === "managed" ? renderPluginUninstall(state, plugin, uninstallPending) : ""}
    <div class="plugin-card-actions">
      ${plugin.rollbackAvailable ? `<button class="action-button" type="button" data-action="abilities.plugin.rollback" data-action-value="${escapeHtml(plugin.pluginId)}"${rollbackPending || pending || uninstallPending || !rollbackAvailable ? " disabled" : ""}><span>↶</span><b>${rollbackPending ? "回滚中" : "回滚版本"}</b></button>` : ""}
      <button class="action-button${plugin.enabled ? "" : " is-primary"}" type="button" data-action="${actionId}" data-action-value="${escapeHtml(plugin.pluginId)}"${pending || rollbackPending || uninstallPending || !actionAvailable ? " disabled" : ""}><span>${plugin.enabled ? "Ⅱ" : "▷"}</span><b>${pending ? "处理中" : plugin.enabled ? "停用" : "启用"}</b></button>
    </div>
  </article>`;
}

function renderPluginDependencyErrors(plugin) {
  if (!plugin.dependencyErrors?.length) return "";
  const items = plugin.dependencyErrors.map((item) => {
    const subject = item.serviceId || item.requiredService || "服务依赖";
    const provider = item.selectedProvider ? `：${item.selectedProvider}` : "";
    const reason = item.reason ? ` · ${item.reason}` : "";
    return `<li><span>${escapeHtml(subject)}${escapeHtml(provider)}</span><small>${escapeHtml(reason || "等待可用依赖")}</small></li>`;
  }).join("");
  return `<section class="plugin-dependency-errors" role="status"><strong>依赖未满足</strong><ul>${items}</ul></section>`;
}

function renderPluginUninstall(state, plugin, pending) {
  const available = plugin.actionsEnabled && state.viewModel?.actions?.["abilities.plugin.uninstall"]?.available;
  return `<details class="plugin-danger-zone" data-capability-key="plugin-uninstall:${escapeHtml(plugin.pluginId)}">
    <summary>卸载插件</summary>
    <div><p>卸载会停用该插件，并移除当前 Bot 的托管制品与贡献。</p><button class="action-button is-danger" type="button" data-plugin-uninstall="${escapeHtml(plugin.pluginId)}"${pending || !available ? " disabled" : ""}><span>×</span><b>${pending ? "卸载中" : "确认卸载"}</b></button></div>
  </details>`;
}

function renderPluginDetails(state, plugin) {
  const contributionGroups = pluginContributionGroups(plugin.contributions);
  const channelLabels = { desktop: "桌宠", qq: "QQ" };
  const channelNames = plugin.surfaces.length
    ? plugin.surfaces.map((surface) => channelLabels[surface] || surface).join("、")
    : "仅后台";
  return `<details class="plugin-card-details" data-capability-key="plugin:${escapeHtml(plugin.pluginId)}">
    <summary><span>查看贡献与权限、连接</span><i aria-hidden="true">⌄</i></summary>
    <div class="plugin-detail-body">
      <dl class="plugin-runtime-facts">
        <div><dt>适用渠道</dt><dd>${escapeHtml(channelNames)}</dd></div>
        <div><dt>运行代次</dt><dd>${plugin.generation || "—"}</dd></div>
        <div><dt>失败回退</dt><dd>${plugin.rollbackAvailable ? "可回滚至有效版本" : "暂无可用回滚"}</dd></div>
      </dl>
      <section class="plugin-detail-section"><strong>运行贡献</strong>
        ${contributionGroups.length
          ? `<div class="plugin-contribution-list">${renderPluginContributionGroups(contributionGroups, plugin.contributions)}</div>`
          : `<p>${plugin.declaredOnly ? "插件尚未激活，当前显示安装阶段的声明。" : "插件没有注册面向模型或渠道的贡献。"}</p>`}
      </section>
      <section class="plugin-detail-section"><strong>权限声明</strong>
        ${plugin.permissions.length
          ? `<div class="plugin-permission-list">${plugin.permissions.map((item) => `<code>${escapeHtml(item)}</code>`).join("")}</div>`
          : `<p>没有声明额外权限。</p>`}
      </section>
      ${plugin.connectionConfigs.length ? renderPluginConnections(state, plugin) : ""}
    </div>
  </details>`;
}

function renderPluginConnections(state, plugin) {
  const actionId = "abilities.plugin.connection.save";
  const savePhase = actionPhase(state, actionId);
  const pending = ["pressed", "pending"].includes(savePhase);
  const available = plugin.actionsEnabled && state.viewModel?.actions?.[actionId]?.available;
  return `<section class="plugin-detail-section plugin-connection-section"><div class="plugin-connection-heading"><strong>连接配置</strong><small>由插件公开 Schema 生成；私密字段只显示已配置状态。</small></div><div class="plugin-connection-list">${plugin.connectionConfigs.map((connection) => renderPluginConnectionForm(state, plugin, connection, { pending, available })).join("")}</div></section>`;
}

function renderPluginConnectionForm(state, plugin, connection, options = {}) {
  const schema = connection.schema || { type: "object", properties: {}, required: [] };
  const properties = schema.properties || {};
  const required = new Set(Array.isArray(schema.required) ? schema.required : []);
  const configuredPrivate = new Set(connection.configuredPrivateFields || []);
  const privateFields = new Set(connection.privateFields || []);
  const status = connectionStatusPresentation(connection);
  const fieldEntries = Object.entries(properties);
  const fields = fieldEntries.map(([key, field]) => renderPluginConnectionField({
    key, field, required: required.has(key), privateField: privateFields.has(key),
    configuredPrivate: configuredPrivate.has(key), values: connection.values || {}
  })).join("");
  const buttonDisabled = options.pending || !options.available;
  return `<details class="plugin-connection-card" data-capability-key="plugin-connection:${escapeHtml(plugin.pluginId)}:${escapeHtml(connection.name)}" open>
    <summary class="plugin-connection-card-summary"><div class="plugin-connection-card-head"><div><strong>${escapeHtml(connection.name)}</strong><small>v${escapeHtml(connection.version)}${connection.description ? ` · ${escapeHtml(connection.description)}` : ""}</small></div><span class="module-state is-${escapeHtml(status.tone)}">${escapeHtml(status.label)}</span></div></summary>
    ${connection.reason ? `<p class="plugin-card-reason">${escapeHtml(connection.reason)}</p>` : ""}
    <form class="capability-config-form plugin-connection-form" data-capability-form="plugin-connection" data-plugin-id="${escapeHtml(plugin.pluginId)}" data-connection-name="${escapeHtml(connection.name)}" data-connection-revision="${connection.revision}">
      <label class="capability-toggle"><input type="checkbox" name="enabled"${connection.enabled || connection.status === "not_configured" ? " checked" : ""}><span><strong>${connection.enabled ? "启用连接" : "保存为停用草稿"}</strong><small>停用草稿可以暂时不填写必填字段。</small></span></label>
      ${fields || `<p class="plugin-stage-note">插件没有声明可编辑字段。</p>`}
      <div class="capability-form-actions"><button class="action-button is-primary" type="submit" data-action="abilities.plugin.connection.save"${buttonDisabled ? " disabled" : ""}><span>✓</span><b>${options.pending ? "保存中" : "保存连接"}</b></button></div>
    </form>
  </details>`;
}

function renderPluginConnectionField({ key, field, required, privateField, configuredPrivate, values }) {
  const source = field && typeof field === "object" ? field : {};
  const type = ["number", "integer", "boolean"].includes(source.type) ? source.type : "string";
  const label = source.title || key;
  const description = source.description || (privateField ? "私密字段不会回显" : "");
  const hasValue = privateField ? configuredPrivate : Object.hasOwn(values, key);
  const currentValue = privateField ? "" : Object.hasOwn(values, key) ? values[key] : source.default;
  const requiredInput = required && !(privateField && configuredPrivate);
  const clearControl = hasValue ? `<span class="plugin-connection-clear"><input type="checkbox" data-connection-clear="${escapeHtml(key)}"><span>清除</span></span>` : "";
  const requiredMark = required ? " *" : "";
  if (type === "boolean") {
    const checked = currentValue === true;
    return `<label class="capability-toggle plugin-connection-field"><input type="checkbox" name="field.${escapeHtml(key)}" data-connection-field="${escapeHtml(key)}" data-connection-type="boolean"${checked ? " checked" : ""}><span><strong>${escapeHtml(label)}${requiredMark}</strong><small>${escapeHtml(description || "启用此选项")}</small></span>${clearControl}</label>`;
  }
  if (Array.isArray(source.enum) && source.enum.length) {
    return `<label class="capability-field plugin-connection-field"><span>${escapeHtml(label)}${requiredMark}${privateField ? " · 私密" : ""}${clearControl}</span><select name="field.${escapeHtml(key)}" data-connection-field="${escapeHtml(key)}" data-connection-type="string"${requiredInput ? " required" : ""}>${source.enum.map((option) => `<option value="${escapeHtml(option)}"${String(option) === String(currentValue) ? " selected" : ""}>${escapeHtml(option)}</option>`).join("")}</select>${description ? `<small class="plugin-connection-help">${escapeHtml(description)}</small>` : ""}</label>`;
  }
  const inputType = privateField ? "password" : source.format === "uri" || source.format === "url" ? "url" : type === "number" || type === "integer" ? "number" : "text";
  const numericAttrs = inputType === "number"
    ? ` min="${escapeHtml(source.minimum ?? "")}" max="${escapeHtml(source.maximum ?? "")}" step="${escapeHtml(source.step ?? (type === "integer" ? "1" : "any"))}"`
    : "";
  const patternAttr = source.pattern && inputType !== "password" ? ` pattern="${escapeHtml(source.pattern)}"` : "";
  const placeholder = privateField && configuredPrivate ? "已配置，留空保持不变" : source.default !== undefined && !hasValue ? `默认：${source.default}` : "";
  const valueAttr = privateField || currentValue === undefined || currentValue === null ? "" : ` value="${escapeHtml(currentValue)}"`;
  return `<label class="capability-field plugin-connection-field"><span>${escapeHtml(label)}${requiredMark}${privateField ? " · 私密" : ""}${clearControl}</span><input name="field.${escapeHtml(key)}" data-connection-field="${escapeHtml(key)}" data-connection-type="${escapeHtml(type)}" type="${inputType}"${valueAttr} placeholder="${escapeHtml(placeholder)}" autocomplete="off"${requiredInput ? " required" : ""}${numericAttrs}${patternAttr}>${description ? `<small class="plugin-connection-help">${escapeHtml(description)}</small>` : ""}</label>`;
}

function connectionStatusPresentation(connection) {
  if (connection.status === "configured" && connection.enabled) return { label: "已配置", tone: "ready" };
  if (connection.status === "disabled" || (connection.status === "configured" && !connection.enabled)) return { label: "已停用", tone: "muted" };
  if (connection.status === "unavailable" || connection.status === "invalid_config") return { label: "读取失败", tone: "danger" };
  return { label: "未配置", tone: "warning" };
}

function pluginContributionGroups(contributions) {
  return [
    ["capabilities", "模型能力"],
    ["commands", "QQ 指令"],
    ["event_handlers", "事件处理"],
    ["hooks", "宿主钩子"],
    ["background_services", "后台服务"],
    ["prompt_blocks", "提示片段"],
    ["skills", "Skill"]
  ].filter(([key]) => contributions[key]?.length);
}

function renderPluginContributionGroups(groups, contributions) {
  return groups.map(([key, label]) => `<div><small>${label}</small><span>${contributions[key].map((item) => `<code>${escapeHtml(item)}</code>`).join("")}</span></div>`).join("");
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
