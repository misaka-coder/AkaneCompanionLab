import { itemKey } from "./items.js";
import { formatError } from "./view.js";

export function createWorkspaceFileActions({ host, transport, view, items, capture, isCurrent, isLoading, refreshWorkspace }) {
  let operation = null;

  async function run(pending, failure, work) {
    if (operation) return { status: "busy" };
    const ticket = capture();
    if (!isCurrent(ticket)) return { status: "stale" };
    const op = { ticket, feedback: view.beginFeedback(pending) };
    operation = op;
    items.setBusy(true);
    try {
      await work(op);
      return { status: isCurrent(ticket) ? "completed" : "stale" };
    } catch (error) {
      if (!isCurrent(ticket)) return { status: "stale" };
      const message = `${failure}失败：${formatError(error)}`;
      op.feedback.setStatus(message); op.feedback.setAlert(message, "error");
      return { status: "failed" };
    } finally {
      if (operation === op) {
        operation = null;
        if (isCurrent(ticket)) items.setBusy(false);
      }
    }
  }

  function copyItemId(item) {
    return run("正在复制编号", "复制", async ({ ticket, feedback }) => {
      const value = itemKey(item);
      if (!value) throw Error("没有可复制的编号");
      await host.copy(value);
      if (isCurrent(ticket)) feedback.setStatus("编号已复制");
    });
  }

  function openWorkspaceItem(item) { return nativeFileAction(item, "open"); }
  function revealWorkspaceItem(item) { return nativeFileAction(item, "reveal"); }
  function copyWorkspaceItemPath(item) { return nativeFileAction(item, "copy_path"); }
  function exportWorkspaceItemToDesktop(item) { return nativeFileAction(item, "save_desktop"); }

  function nativeFileAction(item, action) {
    const labels = {
      open: ["正在打开文件", "打开", "已打开文件"],
      reveal: ["正在打开位置", "打开位置", "已打开所在位置"],
      copy_path: ["正在复制路径", "复制路径", "文件路径已复制"],
      save_desktop: ["正在保存到桌面", "保存", "已保存到桌面"]
    };
    const [pending, failure, success] = labels[action];
    return run(pending, failure, async ({ ticket, feedback }) => {
      const result = await transport.invokeWorkspaceItemAction(ticket, item, action);
      if (!isCurrent(ticket)) return;
      if (action === "copy_path") {
        const localPath = String(result?.local_path || "").trim();
        if (!localPath) throw Error("没有可复制的本地暂存路径");
        await host.copy(localPath);
        if (!isCurrent(ticket)) return;
      }
      feedback.setStatus(success);
      const exportedPath = action === "save_desktop" ? String(result?.path || "").trim() : "";
      if (exportedPath) feedback.setAlert(`已保存到桌面：${exportedPath}`, "info");
    });
  }

  function playWorkspaceAudio(item) {
    return run("准备播放", "命令发送", async ({ ticket }) => {
      const handle = String(item?.handle || item?.id || "").trim();
      if (!handle) throw Error("没有可播放的编号");
      if (!isCurrent(ticket)) return;
      await host.sendCommand("playWorkspaceAudio", {
        itemType: item.item_type || item.type || "attachment", handle,
        title: item.title || "", format: item.format || "", kind: item.kind || ""
      }, { isCurrent: () => isCurrent(ticket) });
    });
  }

  function clearWorkspaceItem(item) {
    return run("正在收起", "收起", async op => {
      const handle = String(item?.handle || item?.id || "").trim();
      if (!handle) throw Error("没有可收起的编号");
      await transport.postWorkspaceAction(op.ticket, { action: "clear", item_type: item.item_type || item.type || "attachment", target: handle });
      if (isCurrent(op.ticket)) { op.feedback.setStatus("已收起"); await refreshAfterMutation(op.ticket); }
    });
  }

  function clearWorkspaceFiles() {
    if (operation || isLoading()) return Promise.resolve({ status: "busy" });
    if (!host.confirm("确认清理所有手边物品？文件和生成物会被移除，但不影响聊天记录。")) return Promise.resolve({ status: "cancelled" });
    return run("正在清理文件", "清理", async op => {
      const result = await transport.postWorkspaceAction(op.ticket, { action: "clear_files" });
      if (!isCurrent(op.ticket)) return;
      const count = Number(result?.managed?.length || 0);
      op.feedback.setStatus(count ? `已清理 ${count} 项` : "没有可清理的文件");
      await refreshAfterMutation(op.ticket);
    });
  }

  async function refreshAfterMutation(ticket) {
    await host.notifyRefresh();
    if (isCurrent(ticket)) await refreshWorkspace({ reload: false });
  }

  function reset() { operation = null; items.setBusy(false); }
  return { copyItemId, openWorkspaceItem, revealWorkspaceItem, copyWorkspaceItemPath, exportWorkspaceItemToDesktop,
    playWorkspaceAudio, clearWorkspaceItem, clearWorkspaceFiles, reset };
}
