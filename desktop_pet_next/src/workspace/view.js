export function createWorkspaceView(document) {
  let feedbackVersion = 0;
  const els = {
    summary: document.querySelector("#workspace-summary"),
    clearFiles: document.querySelector("#clear-workspace-files"),
    refresh: document.querySelector("#refresh-workspace"),
    close: document.querySelector("#close-workspace"),
    alert: document.querySelector("#workspace-alert"),
    projectSummary: document.querySelector("#project-workspace-summary"),
    projectList: document.querySelector("#project-workspace-list"),
    bindProjectDirectory: document.querySelector("#bind-project-directory"),
    createProjectForm: document.querySelector("#create-project-form"),
    projectDisplayName: document.querySelector("#project-display-name"),
    fileCount: document.querySelector("#file-count"),
    outputCount: document.querySelector("#output-count"),
    music: document.querySelector("#workspace-music"),
    content: document.querySelector("#workspace-content"),
    session: document.querySelector("#workspace-session"),
    updated: document.querySelector("#workspace-updated"),
    status: document.querySelector("#workspace-status")
  };

  function updateIdentityUi(state) {
    const sessionId = String(state?.sessionId || "").trim();
    const short = sessionId ? `${sessionId.slice(0, 10)}…${sessionId.slice(-6)}` : "-";
    els.session.textContent = `Session: ${short}`;
    els.session.title = sessionId;
  }

  function setUpdated(value, { failed = false } = {}) {
    const timestamp = Number(value || 0);
    if (!Number.isFinite(timestamp) || timestamp <= 0) {
      els.updated.textContent = "Last refresh: -";
      return;
    }
    const label = failed ? "Last attempt" : "Last refresh";
    els.updated.textContent = `${label}: ${new Date(timestamp).toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit"
    })}`;
  }

  function setAlert(message, status = "info") {
    const text = String(message || "").trim();
    els.alert.hidden = !text;
    els.alert.textContent = text;
    els.alert.dataset.status = status;
  }

  function setStatus(message) {
    feedbackVersion += 1;
    els.status.textContent = message;
  }

  function beginFeedback(message) {
    const version = ++feedbackVersion;
    if (message) els.status.textContent = message;
    return {
      setStatus: text => { if (version === feedbackVersion) els.status.textContent = text; },
      setAlert: (text, status) => { if (version === feedbackVersion) setAlert(text, status); }
    };
  }

  function buildText(tagName, text) {
    const element = document.createElement(tagName);
    element.textContent = text;
    return element;
  }

  return { document, els, updateIdentityUi, setUpdated, setAlert, setStatus, beginFeedback, buildText };
}

export function kindMark(value) {
  const kind = String(value || "").toLowerCase();
  if (kind.includes("audio") || kind.includes("music") || kind.includes("mp3") || kind.includes("wav")) return "♪";
  if (kind.includes("image") || kind.includes("png") || kind.includes("jpg")) return "图";
  if (kind.includes("text") || kind.includes("md") || kind.includes("doc")) return "文";
  return "物";
}

export function formatSize(bytes) {
  const size = Number(bytes || 0);
  if (!Number.isFinite(size) || size <= 0) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function formatUpdatedAt(value) {
  let timestamp = Number(value || 0);
  if (!Number.isFinite(timestamp) || timestamp <= 0) {
    const parsed = Date.parse(String(value || ""));
    if (!Number.isFinite(parsed)) return "";
    timestamp = parsed;
  }
  return new Date(timestamp < 10_000_000_000 ? timestamp * 1000 : timestamp).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

export function formatError(error) {
  return error instanceof Error ? error.message : String(error);
}
