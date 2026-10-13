import { createWorkspaceView } from "../src/workspace/view.js";
import { createWorkspaceApplication } from "../src/workspace/application.js";

export function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
export const tick = () => new Promise(resolve => setImmediate(resolve));
export const json = payload => new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json" } });
export const summary = title => ({ sections: { files: [{ id: title, handle: title, title, can_open: true, can_clear: true }] } });

class Element {
  constructor(tagName) {
    this.tagName = tagName; this.children = []; this.dataset = {}; this.value = "";
    this.disabled = false; this.hidden = false; this.text = ""; this.listeners = new Map();
    this.classList = { add: name => { this.className = `${this.className || ""} ${name}`.trim(); } };
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(""); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text = ""; this.children = [...children]; }
  querySelectorAll(selector) {
    const descendants = this.children.flatMap(child => [child, ...child.querySelectorAll(selector)]);
    return descendants.filter(child => selector === "button" ? child.tagName === "button"
      : selector === 'button[type="submit"]' && child.tagName === "button" && child.type === "submit");
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  focus() { this.focused = true; }
  addEventListener(event, callback) {
    if (!this.listeners.has(event)) this.listeners.set(event, new Set());
    this.listeners.get(event).add(callback);
  }
  removeEventListener(event, callback) { this.listeners.get(event)?.delete(callback); }
}

export function testView() {
  const ids = ["workspace-summary", "clear-workspace-files", "refresh-workspace", "close-workspace", "workspace-alert",
    "project-workspace-summary", "project-workspace-list", "bind-project-directory", "create-project-form", "project-display-name",
    "file-count", "output-count", "workspace-music", "workspace-content", "workspace-session", "workspace-updated", "workspace-status"];
  const elements = new Map(ids.map(id => [id, new Element("div")]));
  const document = new Element("document");
  document.createElement = name => new Element(name);
  document.querySelector = selector => elements.get(selector.slice(1)) || null;
  const submit = new Element("button"); submit.type = "submit";
  elements.get("create-project-form").append(submit);
  return createWorkspaceView(document);
}

export function workspaceHarness(options = {}) {
  const view = testView(), callbacks = new Set(), refreshCallbacks = new Set(), timers = new Map();
  const calls = [], requests = [];
  let nextTimer = 0;
  const host = {
    document: view.document, window: new Element("window"), now: () => 10_000,
    state: { instanceId: "host-a", hostId: "host-a", backendUrl: "http://127.0.0.1:9999", boundBotId: "bot-a",
      sessionId: "session-a", profileUserId: "master", characterPackId: "akane" },
    setTimeout: callback => { const id = ++nextTimer; timers.set(id, callback); return id; },
    clearTimeout: id => timers.delete(id),
    confirm: () => true, copy: async () => {}, close: async () => {}, notifyRefresh: async () => {},
    loadState: async () => ({ ...host.state }),
    sendCommand: async () => {},
    listenSnapshots: async callback => { callbacks.add(callback); return () => callbacks.delete(callback); },
    listenRefresh: async callback => { refreshCallbacks.add(callback); return () => refreshCallbacks.delete(callback); },
    invoke: async (command, args) => {
      calls.push({ command, args });
      if (options.invoke) return options.invoke(command, args);
      if (command === "verify_backend_instance") return { ok: true, instanceId: "host-a" };
      if (command === "manage_project_workspaces") return { ok: true, result: { workspaces: [] } };
      throw Error(`Unexpected fixture command: ${command}`);
    },
    fetch: async (input, init) => {
      requests.push({ input, init });
      return options.fetch ? options.fetch(input, init) : json(summary("current"));
    }
  };
  Object.assign(host, options.host || {});
  const app = createWorkspaceApplication({ host, view });
  function publish(partial = {}, extra = {}) {
    Object.assign(host.state, partial);
    for (const callback of callbacks) callback({ state: { ...host.state }, ...extra });
  }
  function flushTimers() { for (const [id, callback] of [...timers]) { timers.delete(id); callback(); } }
  return { host, view, app, publish, flushTimers, calls, requests, callbacks, refreshCallbacks, timers };
}
