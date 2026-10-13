import { getPackName, getPackReadinessSummary, buildPackRoleLabel, buildPackReadinessIssues, buildPackMeta, displayAssetName } from "./pack-model.js";
import { buildText, buildReadinessRow, buildTechnicalDetails, flashElement } from "./dom.js";

export function createWorkshopRenderer({
  findPack,
  view,
  ensureWorkspacePack,
  els,
  startEditingPack,
  loadContextLibrariesTab,
  calibrationState,
  applyCalibrationPreview,
  loadTestChatTab,
  renderTestChatView,
  showTestChatEmpty,
  setWorkspacePack,
}) {
  function render() {
    const activePack = findPack(view.activePackId);
    ensureWorkspacePack();
    const workspacePack = findPack(view.workspacePackId);
    const activeName = view.activeCharacterName || getPackName(activePack) || "-";
    const workspaceName = getPackName(workspacePack) || "-";
    els.activeCharacter.textContent = activeName;
    els.activeCharacter.title = activePack?.id || "";
    if (els.editingCharacter) {
      els.editingCharacter.textContent = workspaceName;
      els.editingCharacter.title = workspacePack?.id || "";
    }
    els.activePack.textContent = workspacePack ? getPackReadinessSummary(workspacePack) : "-";
    els.activePack.title = workspacePack?.id || "";
    els.activeSession.textContent = view.activeSessionId ? "已隔离" : "-";
    els.activeSession.title = view.activeSessionId || "";
    els.summary.textContent = view.packs.length
      ? `桌宠：${activeName} · 正在编辑：${workspaceName} · ${view.packs.length} 个角色`
      : "还没有读到角色包。";
    els.packCount.textContent = String(view.packs.length);
    renderPackDetails(workspacePack);
    renderPackList();

    if (view.activeTab === "persona" && view.editingPackId !== view.workspacePackId) {
      startEditingPack(view.workspacePackId);
    }
    if (view.activeTab === "context") {
      loadContextLibrariesTab(view.workspacePackId || view.activePackId);
    }
    if (view.activeTab === "calibration" && calibrationState.calibrationReadyTarget) applyCalibrationPreview();
    if (view.activeTab === "test") {
      const targetId = view.workspacePackId || view.testPackId || view.activePackId;
      if (targetId) {
        if (view.testPackId !== targetId) loadTestChatTab(targetId);
        else renderTestChatView(targetId);
      } else {
        showTestChatEmpty();
      }
    }
  }

  function renderPackList() {
    renderFirstUsePanel();
    if (!view.packs.length) {
      els.packList.replaceChildren(buildPackListEmptyState());
      return;
    }

    els.packList.replaceChildren(
      ...view.packs.map((pack) => {
        const card = document.createElement("article");
        card.className = "pack-card";
        card.dataset.packId = pack.id;
        card.dataset.selectPack = pack.id;
        card.tabIndex = 0;
        const isActive = pack.id === view.activePackId;
        const isEditing = pack.id === view.workspacePackId;
        card.classList.toggle("active", isActive);
        card.classList.toggle("editing", isEditing);
        card.setAttribute("aria-label", `选择角色 ${getPackName(pack) || pack.id}`);
        card.setAttribute("aria-current", isEditing ? "true" : "false");

        const identity = document.createElement("div");
        identity.className = "pack-card-identity";
        const avatar = document.createElement("span");
        avatar.className = "pack-avatar";
        avatar.setAttribute("aria-hidden", "true");
        const heading = document.createElement("div");
        heading.className = "pack-card-heading";
        const name = getPackName(pack) || "未命名角色";
        avatar.textContent = Array.from(name)[0] || "角";
        const packId = document.createElement("code");
        packId.textContent = pack.id;
        const subtitle = document.createElement("span");
        subtitle.textContent = buildPackRoleLabel(pack, { isActive, isEditing });
        subtitle.className = isActive || isEditing ? "pack-row-active-note" : "";
        heading.append(buildText("strong", name), packId, subtitle);
        identity.append(avatar, heading);

        const meta = document.createElement("p");
        const issues = buildPackReadinessIssues(pack);
        meta.textContent = issues.length
          ? `还差：${issues.slice(0, 2).map((item) => item.label).join("、")} · ${buildPackMeta(pack)}`
          : buildPackMeta(pack);

        const actions = document.createElement("div");
        actions.className = "pack-card-actions";

        const editBtn = document.createElement("button");
        editBtn.type = "button";
        editBtn.className = "btn-edit-pack";
        editBtn.dataset.editPack = pack.id;
        editBtn.textContent = isEditing ? "继续编辑" : "编辑";

        const applyBtn = document.createElement("button");
        applyBtn.type = "button";
        applyBtn.dataset.applyPack = pack.id;
        applyBtn.disabled = Boolean(view.pendingApplyPackId);
        applyBtn.textContent = pack.id === view.pendingApplyPackId ? "应用中" : isActive ? "重新应用" : "应用";

        actions.append(editBtn, applyBtn);
        card.append(identity, meta, actions);
        return card;
      })
    );
  }

  function renderFirstUsePanel() {
    if (!els.firstUsePanel) return;
    const shouldShow = view.packs.length <= 1;
    els.firstUsePanel.hidden = !shouldShow;
    if (shouldShow) {
      els.firstUsePanel.dataset.state = view.packs.length ? "starter-pack" : "empty";
    } else {
      delete els.firstUsePanel.dataset.state;
    }
  }

  function buildPackListEmptyState() {
    const box = document.createElement("div");
    box.className = "pack-list-empty glass-card";
    const title = document.createElement("strong");
    title.textContent = "暂无角色包";
    const note = document.createElement("p");
    note.textContent = "可以先新建一个草稿角色，再逐步补人设、立绘和校准。";
    box.append(title, note);
    return box;
  }

  function renderPackDetails(pack) {
    const readiness = buildPackReadinessIssues(pack);
    if (els.detailEditPack) els.detailEditPack.disabled = !pack;
    if (els.detailTestPack) els.detailTestPack.disabled = !pack;
    if (!pack) {
      els.packDetailList.replaceChildren(buildReadinessRow({
        ok: false,
        label: "选择一个角色",
        detail: "从左侧列表选择角色后，这里会显示接下来要补什么。",
      }));
      return;
    }
    const rows = [
      { ok: true, label: "角色名称", detail: getPackName(pack) || "未命名角色" },
      { ok: Boolean(pack.defaultOutfit), label: "默认服装", detail: pack.defaultOutfit ? displayAssetName(pack.defaultOutfit) : "还没指定" },
      { ok: Boolean(pack.defaultEmotion), label: "默认表情", detail: pack.defaultEmotion ? displayAssetName(pack.defaultEmotion) : "还没指定" },
      { ok: Number(pack.assetCount || 0) > 0, label: "立绘资源", detail: pack.assetCount ? `${pack.assetCount} 个文件` : "还没有可用图片" },
      { ok: !readiness.length, label: "可用状态", detail: readiness.length ? `建议先补：${readiness.map((item) => item.label).join("、")}` : "可以继续编辑或应用到桌宠" },
    ];
    const technical = [
      ["角色包 ID", pack.id || "-"],
      ["角色 ID", pack.characterId || "-"],
      ["结构版本", pack.schemaVersion || "-"],
      ["文件来源", pack.source || "-"],
    ];
    els.packDetailList.replaceChildren(
      ...rows.map((row) => buildReadinessRow(row)),
      buildTechnicalDetails(technical)
    );
  }

  function selectWorkshopPack(packId) {
    const id = String(packId || "").trim();
    if (!setWorkspacePack(id)) return;
    render();
    setStatus(`已选择：${getPackName(findPack(id)) || id}`);
  }

  function setStatus(message) {
    els.status.textContent = String(message || "");
  }

  function pulsePackCard(packId) {
    const id = String(packId || "").trim();
    if (!id || !els.packList) return;
    const card = Array.from(els.packList.querySelectorAll("[data-pack-id]"))
      .find((item) => item.dataset.packId === id);
    flashElement(card, "switch-flash");
  }

  return { render, renderPackList, selectWorkshopPack, setStatus, pulsePackCard };
}
