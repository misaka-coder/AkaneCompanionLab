export function createUiShellHelpers({
  state,
  appEl,
  historyListEl,
  dialogueTurnsEl,
  choicesHeadingEl,
  choicesRowEl,
  sessionListEl,
  dialogueCodeEl,
  instantToggleEl,
  instantToggleLabelEl,
  voiceToggleEl,
  voiceToggleLabelEl,
  inputEl,
  sendButtonEl,
  newSessionButtonEl,
  renameSessionButtonEl,
  copyIdentityButtonEl,
  importIdentityButtonEl,
  composerDockEl,
  composerToggleEl,
  settingsToggleEl,
  settingsPanelEl,
  settingsBackdropEl,
  settingsStatusCopyEl,
  clearChildren,
  persistVoiceEnabledPreference,
  stopVoicePlayback,
  getCurrentSessionId,
  onSwitchSession,
  onChooseMessage,
  DEFAULT_SPEAKER,
}) {
  function setHistoryOpen(flag) {
    appEl.dataset.historyOpen = String(Boolean(flag));
  }

  function setSettingsOpen(flag) {
    const isOpen = Boolean(flag);
    appEl.dataset.settingsOpen = String(isOpen);
    settingsToggleEl?.setAttribute("aria-expanded", String(isOpen));
    settingsPanelEl?.setAttribute("aria-hidden", String(!isOpen));
    settingsBackdropEl?.setAttribute("aria-hidden", String(!isOpen));
  }

  function updateSettingsStatusCopy() {
    if (!settingsStatusCopyEl) return;
    settingsStatusCopyEl.textContent = "你的偏好会保存在这个浏览器里。";
  }

  function setInstantText(flag) {
    state.instantText = Boolean(flag);
    instantToggleEl.setAttribute("aria-pressed", String(state.instantText));
    if (instantToggleLabelEl) {
      instantToggleLabelEl.textContent = state.instantText ? "瞬显 开" : "瞬显 关";
    } else {
      instantToggleEl.textContent = state.instantText ? "瞬显 开" : "瞬显 关";
    }
  }

  function setVoiceEnabled(flag) {
    state.voiceEnabled = Boolean(flag);
    voiceToggleEl.setAttribute("aria-pressed", String(state.voiceEnabled));
    if (voiceToggleLabelEl) {
      voiceToggleLabelEl.textContent = state.voiceEnabled ? "语音 开" : "语音 关";
    } else {
      voiceToggleEl.textContent = state.voiceEnabled ? "语音 开" : "语音 关";
    }
    persistVoiceEnabledPreference();

    if (!state.voiceEnabled) {
      void stopVoicePlayback();
    }
  }

  function renderSessionList() {
    if (!sessionListEl) return;
    clearChildren(sessionListEl);

    if (!state.sessions.length) {
      const empty = document.createElement("p");
      empty.className = "panel-empty";
      empty.textContent = "还没有可切换的对话。";
      sessionListEl.appendChild(empty);
      return;
    }

    const currentSessionId = getCurrentSessionId();
    for (const session of state.sessions) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "session-entry";
      if (session.sessionId === currentSessionId) {
        button.classList.add("is-active");
        button.setAttribute("aria-current", "true");
      }
      button.textContent = session.displayTitle;
      button.disabled = state.sending;
      button.addEventListener("click", () => {
        void onSwitchSession(session.sessionId);
      });
      sessionListEl.appendChild(button);
    }
  }

  function renderHistory() {
    clearChildren(historyListEl);
    const recentEntries = state.history.slice(-24);

    if (!recentEntries.length) {
      const empty = document.createElement("p");
      empty.className = "panel-empty";
      empty.textContent = "还没有新的对白。";
      historyListEl.appendChild(empty);
      return;
    }

    for (const entry of recentEntries) {
      const article = document.createElement("article");
      article.className = `history-entry history-entry--${entry.kind}`;

      const speaker = document.createElement("span");
      speaker.className = "history-entry__speaker";
      speaker.textContent = entry.speaker;

      const text = document.createElement("div");
      text.className = "history-entry__text";
      text.textContent = entry.content;

      const code = document.createElement("pre");
      code.className = "history-entry__code";
      code.textContent = String(entry.codeSnippet || "").trim();
      code.hidden = !code.textContent;

      article.appendChild(speaker);
      article.appendChild(text);
      article.appendChild(code);
      historyListEl.appendChild(article);
    }

    historyListEl.scrollTop = historyListEl.scrollHeight;
  }

  function renderDialogueTurns(turns) {
    clearChildren(dialogueTurnsEl);
    if (!turns.length) {
      const empty = document.createElement("p");
      empty.className = "panel-empty";
      empty.textContent = "等待角色开始说话……";
      dialogueTurnsEl.appendChild(empty);
      return;
    }

    for (const [index, turn] of turns.entries()) {
      const article = document.createElement("article");
      article.className = "turn-card";
      if (index === turns.length - 1) {
        article.classList.add("is-active");
      }

      const speaker = document.createElement("span");
      speaker.className = "turn-card__speaker";
      speaker.textContent = turn.speaker;

      const text = document.createElement("div");
      text.className = "turn-card__text";
      text.textContent = turn.speech;

      const code = document.createElement("pre");
      code.className = "turn-card__code";
      code.textContent = String(turn.codeSnippet || "").trim();
      code.hidden = !code.textContent;

      article.appendChild(speaker);
      article.appendChild(text);
      article.appendChild(code);
      dialogueTurnsEl.appendChild(article);
    }
  }

  function renderChoices(choices) {
    clearChildren(choicesRowEl);
    const normalized = Array.isArray(choices)
      ? choices
          .map((item) => ({
            id: String(item?.id || ""),
            text: String(item?.text || "").trim(),
          }))
          .filter((item) => item.text)
      : [];

    choicesRowEl.hidden = normalized.length === 0;
    choicesHeadingEl.hidden = normalized.length === 0;
    if (!normalized.length) {
      return;
    }

    for (const choice of normalized) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "choice-button message-action";
      button.textContent = choice.text;
      button.addEventListener("click", () => {
        void onChooseMessage(choice.text);
      });
      choicesRowEl.appendChild(button);
    }
  }

  function normalizeDialogueTurns(payload) {
    const turns = Array.isArray(payload?.dialogue_turns) ? payload.dialogue_turns : [];
    const normalized = turns
      .map((turn) => ({
        speaker: String(turn?.speaker || "").trim(),
        speech: String(turn?.speech || "").trim(),
        codeSnippet: String(turn?.code_snippet || turn?.codeSnippet || "").trim(),
      }))
      .filter((turn) => turn.speaker && turn.speech);

    if (normalized.length) {
      const payloadCodeSnippet = String(payload?.code_snippet || "").trim();
      if (payloadCodeSnippet) {
        normalized[normalized.length - 1].codeSnippet = payloadCodeSnippet;
      }
      return normalized;
    }

    const speechSegments = Array.isArray(payload?.speech_segments)
      ? payload.speech_segments.map((segment) => String(segment || "").trim()).filter(Boolean).slice(0, 3)
      : [];
    if (speechSegments.length) {
      const codeSnippet = String(payload?.code_snippet || "").trim();
      return speechSegments.map((speech, index) => ({
        speaker: DEFAULT_SPEAKER,
        speech,
        codeSnippet: index === speechSegments.length - 1 ? codeSnippet : "",
      }));
    }

    const speech = String(payload?.speech || "").trim();
    const codeSnippet = String(payload?.code_snippet || "").trim();
    return speech ? [{ speaker: DEFAULT_SPEAKER, speech, codeSnippet }] : [];
  }

  function setDialogueCodeSnippet(text) {
    if (!dialogueCodeEl) return;
    const normalized = String(text || "").trim();
    dialogueCodeEl.textContent = normalized;
    dialogueCodeEl.hidden = !normalized;
  }

  function setSendingState(flag) {
    state.sending = flag;
    sendButtonEl.disabled = flag;
    if (newSessionButtonEl) {
      newSessionButtonEl.disabled = flag;
    }
    if (renameSessionButtonEl) {
      renameSessionButtonEl.disabled = flag;
    }
    if (copyIdentityButtonEl) {
      copyIdentityButtonEl.disabled = flag;
    }
    if (importIdentityButtonEl) {
      importIdentityButtonEl.disabled = flag;
    }
    inputEl.disabled = flag;
    sendButtonEl.classList.toggle("is-sending", flag);
    sendButtonEl.textContent = flag ? "发送中..." : "发送";
    document.querySelectorAll(".message-action, .choice-button").forEach((button) => {
      button.disabled = flag;
    });
    document.querySelectorAll(".session-entry").forEach((button) => {
      button.disabled = flag;
    });
  }

  function setComposerExpanded(flag, options = {}) {
    if (!composerDockEl || !composerToggleEl) return;
    const expanded = Boolean(flag);
    composerDockEl.dataset.expanded = String(expanded);
    composerToggleEl.setAttribute("aria-expanded", String(expanded));

    if (expanded && options.focus) {
      requestAnimationFrame(() => {
        inputEl.focus();
        const end = inputEl.value.length;
        inputEl.setSelectionRange(end, end);
      });
    }
  }

  return {
    setHistoryOpen,
    setSettingsOpen,
    updateSettingsStatusCopy,
    setInstantText,
    setVoiceEnabled,
    renderSessionList,
    renderHistory,
    renderDialogueTurns,
    renderChoices,
    normalizeDialogueTurns,
    setDialogueCodeSnippet,
    setSendingState,
    setComposerExpanded,
  };
}
