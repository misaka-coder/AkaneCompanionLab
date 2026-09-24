import { WorkspacePanel } from "./ui/WorkspacePanel.js";
import { BackendClient } from "./services/BackendClient.js";

const root = document.getElementById("workspace-root");
let client = null;
let panel = null;
let identity = { profileUserId: "master", sessionId: "" };
let currentActivity = null;

function initBackend(backendUrl) {
  client = new BackendClient(backendUrl || "http://127.0.0.1:9999");
}

window.akaneAPI?.onSettingsChanged?.((settings) => {
  if (settings?.backendUrl && client?.baseUrl !== settings.backendUrl) {
    initBackend(settings.backendUrl);
  }
  if (settings?.sessionId) {
    identity = {
      profileUserId: settings.profileUserId || "master",
      sessionId: settings.sessionId,
    };
  }
});

window.akaneAPI?.onWorkspaceChanged?.(() => {
  if (panel) void panel.refresh();
});

window.akaneAPI?.onWorkspaceActivityState?.((activity) => {
  currentActivity = activity || null;
  panel?.updateActivity?.();
});

window.akaneAPI?.onWorkspaceInit?.((initSettings) => {
  const s = initSettings || {};
  initBackend(s.backendUrl || "http://127.0.0.1:9999");
  identity = {
    profileUserId: s.profileUserId || "master",
    sessionId: s.sessionId || "",
  };

  panel = new WorkspacePanel(root, {
    backendClient: client,
    getIdentity: () => identity,
    getCurrentActivity: () => currentActivity,
    onNotice: () => {},
    onActivityAction: (action) => window.akaneAPI?.sendWorkspaceActivityAction?.(action),
    standalone: true,
  });

  // Override close to shut the standalone window
  panel.close = () => window.close();

  void panel.open();
  window.akaneAPI?.requestWorkspaceActivityState?.();
});
