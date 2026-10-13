import { getPackName } from "./pack-model.js";
import { setFieldValue, flashElement } from "./dom.js";
import { formatError } from "./shared.js";

export function createContextLibraryController({
  findPack,
  els,
  setWorkspacePack,
  view,
  setStatus,
  isTauriRuntime,
  contextState,
  navigationState,
  updateCatalogControls,
  isPackWriteBusy,
  writeState,
  invoke,
  acceptPackResult,
}) {
  function loadContextLibrariesTab(packId) {
    const pack = findPack(packId);
    if (!pack) {
      if (els.contextLibraryContent) els.contextLibraryContent.hidden = true;
      if (els.contextLibraryEmptyState) els.contextLibraryEmptyState.hidden = false;
      return;
    }

    setWorkspacePack(pack.id);
    if (els.contextLibraryContent) els.contextLibraryContent.hidden = false;
    if (els.contextLibraryEmptyState) els.contextLibraryEmptyState.hidden = true;
    if (els.contextLibraryStatus) els.contextLibraryStatus.textContent = `资料库属于：${getPackName(pack)}。`;
    renderContextLibraries(pack);
  }

  function renderContextLibraries(pack) {
    if (!els.contextLibraryList) return;
    const profile = pack?._rawProfile && typeof pack._rawProfile === "object"
      ? pack._rawProfile
      : {};
    const libraries = Array.isArray(profile.context_libraries)
      ? profile.context_libraries.filter((item) => item && typeof item === "object")
      : [];

    els.contextLibraryList.replaceChildren();
    if (!libraries.length) {
      const empty = document.createElement("div");
      empty.className = "context-library-card context-library-card-empty";
      const title = document.createElement("strong");
      title.textContent = "还没有角色资料库";
      const note = document.createElement("p");
      note.textContent = "新建后，往对应文件夹放入以主题命名的 Markdown 文件即可。";
      empty.append(title, note);
      els.contextLibraryList.appendChild(empty);
      return;
    }

    for (const library of libraries) {
      const folder = String(library.folder || "").trim();
      const name = String(library.name || folder || "未命名资料库").trim();
      const description = String(library.description || "").trim();
      const loadWhen = String(library.load_when || library.loadWhen || "").trim();
      const card = document.createElement("article");
      card.className = "context-library-card";

      const heading = document.createElement("div");
      heading.className = "context-library-card-heading";
      const title = document.createElement("strong");
      title.textContent = name;
      const folderLabel = document.createElement("code");
      folderLabel.textContent = folder || "未设置文件夹";
      heading.append(title, folderLabel);

      const descriptionLine = document.createElement("p");
      descriptionLine.textContent = description || "尚未填写内容说明。";
      const loadLine = document.createElement("p");
      loadLine.className = "context-library-load-when";
      loadLine.textContent = `读取时机：${loadWhen || "尚未填写"}`;
      card.append(heading, descriptionLine, loadLine);
      els.contextLibraryList.appendChild(card);
    }
  }

  function openContextLibraryDialog() {
    const packId = String(view.workspacePackId || view.activePackId || "").trim();
    if (!packId) {
      setStatus("请先选择一个角色包。");
      return;
    }
    if (!isTauriRuntime) {
      setStatus("浏览器预览模式不支持创建资料库。");
      return;
    }
    contextState.contextDialogTarget = { packId, visit: navigationState.workspaceVisit };
    setFieldValue(els.contextLibraryName, "");
    setFieldValue(els.contextLibraryFolder, "");
    setFieldValue(els.contextLibraryDescription, "");
    setFieldValue(els.contextLibraryLoadWhen, "");
    showContextLibraryError("");
    updateCatalogControls();
    els.contextLibraryDialog?.showModal();
  }

  function showContextLibraryError(message) {
    if (!els.contextLibraryError) return;
    els.contextLibraryError.textContent = String(message || "");
    els.contextLibraryError.hidden = !message;
    if (message) flashElement(els.contextLibraryForm, "invalid-flash");
  }

  async function createContextLibrary() {
    const target = contextState.contextDialogTarget;
    if (!target || !els.contextLibraryDialog.open || !findPack(target.packId)) return;
    const { packId } = target;
    const ownsDialog = () => contextState.contextDialogTarget === target && els.contextLibraryDialog.open;
    const ownsView = () => view.activeTab === "context" && view.workspacePackId === packId
      && navigationState.workspaceVisit === target.visit;
    if (isPackWriteBusy(packId)) { showContextLibraryError("请等待当前角色包操作完成后再创建。"); return; }
    const name = String(els.contextLibraryName?.value || "").trim();
    const folder = String(els.contextLibraryFolder?.value || name).trim();
    const description = String(els.contextLibraryDescription?.value || "").trim();
    const loadWhen = String(els.contextLibraryLoadWhen?.value || "").trim();

    if (!name) {
      showContextLibraryError("请填写资料库名称。");
      return;
    }
    if (!description) {
      showContextLibraryError("请用一句话说明这里存什么。");
      return;
    }
    if (!loadWhen) {
      showContextLibraryError("请说明角色应在什么时候读取这组资料。");
      return;
    }

    writeState.contextSaves.add(packId);
    ++writeState.registryRevision;
    updateCatalogControls();
    showContextLibraryError("");
    if (ownsView() && els.contextLibraryStatus) {
      els.contextLibraryStatus.textContent = `正在创建：${name}…`;
    }
    try {
      const result = await invoke("create_character_context_library", {
        request: {
          packId,
          folder,
          name,
          description,
          loadWhen,
        },
      });
      const normalized = acceptPackResult(result, packId);
      if (ownsDialog()) els.contextLibraryDialog.close();
      if (ownsView()) {
        renderContextLibraries(normalized);
        if (els.contextLibraryStatus) {
          els.contextLibraryStatus.textContent = `已创建“${name}”。放入 .md 文件后，模型会在提示词中看到它的用途和读取时机。`;
        }
        setStatus(`已创建角色资料库：${name}`);
      }
    } catch (error) {
      const message = formatError(error);
      if (ownsDialog()) showContextLibraryError(message);
      if (ownsView() && els.contextLibraryStatus) {
        els.contextLibraryStatus.textContent = `创建失败：${message}`;
      }
    } finally {
      writeState.contextSaves.delete(packId);
      ++writeState.registryRevision;
      updateCatalogControls();
    }
  }

  return { loadContextLibrariesTab, openContextLibraryDialog, createContextLibrary };
}
