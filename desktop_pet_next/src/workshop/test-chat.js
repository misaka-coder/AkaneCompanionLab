import { getPackName } from "./pack-model.js";
import { formatTestLayoutSummary, normalizeResponseSegments, splitDisplayText, findTestOutfitAsset, collectTestAvailableEmotions } from "./test-model.js";
import { summarizeList, summarizeText, asArray, formatError, sanitizeScopePart } from "./shared.js";
import { buildText } from "./dom.js";
import { createScaledPetPreview } from "../scaled-pet-preview.js";
import { readResponseError } from "./transport.js";
import { WORKSHOP_TEST_SCOPE_PREFIX } from "./constants.js";

export function createTestChatController({
  findPack,
  view,
  els,
  isTauriRuntime,
  assetState,
  invoke,
  testState,
  getPortraitImageUrl,
  hasBackendPendingPersonaChanges,
  saveDraft,
  applyPack,
  requestWorkshopThink,
  consumeWorkshopThinkStream,
  collectFormData,
  loadDraft,
}) {
  function loadTestChatTab(packId) {
    const pack = findPack(packId);
    if (!pack) {
      showTestChatEmpty();
      return;
    }
    if (view.testPackId && view.testPackId !== packId) {
      cancelTestRequest();
      view.testMessages = [];
      view.testLastEmotion = "";
      view.testLastSegments = [];
      els.testChatInput.value = "";
      setTestStatus("等待测试输入。");
    }
    view.testPackId = packId;
    ensureTestScope(packId);
    if (els.testChatContent) els.testChatContent.hidden = false;
    if (els.testChatEmptyState) els.testChatEmptyState.hidden = true;
    renderTestChatView(packId);
    if (!view.testAssetsByPack[packId]) {
      void refreshTestVisualAssets(packId);
    }
  }

  function showTestChatEmpty() {
    cancelTestRequest();
    view.testPackId = "";
    if (els.testChatContent) els.testChatContent.hidden = true;
    if (els.testChatEmptyState) els.testChatEmptyState.hidden = false;
    renderTestDiagnostics(null, null);
    updateTestChatControls();
  }

  function renderTestChatView(packId = view.testPackId) {
    if (packId !== view.testPackId || view.activeTab !== "test") return;
    const pack = findPack(packId);
    if (!pack) {
      showTestChatEmpty();
      return;
    }
    const scope = ensureTestScope(packId);
    renderTestDiagnostics(pack, scope);
    renderTestChatTranscript();
    updateTestChatControls();
  }

  function renderTestDiagnostics(pack, scope) {
    const packId = pack?.id || "";
    if (els.testScopeCharacter) {
      els.testScopeCharacter.textContent = getPackName(pack) || "未选择";
      els.testScopeCharacter.title = packId || "";
    }
    if (els.testScopeSession) {
      els.testScopeSession.textContent = scope?.sessionId ? "独立测试会话" : "-";
      els.testScopeSession.title = scope?.sessionId || "";
    }
    if (els.testScopeProfile) {
      els.testScopeProfile.textContent = scope?.profileUserId ? "不会混入正式记忆" : "-";
      els.testScopeProfile.title = scope?.profileUserId || "";
    }
    if (els.testResponseEmotion) {
      els.testResponseEmotion.textContent = view.testLastEmotion || "-";
    }
    const visual = pack ? getTestVisualState(pack) : null;
    if (els.testVisualOutfit) {
      els.testVisualOutfit.textContent = visual?.outfit || "-";
      els.testVisualOutfit.title = visual?.outfit || "";
    }
    if (els.testVisualLayout) {
      els.testVisualLayout.textContent = visual?.layout
        ? formatTestLayoutSummary(visual.layout)
        : "未保存";
    }
    renderTestVisualPreview(pack, visual);
    renderPromptFieldSummary(pack);
    renderTestResponseSegments(view.testLastSegments);
  }

  function renderPromptFieldSummary(pack) {
    if (!els.testPromptFields) return;
    if (!pack) {
      els.testPromptFields.textContent = "请选择角色包。";
      return;
    }
    const profile = getEffectiveTestProfile(pack);
    const identity = profile.identity || {};
    const persona = profile.persona_form || {};
    const rows = [
      ["角色名称", identity.name || pack.name || "-"],
      ["应用显示名", identity.app_name || identity.appName || pack.name || "-"],
      ["用户称呼", identity.user_title || identity.userTitle || "-"],
      ["自称", identity.self_reference || identity.selfReference || "-"],
      ["关系", identity.relationship || "-"],
      ["性格关键词", summarizeList(persona.personality_keywords)],
      ["角色核心", summarizeText(persona.character_core || persona.characterCore)],
      ["行为倾向", summarizeText(persona.behavior_style || persona.behaviorStyle)],
      ["说话风格", summarizeText(persona.speaking_style || persona.speakingStyle)],
      ["口头禅", `${asArray(persona.catchphrases).length} 条`],
      ["边界", summarizeText(persona.boundaries)],
      ["互动原则", summarizeText(persona.interaction_principles || persona.interactionPrinciples)],
      ["点击反馈台词", `${asArray(persona.example_lines).length} 条`],
    ];
    els.testPromptFields.replaceChildren(
      ...rows.map(([label, value]) => {
        const row = document.createElement("div");
        row.append(buildText("span", label), buildText("strong", value));
        return row;
      })
    );
  }

  function renderTestResponseSegments(segments) {
    if (!els.testResponseSegments) return;
    const items = Array.isArray(segments) ? segments.filter(Boolean) : [];
    if (!items.length) {
      els.testResponseSegments.textContent = "-";
      return;
    }
    els.testResponseSegments.replaceChildren(
      ...items.map((segment) => buildText("p", segment))
    );
  }

  async function refreshTestVisualAssets(packId) {
    if (!isTauriRuntime || !packId) return;
    if (assetState.testAssetRequests.has(packId)) return assetState.testAssetRequests.get(packId);
    const revision = assetState.portraitAssetRevisions.get(packId) || 0;
    const request = Promise.resolve().then(async () => {
    try {
      const items = await invoke("list_pack_assets", { packId });
      if ((assetState.portraitAssetRevisions.get(packId) || 0) !== revision) return;
      view.testAssetsByPack[packId] = Array.isArray(items) ? items : [];
    } catch (error) {
      if ((assetState.portraitAssetRevisions.get(packId) || 0) !== revision) return;
      // Keep a failed read retryable instead of caching a false empty state.
      if (view.activeTab === "test" && view.testPackId === packId) {
        setTestStatus(`立绘预览读取失败：${formatError(error)}。重新进入此页可重试。`);
      }
    } finally {
      if (assetState.testAssetRequests.get(packId) === request) assetState.testAssetRequests.delete(packId);
    }
    if (view.activeTab === "test" && view.testPackId === packId) {
      renderTestChatView(packId);
    }
    });
    assetState.testAssetRequests.set(packId, request);
    return request;
  }

  function renderTestVisualPreview(pack, visual = null) {
    if (!els.testVisualPreview) return;
    if (!testState.testPreview) {
      const host = document.createElement("div");
      host.className = "test-preview-host";
      const info = document.createElement("p");
      info.className = "test-visual-info";
      els.testVisualPreview.replaceChildren(host, info);
      testState.testPreview = createScaledPetPreview({ host });
    }
    const token = ++testState.testPreviewImageToken;
    const info = els.testVisualPreview.querySelector(".test-visual-info");
    if (!pack) {
      testState.testPreview.clear("请选择角色包。");
      testState.testPreviewTarget = "";
      info.textContent = "";
      return;
    }
    const state = visual || getTestVisualState(pack);
    const emotion = findTestVisualEmotionAsset(pack, state);
    const target = `${pack.id}::${state.outfit}`;
    if (testState.testPreviewTarget !== target) testState.testPreview.clear();
    testState.testPreviewTarget = target;
    testState.testPreview.setLayout(state.layout || null, { petScale: view.desktopScale });
    testState.testPreview.setText(view.testLastSegments[0]);
    info.textContent = `${state.outfit || "-"} / ${state.emotion || "-"} · ${state.layout ? "已加载校准" : "未保存校准"}`;
    if (!emotion?.id) {
      testState.testPreview.clear("未找到默认立绘资源");
      return;
    }
    void getPortraitImageUrl(pack.id, state.outfit, emotion).then((url) => {
      if (token !== testState.testPreviewImageToken) return;
      testState.testPreview.setExpression({ id: emotion.id, name: emotion.name || emotion.id, url });
    }).catch((error) => {
      if (token !== testState.testPreviewImageToken) return;
      testState.testPreview.clear(`立绘读取失败：${formatError(error)}`);
    });
  }

  function renderTestChatTranscript() {
    if (!els.testChatLog) return;
    if (!view.testMessages.length) {
      const empty = document.createElement("p");
      empty.className = "test-chat-placeholder";
      empty.textContent = "还没有测试消息。";
      els.testChatLog.replaceChildren(empty);
      return;
    }
    els.testChatLog.replaceChildren(
      ...view.testMessages.map((message) => {
        const item = document.createElement("article");
        item.className = `test-message ${message.role === "user" ? "user" : "assistant"}`;
        const title = document.createElement("header");
        title.append(
          buildText("strong", message.role === "user" ? "你" : getPackName(findPack(view.testPackId)) || "角色"),
          buildText("span", message.status || "")
        );
        item.append(title);
        const segments = Array.isArray(message.segments) && message.segments.length
          ? message.segments
          : [message.text || ""].filter(Boolean);
        for (const segment of segments) {
          item.append(buildText("p", segment));
        }
        if (message.emotion) {
          const footer = document.createElement("footer");
          footer.textContent = `表情：${message.emotion}`;
          item.append(footer);
        }
        if (message.error) item.append(buildText("p", message.error));
        return item;
      })
    );
    els.testChatLog.scrollTop = els.testChatLog.scrollHeight;
  }

  function beginTestRequest(packId) {
    if (testState.activeTestRequest || view.testRunning || view.testPackId !== packId
      || view.workspacePackId !== packId || view.activeTab !== "test") return null;
    const request = { packId, scope: ensureTestScope(packId), controller: new AbortController(), message: null };
    testState.activeTestRequest = request;
    view.testRunning = true;
    updateTestChatControls();
    return request;
  }

  function isTestRequestCurrent(request) {
    return testState.activeTestRequest === request && !request.controller.signal.aborted
      && view.testPackId === request.packId && view.workspacePackId === request.packId
      && view.testScopes[request.packId] === request.scope;
  }

  function cancelTestRequest() {
    const request = testState.activeTestRequest;
    testState.activeTestRequest = null;
    if (request) {
      request.controller.abort();
      if (request.message) request.message.status = "已停止接收";
    }
    view.testRunning = false;
    updateTestChatControls();
  }

  function finishTestRequest(request) {
    if (testState.activeTestRequest !== request) return;
    testState.activeTestRequest = null;
    view.testRunning = false;
    renderTestChatView(request.packId);
  }

  async function applyTestPackToDesktop() {
    const packId = String(view.testPackId || "").trim();
    const pack = findPack(packId);
    if (!pack) { setTestStatus("请先选择一个角色包。"); return; }
    if (!isTauriRuntime) { setTestStatus("浏览器预览模式不支持应用到桌宠。"); return; }
    const request = beginTestRequest(packId);
    if (!request) return;
    try {
      if (hasBackendPendingPersonaChanges(packId)) {
        setTestStatus("正在保存当前人设字段。");
        const saveResult = await saveDraft({ allowLocalFallback: false, packId });
        if (!isTestRequestCurrent(request)) return;
        if (!saveResult?.ok) {
          setTestStatus("应用已暂停：当前人设没有写入角色包文件。");
          return;
        }
      }
      if (!isTestRequestCurrent(request)) return;
      setTestStatus(`正在应用到桌宠：${getPackName(pack) || packId}`);
      const result = await applyPack(packId);
      if (!isTestRequestCurrent(request)) return;
      setTestStatus(result?.ok ? `已应用到桌宠：${getPackName(pack) || packId}`
        : `应用失败：${result?.error || result?.reason || "unknown error"}`);
    } catch (error) {
      if (isTestRequestCurrent(request)) setTestStatus(`应用失败：${formatError(error)}`);
    } finally {
      finishTestRequest(request);
    }
  }

  async function sendTestChatMessage() {
    const packId = String(view.testPackId || "").trim();
    const pack = findPack(packId);
    const text = String(els.testChatInput?.value || "").trim();
    if (!pack) { setTestStatus("请先选择一个角色包。"); return; }
    if (!text) { setTestStatus("请输入测试内容。"); return; }
    const request = beginTestRequest(packId);
    if (!request) return;
    try {
      if (hasBackendPendingPersonaChanges(packId)) {
        setTestStatus("正在保存当前人设字段。");
        const saveResult = await saveDraft({ allowLocalFallback: false, packId });
        if (!isTestRequestCurrent(request)) return;
        if (!saveResult?.ok) {
          setTestStatus("测试已暂停：当前人设没有写入角色包文件。");
          return;
        }
      }
      if (!isTestRequestCurrent(request)) return;
      const requestPack = findPack(packId) || pack;
      view.testMessages.push({ role: "user", text, status: "已发送" });
      const assistantMessage = { role: "assistant", text: "", segments: [], emotion: "", status: "等待回复" };
      request.message = assistantMessage;
      view.testMessages.push(assistantMessage);
      view.testLastEmotion = "";
      view.testLastSegments = [];
      els.testChatInput.value = "";
      renderTestChatView(packId);
      setTestStatus("正在请求 /think。");
      const response = await requestWorkshopThink({ pack: requestPack, scope: request.scope,
        message: text, signal: request.controller.signal });
      if (!isTestRequestCurrent(request)) { await response.body?.cancel?.(); return; }
      if (!response.ok) throw new Error(await readResponseError(response, `HTTP ${response.status}`));
      await consumeWorkshopThinkStream(response, assistantMessage, request);
      if (!isTestRequestCurrent(request)) return;
      if (!assistantMessage.text && !assistantMessage.segments.length) throw new Error("未收到回复内容");
      assistantMessage.status = "完成";
      setTestStatus("测试回复完成。");
    } catch (error) {
      if (!isTestRequestCurrent(request)) return;
      if (request.message) {
        request.message.status = "失败";
        request.message.error = `请求失败：${formatError(error)}`;
      }
      setTestStatus(`测试失败：${formatError(error)}`);
    } finally {
      finishTestRequest(request);
    }
  }

  function applyTestPayload(payload, assistantMessage) {
    if (!payload || typeof payload !== "object") return;
    const segments = normalizeResponseSegments(payload.speech_segments || payload.segments);
    if (segments.length) {
      assistantMessage.segments = segments;
      assistantMessage.text = segments.join("");
    } else {
      const speech = String(payload.speech || payload.text || "").trim();
      if (speech) {
        assistantMessage.text = speech;
        assistantMessage.segments = [];
      }
    }
    const emotion = String(payload.emotion || "").trim();
    if (emotion) assistantMessage.emotion = emotion;
  }

  function updateTestMessageDiagnostics(message) {
    view.testLastEmotion = String(message.emotion || view.testLastEmotion || "").trim();
    view.testLastSegments = Array.isArray(message.segments) && message.segments.length
      ? message.segments
      : splitDisplayText(message.text);
    if (els.testResponseEmotion) els.testResponseEmotion.textContent = view.testLastEmotion || "-";
    renderTestResponseSegments(view.testLastSegments);
  }

  async function* readNdjsonEvents(response) {
    const reader = response.body?.getReader?.();
    if (!reader) {
      const raw = await response.text();
      for (const line of raw.split(/\r?\n/)) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try { yield JSON.parse(trimmed); } catch { /* skip malformed line */ }
      }
      return;
    }

    try {
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split(/\r?\n/);
        buffer = lines.pop() || "";
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          try { yield JSON.parse(trimmed); } catch { /* skip malformed line */ }
        }
      }
      buffer += decoder.decode();
      const tail = buffer.trim();
      if (tail) {
        try { yield JSON.parse(tail); } catch { /* skip malformed tail */ }
      }
    } finally {
      try { await reader.cancel(); } finally { reader.releaseLock(); }
    }
  }

  function resetTestChatSession() {
    cancelTestRequest();
    const packId = String(view.testPackId || view.activePackId || "").trim();
    if (!packId) {
      showTestChatEmpty();
      return;
    }
    delete view.testScopes[packId];
    view.testMessages = [];
    view.testLastEmotion = "";
    view.testLastSegments = [];
    ensureTestScope(packId);
    renderTestChatView(packId);
    setTestStatus("已创建新的隔离测试会话。");
  }

  function ensureTestScope(packId) {
    const id = sanitizeScopePart(packId || "character");
    if (!view.testScopes[packId]) {
      const suffix = `${Date.now().toString(36)}_${crypto.randomUUID().slice(0, 8)}`;
      view.testScopes[packId] = {
        sessionId: `${WORKSHOP_TEST_SCOPE_PREFIX}_${id}_${suffix}`,
        profileUserId: `${WORKSHOP_TEST_SCOPE_PREFIX}_profile_${id}_${suffix}`,
      };
    }
    return view.testScopes[packId];
  }

  function buildTestCurrentVisual(pack) {
    const visual = getTestVisualState(pack);
    return {
      character_pack_id: pack.id,
      emotion: visual.emotion,
      character: {
        character_pack_id: pack.id,
        outfit: visual.outfit,
        layout: visual.layout || null,
        available_emotions: visual.availableEmotions,
      },
      scene: {},
      available_emotions: visual.availableEmotions.map((item) => item.id),
    };
  }

  function getTestVisualState(pack) {
    const profile = pack?._rawProfile && typeof pack._rawProfile === "object" ? pack._rawProfile : {};
    const appearance = profile.appearance && typeof profile.appearance === "object" ? profile.appearance : {};
    const layoutOutfits = profile.layout?.outfits && typeof profile.layout.outfits === "object"
      ? profile.layout.outfits
      : {};
    const assets = Array.isArray(view.testAssetsByPack[pack?.id]) ? view.testAssetsByPack[pack.id] : [];
    const defaultOutfit = String(pack?.defaultOutfit || appearance.default_outfit || appearance.defaultOutfit || "").trim();
    const assetOutfit = findTestOutfitAsset(assets, defaultOutfit) || assets[0] || null;
    const firstLayoutOutfit = Object.keys(layoutOutfits)[0] || "";
    const outfit = String(defaultOutfit || assetOutfit?.id || firstLayoutOutfit || "").trim();
    const resolvedOutfitAsset = findTestOutfitAsset(assets, outfit) || assetOutfit;
    const defaultEmotion = String(
      view.testLastEmotion ||
        pack?.defaultEmotion ||
        appearance.default_emotion ||
        appearance.defaultEmotion ||
        ""
    ).trim();
    const firstAssetEmotion = String(resolvedOutfitAsset?.emotions?.[0]?.id || "").trim();
    const emotion = String(defaultEmotion || firstAssetEmotion || "normal").trim();
    const layout = layoutOutfits[outfit] || layoutOutfits[defaultOutfit] || null;
    return {
      outfit,
      emotion,
      layout: layout && typeof layout === "object" ? layout : null,
      availableEmotions: collectTestAvailableEmotions(appearance, resolvedOutfitAsset, emotion),
    };
  }

  function findTestVisualEmotionAsset(pack, visual) {
    const assets = Array.isArray(view.testAssetsByPack[pack?.id]) ? view.testAssetsByPack[pack.id] : [];
    const outfit = findTestOutfitAsset(assets, visual?.outfit) || assets[0] || null;
    const emotions = Array.isArray(outfit?.emotions) ? outfit.emotions : [];
    const id = String(visual?.emotion || "").trim();
    return emotions.find((item) => String(item?.id || "").trim() === id) || emotions[0] || null;
  }

  function getEffectiveTestProfile(pack) {
    if (hasBackendPendingPersonaChanges(pack?.id)) {
      const data = pack.id === view.editingPackId ? collectFormData() : loadDraft(pack.id);
      return {
        ...(pack?._rawProfile || {}),
        identity: { ...((pack?._rawProfile || {}).identity || {}), ...data.identity },
        persona_form: { ...((pack?._rawProfile || {}).persona_form || {}), ...data.persona_form },
      };
    }
    return pack?._rawProfile || {};
  }

  function setTestStatus(message) {
    if (els.testChatStatus) els.testChatStatus.textContent = String(message || "");
  }

  function updateTestChatControls() {
    const disabled = view.testRunning || !view.testPackId;
    if (els.testChatSend) els.testChatSend.disabled = disabled;
    if (els.testChatInput) els.testChatInput.disabled = view.testRunning || !view.testPackId;
    if (els.testChatClear) {
      els.testChatClear.disabled = !view.testPackId;
      els.testChatClear.textContent = view.testRunning ? "停止接收并新建会话" : "新测试会话";
    }
    if (els.testApplyPack) els.testApplyPack.disabled = disabled || !isTauriRuntime;
  }

  return { loadTestChatTab, showTestChatEmpty, renderTestChatView, renderTestChatTranscript, isTestRequestCurrent, cancelTestRequest, applyTestPackToDesktop, sendTestChatMessage, applyTestPayload, updateTestMessageDiagnostics, readNdjsonEvents, resetTestChatSession, buildTestCurrentVisual };
}
