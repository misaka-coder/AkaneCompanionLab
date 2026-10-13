export function capabilityFormPayload(form, actionId) {
  const data = new FormData(form);
  const enabled = data.get("enabled") === "on";
  if (form.dataset.capabilityForm === "provider") {
    return {
      providerId: form.dataset.providerId || "",
      enabled,
      endpoint: String(data.get("endpoint") || "").trim()
    };
  }
  if (form.dataset.capabilityForm === "mcp") {
    const command = String(data.get("command") || "").trim();
    if (actionId === "abilities.mcp.config.save" && !command) {
      const commandInput = form.elements.namedItem("command");
      commandInput?.setCustomValidity("替换 MCP 配置前，请填写完整启动命令。");
      commandInput?.reportValidity();
      commandInput?.addEventListener("input", () => commandInput.setCustomValidity(""), { once: true });
      return null;
    }
    return {
      serverId: form.dataset.serverId || "",
      enabled,
      command,
      displayName: String(data.get("displayName") || "").trim(),
      cwd: String(data.get("cwd") || "").trim(),
      args: String(data.get("args") || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
    };
  }
  if (form.dataset.capabilityForm === "workflow") {
    return {
      workflowId: form.dataset.workflowId || "",
      enabled,
      workflowPath: String(data.get("workflowPath") || "").trim(),
      inputImageSlot: String(data.get("inputImageSlot") || "").trim(),
      outputImageSlot: String(data.get("outputImageSlot") || "").trim()
    };
  }
  if (form.dataset.capabilityForm === "plugin-connection") {
    const values = {};
    const clearFields = new Set(Array.from(form.querySelectorAll("[data-connection-clear]:checked"))
      .map((element) => String(element.dataset.connectionClear || "").trim()).filter(Boolean));
    for (const field of form.querySelectorAll("[data-connection-field]")) {
      const key = String(field.dataset.connectionField || "").trim();
      if (!key || clearFields.has(key)) continue;
      const type = String(field.dataset.connectionType || "string");
      if (type === "boolean") {
        values[key] = Boolean(field.checked);
        continue;
      }
      const raw = String(field.value || "").trim();
      if (!raw) continue;
      if (type === "number" || type === "integer") {
        const number = Number(raw);
        if (!Number.isFinite(number)) {
          field.setCustomValidity("请输入有效数字。");
          field.reportValidity();
          field.addEventListener("input", () => field.setCustomValidity(""), { once: true });
          return null;
        }
        values[key] = type === "integer" ? Math.trunc(number) : number;
      } else {
        values[key] = raw;
      }
    }
    return {
      pluginId: form.dataset.pluginId || "",
      connectionName: form.dataset.connectionName || "",
      expectedRevision: Number(form.dataset.connectionRevision || 0),
      enabled,
      values,
      clearFields: [...clearFields]
    };
  }
  if (form.dataset.capabilityForm === "plugin-stage") {
    const path = String(data.get("pluginPath") || "").trim();
    if (!path) {
      const input = form.elements.namedItem("pluginPath");
      input?.setCustomValidity("请填写当前宿主可以读取的插件路径。");
      input?.reportValidity();
      input?.addEventListener("input", () => input.setCustomValidity(""), { once: true });
      return null;
    }
    if (actionId === "abilities.plugin.stageWheel" && !path.toLowerCase().endsWith(".whl")) {
      const input = form.elements.namedItem("pluginPath");
      input?.setCustomValidity("wheel 文件路径需要以 .whl 结尾。");
      input?.reportValidity();
      input?.addEventListener("input", () => input.setCustomValidity(""), { once: true });
      return null;
    }
    return { path };
  }
  if (form.dataset.capabilityForm === "voice-profile") {
    const providerId = form.dataset.providerId || "";
    if (providerId === "provider.tts.irodori.local") {
      return { providerId, voiceProfileId: String(data.get("voiceProfileId") || "").trim(),
        displayName: String(data.get("displayName") || "").trim(),
        voiceProfileEnabled: data.get("voiceProfileEnabled") === "on",
        irodoriVoiceId: String(data.get("irodoriVoiceId") || "").trim() };
    }
    if (actionId === "abilities.provider.voiceProfile.inspectFolder") {
      const folderPath = String(data.get("folderPath") || "").trim();
      if (!folderPath) {
        const input = form.elements.namedItem("folderPath");
        input?.setCustomValidity("请先填写 GPT-SoVITS 模型目录。");
        input?.reportValidity();
        input?.addEventListener("input", () => input.setCustomValidity(""), { once: true });
        return null;
      }
      return { providerId, folderPath };
    }
    const emotionVoiceMap = collectEmotionVoiceMap(form, data);
    if (emotionVoiceMap === null) return null;
    return {
      providerId,
      voiceProfileId: String(data.get("voiceProfileId") || "").trim(),
      displayName: String(data.get("displayName") || "").trim(),
      voiceProfileEnabled: data.get("voiceProfileEnabled") === "on",
      textLang: String(data.get("textLang") || "").trim(),
      promptLang: String(data.get("promptLang") || "").trim(),
      mediaType: String(data.get("mediaType") || "").trim(),
      refAudioPath: String(data.get("refAudioPath") || "").trim(),
      promptText: String(data.get("promptText") || "").trim(),
      parallelInfer: String(data.get("parallelInfer") || "").trim(),
      splitBucket: String(data.get("splitBucket") || "").trim(),
      batchSize: String(data.get("batchSize") || "").trim(),
      topK: String(data.get("topK") || "").trim(),
      topP: String(data.get("topP") || "").trim(),
      temperature: String(data.get("temperature") || "").trim(),
      speedFactor: String(data.get("speedFactor") || "").trim(),
      fragmentInterval: String(data.get("fragmentInterval") || "").trim(),
      textSplitMethod: String(data.get("textSplitMethod") || "").trim(),
      emotionVoiceMap
    };
  }
  return null;
}

function collectEmotionVoiceMap(form, data) {
  const result = {};
  for (const row of form.querySelectorAll("[data-emotion-sample-row]")) {
    const index = String(row.dataset.emotionSampleRow || "").trim();
    if (!index || data.get(`emotionRemove.${index}`) === "on") continue;
    const emotionId = String(data.get(`emotionId.${index}`) || "").trim();
    const aliases = String(data.get(`emotionAliases.${index}`) || "").split(/[,，;；\s]+/).map((item) => item.trim()).filter(Boolean);
    const refAudioPath = String(data.get(`emotionRefAudioPath.${index}`) || "").trim();
    const promptText = String(data.get(`emotionPromptText.${index}`) || "").trim();
    const configured = row.dataset.emotionConfigured === "true";
    if (!emotionId && !aliases.length && !refAudioPath && !promptText) continue;
    if (!emotionId) {
      const input = row.querySelector('[name^="emotionId."]');
      input?.setCustomValidity("请填写情绪标识。");
      input?.reportValidity();
      input?.addEventListener("input", () => input.setCustomValidity(""), { once: true });
      return null;
    }
    if (!configured && Boolean(refAudioPath) !== Boolean(promptText)) {
      const fieldName = refAudioPath ? "emotionPromptText" : "emotionRefAudioPath";
      const input = row.querySelector(`[name^="${fieldName}."]`);
      input?.setCustomValidity("参考音频和对应文本必须成对填写。");
      input?.reportValidity();
      input?.addEventListener("input", () => input.setCustomValidity(""), { once: true });
      return null;
    }
    if (Object.hasOwn(result, emotionId)) {
      const input = row.querySelector('[name^="emotionId."]');
      input?.setCustomValidity("情绪标识不能重复；请改用匹配别名。");
      input?.reportValidity();
      input?.addEventListener("input", () => input.setCustomValidity(""), { once: true });
      return null;
    }
    result[emotionId] = {
      aliases,
      ...(refAudioPath ? { refAudioPath } : {}),
      ...(promptText ? { promptText } : {})
    };
  }
  return result;
}
