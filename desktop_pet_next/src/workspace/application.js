import { createWorkspaceView, formatError } from "./view.js";
import { createWorkspaceItemsView } from "./items.js";
import { createWorkspaceMusicView } from "./music.js";
import { createWorkspaceTransport, normalizeBackendUrl } from "./transport.js";
import { createProjectWorkspaces } from "./projects.js";
import { createWorkspaceFileActions } from "./files.js";
import { createWorkspaceBindings } from "./events.js";

export function createWorkspaceApplication({ host, view = createWorkspaceView(host.document) }) {
  let active = false, closed = false, state = null, version = 0, stateRevision = 0;
  let refresh = null, refreshTimer = null, bindings = null, pendingSnapshot = null;
  let scopeAbort = new AbortController();
  const isLoading = () => Boolean(refresh);
  const items = createWorkspaceItemsView(view, { isLoading });
  const renderMusicPanel = createWorkspaceMusicView(view);
  const capture = () => ({ version, state: Object.freeze({ ...(state || {}) }), signal: scopeAbort.signal });
  const isCurrent = ticket => active && Boolean(state) && ticket.version === version && !ticket.signal.aborted;
  const transport = createWorkspaceTransport({ invoke: host.invoke, fetch: host.fetch, now: host.now, isCurrent });
  const projects = createProjectWorkspaces({ host, view, capture, isCurrent });
  const files = createWorkspaceFileActions({ host, transport, view, items, capture, isCurrent, isLoading, refreshWorkspace });
  const owns = work => active && refresh === work;

  function updateAvailability() {
    view.els.refresh.disabled = isLoading();
    items.updateWorkspaceActions();
  }

  function applyState(next, work = null) {
    const changed = stateIdentityKey(state) !== stateIdentityKey(next);
    state = { ...next }; stateRevision += 1;
    if (changed) {
      version += 1; scopeAbort.abort(); scopeAbort = new AbortController();
      host.clearTimeout(refreshTimer); refreshTimer = null;
      if (refresh !== work) refresh = null;
      files.reset(); projects.reset(); items.renderLoading(); renderMusicPanel(null);
      view.setUpdated(0); view.setAlert(""); view.setStatus("正在切换工作区");
      updateAvailability();
    }
    view.updateIdentityUi(state);
    return changed;
  }

  function applySettingsSnapshot(snapshot, work = null) {
    if (!active || !snapshot?.state) return;
    if (!state) { pendingSnapshot = snapshot; return; }
    const incoming = snapshot.state;
    if ((incoming.instanceId && state.instanceId !== incoming.instanceId)
      || (state.hostId && incoming.hostId && state.hostId !== incoming.hostId)) {
      view.setAlert(`已忽略来自实例 ${incoming.instanceId || incoming.hostId} 的旧窗口状态。`, "error");
      return;
    }
    const changed = applyState({ ...state, ...incoming }, work);
    if (changed && !work) scheduleWorkspaceRefresh();
    if ("music" in snapshot) renderMusicPanel(snapshot.music || null);
  }

  async function reloadState(work) {
    const revision = stateRevision;
    try {
      const persisted = await host.loadState();
      if (!owns(work)) return false;
      // A newer live snapshot owns the state even when its identity did not change.
      if (revision !== stateRevision) return Boolean(state);
      applyState(persisted, work);
      if (pendingSnapshot) {
        const snapshot = pendingSnapshot; pendingSnapshot = null;
        applySettingsSnapshot(snapshot, work);
      }
      return true;
    } catch (error) {
      if (!owns(work)) return false;
      if (revision !== stateRevision) return Boolean(state);
      throw Error(`读取设置失败：${formatError(error)}`);
    }
  }

  function refreshWorkspace({ reload = true } = {}) {
    if (!active) return Promise.resolve({ status: "stale" });
    host.clearTimeout(refreshTimer); refreshTimer = null;
    if (refresh) {
      refresh.queued = true; refresh.queuedReload ||= reload;
      return refresh.promise;
    }
    const work = { queued: false, queuedReload: false };
    refresh = work;
    updateAvailability();
    work.promise = Promise.resolve().then(() => drainRefresh(work, reload));
    return work.promise;
  }

  async function drainRefresh(work, reload) {
    try {
      do {
        reload ||= work.queuedReload;
        work.queued = false; work.queuedReload = false;
        if (!owns(work)) return { status: "stale" };
        view.setStatus("刷新中"); view.setAlert(""); items.renderLoading();
        if (reload || !state) {
          try { if (!await reloadState(work)) return { status: "stale" }; }
          catch (error) {
            if (!owns(work)) return { status: "stale" };
            renderSettingsFailure(error);
            if (work.queued) { reload = work.queuedReload; continue; }
            return { status: "failed" };
          }
        }
        if (!owns(work)) return { status: "stale" };
        const ticket = capture(), feedback = view.beginFeedback("刷新中");
        // Project listing must not hold the file tray hostage to a slow native call.
        void projects.refresh(ticket);
        if (!String(state.sessionId || "").trim()) {
          items.renderEmpty("会话还没准备好，先在桌宠里发一条消息。");
          feedback.setStatus("等待会话");
        } else {
          try {
            const payload = await transport.fetchWorkspaceSummary(ticket);
            if (!owns(work) || !isCurrent(ticket)) return { status: "stale" };
            items.renderPayload(payload || {}); view.setUpdated(host.now()); feedback.setStatus("已刷新");
          } catch (error) {
            if (!owns(work) || !isCurrent(ticket)) return { status: "stale" };
            items.renderEmpty("手边物品暂时打不开，等后端回来后再刷新。");
            feedback.setAlert(`确认后端已经启动：${formatError(error)}`, "error");
            view.setUpdated(host.now(), { failed: true }); feedback.setStatus("刷新失败");
          }
        }
        reload = work.queuedReload;
      } while (owns(work) && work.queued);
      return { status: "settled" };
    } catch (error) {
      if (!owns(work)) return { status: "stale" };
      renderSettingsFailure(error);
      return { status: "failed" };
    } finally {
      if (owns(work)) { refresh = null; updateAvailability(); }
    }
  }

  function renderSettingsFailure(error) {
    items.renderEmpty("工作区设置暂时读不到，请重试刷新。");
    projects.reset("设置读取失败，项目暂时不可用");
    view.setAlert(formatError(error), "error"); view.setStatus("刷新失败");
    view.setUpdated(host.now(), { failed: true });
  }

  function scheduleWorkspaceRefresh(delay = 220) {
    if (!active) return;
    host.clearTimeout(refreshTimer);
    refreshTimer = host.setTimeout(() => { refreshTimer = null; void refreshWorkspace({ reload: false }); }, delay);
  }

  async function sendCommand(command, value) {
    const ticket = capture(), feedback = view.beginFeedback("正在调整音乐");
    try {
      if (!isCurrent(ticket)) return;
      await host.sendCommand(command, value, { isCurrent: () => isCurrent(ticket) });
      if (isCurrent(ticket)) feedback.setStatus("音乐操作已发送");
    }
    catch (error) { if (isCurrent(ticket)) feedback.setStatus(`命令发送失败：${formatError(error)}`); }
  }

  async function closeWindow() { dispose(); await host.close(); }

  async function start() {
    if (active || closed) return;
    active = true;
    bindings = createWorkspaceBindings({ host, view, items, files, projects, refreshWorkspace, closeWindow, dispose,
      applySettingsSnapshot, scheduleWorkspaceRefresh, isActive: () => active, sendCommand });
    const sync = bindings.startSync();
    await refreshWorkspace();
    const error = await sync;
    if (active && error && view.els.alert.hidden) view.setAlert(`窗口同步暂时不可用，可手动刷新：${formatError(error)}`, "error");
  }

  function dispose() {
    if (!active) return;
    active = false; closed = true; version += 1; scopeAbort.abort();
    host.clearTimeout(refreshTimer); refreshTimer = null; refresh = null; pendingSnapshot = null;
    bindings?.dispose(); bindings = null;
  }

  return { start, refreshWorkspace, dispose };
}

function stateIdentityKey(value) {
  if (!value) return "";
  return JSON.stringify([value.hostId || value.instanceId, value.instanceId,
    value.boundBotId || value.instanceId, normalizeBackendUrl(value.backendUrl),
    value.profileUserId || "master", value.sessionId || "", value.characterPackId || ""]);
}
