import { formatError } from "./view.js";

export function createProjectWorkspaces({ host, view, capture, isCurrent }) {
  const { document, els, buildText } = view;
  let projectBusy = false, operation = null, readSequence = 0;

  async function refreshProjectWorkspaces(ticket = capture()) {
    if (!els.projectList || !isCurrent(ticket)) return;
    const sequence = ++readSequence;
    try {
      const payload = await host.invoke("manage_project_workspaces", {
        action: "list", workspace_id: null, display_name: null, include_archived: false
      });
      if (!isCurrent(ticket) || sequence !== readSequence) return;
      if (!payload?.ok) throw Error(projectReasonLabel(payload?.reason));
      renderProjectWorkspaces(payload.result || {});
    } catch (error) {
      if (!isCurrent(ticket) || sequence !== readSequence) return;
      els.projectSummary.textContent = "项目工作区暂时不可用";
      els.projectList.replaceChildren(buildProjectEmpty(`读取失败：${formatError(error)}`));
    }
  }

  async function run(pending, failure, request, onSuccess) {
    if (projectBusy) return { status: "busy" };
    const ticket = capture();
    if (!isCurrent(ticket)) return { status: "stale" };
    const op = { ticket, feedback: view.beginFeedback(pending) };
    operation = op; projectBusy = true; readSequence += 1;
    updateProjectBusyState();
    try {
      const payload = await request();
      if (!isCurrent(ticket)) return { status: "stale" };
      if (payload?.status === "cancelled") { op.feedback.setStatus("已取消选择目录"); return { status: "cancelled" }; }
      if (!payload?.ok) throw Error(projectReasonLabel(payload?.reason));
      onSuccess(payload, op.feedback);
      await refreshProjectWorkspaces(ticket);
      return { status: isCurrent(ticket) ? "completed" : "stale" };
    } catch (error) {
      if (!isCurrent(ticket)) return { status: "stale" };
      op.feedback.setAlert(`${failure}失败：${formatError(error)}`, "error");
      op.feedback.setStatus(`${failure}失败`);
      return { status: "failed" };
    } finally {
      if (operation === op) {
        operation = null; projectBusy = false;
        if (isCurrent(capture())) {
          updateProjectBusyState();
          if (!isCurrent(ticket)) void refreshProjectWorkspaces();
        }
      }
    }
  }

  function bindProjectDirectory() {
    return run("正在选择项目目录", "绑定项目", () => host.invoke("bind_project_directory"), (payload, feedback) => {
      feedback.setStatus(payload.result?.already_bound ? "已切换到这个项目" : "已绑定并选择项目");
    });
  }

  function createManagedProject() {
    if (projectBusy) return Promise.resolve({ status: "busy" });
    const draft = String(els.projectDisplayName?.value || ""), displayName = draft.trim();
    if (!displayName) { els.projectDisplayName?.focus(); return Promise.resolve({ status: "empty" }); }
    return run("正在创建项目", "创建项目", () => host.invoke("manage_project_workspaces", {
      action: "create", workspace_id: null, display_name: displayName, include_archived: false
    }), (_payload, feedback) => {
      if (els.projectDisplayName.value === draft) els.projectDisplayName.value = "";
      feedback.setStatus("已创建并选择项目");
    });
  }

  function runProjectAction(action, workspaceId) {
    if (!workspaceId) return Promise.resolve({ status: "empty" });
    return run("正在更新项目", "项目操作", () => host.invoke("manage_project_workspaces", {
      action, workspace_id: workspaceId, display_name: null, include_archived: false
    }), (_payload, feedback) => feedback.setStatus(action === "select" ? "已切换项目" : "项目已归档"));
  }

  function archiveProject(workspaceId) {
    if (projectBusy || !workspaceId || !host.confirm("归档这个项目？文件不会被删除，之后仍可重新绑定或恢复。")) {
      return Promise.resolve({ status: "cancelled" });
    }
    return runProjectAction("archive", workspaceId);
  }

  function reset(message = "正在读取项目工作区") {
    readSequence += 1;
    els.projectSummary.textContent = message;
    els.projectList.replaceChildren(buildProjectEmpty(message));
    updateProjectBusyState();
  }

  function renderProjectWorkspaces(payload) {
    const workspaces = Array.isArray(payload?.workspaces) ? payload.workspaces : [];
    const selectedId = String(payload?.selected_workspace_id || "");
    const selected = workspaces.find((item) => String(item.workspace_id || "") === selectedId && item.available !== false);
    els.projectSummary.textContent = selected
      ? `${selected.display_name || "未命名项目"} · ${selected.root_kind === "host_bound" ? "已绑定目录" : "Akane 受管目录"}`
      : "尚未选择项目，编程任务会先请你创建或选择";
    els.projectList.replaceChildren();
    if (!workspaces.length) {
      els.projectList.append(buildProjectEmpty("还没有项目。可以创建新项目，或绑定电脑上的现有目录。"));
      return;
    }
    for (const item of workspaces) {
      const row = document.createElement("article");
      row.className = "project-workspace-row";
      if (item.selected) row.classList.add("is-selected");
      const body = document.createElement("div");
      const title = buildText("strong", String(item.display_name || "未命名项目"));
      const meta = buildText(
        "span",
        item.available === false
          ? "目录当前不可访问"
          : item.root_kind === "host_bound" ? "电脑上的现有目录" : "Akane 受管项目"
      );
      body.append(title, meta);
      const actions = document.createElement("div");
      if (!item.selected) {
        actions.append(buildProjectButton("选择", "select", item.workspace_id, item.available === false));
      } else {
        const current = buildText("span", "当前");
        current.className = "project-current-badge";
        actions.append(current);
      }
      actions.append(buildProjectButton("归档", "archive", item.workspace_id, false));
      row.append(body, actions);
      els.projectList.append(row);
    }
  }

  function buildProjectEmpty(message) {
    const empty = document.createElement("p");
    empty.className = "project-workspace-empty";
    empty.textContent = message;
    return empty;
  }

  function buildProjectButton(label, action, workspaceId, disabled) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.dataset.projectAction = action;
    button.dataset.workspaceId = String(workspaceId || "");
    button.dataset.permanentDisabled = disabled ? "true" : "false";
    button.disabled = Boolean(disabled || projectBusy);
    return button;
  }

  function updateProjectBusyState() {
    if (els.bindProjectDirectory) els.bindProjectDirectory.disabled = projectBusy;
    if (els.projectDisplayName) els.projectDisplayName.disabled = projectBusy;
    const submit = els.createProjectForm?.querySelector('button[type="submit"]');
    if (submit) submit.disabled = projectBusy;
    for (const button of els.projectList?.querySelectorAll("button") || []) {
      button.disabled = projectBusy || button.dataset.permanentDisabled === "true";
    }
  }

  function projectReasonLabel(reason) {
    const labels = {
      project_binding_requires_local_backend: "绑定现有目录只支持纯本地后端",
      host_directory_protected: "不能把 Akane 源码、运行数据或 Python 环境绑定为项目",
      host_directory_managed_by_runtime: "这个目录已经属于 Akane 的受管工作区",
      host_directory_missing: "所选目录已经不存在",
      host_directory_invalid: "所选位置不是可用目录",
      workspace_missing: "项目目录当前不可访问",
      project_workspace_unconfigured: "项目工作区尚未启用",
      admin_auth_required: "本地管理授权不可用",
      picker_cancelled: "已取消选择目录"
    };
    return labels[String(reason || "")] || String(reason || "project_workspace_failed");
  }

  return { refresh: refreshProjectWorkspaces, bindProjectDirectory, createManagedProject, runProjectAction, archiveProject, reset };
}
