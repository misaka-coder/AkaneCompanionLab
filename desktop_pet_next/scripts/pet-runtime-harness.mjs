import { createServer } from "vite";

export async function loadPetRuntime() {
  const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: "custom" });
  try {
    const module = await server.ssrLoadModule("/src/pet/application.js");
    return { ...module, load: path => server.ssrLoadModule(path), close: () => server.close() };
  } catch (error) { await server.close(); throw error; }
}

export const tick = () => new Promise(resolve => setImmediate(resolve));
export function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

export function mockBackend({ defaultBotId = "test-bot", stream = [], override } = {}) {
  const requests = [];
  const json = payload => new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json" } });
  const fetch = async (input, init = {}) => {
    const url = new URL(String(input));
    requests.push({ url, init });
    const custom = await override?.(url, init);
    if (custom !== undefined) return custom;
    if (url.pathname === "/health") return json({ status: "ok", root_binding: "valid", instance_id: "local-default" });
    if (url.pathname === "/api/bots") return json({ bots: [{ botId: defaultBotId, available: true }], defaultBotId });
    if (url.pathname.endsWith("/desktop-pet/health")) return json({ status: "ok", capabilities: [], features: { care: { enabled: false } } });
    if (url.pathname.endsWith("/resource-manifest")) return json({ characters: { outfits: [{ id: "猫娘", emotions: [{ id: "正常", path: "/synthetic.png" }] }] } });
    if (url.pathname.endsWith("/sessions/ensure")) return json({ session: {} });
    if (url.pathname.endsWith("/think/stop")) return json({ ok: true });
    if (url.pathname.endsWith("/think")) return new Response(stream.map(event => JSON.stringify(event) + "\n").join(""), { headers: { "Content-Type": "application/x-ndjson" } });
    if (url.pathname.endsWith("/workspace/summary")) return json({ sections: { files: [], outputs: [] } });
    throw new Error(`Unmocked backend route: ${url.pathname}`);
  };
  return { fetch, requests, json };
}

class FakeElement extends EventTarget {
  dataset = {};
  style = { setProperty() {}, removeProperty() {} };
  values = new Set();
  classList = {
    add: (...names) => names.forEach(name => this.values.add(name)),
    remove: (...names) => names.forEach(name => this.values.delete(name)),
    toggle: (name, enabled) => { if (enabled ?? !this.values.has(name)) this.values.add(name); else this.values.delete(name); },
    contains: name => this.values.has(name)
  };
  textContent = "";
  value = "";
  hidden = true;
  paused = true;
  ended = false;
  duration = 2;
  currentTime = 0;
  offsetWidth = 200;
  offsetHeight = 80;
  clientWidth = 200;
  clientHeight = 80;
  scrollHeight = 40;
  attributes = new Map();
  children = [];
  getAttribute(name) { return this.attributes.get(name) || ""; }
  setAttribute(name, value) { this.attributes.set(name, value); }
  removeAttribute(name) { this.attributes.delete(name); if (name === "src") this.src = ""; }
  replaceChildren(...items) { this.children = items; }
  append(...items) { this.children.push(...items); }
  appendChild(item) { this.children.push(item); return item; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  getBoundingClientRect() { return { x: 0, y: 0, width: 200, height: 80, top: 0, left: 0, right: 200, bottom: 80 }; }
  focus() {}
  pause() { this.paused = true; }
  play() { this.paused = false; return Promise.resolve(); }
  load() {}
  matches() { return false; }
  setSelectionRange() {}
}

export function createEnvironment({ fetch, tauri = false, invoke, persistedState = {}, mediaDevices = {}, MediaRecorder } = {}) {
  const elements = {};
  const doc = {
    documentElement: new FakeElement(), activeElement: null,
    createElement: () => new FakeElement(),
    querySelector: () => null
  };
  const window = Object.assign(new EventTarget(), {
    innerWidth: 600, innerHeight: 800, devicePixelRatio: 1,
    setTimeout, clearTimeout, setInterval, clearInterval, queueMicrotask,
    requestAnimationFrame: callback => setTimeout(() => callback(Date.now()), 1),
    cancelAnimationFrame: clearTimeout,
    navigator: { mediaDevices }
  });
  const els = new Proxy(elements, { get(target, key) { return target[key] ??= new FakeElement(); } });
  const nativeHandlers = new Map();
  const nativeCalls = [];
  const platform = {
    window, document: doc, navigator: window.navigator, MediaRecorder, isTauriRuntime: tauri,
    backendFetch: fetch || (async () => { throw new Error("mock backend offline"); }),
    async invoke(command, payload) {
      nativeCalls.push({ command, payload });
      if (invoke) {
        const result = await invoke(command, payload);
        if (result !== undefined) return result;
      }
      if (command === "get_window_geometry") return { x: 0, y: 0, width: 300, height: 400 };
      if (command === "get_client_launch_binding") return { instanceId: "local-default", hostId: "local-default" };
      if (command === "load_pet_state") return { instanceId: "local-default", hostId: "local-default", characterPackId: "akane_v1", boundBotId: "local-default", voiceEnabled: false, ...persistedState };
      if (command === "verify_backend_instance") return { ok: true, instanceId: "local-default" };
      if (command === "list_character_packs") return [];
      return null;
    },
    async tauriCall(command, payload, options) {
      try { return await this.invoke(command, payload); }
      catch (error) { if (!options?.quiet) this.onError?.(command, error); return null; }
    },
    async listen(event, callback) {
      nativeHandlers.set(event, callback);
      return () => nativeHandlers.delete(event);
    },
    emit: async () => {}, emitTo: async () => {},
    convertFileSrc: value => value
  };
  const register = event => callback => platform.listen(event, callback);
  platform.appWindow = tauri ? {
    onMoved: register("moved"), onResized: register("resized"), onDragDropEvent: register("drop"),
    setFocus: async () => {}
  } : null;
  platform.currentMonitor = platform.primaryMonitor = async () => ({
    workArea: { position: { x: 0, y: 0 }, size: { width: 1920, height: 1080 } }
  });
  window.fetch = platform.backendFetch;
  // Browser-only primitives used by the existing visual renderer and CSS helpers.
  globalThis.window = window;
  globalThis.document = doc;
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => "0px" });
  globalThis.ResizeObserver = class { observe() {} disconnect() {} };
  globalThis.Image = class {
    naturalWidth = 200; naturalHeight = 400;
    set src(value) { this.url = value; queueMicrotask(() => this.onload?.()); }
    decode() { return Promise.resolve(); }
  };
  return { platform, elements: els, nativeHandlers, nativeCalls };
}
